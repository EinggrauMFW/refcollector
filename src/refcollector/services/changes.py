"""`update_reference` change types and `review_decision` (spec 7.4, 9.2, 11.4 tools 15 and CLI review).

Approval rule (D-20): an approval covers one occurrence revision. Changes to the text, the candidate
set or accepted metadata fields make it stale (`stale`): identity moves to needs_review with
APPROVAL_STALE and both events are audited. Neutral changes (exclude, restore, locks, local copy)
bump the revision too, so they carry the approval to the new revision (`rebase`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from refcollector.contracts.api import (
    EditText,
    Exclude,
    MarkUnresolved,
    Merge,
    RejectCandidate,
    Restore,
    ReviewDecisionIn,
    SelectCandidate,
    SetDoi,
    SetFieldLocks,
    SetFields,
    Split,
)
from refcollector.contracts.ports import OccurrenceQuery, ResolutionResult, ScoredCandidate
from refcollector.domain.enums import (
    ApprovalSurface,
    AuditEventType,
    DecisionAction,
    ExtractionState,
    IdentityState,
    ReasonCode,
)
from refcollector.domain.models import (
    SETTABLE_FIELDS,
    Approval,
    Candidate,
    Decision,
    Occurrence,
    ProjectWork,
    ProviderEvidence,
    WorkMetadata,
)
from refcollector.errors import InvalidInput, InvalidState, NotFound, RevisionConflict
from refcollector.identity.identifiers import normalize_doi
from refcollector.ids import new_id
from refcollector.services.support import Tx, run_sync

if TYPE_CHECKING:
    from refcollector.services.service import RefCollectorServiceImpl

Outcome = tuple[list[Occurrence], list[Occurrence], Occurrence]  # changed, created, primary


# ---------- shared helpers ----------


def load(
    svc: RefCollectorServiceImpl, tx: Tx, occurrence_id: str, expected: int, *, active: bool = True
) -> Occurrence:
    occ = svc.store.occurrences.get(tx.project.project_id, occurrence_id)
    if occ.revision != expected:
        raise RevisionConflict(details={"current_revisions": {occurrence_id: occ.revision}})
    if active and occ.extraction == ExtractionState.EXCLUDED:
        raise InvalidState(f"{occurrence_id} is excluded", recovery_hint="Restore it first.")
    return occ


def rebase(occ: Occurrence) -> None:
    """Keep a current approval valid across a change that does not touch text, candidates or fields."""
    if (
        occ.identity == IdentityState.ACCEPTED_USER
        and occ.approval is not None
        and occ.approval.occurrence_revision == occ.revision
    ):
        occ.approval = occ.approval.model_copy(update={"occurrence_revision": occ.revision + 1})


def stale(tx: Tx, occ: Occurrence) -> None:
    """7.4: a user approval does not survive a change to text, candidates or accepted fields."""
    if occ.identity == IdentityState.ACCEPTED_USER and occ.approval is not None:
        tx.audit(
            AuditEventType.APPROVAL_STALE,
            occ.occurrence_id,
            approval_id=occ.approval.approval_id,
            candidate_id=occ.approval.candidate_id,
            approved_revision=occ.approval.occurrence_revision,
        )
        occ.identity = IdentityState.NEEDS_REVIEW
        occ.identity_reasons = list(dict.fromkeys([ReasonCode.APPROVAL_STALE, *occ.identity_reasons]))
        occ.approval = None


def unaccept(occ: Occurrence) -> None:
    occ.work_id = None
    occ.accepted_candidate_id = None
    occ.approval = None


def save(svc: RefCollectorServiceImpl, occ: Occurrence, expected: int | None = None) -> Occurrence:
    return svc.store.occurrences.update(occ, expected_revision=expected)


def siblings(svc: RefCollectorServiceImpl, occ: Occurrence) -> list[Occurrence]:
    return svc.all_occurrences(occ.project_id, OccurrenceQuery(source_id=occ.source_id, sort="order"))


def next_order(svc: RefCollectorServiceImpl, occ: Occurrence) -> float:
    later = [o.order for o in siblings(svc, occ) if o.order > occ.order]
    return min(later) if later else occ.order + 1.0


def record_decision(
    svc: RefCollectorServiceImpl,
    tx: Tx,
    occ: Occurrence,
    action: DecisionAction,
    candidate_id: str | None,
    reasons: list[ReasonCode],
    note: str | None = None,
    approval: Approval | None = None,
) -> Decision:
    decision = svc.store.decisions.add(
        Decision(
            decision_id=new_id("decision"),
            project_id=occ.project_id,
            occurrence_id=occ.occurrence_id,
            action=action,
            candidate_id=candidate_id,
            identity_after=occ.identity,
            reasons=reasons,
            provenance=svc.provenance(tx.ctx),
            approval=approval,
            occurrence_revision=occ.revision,
            note=note,
            created_at=tx.now,
        )
    )
    tx.audit(
        AuditEventType.DECISION_RECORDED,
        occ.occurrence_id,
        decision_id=decision.decision_id,
        action=action.value,
        candidate_id=candidate_id,
        identity_after=occ.identity.value,
    )
    return decision


def _filled(meta: WorkMetadata) -> dict[str, Any]:
    return {k: v for k, v in meta.model_dump().items() if v not in (None, "", [])}


def accept_work(
    svc: RefCollectorServiceImpl, tx: Tx, cand: Candidate
) -> tuple[ProjectWork, list[Occurrence]]:
    """Find or create the project's work for an accepted candidate (AT-18: one work per DOI).

    Works are per project (AT-42/43); metadata merge respects locks via the resolver (9.4).
    """
    pid, meta = tx.project.project_id, cand.metadata
    rank = 0 if cand.identifier_linked else 1
    work = None
    if meta.doi:
        work = svc.store.works.find_by_link_key(pid, "doi", meta.doi)
    if work is None and meta.openalex_id:
        work = svc.store.works.find_by_link_key(pid, "openalex_id", meta.openalex_id)
    links = {k: v for k, v in (("doi", meta.doi), ("openalex_id", meta.openalex_id)) if v}
    if work is None:
        work = svc.store.works.add(
            ProjectWork(
                work_id=new_id("work"),
                project_id=pid,
                metadata=meta.model_copy(deep=True),
                metadata_source_ranks={k: rank for k in _filled(meta)},
                link_keys=links,
            )
        )
        return work, []
    resolver = svc.identity_factory(svc.snapshot(tx.project))
    merged = resolver.merge_metadata(work, meta, source_rank=rank)
    ranks = dict(work.metadata_source_ranks)
    for name, value in merged.model_dump().items():
        if value != getattr(work.metadata, name):
            ranks[name] = rank
    changed = merged != work.metadata
    work.metadata, work.metadata_source_ranks = merged, ranks
    work.link_keys = {**links, **work.link_keys}
    work = svc.store.works.update(work)
    others = _stale_sharing(svc, tx, work.work_id, cand.occurrence_id) if changed else []
    return work, others


def candidate_of(svc: RefCollectorServiceImpl, tx: Tx, occ: Occurrence, candidate_id: str) -> Candidate:
    cand = svc.store.candidates.get(occ.project_id, candidate_id)
    if cand.occurrence_id != occ.occurrence_id:
        raise NotFound(f"{candidate_id} not found", details={"id": candidate_id})
    return cand


def _reject(svc: RefCollectorServiceImpl, tx: Tx, occ: Occurrence, cand: Candidate) -> list[ReasonCode]:
    """Mark `cand` rejected (never re-accepted) and settle the occurrence's identity."""
    cand.rejected = True
    svc.store.candidates.update(cand)
    remaining = [
        c
        for c in svc.store.candidates.list_for_occurrence(occ.project_id, occ.occurrence_id)
        if not c.rejected
    ]
    approved = occ.identity == IdentityState.ACCEPTED_USER and occ.approval is not None
    stale(tx, occ)
    if occ.accepted_candidate_id == cand.candidate_id:
        unaccept(occ)
        occ.identity = IdentityState.NEEDS_REVIEW if remaining or approved else IdentityState.REJECTED
        if not approved:
            occ.identity_reasons = []
    else:
        if not remaining and not approved and occ.identity == IdentityState.NEEDS_REVIEW:
            occ.identity = IdentityState.REJECTED
    return list(cand.reasons)


# ---------- change handlers ----------


def edit_text(svc: RefCollectorServiceImpl, tx: Tx, c: EditText, _: Any) -> Outcome:
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    text = c.text.strip()
    if not text:
        raise InvalidInput("text is empty")
    before = occ.text
    occ.edited_text = text  # raw_text stays (principle 2)
    occ.extraction = ExtractionState.CORRECTED
    stale(tx, occ)
    new = save(svc, occ, c.expected_revision)
    tx.audit(AuditEventType.OCCURRENCE_EDITED, occ.occurrence_id, previous_text=before, text=text)
    return [new], [], new


def split(svc: RefCollectorServiceImpl, tx: Tx, c: Split, _: Any) -> Outcome:
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    upper = next_order(svc, occ)
    k = len(c.parts)
    created = []
    for i, part in enumerate(c.parts):
        text = part.text.strip()
        if not text:
            raise InvalidInput("split part is empty")
        created.append(
            Occurrence(
                occurrence_id=new_id("occurrence"),
                project_id=occ.project_id,
                source_id=occ.source_id,
                section_id=occ.section_id,
                order=occ.order + (upper - occ.order) * (i + 1) / (k + 1),
                raw_text=text,
                spans=[part.span] if part.span else [s.model_copy() for s in occ.spans],
                extraction=ExtractionState.CORRECTED,
                predecessor_ids=[occ.occurrence_id],
                created_at=tx.now,
                updated_at=tx.now,
            )
        )
    occ.extraction = ExtractionState.EXCLUDED  # superseded by the parts; restorable
    occ.excluded_reason = "split"
    rebase(occ)
    new = save(svc, occ, c.expected_revision)
    svc.store.occurrences.add_many(created)
    tx.audit(
        AuditEventType.OCCURRENCE_SPLIT,
        occ.occurrence_id,
        created=[o.occurrence_id for o in created],
    )
    return [new], created, new


def merge(svc: RefCollectorServiceImpl, tx: Tx, c: Merge, _: Any) -> Outcome:
    pid = tx.project.project_id
    if len(set(c.occurrence_ids)) != len(c.occurrence_ids) or set(c.expected_revisions) != set(
        c.occurrence_ids
    ):
        raise InvalidInput("expected_revisions must list exactly the merged occurrence_ids")
    svc.store.occurrences.check_revisions(pid, c.expected_revisions)  # AT-39: all or nothing
    parts = sorted(svc.store.occurrences.get_many(pid, c.occurrence_ids), key=lambda o: o.order)
    if len({o.source_id for o in parts}) != 1:
        raise InvalidInput("merged occurrences must come from one source")
    if any(o.extraction == ExtractionState.EXCLUDED for o in parts):
        raise InvalidState("an occurrence is excluded", recovery_hint="Restore it first.")
    text = (c.text or " ".join(o.text for o in parts)).strip()
    if not text:
        raise InvalidInput("merged text is empty")
    created = Occurrence(
        occurrence_id=new_id("occurrence"),
        project_id=pid,
        source_id=parts[0].source_id,
        section_id=parts[0].section_id,
        order=parts[0].order,
        raw_text=text,
        spans=[s.model_copy() for o in parts for s in o.spans],
        extraction=ExtractionState.CORRECTED,
        predecessor_ids=[o.occurrence_id for o in parts],
        created_at=tx.now,
        updated_at=tx.now,
    )
    changed = []
    for o in parts:
        o.extraction = ExtractionState.EXCLUDED
        o.excluded_reason = "merged"
        rebase(o)
        changed.append(save(svc, o, c.expected_revisions[o.occurrence_id]))
    svc.store.occurrences.add_many([created])
    tx.audit(
        AuditEventType.OCCURRENCES_MERGED,
        created.occurrence_id,
        merged=[o.occurrence_id for o in parts],
    )
    return changed, [created], created


def exclude(svc: RefCollectorServiceImpl, tx: Tx, c: Exclude, _: Any) -> Outcome:
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    occ.extraction = ExtractionState.EXCLUDED
    occ.excluded_reason = c.reason
    rebase(occ)
    new = save(svc, occ, c.expected_revision)
    tx.audit(AuditEventType.OCCURRENCE_EXCLUDED, occ.occurrence_id, reason=c.reason)
    return [new], [], new


def restore(svc: RefCollectorServiceImpl, tx: Tx, c: Restore, _: Any) -> Outcome:
    occ = load(svc, tx, c.occurrence_id, c.expected_revision, active=False)
    if occ.extraction != ExtractionState.EXCLUDED:
        raise InvalidState(f"{c.occurrence_id} is not excluded")
    if occ.edited_text is not None or occ.predecessor_ids:
        occ.extraction = ExtractionState.CORRECTED
    else:
        occ.extraction = ExtractionState.NEEDS_REVIEW if occ.warnings else ExtractionState.EXTRACTED
    occ.excluded_reason = None
    rebase(occ)
    new = save(svc, occ, c.expected_revision)
    tx.audit(AuditEventType.OCCURRENCE_RESTORED, occ.occurrence_id, reason=c.reason)
    return [new], [], new


def select_candidate(svc: RefCollectorServiceImpl, tx: Tx, c: SelectCandidate, _: Any) -> Outcome:
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    cand = candidate_of(svc, tx, occ, c.candidate_id)
    if cand.rejected:
        raise InvalidState("candidate was rejected and cannot be accepted")
    work, others = accept_work(svc, tx, cand)
    occ.work_id, occ.accepted_candidate_id, occ.approval = work.work_id, cand.candidate_id, None
    occ.identity, occ.identity_reasons = IdentityState.ACCEPTED_AGENT, list(cand.reasons)
    new = save(svc, occ, c.expected_revision)
    record_decision(svc, tx, new, DecisionAction.ACCEPT, cand.candidate_id, list(cand.reasons), c.note)
    return [new, *others], [], new


def reject_candidate(svc: RefCollectorServiceImpl, tx: Tx, c: RejectCandidate, _: Any) -> Outcome:
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    cand = candidate_of(svc, tx, occ, c.candidate_id)
    reasons = _reject(svc, tx, occ, cand)
    new = save(svc, occ, c.expected_revision)
    record_decision(svc, tx, new, DecisionAction.REJECT, cand.candidate_id, reasons, c.note)
    return [new], [], new


def mark_unresolved(svc: RefCollectorServiceImpl, tx: Tx, c: MarkUnresolved, _: Any) -> Outcome:
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    unaccept(occ)
    occ.identity, occ.identity_reasons = IdentityState.UNRESOLVED, []
    new = save(svc, occ, c.expected_revision)
    record_decision(svc, tx, new, DecisionAction.MARK_UNRESOLVED, None, [])
    return [new], [], new


def prepare_set_doi(svc: RefCollectorServiceImpl, ctx: Any, project_id: str, c: SetDoi) -> ResolutionResult:
    """Provider lookups run before the write transaction opens (no network under the DB lock)."""
    project = svc.store.projects.get(project_id)
    occ = svc.store.occurrences.get(project_id, c.occurrence_id)
    if occ.revision != c.expected_revision:
        raise RevisionConflict(details={"current_revisions": {occ.occurrence_id: occ.revision}})
    doi = normalize_doi(c.doi)
    if not doi:
        raise InvalidInput("not a valid DOI", details={"doi": c.doi})
    rejected = [
        x.metadata
        for x in svc.store.candidates.list_for_occurrence(project_id, occ.occurrence_id)
        if x.rejected
    ]
    resolver = svc.identity_factory(svc.snapshot(project))
    return run_sync(resolver.resolve(occ.text, forced_doi=doi, rejected=rejected))


def _to_candidate(pid: str, oid: str, sc: ScoredCandidate, rank: int) -> Candidate:
    return Candidate(
        candidate_id=new_id("candidate"),
        project_id=pid,
        occurrence_id=oid,
        rank=rank,
        metadata=sc.metadata,
        features=dict(sc.features),
        reasons=list(sc.reasons),
        evidence=[
            ProviderEvidence(
                provider=r.provider,
                record_key=r.record_key,
                fetched_at=r.fetched_at,
                identifier_linked=sc.identifier_linked,
            )
            for r in sc.evidence
        ],
        identifier_linked=sc.identifier_linked,
    )


def set_doi(svc: RefCollectorServiceImpl, tx: Tx, c: SetDoi, result: ResolutionResult) -> Outcome:
    """9.2: accept only when the identity policy finds no conflict; else needs_review with reasons."""
    pid = tx.project.project_id
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    old = svc.store.candidates.list_for_occurrence(pid, occ.occurrence_id)
    kept = [x for x in old if x.rejected]
    fresh = [
        _to_candidate(pid, occ.occurrence_id, sc, i + 1)
        for i, sc in enumerate(s for s in result.candidates if not s.features.get("rejected"))
    ]
    for i, x in enumerate(kept):
        x.rank = len(fresh) + i + 1
    svc.store.candidates.replace_for_occurrence(pid, occ.occurrence_id, [*fresh, *kept])
    tx.audit(AuditEventType.CANDIDATES_UPDATED, occ.occurrence_id, count=len(fresh), source="set_doi")
    was_approved = occ.identity == IdentityState.ACCEPTED_USER and occ.approval is not None
    stale(tx, occ)
    accepted = None
    others = []
    if result.state == IdentityState.ACCEPTED_AUTO and result.accepted_index is not None:
        accepted = fresh[result.accepted_index] if result.accepted_index < len(fresh) else None
    if accepted is not None:
        work, others = accept_work(svc, tx, accepted)
        occ.work_id, occ.accepted_candidate_id, occ.approval = work.work_id, accepted.candidate_id, None
        occ.identity, occ.identity_reasons = IdentityState.ACCEPTED_AGENT, list(accepted.reasons)
    else:
        unaccept(occ)
        occ.identity = IdentityState.NEEDS_REVIEW
        occ.identity_reasons = list(result.reasons)
    if was_approved:
        occ.identity = IdentityState.NEEDS_REVIEW
        occ.identity_reasons = list(dict.fromkeys([ReasonCode.APPROVAL_STALE, *occ.identity_reasons]))
    new = save(svc, occ, c.expected_revision)
    record_decision(
        svc,
        tx,
        new,
        DecisionAction.SET_DOI,
        accepted.candidate_id if accepted else None,
        list(new.identity_reasons),
    )
    return [new, *others], [], new


def _work_for(svc: RefCollectorServiceImpl, tx: Tx, occ: Occurrence) -> ProjectWork:
    pid = tx.project.project_id
    if occ.work_id:
        return svc.store.works.get(pid, occ.work_id)
    work = svc.store.works.add(ProjectWork(work_id=new_id("work"), project_id=pid, metadata=WorkMetadata()))
    occ.work_id = work.work_id
    return work


def _stale_sharing(svc: RefCollectorServiceImpl, tx: Tx, work_id: str, skip: str) -> list[Occurrence]:
    """Other occurrences of the same work see the field change too (7.4)."""
    out = []
    for other in svc.all_occurrences(tx.project.project_id, OccurrenceQuery(work_id=work_id)):
        if other.occurrence_id == skip or other.identity != IdentityState.ACCEPTED_USER:
            continue
        stale(tx, other)
        out.append(save(svc, other))
    return out


def set_fields(svc: RefCollectorServiceImpl, tx: Tx, c: SetFields, _: Any) -> Outcome:
    bad = sorted(set(c.fields) - SETTABLE_FIELDS)
    if bad or not c.fields:
        raise InvalidInput("fields must be a non-empty set of settable fields", details={"unknown": bad})
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    work = _work_for(svc, tx, occ)
    current = WorkMetadata.model_validate({**work.metadata.model_dump(), **work.overrides})
    try:
        checked = WorkMetadata.model_validate({**current.model_dump(), **c.fields})
    except ValidationError as exc:
        raise InvalidInput(
            "invalid field value", details={"errors": exc.errors(include_url=False, include_context=False)}
        ) from exc
    dumped = checked.model_dump(mode="json")
    work.overrides = {**work.overrides, **{k: dumped[k] for k in c.fields}}
    svc.store.works.update(work)
    stale(tx, occ)
    new = save(svc, occ, c.expected_revision)
    others = _stale_sharing(svc, tx, work.work_id, occ.occurrence_id)
    tx.audit(AuditEventType.FIELDS_SET, occ.occurrence_id, fields={k: dumped[k] for k in c.fields})
    return [new, *others], [], new


def set_field_locks(svc: RefCollectorServiceImpl, tx: Tx, c: SetFieldLocks, _: Any) -> Outcome:
    bad = sorted(set(c.locks) - SETTABLE_FIELDS)
    if bad or not c.locks:
        raise InvalidInput("locks must name settable fields", details={"unknown": bad})
    occ = load(svc, tx, c.occurrence_id, c.expected_revision)
    if not occ.work_id:
        raise InvalidState(
            "no work to lock fields on", recovery_hint="Accept a candidate or set fields first."
        )
    work = svc.store.works.get(occ.project_id, occ.work_id)
    locks = {**work.locks, **c.locks}
    work.locks = {k: v for k, v in locks.items() if v}  # per project (AT-43)
    svc.store.works.update(work)
    rebase(occ)
    new = save(svc, occ, c.expected_revision)
    tx.audit(AuditEventType.LOCKS_SET, occ.occurrence_id, locks=dict(c.locks))
    return [new], [], new


HANDLERS = {
    "edit_text": edit_text,
    "split": split,
    "merge": merge,
    "exclude": exclude,
    "restore": restore,
    "select_candidate": select_candidate,
    "reject_candidate": reject_candidate,
    "set_doi": set_doi,
    "set_fields": set_fields,
    "set_field_locks": set_field_locks,
    "mark_unresolved": mark_unresolved,
}


# ---------- review_decision (TTY approval surface, 7.4) ----------


def review(svc: RefCollectorServiceImpl, tx: Tx, req: ReviewDecisionIn) -> Outcome:
    occ = load(svc, tx, req.occurrence_id, req.expected_revision)
    cand = candidate_of(svc, tx, occ, req.candidate_id)
    if req.action == "reject":
        reasons = _reject(svc, tx, occ, cand)
        new = save(svc, occ, req.expected_revision)
        record_decision(svc, tx, new, DecisionAction.REJECT, cand.candidate_id, reasons, req.note)
        return [new], [], new
    if cand.rejected:
        raise InvalidState("candidate was rejected and cannot be accepted")
    work, others = accept_work(svc, tx, cand)
    occ.work_id, occ.accepted_candidate_id = work.work_id, cand.candidate_id
    occ.identity, occ.identity_reasons = IdentityState.ACCEPTED_USER, list(cand.reasons)
    # The commit that records the approval bumps the revision and stores the new value (D-20, W-18).
    approval = Approval(
        approval_id=new_id("approval"),
        action=DecisionAction.ACCEPT,
        candidate_id=cand.candidate_id,
        occurrence_revision=occ.revision + 1,
        approved_at=tx.now,
        surface=ApprovalSurface.CLI_TTY,
    )
    occ.approval = approval
    new = save(svc, occ, req.expected_revision)
    assert new.approval is not None and new.approval.occurrence_revision == new.revision
    tx.audit(
        AuditEventType.APPROVAL_RECORDED,
        occ.occurrence_id,
        approval_id=approval.approval_id,
        candidate_id=cand.candidate_id,
        occurrence_revision=new.revision,
    )
    record_decision(
        svc, tx, new, DecisionAction.ACCEPT, cand.candidate_id, list(cand.reasons), req.note, approval
    )
    return [new, *others], [], new
