"""Application service API (spec 5, 11.4): request/response DTOs and the service Protocol.

Every interface (MCP, CLI) calls `RefCollectorService`. Tool input models forbid unknown
fields, so `origin`, `channel` or `approval` in arguments fail validation with INVALID_INPUT
(7.4, AT-33). Provenance comes only from `CallContext`, which interfaces build themselves.

`TOOLS` maps each P0 MCP tool to its models, scope and flags; WP-11 generates schemas from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from refcollector.domain.enums import (
    AccessClassification,
    Channel,
    CopyVersion,
    DiscoveryState,
    DraftLanguage,
    ExportFormat,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyCheck,
    LocalCopyState,
    PolicyProfile,
    RetrievalState,
    Scope,
    Selection,
    SourceType,
    Stage,
    VersionPolicy,
)
from refcollector.domain.models import CheckResult, Preflight, SectionInfo, Span, WorkMetadata
from refcollector.errors import FeatureNotAvailable

IdempotencyKey = Annotated[str, Field(min_length=8, max_length=128)]
CitationText = Annotated[str, Field(min_length=1, max_length=4000)]
Limit = Annotated[int, Field(ge=1, le=100)]


class Dto(BaseModel):
    model_config = ConfigDict(extra="forbid")


# P1 values are absent from P0 schemas; a client that sends one gets FEATURE_NOT_AVAILABLE,
# not INVALID_INPUT (spec 3, AT-41). The before-validators below raise the RcError, which
# Pydantic does not wrap (it only wraps ValueError/AssertionError).
P1_SOURCE_TYPES = frozenset({"doi", "arxiv", "identifiers", "bibtex", "ris", "csl_json", "upload"})
P1_STAGES = frozenset({"contexts"})
P1_EXPORT_FORMATS = frozenset({"ris", "obsidian"})


def _reject_p1(values: Any, field_name: str, p1: frozenset[str]) -> None:
    if not isinstance(values, dict):
        return
    raw = values.get(field_name)
    items = raw if isinstance(raw, list) else [raw]
    found = sorted({v for v in items if isinstance(v, str) and v in p1})
    if found:
        raise FeatureNotAvailable(
            f"{field_name}: {', '.join(found)} is not in P0", details={field_name: found}
        )


# ---------- call context (never from tool arguments) ----------


@dataclass(frozen=True)
class CallContext:
    principal: str  # local OS user in P0
    channel: Channel
    scopes: frozenset[Scope]
    request_id: str
    tty: bool = False  # True only for `refcollector review` on a real terminal

    @property
    def origin(self) -> str:
        """7.4: MCP and non-interactive CLI are `agent`; TTY review is `interactive`."""
        if self.channel == Channel.EXECUTOR:
            return "automation"
        return "interactive" if self.channel == Channel.CLI_TTY_REVIEW and self.tty else "agent"


class Meta(Dto):
    request_id: str
    project_revision: int | None = None


class Out(Dto):
    meta: Meta | None = None  # filled by the service


# ---------- shared views ----------


class ArtifactDescriptor(Dto):
    artifact_id: str
    kind: str
    media_type: str
    bytes: int
    sha256: str
    project_revision: int
    name: str
    path: str | None = None  # file:// path inside RC_HOME for local clients (11.2)


class IdentityView(Dto):
    state: IdentityState
    reasons: list[str] = Field(default_factory=list)
    candidates: int = 0


class AccessView(Dto):
    discovery: DiscoveryState
    classification: AccessClassification | None = None
    checked_at: datetime | None = None


class OccurrenceSummary(Dto):
    """Spec 7.6."""

    occurrence_id: str
    source_id: str
    revision: int
    label: str | None
    raw_text: str
    edited_text: str | None
    spans: list[Span]
    extraction: ExtractionState
    identity: IdentityView
    access: AccessView
    local_copy: LocalCopyState
    retrieval: RetrievalState
    warnings: list[str] = Field(default_factory=list)


class CandidateView(Dto):
    candidate_id: str
    rank: int
    metadata: WorkMetadata
    features: dict[str, Any]
    reasons: list[str]
    providers: list[str]
    identifier_linked: bool
    rejected: bool


class StageCounts(Dto):
    name: str
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed_retryable: int = 0
    failed_permanent: int = 0
    skipped: int = 0


class JobSummary(Dto):
    job_id: str
    state: JobState
    stages: list[str]
    created_at: datetime


# ---------- 1 get_capabilities ----------


class GetCapabilitiesIn(Dto):
    pass


class ProviderStatus(Dto):
    name: str
    enabled: bool
    configured: bool


class GetCapabilitiesOut(Out):
    server_version: str
    release: Literal["P0"] = "P0"
    source_types: list[SourceType]
    stages: list[Stage]
    adapters: dict[str, bool]
    providers: list[ProviderStatus]
    local_copies: list[str]
    export_formats: list[ExportFormat]
    approval_surfaces: list[str]
    limits: dict[str, int | float]
    scopes_granted: list[Scope]
    config: dict[str, Any]  # {email_set: bool, keys: {name: bool}}


# ---------- 2-4 projects ----------


class ListProjectsIn(Dto):
    cursor: str | None = None
    limit: Limit | None = None


class ProjectListItem(Dto):
    project_id: str
    name: str
    revision: int
    counts: dict[str, int]
    updated_at: datetime


class ListProjectsOut(Out):
    projects: list[ProjectListItem]
    next_cursor: str | None


class CreateProjectIn(Dto):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    policy_profile: PolicyProfile | None = None
    version_policy: VersionPolicy | None = None
    idempotency_key: IdempotencyKey


class CreateProjectOut(Out):
    project_id: str
    revision: int


class GetProjectIn(Dto):
    project_id: str


class GetProjectOut(Out):
    project_id: str
    name: str
    revision: int
    policy: dict[str, Any]  # {profile, version_policy, identity_policy_version, eligibility_policy_version}
    network_mode: str
    counts: dict[str, Any]  # {occurrences, by_identity{}, by_access{}, by_retrieval{}}
    sources_count: int
    recent_jobs: list[JobSummary]
    recent_artifacts: list[ArtifactDescriptor]


# ---------- 5-8 sources ----------


class LocalFileInput(Dto):
    type: Literal["local_file"]
    path: str


class TextInput(Dto):
    type: Literal["text"]
    text: Annotated[str, Field(min_length=1)]  # 1 MB limit checked in bytes by the service
    label: str | None = None


class ImportSourceIn(Dto):
    project_id: str
    input: Annotated[LocalFileInput | TextInput, Field(discriminator="type")]
    idempotency_key: IdempotencyKey

    @model_validator(mode="before")
    @classmethod
    def _p1_types(cls, values: Any) -> Any:
        if isinstance(values, dict) and isinstance(values.get("input"), dict):
            _reject_p1(values["input"], "type", P1_SOURCE_TYPES)
        return values


class ImportSourceOut(Out):
    source_id: str
    reused: bool
    preflight: Preflight


class ListSourcesIn(Dto):
    project_id: str
    cursor: str | None = None
    limit: Limit | None = None


class SourceListItem(Dto):
    source_id: str
    label: str
    type: SourceType
    pages: int
    sections: int
    extraction_state: ExtractionState
    occurrence_count: int
    imported_at: datetime


class ListSourcesOut(Out):
    sources: list[SourceListItem]
    next_cursor: str | None


class GetSourceIn(Dto):
    project_id: str
    source_id: str


class GetSourceOut(Out):
    source: SourceListItem
    revision: int
    preflight: Preflight
    sections: list[SectionInfo]
    extraction: dict[str, Any]  # {state, adapter, occurrences, warnings_by_code{}}
    jobs: list[JobSummary]


class SectionRange(Dto):
    page_start: Annotated[int, Field(ge=0)]
    page_end: Annotated[int, Field(ge=0)]
    offset_start: int | None = None
    offset_end: int | None = None


class SetSourceSectionsIn(Dto):
    project_id: str
    source_id: str
    sections: Annotated[list[SectionRange], Field(min_length=1)]
    expected_source_revision: int
    idempotency_key: IdempotencyKey


class SetSourceSectionsOut(Out):
    sections: list[SectionInfo]
    requires_reextract: Literal[True] = True


# ---------- 9-11 jobs ----------


class SourceScope(Dto):
    source_ids: Annotated[list[str], Field(min_length=1)]


class OccurrenceScope(Dto):
    occurrence_ids: Annotated[list[str], Field(min_length=1)]


class JobOptions(Dto):
    max_occurrences: Annotated[int, Field(ge=1)] | None = None
    refresh: bool = False


class StartJobIn(Dto):
    project_id: str
    stages: Annotated[list[Stage], Field(min_length=1)]
    scope: SourceScope | OccurrenceScope
    options: JobOptions | None = None
    idempotency_key: IdempotencyKey

    @model_validator(mode="before")
    @classmethod
    def _p1_stages(cls, values: Any) -> Any:
        _reject_p1(values, "stages", P1_STAGES)
        return values


class StartJobOut(Out):
    job_id: str
    state: Literal["queued"] = "queued"
    steps_planned: int
    next_poll_after_ms: int


class GetJobIn(Dto):
    project_id: str
    job_id: str


class JobResults(Dto):
    source_ids: list[str] = Field(default_factory=list)
    occurrence_count: int = 0
    changed_filter: dict[str, str]  # {"job_id": ...}, accepted by list_references.filter
    artifact_ids: list[str] = Field(default_factory=list)


class GetJobOut(Out):
    job_id: str
    state: JobState
    stages: list[StageCounts]
    warnings: list[str]
    results: JobResults
    next_poll_after_ms: int | None = None


class ControlJobIn(Dto):
    project_id: str
    job_id: str
    action: Literal["pause", "resume", "cancel", "retry_failed"]
    idempotency_key: IdempotencyKey


class ControlJobOut(Out):
    job_id: str
    state: JobState


# ---------- 12-15 references ----------


class ReferenceFilter(Dto):
    source_id: str | None = None
    job_id: str | None = None
    extraction: ExtractionState | None = None
    identity: IdentityState | None = None
    discovery: DiscoveryState | None = None
    classification: AccessClassification | None = None
    local_copy: LocalCopyState | None = None
    retrieval: RetrievalState | None = None
    reason: str | None = None


class ListReferencesIn(Dto):
    project_id: str
    filter: ReferenceFilter | None = None
    query: Annotated[str, Field(max_length=500)] | None = None
    sort: Literal["order", "year", "updated"] = "order"
    fields: list[str] | None = None
    cursor: str | None = None
    limit: Limit | None = None


class ListReferencesOut(Out):
    occurrences: list[dict[str, Any]]  # OccurrenceSummary, trimmed to `fields` when given
    next_cursor: str | None
    total: int


ReferenceSection = Literal["candidates", "evidence", "access", "local_copies", "files", "history"]


class GetReferenceIn(Dto):
    project_id: str
    occurrence_id: str
    sections: list[ReferenceSection] | None = None


class GetReferenceOut(Out):
    occurrence: OccurrenceSummary
    work: WorkMetadata | None = None
    locks: dict[str, bool] = Field(default_factory=dict)
    candidates: list[CandidateView] | None = None
    evidence: list[dict[str, Any]] | None = None
    access: list[dict[str, Any]] | None = None  # AccessCheck history, current first
    local_copies: list[dict[str, Any]] | None = None
    files: list[dict[str, Any]] | None = None
    history: list[dict[str, Any]] | None = None  # audit events and decisions


class AddReferenceIn(Dto):
    project_id: str
    source_id: str
    text: CitationText
    after_occurrence_id: str | None = None
    idempotency_key: IdempotencyKey


class AddReferenceOut(Out):
    occurrence_id: str
    revision: int


class EditText(Dto):
    type: Literal["edit_text"]
    occurrence_id: str
    text: CitationText
    expected_revision: int


class SplitPart(Dto):
    text: CitationText
    span: Span | None = None


class Split(Dto):
    type: Literal["split"]
    occurrence_id: str
    parts: Annotated[list[SplitPart], Field(min_length=2, max_length=20)]
    expected_revision: int


class Merge(Dto):
    type: Literal["merge"]
    occurrence_ids: Annotated[list[str], Field(min_length=2, max_length=10)]
    text: CitationText | None = None
    expected_revisions: dict[str, int]


class Exclude(Dto):
    type: Literal["exclude"]
    occurrence_id: str
    reason: Annotated[str, Field(max_length=500)] | None = None
    expected_revision: int


class Restore(Dto):
    type: Literal["restore"]
    occurrence_id: str
    reason: Annotated[str, Field(max_length=500)] | None = None
    expected_revision: int


class SelectCandidate(Dto):
    type: Literal["select_candidate"]
    occurrence_id: str
    candidate_id: str
    note: Annotated[str, Field(max_length=2000)] | None = None
    expected_revision: int


class RejectCandidate(Dto):
    type: Literal["reject_candidate"]
    occurrence_id: str
    candidate_id: str
    note: Annotated[str, Field(max_length=2000)] | None = None
    expected_revision: int


class SetDoi(Dto):
    type: Literal["set_doi"]
    occurrence_id: str
    doi: Annotated[str, Field(min_length=7, max_length=300)]
    expected_revision: int


class SetFields(Dto):
    type: Literal["set_fields"]
    occurrence_id: str
    fields: dict[str, Any]  # keys must be in domain.models.SETTABLE_FIELDS
    expected_revision: int


class SetFieldLocks(Dto):
    type: Literal["set_field_locks"]
    occurrence_id: str
    locks: dict[str, bool]
    expected_revision: int


class MarkUnresolved(Dto):
    type: Literal["mark_unresolved"]
    occurrence_id: str
    expected_revision: int


Change = Annotated[
    EditText
    | Split
    | Merge
    | Exclude
    | Restore
    | SelectCandidate
    | RejectCandidate
    | SetDoi
    | SetFields
    | SetFieldLocks
    | MarkUnresolved,
    Field(discriminator="type"),
]


class UpdateReferenceIn(Dto):
    project_id: str
    change: Change
    idempotency_key: IdempotencyKey


class RevisionRef(Dto):
    occurrence_id: str
    revision: int


class UpdateReferenceOut(Out):
    changed: list[RevisionRef]
    created: list[RevisionRef]
    identity_state: IdentityState | None = None


# ---------- 16 resolve_citation ----------


class CitationFields(Dto):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None


class ResolveCitationIn(Dto):
    citation: CitationText | None = None
    fields: CitationFields | None = None


class RankedCandidate(Dto):
    rank: int
    metadata: WorkMetadata
    scores: dict[str, Any]
    reasons: list[str]


class ResolveCitationOut(Out):
    candidates: list[RankedCandidate]
    decision_hint: Literal["strong", "needs_review", "unresolved"]


# ---------- 17-18 files ----------


class AttachFileIn(Dto):
    project_id: str
    occurrence_id: str
    path: str
    version_hint: CopyVersion | None = None
    idempotency_key: IdempotencyKey


class AttachFileOut(Out):
    local_copy_id: str
    checks: dict[LocalCopyCheck, CheckResult]
    local_copy: LocalCopyState


class DownloadBudget(Dto):
    max_files: Annotated[int, Field(ge=1)] | None = None
    max_bytes: Annotated[int, Field(ge=1)] | None = None


class FilterScope(Dto):
    filter: ReferenceFilter


class DownloadFilesIn(Dto):
    project_id: str
    scope: OccurrenceScope | FilterScope
    version_policy: VersionPolicy | None = None
    budget: DownloadBudget | None = None
    idempotency_key: IdempotencyKey


class JobOut(Out):
    job_id: str


# ---------- 19-22 exports, drafts, artifacts ----------


class CreateExportIn(Dto):
    project_id: str
    project_revision: int
    formats: Annotated[list[ExportFormat], Field(min_length=1)]
    selection: Selection = Selection.ACCEPTED
    idempotency_key: IdempotencyKey

    @model_validator(mode="before")
    @classmethod
    def _p1_formats(cls, values: Any) -> Any:
        _reject_p1(values, "formats", P1_EXPORT_FORMATS)
        return values


class SenderIn(Dto):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    affiliation: Annotated[str, Field(max_length=300)] | None = None
    purpose: Annotated[str, Field(max_length=500)] | None = None
    honorific_style: Annotated[str, Field(max_length=50)] | None = None


class DraftRequestsIn(Dto):
    project_id: str
    scope: OccurrenceScope
    sender: SenderIn
    language: DraftLanguage
    group_by_author: bool = False
    idempotency_key: IdempotencyKey


class ListArtifactsIn(Dto):
    project_id: str
    kind: str | None = None
    cursor: str | None = None
    limit: Limit | None = None


class ListArtifactsOut(Out):
    artifacts: list[ArtifactDescriptor]
    next_cursor: str | None


class ReadArtifactIn(Dto):
    project_id: str
    artifact_id: str
    offset: Annotated[int, Field(ge=0)] = 0
    length: Annotated[int, Field(ge=1, le=65536)] | None = None


class ReadArtifactOut(Out):
    descriptor: ArtifactDescriptor
    text: str | None = None
    eof: bool | None = None


# ---------- non-tool service calls (CLI only) ----------


class UpdateProjectSettingsIn(Dto):
    """CLI only, never an MCP tool: agents must not raise their own permissions (11.2, 13)."""

    project_id: str
    network_mode: Literal["offline", "metadata", "retrieval"] | None = None
    download_enabled: bool | None = None
    version_policy: VersionPolicy | None = None
    idempotency_key: IdempotencyKey


class ReviewDecisionIn(Dto):
    """A decision taken on the TTY approval surface (7.4). Requires ctx.channel == cli_tty_review
    and ctx.tty; else NOT_AUTHORIZED. Accept/choose records an Approval -> accepted_user."""

    project_id: str
    occurrence_id: str
    action: Literal["accept", "reject"]
    candidate_id: str
    expected_revision: int
    note: str | None = None
    idempotency_key: IdempotencyKey


# ---------- the service ----------


class RefCollectorService(Protocol):
    """One method per P0 tool (11.4) plus CLI-only calls. Every method raises errors.RcError."""

    def get_capabilities(self, ctx: CallContext, req: GetCapabilitiesIn) -> GetCapabilitiesOut: ...
    def list_projects(self, ctx: CallContext, req: ListProjectsIn) -> ListProjectsOut: ...
    def create_project(self, ctx: CallContext, req: CreateProjectIn) -> CreateProjectOut: ...
    def get_project(self, ctx: CallContext, req: GetProjectIn) -> GetProjectOut: ...
    def import_source(self, ctx: CallContext, req: ImportSourceIn) -> ImportSourceOut: ...
    def list_sources(self, ctx: CallContext, req: ListSourcesIn) -> ListSourcesOut: ...
    def get_source(self, ctx: CallContext, req: GetSourceIn) -> GetSourceOut: ...
    def set_source_sections(self, ctx: CallContext, req: SetSourceSectionsIn) -> SetSourceSectionsOut: ...
    def start_job(self, ctx: CallContext, req: StartJobIn) -> StartJobOut: ...
    def get_job(self, ctx: CallContext, req: GetJobIn) -> GetJobOut: ...
    def control_job(self, ctx: CallContext, req: ControlJobIn) -> ControlJobOut: ...
    def list_references(self, ctx: CallContext, req: ListReferencesIn) -> ListReferencesOut: ...
    def get_reference(self, ctx: CallContext, req: GetReferenceIn) -> GetReferenceOut: ...
    def add_reference(self, ctx: CallContext, req: AddReferenceIn) -> AddReferenceOut: ...
    def update_reference(self, ctx: CallContext, req: UpdateReferenceIn) -> UpdateReferenceOut: ...
    def resolve_citation(self, ctx: CallContext, req: ResolveCitationIn) -> ResolveCitationOut: ...
    def attach_file(self, ctx: CallContext, req: AttachFileIn) -> AttachFileOut: ...
    def download_files(self, ctx: CallContext, req: DownloadFilesIn) -> JobOut: ...
    def create_export(self, ctx: CallContext, req: CreateExportIn) -> JobOut: ...
    def draft_requests(self, ctx: CallContext, req: DraftRequestsIn) -> JobOut: ...
    def list_artifacts(self, ctx: CallContext, req: ListArtifactsIn) -> ListArtifactsOut: ...
    def read_artifact(self, ctx: CallContext, req: ReadArtifactIn) -> ReadArtifactOut: ...

    # CLI only
    def update_project_settings(self, ctx: CallContext, req: UpdateProjectSettingsIn) -> GetProjectOut: ...
    def review_decision(self, ctx: CallContext, req: ReviewDecisionIn) -> UpdateReferenceOut: ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input: type[Dto]
    output: type[Dto]
    scope: Scope | None  # None = no scope (get_capabilities); start_job checks per stage
    mutating: bool  # requires idempotency_key
    read_only: bool
    open_world: bool = False
    stage_scopes: dict[Stage, Scope] = field(default_factory=dict)


STAGE_SCOPES = {
    Stage.EXTRACT: Scope.REFERENCES_WRITE,
    Stage.RESOLVE: Scope.METADATA_RESOLVE,
    Stage.DISCOVER_ACCESS: Scope.ACCESS_DISCOVER,
}

_TOOL_LIST = [
    ToolSpec("get_capabilities", GetCapabilitiesIn, GetCapabilitiesOut, None, False, True),
    ToolSpec("list_projects", ListProjectsIn, ListProjectsOut, Scope.PROJECTS_READ, False, True),
    ToolSpec("create_project", CreateProjectIn, CreateProjectOut, Scope.PROJECTS_WRITE, True, False),
    ToolSpec("get_project", GetProjectIn, GetProjectOut, Scope.PROJECTS_READ, False, True),
    ToolSpec("import_source", ImportSourceIn, ImportSourceOut, Scope.SOURCES_IMPORT, True, False),
    ToolSpec("list_sources", ListSourcesIn, ListSourcesOut, Scope.REFERENCES_READ, False, True),
    ToolSpec("get_source", GetSourceIn, GetSourceOut, Scope.REFERENCES_READ, False, True),
    ToolSpec(
        "set_source_sections", SetSourceSectionsIn, SetSourceSectionsOut, Scope.REFERENCES_WRITE, True, False
    ),
    ToolSpec("start_job", StartJobIn, StartJobOut, None, True, False, stage_scopes=STAGE_SCOPES),
    ToolSpec("get_job", GetJobIn, GetJobOut, Scope.PROJECTS_READ, False, True),
    ToolSpec("control_job", ControlJobIn, ControlJobOut, Scope.PROJECTS_WRITE, True, False),
    ToolSpec("list_references", ListReferencesIn, ListReferencesOut, Scope.REFERENCES_READ, False, True),
    ToolSpec("get_reference", GetReferenceIn, GetReferenceOut, Scope.REFERENCES_READ, False, True),
    ToolSpec("add_reference", AddReferenceIn, AddReferenceOut, Scope.REFERENCES_WRITE, True, False),
    ToolSpec("update_reference", UpdateReferenceIn, UpdateReferenceOut, Scope.REFERENCES_WRITE, True, False),
    ToolSpec(
        "resolve_citation", ResolveCitationIn, ResolveCitationOut, Scope.METADATA_RESOLVE, False, True, True
    ),
    ToolSpec("attach_file", AttachFileIn, AttachFileOut, Scope.FILES_ATTACH, True, False),
    ToolSpec("download_files", DownloadFilesIn, JobOut, Scope.FILES_DOWNLOAD, True, False, True),
    ToolSpec("create_export", CreateExportIn, JobOut, Scope.EXPORTS_CREATE, True, False),
    ToolSpec("draft_requests", DraftRequestsIn, JobOut, Scope.DRAFTS_CREATE, True, False),
    ToolSpec("list_artifacts", ListArtifactsIn, ListArtifactsOut, Scope.PROJECTS_READ, False, True),
    ToolSpec("read_artifact", ReadArtifactIn, ReadArtifactOut, Scope.PROJECTS_READ, False, True),
]

TOOLS: dict[str, ToolSpec] = {t.name: t for t in _TOOL_LIST}


# ---------- composition root (WP-10 implements refcollector.bootstrap.open_app) ----------


class AppHandle(Protocol):
    """What MCP and CLI get from `refcollector.bootstrap.open_app(settings)`."""

    service: RefCollectorService

    def start_executor(self) -> bool:
        """Try to become the executor (6.1); else poll for the lock in the background.
        Returns True when this process holds the lock now."""
        ...

    def close(self) -> None: ...
