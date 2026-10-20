"""Extraction, identity and access steps. All entity writes belong to commit."""

from refcollector.contracts.ports import OccurrenceQuery, StepResult
from refcollector.domain.enums import (
    ACCEPTED_IDENTITIES,
    AuditEventType,
    Channel,
    DecisionAction,
    DiscoveryState,
    ExtractionState,
    IdentityState,
    LocalCopyState,
    Origin,
    ReasonCode,
    RetrievalState,
    SourceType,
    StepType,
)
from refcollector.domain.models import Decision, Occurrence, ProjectWork, Provenance, WorkMetadata, utcnow
from refcollector.errors import IntegrationUnavailable
from refcollector.identity.identifiers import normalize_doi
from refcollector.identity.resolver import DefaultIdentityResolver
from refcollector.ids import new_id
from refcollector.pipeline.state import audit, blocking, capture, check_cancel, occurrences, validate
from refcollector.services.changes import _to_candidate


def same_keys(left, right):
    shared = set(left) & set(right)
    return bool(shared) and all(left[key] == right[key] for key in shared)


class ExtractHandler:
    step_type = StepType.EXTRACT

    def __init__(self, extractor):
        self.extractor = extractor

    async def run(self, ctx):
        data = await blocking(capture, ctx)
        source = data["source"]
        expected = ctx.job.snapshot.adapter_versions.get(self.extractor.name)
        if expected != self.extractor.version:
            raise IntegrationUnavailable("Snapshotted extraction adapter is unavailable")
        check_cancel(ctx)
        previous = [
            e
            for e in ctx.store.audit.list(source.project_id, subject_id=source.source_id)
            if e.type == AuditEventType.OCCURRENCES_EXTRACTED
        ]
        last = previous[-1] if previous else None
        if (
            last
            and last.payload.get("source_revision") == source.revision
            and last.payload.get("max_occurrences") == ctx.job.options.get("max_occurrences")
            and last.payload.get("used_cap", 0) == 0
            and source.extraction_adapter == f"{self.extractor.name}/{self.extractor.version}"
        ):

            def reuse(store):
                validate(ctx, store, data)
                rows = [o for o in data["rows"] if o.extraction != ExtractionState.EXCLUDED]
                cap = ctx.job.options.get("max_occurrences")
                remaining = max(0, cap - data["used_cap"]) if cap else len(rows)
                store.occurrences.link_to_job(
                    source.project_id,
                    ctx.job.job_id,
                    [o.occurrence_id for o in rows[:remaining]],
                )
                return source.source_id

            return StepResult(reuse)

        def extract():
            raw = ctx.store.files.path_for(source.project_id, source.sha256).read_bytes()
            payload = raw.decode("utf-8") if source.type == SourceType.TEXT else raw
            return self.extractor.extract(payload, source.type, source.sections or None)

        result = await blocking(extract)
        check_cancel(ctx)

        def commit(store):
            validate(ctx, store, data)
            # Preserve every reviewed identity and edit. Re-extraction supersedes only untouched rows.
            old = data["rows"]
            protected = [
                o
                for o in old
                if o.edited_text is not None
                or o.predecessor_ids
                or o.identity != IdentityState.UNPROCESSED
                or o.extraction == ExtractionState.EXCLUDED
            ]
            protected_text = {o.raw_text for o in protected}
            created = []
            entries = result.entries
            cap = ctx.job.options.get("max_occurrences")
            remaining = max(0, cap - data["used_cap"]) if cap else None
            if cap:
                entries = entries[:remaining]
            for index, entry in enumerate(entries):
                if entry.text in protected_text:
                    continue
                predecessors = [
                    o.occurrence_id for o in old if o not in protected and o.raw_text == entry.text
                ]
                created.append(
                    Occurrence(
                        occurrence_id=new_id("occurrence"),
                        project_id=source.project_id,
                        source_id=source.source_id,
                        section_id=entry.section_id,
                        order=float(index + 1),
                        label=entry.label,
                        raw_text=entry.text,
                        spans=entry.spans,
                        warnings=entry.warnings,
                        extraction=ExtractionState.NEEDS_REVIEW
                        if entry.warnings
                        else ExtractionState.EXTRACTED,
                        predecessor_ids=predecessors,
                    )
                )
            for previous in old:
                if previous in protected:
                    continue
                changed = previous.model_copy(
                    update={"extraction": ExtractionState.EXCLUDED, "excluded_reason": "re-extracted"}
                )
                store.occurrences.update(changed, expected_revision=previous.revision)
            store.occurrences.add_many(created)
            linked = [
                o.occurrence_id for o in [*created, *protected] if o.extraction != ExtractionState.EXCLUDED
            ]
            if remaining is not None:
                linked = linked[:remaining]
            store.occurrences.link_to_job(source.project_id, ctx.job.job_id, linked)
            source.sections = result.sections
            source.extraction_state = ExtractionState.EXTRACTED
            source.extraction_adapter = f"{self.extractor.name}/{self.extractor.version}"
            updated = store.sources.update(source, expected_revision=data["source"].revision)
            audit(
                ctx,
                store,
                AuditEventType.OCCURRENCES_EXTRACTED,
                source.source_id,
                occurrence_ids=linked,
                adapter=source.extraction_adapter,
                source_revision=updated.revision,
                max_occurrences=ctx.job.options.get("max_occurrences"),
                used_cap=data["used_cap"],
            )
            return source.source_id

        return StepResult(commit, result.warnings)


class ResolveHandler:
    step_type = StepType.RESOLVE

    def __init__(self, providers):
        self.providers = providers

    async def run(self, ctx):
        data = await blocking(capture, ctx)
        occ = data["occ"]
        previous_work_id = occ.work_id
        check_cancel(ctx)
        async with self.providers.job(ctx.job) as (_, resolver, _access):
            result = await resolver.resolve(
                occ.text, rejected=[c.metadata for c in data["candidates"] if c.rejected]
            )
        check_cancel(ctx)

        def commit(store):
            validate(ctx, store, data)
            for scored in result.candidates:
                for record in scored.evidence:
                    store.provider_records.upsert(record)
            kept = [c for c in data["candidates"] if c.rejected]
            fresh = [
                _to_candidate(occ.project_id, occ.occurrence_id, c, i + 1)
                for i, c in enumerate(result.candidates)
            ]
            for candidate in fresh:
                candidate.rejected = bool(candidate.features.get("rejected"))
            for i, candidate in enumerate(kept):
                candidate.rank = len(fresh) + i + 1
            store.candidates.replace_for_occurrence(occ.project_id, occ.occurrence_id, [*fresh, *kept])
            explicit = occ.identity in (
                IdentityState.ACCEPTED_AGENT,
                IdentityState.ACCEPTED_USER,
                IdentityState.REJECTED,
            ) or any(
                d.action == DecisionAction.MARK_UNRESOLVED and d.provenance.origin != Origin.AUTOMATION
                for d in data["decisions"]
            )
            approved = occ.identity == IdentityState.ACCEPTED_USER
            accepted = None
            if result.state == IdentityState.ACCEPTED_AUTO and result.accepted_index is not None:
                accepted = fresh[result.accepted_index]
                if accepted.rejected:
                    accepted = None
            if accepted:
                links = {
                    key: normalize_doi(value) if key == "doi" else value
                    for key in ("doi", "openalex_id")
                    if (value := getattr(accepted.metadata, key))
                }
                matched = [
                    work
                    for key, value in links.items()
                    if (work := store.works.find_by_link_key(occ.project_id, key, value))
                ]
                if len({work.work_id for work in matched}) > 1 or any(
                    not same_keys(work.link_keys, links) for work in matched
                ):
                    # One matching identifier cannot override another conflicting identifier.
                    accepted.reasons = list(
                        dict.fromkeys([*accepted.reasons, ReasonCode.PROVIDER_DISAGREEMENT])
                    )
                    store.candidates.replace_for_occurrence(
                        occ.project_id, occ.occurrence_id, [*fresh, *kept]
                    )
                    result.state = IdentityState.NEEDS_REVIEW
                    result.reasons = list(dict.fromkeys([*result.reasons, ReasonCode.PROVIDER_DISAGREEMENT]))
                    accepted = None
            if explicit and accepted:
                previous = data["work"]
                candidate_links = {
                    k: getattr(accepted.metadata, k)
                    for k in ("doi", "openalex_id")
                    if getattr(accepted.metadata, k)
                }
                if not previous or not same_keys(previous.link_keys, candidate_links):
                    accepted = None
            if accepted:
                meta = accepted.metadata.model_copy(deep=True)
                if meta.doi:
                    meta.doi = normalize_doi(meta.doi)
                links = {k: getattr(meta, k) for k in ("doi", "openalex_id") if getattr(meta, k)}
                work = None
                for key, value in links.items():
                    work = store.works.find_by_link_key(occ.project_id, key, value)
                    if work:
                        break
                # A provisional project work can already hold user locks and overrides.
                previous = data["work"]
                if (
                    work is None
                    and previous
                    and (not previous.link_keys or same_keys(previous.link_keys, links))
                ):
                    work = store.works.get(occ.project_id, previous.work_id)
                new = work is None
                work = work or ProjectWork(
                    work_id=new_id("work"), project_id=occ.project_id, metadata=WorkMetadata()
                )
                merged = DefaultIdentityResolver([]).merge_work_metadata(
                    work, meta, source_rank=0 if accepted.identifier_linked else 1
                )
                merged.link_keys = {**links, **work.link_keys}
                (store.works.add if new else store.works.update)(merged)
                if not new and (merged.metadata != work.metadata or merged.overrides != work.overrides):
                    for other in occurrences(store, occ.project_id, OccurrenceQuery(work_id=work.work_id)):
                        if (
                            other.occurrence_id == occ.occurrence_id
                            or other.identity != IdentityState.ACCEPTED_USER
                        ):
                            continue
                        other.identity, other.approval = IdentityState.NEEDS_REVIEW, None
                        other.identity_reasons = list(
                            dict.fromkeys([ReasonCode.APPROVAL_STALE, *other.identity_reasons])
                        )
                        store.occurrences.update(other, expected_revision=other.revision)
                        audit(ctx, store, AuditEventType.APPROVAL_STALE, other.occurrence_id)
                if not explicit:
                    occ.work_id, occ.accepted_candidate_id = merged.work_id, accepted.candidate_id
                    occ.identity, occ.identity_reasons = IdentityState.ACCEPTED_AUTO, list(result.reasons)
            elif not explicit:
                occ.identity, occ.identity_reasons = result.state, list(result.reasons)
                occ.work_id, occ.accepted_candidate_id = None, None
            if approved:
                occ.identity = IdentityState.NEEDS_REVIEW
                occ.identity_reasons = list(dict.fromkeys([ReasonCode.APPROVAL_STALE, *occ.identity_reasons]))
                audit(ctx, store, AuditEventType.APPROVAL_STALE, occ.occurrence_id)
                occ.approval = None
            # Keep the explicitly selected candidate available after a refresh.
            if explicit and occ.accepted_candidate_id:
                selected = next(
                    (c for c in data["candidates"] if c.candidate_id == occ.accepted_candidate_id), None
                )
                if selected and selected not in kept:
                    selected.rank = len(fresh) + len(kept) + 1
                    store.candidates.replace_for_occurrence(
                        occ.project_id, occ.occurrence_id, [*fresh, *kept, selected]
                    )
            if occ.work_id != previous_work_id:
                occ.discovery = DiscoveryState.NOT_CHECKED
                occ.classification, occ.access_checked_at = None, None
                occ.local_copy, occ.retrieval = LocalCopyState.NONE, RetrievalState.NOT_REQUESTED
            updated = store.occurrences.update(occ, expected_revision=data["occ"].revision)
            store.occurrences.link_to_job(occ.project_id, ctx.job.job_id, [occ.occurrence_id])
            if not explicit:
                store.decisions.add(
                    Decision(
                        decision_id=new_id("decision"),
                        project_id=occ.project_id,
                        occurrence_id=occ.occurrence_id,
                        action=DecisionAction.ACCEPT if accepted else DecisionAction.MARK_UNRESOLVED,
                        candidate_id=accepted.candidate_id if accepted else None,
                        identity_after=occ.identity,
                        reasons=occ.identity_reasons,
                        provenance=Provenance(
                            principal="executor", channel=Channel.EXECUTOR, origin=Origin.AUTOMATION
                        ),
                        occurrence_revision=updated.revision,
                    )
                )
            audit(
                ctx,
                store,
                AuditEventType.CANDIDATES_UPDATED,
                occ.occurrence_id,
                providers_failed=result.providers_failed,
                count=len(fresh),
            )
            return occ.occurrence_id

        return StepResult(commit, result.providers_failed)


class DiscoverHandler:
    step_type = StepType.DISCOVER_ACCESS

    def __init__(self, providers):
        self.providers = providers

    async def run(self, ctx):
        data = await blocking(capture, ctx)
        check_cancel(ctx)
        async with self.providers.job(ctx.job) as (_, _resolver, access):
            result = await access.discover(
                ctx.job.project_id, data["work"], version_policy=ctx.job.snapshot.version_policy, now=utcnow()
            )
        check_cancel(ctx)

        def commit(store):
            validate(ctx, store, data)
            store.access_checks.add_current(result)
            for occ in data["rows"]:
                if occ.identity not in ACCEPTED_IDENTITIES or occ.extraction == ExtractionState.EXCLUDED:
                    continue
                occ.discovery, occ.classification = result.discovery, result.classification
                occ.access_checked_at = result.checked_at
                if occ.approval:
                    occ.approval.occurrence_revision = occ.revision + 1
                store.occurrences.update(occ, expected_revision=occ.revision)
            audit(ctx, store, AuditEventType.ACCESS_CHECKED, result.work_id, check_id=result.check_id)
            return result.check_id

        return StepResult(commit, [f"{f.source}: {f.error}" for f in result.sources_failed])
