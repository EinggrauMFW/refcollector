"""DTOs and helper functions for the RefCollector Library HTTP API.

This module implements the Pydantic models from section 4 of the contract
and the pure citation_status function from section 3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from refcollector.contracts.api import StageCounts
from refcollector.domain.enums import (
    ACCEPTED_IDENTITIES,
    AccessClassification,
    DiscoveryState,
    ExtractionState,
    IdentityState,
    RetrievalState,
)

PaperKind = Literal["uploaded", "downloaded"]
CitationStatus = Literal[
    "extracted",
    "check",
    "no_match",
    "matched",
    "pdf_available",
    "no_pdf",
    "downloaded",
    "failed",
]


class Author(BaseModel):
    """Author display model."""

    display: str


class Permissions(BaseModel):
    """Collection permissions state."""

    folder_granted: bool
    network_mode: str
    downloads_enabled: bool
    downloads_source: Literal["project", "global", "none"]
    can_download: bool


class Collection(BaseModel):
    """Collection summary model."""

    collection_id: str
    name: str
    folder: str | None
    paper_count: int
    check_count: int
    permissions: Permissions
    settings_revision: str


class PaperCard(BaseModel):
    """Shelf paper card model."""

    paper_id: str
    kind: PaperKind
    title: str
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    file_name: str
    folder_path: str | None = None
    citation_count: int = 0
    status_counts: dict[CitationStatus, int] = Field(default_factory=dict)
    cited_by: list[str] = Field(default_factory=list)
    active_job_id: str | None = None
    file_url: str


class ShelfPage(BaseModel):
    """Paged shelf response."""

    papers: list[PaperCard]
    next_cursor: str | None = None
    total: int


class Citation(BaseModel):
    """Citation summary inside an expanded paper."""

    citation_id: str
    order: float
    text: str
    status: CitationStatus
    reasons: list[str] = Field(default_factory=list)
    title: str | None = None
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    can_download: bool = False
    can_download_by_doi: bool = False
    metadata_review_pending: bool = False
    downloaded_paper_id: str | None = None
    revision: int = 1
    check_kind: Literal["identity", "file"] | None = None


class PaperDetail(BaseModel):
    """Detail view of a paper."""

    paper: PaperCard
    pages: int | None = None
    imported_at: datetime | None = None
    citations: list[Citation] = Field(default_factory=list)


class Candidate(BaseModel):
    """Candidate view for citation review."""

    candidate_id: str
    rank: int
    title: str | None = None
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    reasons: list[str] = Field(default_factory=list)
    rejected: bool = False


class PendingFile(BaseModel):
    """Pending file information for file review."""

    work_file_id: str
    file_kind: Literal["work_file", "citation_file"] = "work_file"
    size: int
    source_url: str | None = None
    failed_checks: list[str] = Field(default_factory=list)


class CitationDetail(BaseModel):
    """Detail view of a citation including candidates."""

    citation: Citation
    candidates: list[Candidate] = Field(default_factory=list)
    pending_file: PendingFile | None = None


class DefaultsOut(BaseModel):
    """Default collection directory paths."""

    default_root: str


class JobStatus(BaseModel):
    """Job status polling model."""

    job_id: str
    state: str
    terminal: bool
    stages: list[StageCounts] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_poll_after_ms: int = 2000
    kind: Literal["extract", "find_metadata", "download"] = "find_metadata"
    paper_ids: list[str] = Field(default_factory=list)


class Intent(BaseModel):
    """Web intent confirmation wrapper."""

    confirmation_token: str
    expires_at: datetime
    summary: dict[str, Any] = Field(default_factory=dict)
    description: list[str] = Field(default_factory=list)


class CreateCollectionIn(BaseModel):
    """Create collection input."""

    name: str
    folder: str
    idempotency_key: str


class RenameCollectionIn(BaseModel):
    """Rename collection input."""

    name: str


class CollectionConsequences(BaseModel):
    """Summary of records and file consequences before deletion."""

    collection_id: str
    name: str
    paper_count: int
    citation_count: int
    file_count: int
    folder_path: str | None
    message: str
    has_active_jobs: bool = False


class PermissionIntentIn(BaseModel):
    """Permission intent input."""

    change: Literal["add_allowed_folder", "set_project_network", "set_project_downloads"]
    expected_settings_revision: str
    idempotency_key: str


class RunIn(BaseModel):
    """Run metadata finding or download input."""

    use_fallback: bool = False

    paper_id: str | None = None
    citation_id: str | None = None
    idempotency_key: str


class FileReviewIn(BaseModel):
    """File review decision input."""

    work_file_id: str
    file_kind: Literal["work_file", "citation_file"] = "work_file"
    action: Literal["approve", "reject"]
    expected_revision: int
    idempotency_key: str


class ReviewIn(BaseModel):
    """Citation review decision input."""

    action: Literal["accept", "reject"]
    candidate_id: str
    expected_revision: int
    idempotency_key: str


class Accepted(BaseModel):
    """Action response when background work starts."""

    job_id: str | None = None
    disabled_reason: str | None = None


def format_author(person: Any) -> Author:
    """Format a Person object or dictionary into an Author DTO."""
    if person is None:
        return Author(display="")
    if isinstance(person, dict):
        literal = person.get("literal")
        if literal:
            return Author(display=literal)
        family = person.get("family")
        given = person.get("given")
        name = ", ".join(part for part in (family, given) if part)
        return Author(display=name)

    literal = getattr(person, "literal", None)
    if literal:
        return Author(display=literal)
    family = getattr(person, "family", None)
    given = getattr(person, "given", None)
    name = ", ".join(part for part in (family, given) if part)
    return Author(display=name)


def is_downloadable(occ: Any) -> bool:
    """Check if download is allowed for an occurrence.

    Matches RefCollectorService._downloadable rule:
    identity in ACCEPTED_IDENTITIES and work_id is not None
    and discovery != NOT_CHECKED and extraction != EXCLUDED.
    """
    if isinstance(occ, dict):
        ident = occ.get("identity")
        work_id = occ.get("work_id")
        access_obj = occ.get("access")
        disc = occ.get("discovery")
        if isinstance(access_obj, dict) and disc is None:
            disc = access_obj.get("discovery")
        ext = occ.get("extraction")
    else:
        ident = getattr(occ, "identity", None)
        work_id = getattr(occ, "work_id", None)
        access_obj = getattr(occ, "access", None)
        if access_obj is not None:
            disc = getattr(access_obj, "discovery", None)
        else:
            disc = getattr(occ, "discovery", None)
        ext = getattr(occ, "extraction", None)
    if isinstance(ident, dict):
        ident_val = ident.get("state")
    elif hasattr(ident, "state"):
        ident_val = ident.state
    elif hasattr(ident, "value"):
        ident_val = ident
    elif isinstance(ident, str):
        ident_val = ident
    else:
        ident_val = None

    if isinstance(ident_val, str):
        try:
            ident_state = IdentityState(ident_val)
        except ValueError:
            ident_state = None
    else:
        ident_state = ident_val

    if ident_state not in ACCEPTED_IDENTITIES:
        return False

    # Public summaries omit work_id. The service checks the full occurrence
    # again before enqueuing; explicit missing work IDs still reject full records.
    has_work_id = "work_id" in occ if isinstance(occ, dict) else hasattr(occ, "work_id")
    if has_work_id and not work_id:
        return False

    disc_val = disc.value if hasattr(disc, "value") else str(disc or "")
    if disc_val == DiscoveryState.NOT_CHECKED.value or not disc_val:
        return False

    ext_val = ext.value if hasattr(ext, "value") else str(ext or "")
    if ext_val == ExtractionState.EXCLUDED.value:
        return False

    return True


def citation_status(occ: Any, has_verified_file: bool = False) -> CitationStatus | None:
    """Compute CitationStatus for an occurrence according to contract section 3.

    First matching rule wins:
    1. extraction == failed or retrieval == failed -> failed
    2. extraction == excluded -> None (hidden)
    3. retrieval in {verified, satisfied_by_local_copy} or has_verified_file -> downloaded
    4. identity == needs_review or retrieval == needs_review or extraction == needs_review -> check
    5. identity in {unresolved, rejected} -> no_match
    6. identity == unprocessed -> extracted
    7. accepted identity and discovery == not_checked -> matched
    8. accepted identity and classification == eligible_found -> pdf_available
    9. accepted identity, discovery checked, other classification -> no_pdf
    """
    if isinstance(occ, dict):
        ext_raw = occ.get("extraction")
        ret_raw = occ.get("retrieval")
        ident_raw = occ.get("identity")
        disc_raw = occ.get("discovery")
        cls_raw = occ.get("classification")
        access_raw = occ.get("access")
        if isinstance(access_raw, dict):
            if disc_raw is None:
                disc_raw = access_raw.get("discovery")
            if cls_raw is None:
                cls_raw = access_raw.get("classification")
    else:
        ext_raw = getattr(occ, "extraction", None)
        ret_raw = getattr(occ, "retrieval", None)
        ident_raw = getattr(occ, "identity", None)
        access_obj = getattr(occ, "access", None)
        if access_obj is not None:
            disc_raw = getattr(access_obj, "discovery", None)
            cls_raw = getattr(access_obj, "classification", None)
        else:
            disc_raw = getattr(occ, "discovery", None)
            cls_raw = getattr(occ, "classification", None)

    # Normalize extraction
    ext_val = ext_raw.value if hasattr(ext_raw, "value") else (str(ext_raw) if ext_raw is not None else "")

    # Normalize retrieval
    if isinstance(ret_raw, dict):
        ret_val = ret_raw.get("value", "")
    elif hasattr(ret_raw, "value"):
        ret_val = ret_raw.value
    else:
        ret_val = str(ret_raw) if ret_raw is not None else ""

    citation_state = (
        occ.get("citation_file_state") if isinstance(occ, dict) else getattr(occ, "citation_file_state", None)
    )
    if ext_val != ExtractionState.EXCLUDED.value:
        if citation_state == RetrievalState.VERIFIED:
            return "downloaded"
        if citation_state == RetrievalState.NEEDS_REVIEW:
            return "check"

    # Rule 1: extraction == failed or retrieval == failed
    if ext_val == ExtractionState.FAILED.value or ret_val == RetrievalState.FAILED.value:
        return "failed"

    # Rule 2: extraction == excluded
    if ext_val == ExtractionState.EXCLUDED.value:
        return None

    # Rule 3: retrieval in {verified, satisfied_by_local_copy} or has_verified_file
    if (
        ret_val in (RetrievalState.VERIFIED.value, RetrievalState.SATISFIED_BY_LOCAL_COPY.value)
        or has_verified_file
    ):
        return "downloaded"

    # Normalize identity
    if isinstance(ident_raw, dict):
        ident_val = ident_raw.get("state") or ident_raw.get("value") or ""
        if hasattr(ident_val, "value"):
            ident_val = ident_val.value
    elif hasattr(ident_raw, "state"):
        ident_st = ident_raw.state
        ident_val = ident_st.value if hasattr(ident_st, "value") else str(ident_st or "")
    elif hasattr(ident_raw, "value"):
        ident_val = ident_raw.value
    else:
        ident_val = str(ident_raw) if ident_raw is not None else ""

    # Rule 4: identity == needs_review or retrieval == needs_review or extraction == needs_review
    if (
        ident_val == IdentityState.NEEDS_REVIEW.value
        or ret_val == RetrievalState.NEEDS_REVIEW.value
        or ext_val == ExtractionState.NEEDS_REVIEW.value
    ):
        return "check"

    # Rule 5: identity in {unresolved, rejected}
    if ident_val in (IdentityState.UNRESOLVED.value, IdentityState.REJECTED.value):
        return "no_match"

    # Rule 6: identity == unprocessed
    if ident_val == IdentityState.UNPROCESSED.value:
        return "extracted"

    # Rule 7-9: accepted identities
    accepted_values = {e.value for e in ACCEPTED_IDENTITIES}
    if ident_val in accepted_values:
        if hasattr(disc_raw, "value"):
            disc_val = disc_raw.value
        else:
            disc_val = str(disc_raw) if disc_raw is not None else None

        if hasattr(cls_raw, "value"):
            cls_val = cls_raw.value
        else:
            cls_val = str(cls_raw) if cls_raw is not None else None

        # Rule 7: accepted identity and discovery == not_checked
        if disc_val == DiscoveryState.NOT_CHECKED.value:
            return "matched"

        # Rule 8: accepted identity and classification == eligible_found
        if cls_val == AccessClassification.ELIGIBLE_FOUND.value:
            return "pdf_available"

        # Rule 9: accepted identity, discovery checked, other classification
        return "no_pdf"

    return "extracted"


def filter_shelf_papers(
    papers: list[PaperCard],
    q: str | None = None,
    kind: str | None = None,
    status: str | None = None,
) -> list[PaperCard]:
    """Filter shelf papers by search query, paper kind, and citation status."""
    result = papers

    if kind:
        result = [p for p in result if p.kind == kind]

    if status:
        normalized_status = status
        if status == "needs_check":
            normalized_status = "check"
        elif status == "missing_pdf":
            normalized_status = "no_pdf"

        filtered: list[PaperCard] = []
        for p in result:
            if p.kind == "downloaded":
                if normalized_status == "downloaded":
                    filtered.append(p)
            else:
                if p.status_counts.get(normalized_status, 0) > 0:  # type: ignore[arg-type]
                    filtered.append(p)
        result = filtered

    if q:
        term = q.strip().lower()
        if term:
            filtered = []
            for p in result:
                match = (
                    term in p.title.lower()
                    or term in p.file_name.lower()
                    or (p.venue and term in p.venue.lower())
                    or (p.doi and term in p.doi.lower())
                    or (p.year and term in str(p.year))
                    or any(term in a.display.lower() for a in p.authors)
                )
                if match:
                    filtered.append(p)
            result = filtered

    return result


def paginate_shelf_papers(
    papers: list[PaperCard],
    cursor: str | None = None,
    limit: int = 50,
) -> ShelfPage:
    """Paginate a list of shelf papers using offset cursors."""
    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            offset = 0

    total = len(papers)
    page_papers = papers[offset : offset + limit]
    next_offset = offset + limit
    next_cursor = str(next_offset) if next_offset < total else None

    return ShelfPage(papers=page_papers, next_cursor=next_cursor, total=total)
