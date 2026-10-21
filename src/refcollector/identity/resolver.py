"""WP-06: IdentityResolver implementation. Spec 9.1, 9.4."""

from __future__ import annotations

import re
from collections.abc import Sequence
from copy import deepcopy

from refcollector.config import IDENTITY_POLICY_VERSION
from refcollector.contracts.ports import (
    CitationQuery,
    MetadataProvider,
    ResolutionResult,
    ScoredCandidate,
)
from refcollector.domain.enums import IdentityState as State
from refcollector.domain.enums import ReasonCode as R
from refcollector.domain.models import ProjectWork, ProviderRecord, WorkMetadata
from refcollector.errors import Cancelled, InvalidInput, RcError

from .features import citation_type, compare, guess_title
from .identifiers import IdentifierKind, normalize_doi, read_identifiers
from .normalize import _norm, _tokens, normalize_metadata
from .policy_v1 import close_candidates

IDENTIFIER_ORDER = ("crossref", "datacite", "doiorg")
SEARCH_ORDER = ("crossref", "openalex", "semanticscholar")
SOURCE_RANKS = {"identifier_linked": 0, "corroborating": 1, "parsed": 2, "agent": 3}


def _merge_metadata_fields(
    work: ProjectWork | None, accepted: WorkMetadata, source_rank: int
) -> tuple[WorkMetadata, dict[str, int]]:
    """One pure field merge for both public methods. Lower ranks have precedence.

    An unranked nonempty value has unknown origin. Only rank 0 may replace it;
    keeping it does not invent provenance. Empty values may be filled at any rank.
    Equal ranks allow refresh. Missing incoming values never clear existing data.
    Locks override both data and ranks; locked fields retain their recorded ranks.
    """
    if type(source_rank) is not int or source_rank not in SOURCE_RANKS.values():
        raise InvalidInput("source_rank must be 0 (identifier), 1 (provider), 2 (parsed), or 3 (agent).")
    incoming = accepted.model_dump()
    if work is None:
        return accepted.model_copy(deep=True), {
            field: source_rank for field, value in incoming.items() if value not in (None, "", [])
        }
    data = work.metadata.model_dump()
    ranks = dict(work.metadata_source_ranks)
    for field in WorkMetadata.model_fields:
        if work.locks.get(field):
            if field in work.overrides:
                data[field] = deepcopy(work.overrides[field])
            continue
        if incoming[field] in (None, "", []):
            continue
        previous_rank = ranks.get(field)
        if (
            data[field] in (None, "", [])
            or (previous_rank is not None and source_rank <= previous_rank)
            or (previous_rank is None and source_rank == 0)
        ):
            data[field] = incoming[field]
            ranks[field] = source_rank
    return WorkMetadata.model_validate(data), ranks


def _same_work(left: WorkMetadata, right: WorkMetadata) -> bool:
    """Strong identifiers first; never collapse distinct editions or DOI siblings by title."""
    for field in ("doi", "isbn", "arxiv", "pmid", "openalex_id"):
        a, b = getattr(left, field), getattr(right, field)
        if a and b:
            return a == b
    # Without a shared identifier, require complete bibliographic agreement.
    return bool(
        left.title
        and right.title
        and left.authors
        and right.authors
        and left.year
        and _tokens(left.title) == _tokens(right.title)
        and left.authors == right.authors
        and left.year == right.year
        and left.edition == right.edition
        and left.type == right.type
    )


def _rejected(metadata: WorkMetadata, rejected: Sequence[WorkMetadata]) -> bool:
    """A rejection follows a stable identifier even when a provider refreshes fields."""
    for old in rejected:
        old = normalize_metadata(old)
        for field in ("doi", "isbn", "arxiv", "pmid", "openalex_id"):
            if getattr(metadata, field) and getattr(metadata, field) == getattr(old, field):
                return True
        if metadata == old or _same_work(metadata, old):
            return True
    return False


def _acceptable(candidate: ScoredCandidate) -> bool:
    f = candidate.features
    blocked = {
        R.EDITION_CONFLICT,
        R.VERSION_DIFFERENCE,
        R.PROVIDER_DISAGREEMENT,
        R.AUTHOR_CONFLICT,
        R.GENERIC_TITLE,
        R.INSUFFICIENT_FIELDS,
    }
    if candidate.identifier_linked:
        blocked |= {R.DOI_TITLE_CONFLICT, R.YEAR_CONFLICT}
    return bool(
        f["legacy_accepted"]
        and f["supporting_fields"]
        and f["title_distinctive"]
        and f["title_score"] >= 0.75
        and not f.get("rejected")
        and (not candidate.identifier_linked or f.get("identifier_agreement"))
        and not blocked.intersection(candidate.reasons)
    )


def _can_choose(candidates: Sequence[ScoredCandidate]) -> bool:
    return bool(
        candidates
        and _acceptable(candidates[0])
        and not (len(candidates) > 1 and close_candidates(candidates[0], candidates[1]))
        and not any(c.identifier_linked and not _acceptable(c) for c in candidates)
    )


class DefaultIdentityResolver:
    """Use only injected providers, already configured for the job's network policy.

    SOURCE_RANKS: 0 identifier-linked, 1 corroborating, 2 parsed, 3 agent.
    No state, persistence, or automatic construction of providers occurs here.
    """

    def __init__(self, providers: Sequence[MetadataProvider]) -> None:
        self._providers = {provider.name: provider for provider in providers}

    def evaluate(self, citation: str, metadata: WorkMetadata) -> ScoredCandidate:
        candidate = compare(citation, metadata)
        ids = read_identifiers(citation)
        candidate.identifier_linked = any(
            getattr(candidate.metadata, kind) == value for kind, value in ids.items()
        )
        if candidate.identifier_linked and candidate.features["title_score"] < 0.75:
            candidate.reasons.append(R.DOI_TITLE_CONFLICT)
        return candidate

    async def resolve(
        self,
        citation: str | None,
        *,
        fields: CitationQuery | None = None,
        forced_doi: str | None = None,
        rejected: Sequence[WorkMetadata] = (),
    ) -> ResolutionResult:
        text = citation or (fields.text if fields else None) or ""
        if fields:
            # Explicit fields supplement raw text. Their title is compared directly.
            structured = " ".join(
                part
                for part in (
                    " ".join(fields.authors),
                    f"({fields.year})." if fields.year else None,
                    fields.title,
                )
                if part
            )
            text = " ".join(part for part in (structured, text) if part)
        query = CitationQuery(
            text=text or None,
            title=(fields.title if fields else None) or guess_title(text),
            authors=list(fields.authors) if fields else [],
            year=fields.year if fields else None,
        )
        ids = read_identifiers(text)
        if forced_doi is not None:
            doi = normalize_doi(forced_doi)
            if not doi:
                raise InvalidInput("The forced DOI has no valid DOI syntax.")
            ids["doi"] = doi
        records: list[tuple[ProviderRecord, bool]] = []
        failed: list[str] = []

        async def by_identifier(name: str, kind: IdentifierKind, value: str) -> None:
            provider = self._providers.get(name)
            if provider is None:
                return
            try:
                rec = await provider.by_identifier(kind, value)
                if rec is not None:
                    # The returned identifier must match; lookup success alone is not proof.
                    records.append((rec, True))
            except Cancelled:
                raise
            except RcError:
                if name not in failed:
                    failed.append(name)

        async def search(name: str, search_query: CitationQuery, *, grey: bool = False) -> None:
            provider = self._providers.get(name)
            if provider is None or not (search_query.text or search_query.title):
                return
            try:
                recs = await provider.search(search_query, rows=5)
                expected = citation_type(text)
                for rec in recs:
                    if not grey or (
                        rec.metadata.type in {"report", "standard", "book", "dissertation"}
                        and (not expected or rec.metadata.type == expected)
                    ):
                        records.append((rec, False))
            except Cancelled:
                raise
            except RcError:
                if name not in failed:
                    failed.append(name)

        if "doi" in ids:
            for name in IDENTIFIER_ORDER:
                await by_identifier(name, "doi", ids["doi"])
        for kind in ("arxiv", "pmid"):
            if kind in ids:
                for name in (*IDENTIFIER_ORDER, "openalex", "semanticscholar"):
                    await by_identifier(name, kind, ids[kind])
        for name in SEARCH_ORDER:
            await search(name, query)

        def ranked() -> list[ScoredCandidate]:
            candidates: list[ScoredCandidate] = []
            provider_order = {
                name: i for i, name in enumerate((*IDENTIFIER_ORDER, *SEARCH_ORDER[1:], "openlibrary"))
            }
            ordered = sorted(
                records,
                key=lambda pair: (
                    not pair[1],
                    provider_order.get(pair[0].provider, 99),
                    pair[0].provider,
                    pair[0].record_key,
                    pair[0].payload_hash,
                    pair[0].metadata.model_dump_json(),
                ),
            )
            for rec, linked in ordered:
                md = normalize_metadata(rec.metadata)
                candidate = compare(text, md, title=query.title)
                candidate.identifier_linked = linked
                if linked and candidate.features["title_score"] < 0.75:
                    candidate.reasons.append(R.DOI_TITLE_CONFLICT)
                if ids.get("doi") and md.doi and md.doi != ids["doi"]:
                    candidate.features["doi_agreement"] = False
                    candidate.reasons.append(R.PROVIDER_DISAGREEMENT)
                elif ids.get("doi") and md.doi:
                    candidate.features["doi_agreement"] = True
                candidate.features["identifier_agreement"] = any(
                    getattr(md, kind) == value for kind, value in ids.items()
                )
                candidate.features["rejected"] = _rejected(md, rejected)
                existing = next((c for c in candidates if _same_work(c.metadata, md)), None)
                if existing is None:
                    candidates.append(candidate)
                    existing = candidate
                else:
                    # Keep the first (identifier-priority) metadata. Conflicts remain visible.
                    for field in (
                        "title",
                        "authors",
                        "year",
                        "venue",
                        "volume",
                        "issue",
                        "pages",
                        "edition",
                        "type",
                    ):
                        a, b = getattr(existing.metadata, field), getattr(md, field)
                        if a and b and (a != b if field != "title" else _tokens(a) != _tokens(b)):
                            if R.PROVIDER_DISAGREEMENT not in existing.reasons:
                                existing.reasons.append(R.PROVIDER_DISAGREEMENT)
                            existing.features[f"provider_conflict_{field}"] = True
                    data = existing.metadata.model_dump()
                    for field, value in md.model_dump().items():
                        if data[field] in (None, "", []) and value not in (None, "", []):
                            data[field] = value
                    merged = compare(text, WorkMetadata.model_validate(data), title=query.title)
                    # Missing fields from an earlier partial response can be supplied by
                    # another record. Conflicting evidence cannot be repaired by a merge.
                    observed_reasons = [
                        r for r in existing.reasons + candidate.reasons if r != R.INSUFFICIENT_FIELDS
                    ]
                    existing.metadata = merged.metadata
                    existing.score = merged.score
                    existing.features.update(merged.features)
                    existing.identifier_linked |= linked
                    existing.features["identifier_agreement"] = any(
                        getattr(existing.metadata, kind) == value for kind, value in ids.items()
                    )
                    existing.reasons = list(dict.fromkeys(merged.reasons + observed_reasons))
                if not any(
                    (r.provider, r.record_key, r.payload_hash)
                    == (rec.provider, rec.record_key, rec.payload_hash)
                    for r in existing.evidence
                ):
                    existing.evidence.append(rec.model_copy(deep=True))
                existing.features["provider_count"] = len({r.provider for r in existing.evidence})
            return sorted(
                candidates,
                key=lambda c: (
                    -c.score,
                    c.metadata.doi or "",
                    c.metadata.openalex_id or "",
                    _norm(c.metadata.title or ""),
                    c.metadata.model_dump_json(),
                ),
            )

        candidates = ranked()
        if not _can_choose(candidates):
            if "isbn" in ids:
                await by_identifier("openlibrary", "isbn", ids["isbn"])
            # CitationQuery has no type filter. Query the title/pattern and filter returned
            # types locally, without modifying or bypassing the frozen provider interface.
            if citation_type(text):
                pattern = re.search(r"\b(?:ISO|IEC|ASTM|IEEE)\s*[-:]?\s*\d{2,}(?::\d{4})?", text, re.I)
                grey_title = " ".join(
                    p for p in (pattern.group() if pattern else None, query.title or text) if p
                )
                await search("openalex", CitationQuery(text=text, title=grey_title), grey=True)
            candidates = ranked()

        reasons = []
        # Decision reasons describe the leading match and global blockers. Other
        # candidate reasons remain with their evidence, not the chosen work.
        if candidates:
            reasons.extend(candidates[0].reasons)
        for candidate in candidates[1:]:
            if candidate.identifier_linked and not _acceptable(candidate):
                reasons.extend(candidate.reasons)
        if failed:
            reasons.append(R.PROVIDER_UNAVAILABLE)
        accepted_index = None
        # Spec 9.1 step 7 (D-31): no identifier-linked candidate and none reaching the title
        # threshold means no acceptable candidate. Weak candidates stay listed for a reviewer.
        weak_only = not any(c.identifier_linked or c.features["title_score"] >= 0.75 for c in candidates)
        state = State.NEEDS_REVIEW if candidates and not weak_only else State.UNRESOLVED
        if candidates:
            top = candidates[0]
            close = len(candidates) > 1 and close_candidates(top, candidates[1])
            if close:
                reasons.append(R.MULTIPLE_CLOSE_CANDIDATES)
                top.reasons = list(dict.fromkeys([*top.reasons, R.MULTIPLE_CLOSE_CANDIDATES]))
            # A conflicting linked record cannot be hidden by a better text result.
            linked_conflict = any(c.identifier_linked and not _acceptable(c) for c in candidates)
            if _acceptable(top) and not close and not linked_conflict:
                state, accepted_index = State.ACCEPTED_AUTO, 0
        else:
            reasons.append(R.INSUFFICIENT_FIELDS)
        return ResolutionResult(
            state,
            list(dict.fromkeys(reasons)),
            candidates[:5],
            accepted_index,
            failed,
            IDENTITY_POLICY_VERSION,
        )

    def merge_metadata(
        self, work: ProjectWork | None, accepted: WorkMetadata, *, source_rank: int
    ) -> WorkMetadata:
        """Pure compatibility method returning metadata only, with the same field policy.

        Ranks: 0 identifier-linked, 1 corroborating, 2 parsed, 3 agent. Use
        merge_work_metadata when persisting updates so their ranks are saved too.
        """
        metadata, _ = _merge_metadata_fields(work, accepted, source_rank)
        return metadata

    def merge_work_metadata(
        self, work: ProjectWork, accepted: WorkMetadata, *, source_rank: int
    ) -> ProjectWork:
        """WP-10 integration: return a detached work with merged metadata and ranks.

        Persist the returned ProjectWork with Store.works.update in the caller's
        transaction. For a new work, construct an empty ProjectWork, then call this
        method before Store.works.add. No persistence or input mutation occurs here.
        IDs, locks, overrides, notes, link keys, and timestamps are copied unchanged.
        """
        metadata, ranks = _merge_metadata_fields(work, accepted, source_rank)
        result = work.model_copy(deep=True)
        result.metadata = metadata
        result.metadata_source_ranks = ranks
        return result
