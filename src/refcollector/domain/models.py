"""Domain entities (spec 7.2) as Pydantic v2 models.

Storage keeps each entity as a JSON document plus indexed columns (see
storage/migrations/0001_init.sql). Unknown fields are rejected everywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from refcollector.domain.enums import (
    AccessClassification,
    ApprovalSurface,
    AuditEventType,
    Channel,
    CheckOutcome,
    CopyVersion,
    DecisionAction,
    DiscoveryState,
    ExtractionState,
    ExtractionWarning,
    IdentityState,
    JobState,
    LocalCopyCheck,
    LocalCopyOrigin,
    LocalCopyState,
    LocationBasis,
    NetworkMode,
    Origin,
    PolicyProfile,
    ReasonCode,
    RetrievalState,
    SkipReason,
    SourceType,
    StepState,
    StepType,
    VersionPolicy,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------- shared value objects ----------


class Person(Model):
    """A personal name (family/given) or a corporate/single name (literal). AT-07."""

    family: str | None = None
    given: str | None = None
    literal: str | None = None


class WorkMetadata(Model):
    """Normalised bibliographic fields. Field names are the `set_fields` vocabulary (11.4 tool 15)."""

    title: str | None = None
    authors: list[Person] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    type: str | None = None  # Crossref-style work type
    edition: str | None = None
    doi: str | None = None  # lower-case, no URL prefix
    url: str | None = None
    isbn: str | None = None
    arxiv: str | None = None
    pmid: str | None = None
    openalex_id: str | None = None


SETTABLE_FIELDS = frozenset(WorkMetadata.model_fields) - {"openalex_id"}


class Span(Model):
    """Location of citation text. bbox is (x0, y0, x1, y1) as fractions of the page size."""

    page_index: int
    page_label: str | None = None
    bbox: tuple[float, float, float, float] | None = None


class Provenance(Model):
    """Who made a change (7.4). Never taken from tool arguments."""

    principal: str
    channel: Channel
    origin: Origin


class Approval(Model):
    approval_id: str
    action: DecisionAction
    candidate_id: str | None
    occurrence_revision: int
    approved_at: datetime
    surface: ApprovalSurface


class ConfigSnapshot(Model):
    """Job config snapshot (6.6). A resumed job uses this, not current settings."""

    policy_profile: PolicyProfile
    network_mode: NetworkMode
    provider_allowlist: list[str]
    identity_policy_version: str
    eligibility_policy_version: str
    adapter_versions: dict[str, str]
    version_policy: VersionPolicy
    contact_email: str | None = None


# ---------- project-scoped entities ----------


class Project(Model):
    project_id: str
    name: str
    revision: int = 0
    policy_profile: PolicyProfile = PolicyProfile.DEFAULT
    version_policy: VersionPolicy = VersionPolicy.PREFER_PUBLISHED
    network_mode: NetworkMode = NetworkMode.METADATA  # D-12
    download_enabled: bool = False  # per-project files:download grant (11.2)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SectionInfo(Model):
    """A bibliography section (detected, inferred or manual). Pages are 0-based, inclusive."""

    section_id: str
    page_start: int
    page_end: int
    offset_start: int | None = None
    offset_end: int | None = None
    heading: str | None = None
    inferred: bool = False
    manual: bool = False


class Preflight(Model):
    pages: int
    text_coverage: float  # share of pages with a text layer, 0..1
    sections: list[SectionInfo] = Field(default_factory=list)
    estimated_entries: int = 0
    warnings: list[str] = Field(default_factory=list)


class Source(Model):
    source_id: str
    project_id: str
    type: SourceType
    label: str
    sha256: str  # of the imported bytes (PDF) or UTF-8 text
    origin: str  # resolved local path, or "pasted"
    preflight: Preflight
    sections: list[SectionInfo] = Field(default_factory=list)
    extraction_state: ExtractionState = ExtractionState.PENDING
    extraction_adapter: str | None = None
    revision: int = 0  # expected_source_revision for set_source_sections
    imported_at: datetime = Field(default_factory=utcnow)


class Occurrence(Model):
    """One citation in one source (7.2, 7.6)."""

    occurrence_id: str
    project_id: str
    source_id: str
    section_id: str | None = None
    order: float  # reading order inside the source; inserts use midpoints
    label: str | None = None
    raw_text: str
    normalized_text: str | None = None
    edited_text: str | None = None
    spans: list[Span] = Field(default_factory=list)
    warnings: list[ExtractionWarning] = Field(default_factory=list)
    extraction: ExtractionState = ExtractionState.EXTRACTED
    identity: IdentityState = IdentityState.UNPROCESSED
    identity_reasons: list[ReasonCode] = Field(default_factory=list)
    work_id: str | None = None  # accepted ProjectWork
    accepted_candidate_id: str | None = None
    approval: Approval | None = None  # current approval, if any
    discovery: DiscoveryState = DiscoveryState.NOT_CHECKED
    classification: AccessClassification | None = None
    access_checked_at: datetime | None = None
    local_copy: LocalCopyState = LocalCopyState.NONE
    retrieval: RetrievalState = RetrievalState.NOT_REQUESTED
    predecessor_ids: list[str] = Field(default_factory=list)  # split/merge/re-extract lineage
    excluded_reason: str | None = None
    revision: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def text(self) -> str:
        """The citation text in force: the edit if any, else the raw text (principle 2)."""
        return self.edited_text if self.edited_text is not None else self.raw_text


class ProviderEvidence(Model):
    provider: str
    record_key: str
    fetched_at: datetime
    identifier_linked: bool = False


class Candidate(Model):
    candidate_id: str
    project_id: str
    occurrence_id: str
    rank: int  # 1 = best
    metadata: WorkMetadata
    features: dict[str, float | int | bool | None] = Field(default_factory=dict)
    reasons: list[ReasonCode] = Field(default_factory=list)
    evidence: list[ProviderEvidence] = Field(default_factory=list)
    identifier_linked: bool = False
    rejected: bool = False


class Decision(Model):
    decision_id: str
    project_id: str
    occurrence_id: str
    action: DecisionAction
    candidate_id: str | None = None
    identity_after: IdentityState
    reasons: list[ReasonCode] = Field(default_factory=list)
    provenance: Provenance
    approval: Approval | None = None
    occurrence_revision: int
    note: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ProjectWork(Model):
    """Accepted work as used by this project (7.1). Never shared across projects in P0."""

    work_id: str
    project_id: str
    metadata: WorkMetadata
    overrides: dict[str, Any] = Field(default_factory=dict)  # set_fields values
    locks: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    link_keys: dict[str, str] = Field(default_factory=dict)  # {"doi": ..., "openalex_id": ...}
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AccessLocation(Model):
    location_id: str
    url: str
    provider: str
    version: CopyVersion = CopyVersion.UNKNOWN
    licence: str | None = None  # unknown stays None; grants no redistribution right
    basis: LocationBasis
    host_type: str | None = None
    eligible: bool
    is_pdf: bool = False


class SourceFailure(Model):
    source: str
    error: str


class AccessCheck(Model):
    """One discovery run for a work (7.5). Earlier checks stay, marked superseded."""

    check_id: str
    project_id: str
    work_id: str
    checked_at: datetime
    sources_consulted: list[str]
    sources_failed: list[SourceFailure] = Field(default_factory=list)
    eligibility_policy_version: str
    discovery: DiscoveryState
    classification: AccessClassification
    locations: list[AccessLocation] = Field(default_factory=list)
    restriction_evidence: list[str] = Field(default_factory=list)
    current: bool = True


class CheckResult(Model):
    outcome: CheckOutcome
    reason: str | None = None


class LocalCopy(Model):
    local_copy_id: str
    project_id: str
    work_id: str
    occurrence_id: str | None = None
    origin: LocalCopyOrigin
    sha256: str | None = None
    version: CopyVersion = CopyVersion.UNKNOWN
    checks: dict[LocalCopyCheck, CheckResult] = Field(default_factory=dict)
    state: LocalCopyState
    failed_check: LocalCopyCheck | None = None
    created_at: datetime = Field(default_factory=utcnow)


class WorkFile(Model):
    """A retrieved file for a work after verification (10.4)."""

    work_file_id: str
    project_id: str
    work_id: str
    sha256: str
    bytes: int
    source_url: str
    final_url: str
    location_id: str | None = None
    state: RetrievalState  # verified or needs_review
    reasons: list[str] = Field(default_factory=list)
    export_name: str  # {year}_{first_author}_{short_title}.pdf
    retrieved_at: datetime = Field(default_factory=utcnow)


class Artifact(Model):
    """Descriptor (11.2): {artifact_id, kind, media_type, bytes, sha256, project_revision}."""

    artifact_id: str
    project_id: str
    kind: str  # ArtifactKind value, plus the export format for exports
    format: str | None = None
    name: str
    media_type: str
    bytes: int
    sha256: str
    project_revision: int
    job_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Job(Model):
    job_id: str
    project_id: str
    stages: list[StepType]
    scope: dict[str, Any]  # {"source_ids": [...]} | {"occurrence_ids": [...]} | {"filter": {...}}
    options: dict[str, Any] = Field(default_factory=dict)
    state: JobState = JobState.QUEUED
    current_stage: int = 0  # index into stages
    snapshot: ConfigSnapshot
    snapshot_hash: str
    warnings: list[str] = Field(default_factory=list)
    provenance: Provenance
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Step(Model):
    step_id: str
    job_id: str
    project_id: str
    step_type: StepType
    subject_id: str  # source, occurrence, work or job id, by step type
    input_fingerprint: str
    snapshot_hash: str
    state: StepState = StepState.QUEUED
    attempt: int = 0
    max_attempts: int = 5
    executor_id: str | None = None
    not_before: datetime | None = None  # retry_wait until
    last_error: dict[str, Any] | None = None  # RcError.to_payload()
    output_ref: str | None = None
    skip_reason: SkipReason | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AuditEvent(Model):
    event_id: str
    project_id: str
    type: AuditEventType
    subject_id: str | None = None
    provenance: Provenance
    project_revision: int
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class IdempotencyRecord(Model):
    """Scope (principal, project_id, tool, key); stored 7 days (11.3)."""

    principal: str
    project_id: str  # "" for tools without a project (create_project)
    tool: str
    key: str
    fingerprint: str
    result: dict[str, Any]
    created_at: datetime
    expires_at: datetime


# ---------- shared tier ----------


class ProviderRecord(Model):
    provider: str
    record_key: str  # e.g. DOI, OpenAlex ID, S2 paper ID
    fetched_at: datetime
    payload_hash: str
    metadata: WorkMetadata
    raw: dict[str, Any] = Field(default_factory=dict)
    oa_pdf_url: str | None = None


class FileBlob(Model):
    sha256: str
    bytes: int
    media_type: str
    created_at: datetime = Field(default_factory=utcnow)
