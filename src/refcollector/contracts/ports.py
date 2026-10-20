"""Frozen interfaces between work packages (contracts v1).

Owners implement these; everyone else depends only on these Protocols, the domain models
and the errors. Changes go through the orchestrator (CONTRACT_CHANGE_REQUEST.md).

Concurrency (D-08): repositories, FileStore, extraction and verification are synchronous.
Provider adapters, HttpClient, discovery, retrieval and step handlers are async.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, Literal, Protocol, TypeVar

from refcollector.domain.enums import (
    CopyVersion,
    ExtractionWarning,
    IdentityState,
    NetworkMode,
    ReasonCode,
    RetrievalState,
    SourceType,
    StepType,
    VersionPolicy,
)
from refcollector.domain.models import (
    AccessCheck,
    AccessLocation,
    Artifact,
    AuditEvent,
    Candidate,
    ConfigSnapshot,
    Decision,
    FileBlob,
    IdempotencyRecord,
    Job,
    LocalCopy,
    Occurrence,
    Preflight,
    Project,
    ProjectWork,
    ProviderRecord,
    SectionInfo,
    Source,
    Span,
    Step,
    WorkFile,
    WorkMetadata,
)

T = TypeVar("T")


# =====================================================================================
# Storage (WP-01). Implementations live in storage/repos.py, files.py, audit.py,
# idempotency.py. Every method raises errors.NotFound for a missing or cross-project ID.
# =====================================================================================


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    next_after: str | None  # opaque position for the next call; None at the end
    total: int | None = None


class ProjectRepo(Protocol):
    def add(self, project: Project) -> Project: ...
    def get(self, project_id: str) -> Project: ...
    def update(self, project: Project) -> Project: ...
    def list(self, *, after: str | None = None, limit: int = 25) -> Page[Project]: ...
    def bump_revision(self, project_id: str) -> int:
        """+1 on every committed mutation in the project (11.3). Returns the new value."""
        ...


class SourceRepo(Protocol):
    def add(self, source: Source) -> Source: ...
    def get(self, project_id: str, source_id: str) -> Source: ...
    def find_by_hash(self, project_id: str, sha256: str) -> Source | None: ...
    def update(self, source: Source, *, expected_revision: int | None = None) -> Source:
        """Bumps `source.revision`. Mismatch -> errors.RevisionConflict."""
        ...

    def list(self, project_id: str, *, after: str | None = None, limit: int = 25) -> Page[Source]: ...


@dataclass(frozen=True)
class OccurrenceQuery:
    """Filter for list_references (11.4 tool 12). None = no condition."""

    source_id: str | None = None
    job_id: str | None = None
    extraction: str | None = None
    identity: str | None = None
    discovery: str | None = None
    classification: str | None = None
    local_copy: str | None = None
    retrieval: str | None = None
    reason: str | None = None  # ReasonCode or ExtractionWarning value
    work_id: str | None = None
    ids: Sequence[str] | None = None
    text: str | None = None  # substring match on the text in force
    sort: Literal["order", "year", "updated"] = "order"


class OccurrenceRepo(Protocol):
    def add_many(self, occurrences: Sequence[Occurrence]) -> None: ...
    def get(self, project_id: str, occurrence_id: str) -> Occurrence: ...
    def get_many(self, project_id: str, occurrence_ids: Sequence[str]) -> list[Occurrence]: ...
    def update(self, occurrence: Occurrence, *, expected_revision: int | None = None) -> Occurrence:
        """Writes the occurrence with revision + 1 and returns it.

        If `expected_revision` is given and differs from the stored revision ->
        errors.RevisionConflict(details={"current_revisions": {id: rev}}).
        """
        ...

    def check_revisions(self, project_id: str, expected: Mapping[str, int]) -> None:
        """All-or-nothing check for multi-occurrence edits (merge, AT-39)."""
        ...

    def list(
        self, project_id: str, query: OccurrenceQuery, *, after: str | None = None, limit: int = 25
    ) -> Page[Occurrence]: ...
    def link_to_job(self, project_id: str, job_id: str, occurrence_ids: Sequence[str]) -> None: ...


class CandidateRepo(Protocol):
    def replace_for_occurrence(
        self, project_id: str, occurrence_id: str, candidates: Sequence[Candidate]
    ) -> None: ...
    def list_for_occurrence(self, project_id: str, occurrence_id: str) -> list[Candidate]: ...
    def get(self, project_id: str, candidate_id: str) -> Candidate: ...
    def update(self, candidate: Candidate) -> Candidate: ...


class DecisionRepo(Protocol):
    def add(self, decision: Decision) -> Decision: ...
    def list_for_occurrence(self, project_id: str, occurrence_id: str) -> list[Decision]: ...


class WorkRepo(Protocol):
    def add(self, work: ProjectWork) -> ProjectWork: ...
    def get(self, project_id: str, work_id: str) -> ProjectWork: ...
    def update(self, work: ProjectWork) -> ProjectWork: ...
    def find_by_link_key(
        self, project_id: str, key: Literal["doi", "openalex_id"], value: str
    ) -> ProjectWork | None:
        """Per-project lookup only (AT-18, AT-42/43)."""
        ...


class AccessCheckRepo(Protocol):
    def add_current(self, check: AccessCheck) -> AccessCheck:
        """Stores `check` as current and marks earlier checks for the work superseded (AT-37)."""
        ...

    def current(self, project_id: str, work_id: str) -> AccessCheck | None: ...
    def history(self, project_id: str, work_id: str) -> list[AccessCheck]: ...


class LocalCopyRepo(Protocol):
    def add(self, copy: LocalCopy) -> LocalCopy: ...
    def list_for_work(self, project_id: str, work_id: str) -> list[LocalCopy]: ...


class WorkFileRepo(Protocol):
    def add(self, wf: WorkFile) -> WorkFile: ...
    def list_for_work(self, project_id: str, work_id: str) -> list[WorkFile]: ...


class ArtifactRepo(Protocol):
    def add(self, artifact: Artifact) -> Artifact: ...
    def get(self, project_id: str, artifact_id: str) -> Artifact: ...
    def list(
        self, project_id: str, *, kind: str | None = None, after: str | None = None, limit: int = 25
    ) -> Page[Artifact]: ...


class AuditRepo(Protocol):
    def append(self, event: AuditEvent) -> AuditEvent: ...
    def list(self, project_id: str, *, subject_id: str | None = None) -> list[AuditEvent]: ...


class IdempotencyRepo(Protocol):
    def get(
        self, principal: str, project_id: str, tool: str, key: str, *, now: datetime
    ) -> IdempotencyRecord | None:
        """Returns None for a missing or expired record (expired = treated as new, 11.3)."""
        ...

    def put(self, record: IdempotencyRecord) -> None: ...
    def purge_expired(self, now: datetime) -> int: ...


class ProviderRecordRepo(Protocol):
    """Shared tier (7.1)."""

    def upsert(self, record: ProviderRecord) -> None: ...
    def get(self, provider: str, record_key: str) -> ProviderRecord | None: ...


class FileStore(Protocol):
    """Content-addressed store under RC_HOME/store (6.4). Shared bytes, per-project grants."""

    def new_tmp_path(self, suffix: str = "") -> Path:
        """A fresh path in RC_HOME/tmp. The caller writes, verifies, then calls commit()."""
        ...

    def commit(self, tmp_path: Path, media_type: str) -> FileBlob:
        """Hash, atomically move into store/<sha[:2]>/<sha>, upsert file_blob. Idempotent per hash."""
        ...

    def grant(self, project_id: str, sha256: str) -> None: ...
    def path_for(self, project_id: str, sha256: str) -> Path:
        """Store path, only when the project holds a grant; else errors.NotFound."""
        ...

    def has_grant(self, project_id: str, sha256: str) -> bool: ...
    def cleanup_tmp(self) -> int:
        """Start-up: delete stray temp files. Returns the count."""
        ...

    def index_orphans(self) -> int:
        """Start-up: add file_blob rows for store files that have none. Returns the count."""
        ...


class Store(Protocol):
    """All repositories over one Database. `transaction()` is reentrant (storage/db.py)."""

    projects: ProjectRepo
    sources: SourceRepo
    occurrences: OccurrenceRepo
    candidates: CandidateRepo
    decisions: DecisionRepo
    works: WorkRepo
    access_checks: AccessCheckRepo
    local_copies: LocalCopyRepo
    work_files: WorkFileRepo
    artifacts: ArtifactRepo
    audit: AuditRepo
    idempotency: IdempotencyRepo
    provider_records: ProviderRecordRepo
    files: FileStore

    def transaction(self, immediate: bool = False) -> AbstractContextManager[Any]: ...


# =====================================================================================
# Executor (WP-02)
# =====================================================================================


@dataclass(frozen=True)
class StepSpec:
    """A step to create. The executor fills IDs, snapshot hash and state."""

    step_type: StepType
    subject_id: str
    input_fingerprint: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepContext:
    step: Step
    job: Job
    store: Store
    is_cancelled: Callable[[], bool]  # handlers check this at each I/O boundary (6.4)


@dataclass
class StepResult:
    """Returned by a handler. `commit` runs inside the executor's transaction together with
    the step's `succeeded` update (6.4); it returns the step's output_ref."""

    commit: Callable[[Store], str | None]
    warnings: list[str] = field(default_factory=list)


class StepHandler(Protocol):
    step_type: StepType

    async def run(self, ctx: StepContext) -> StepResult:
        """Do the work; raise errors.RcError on failure (retryable per error.retryable)."""
        ...


class JobPlanner(Protocol):
    """Creates the steps of a stage when the job reaches it (WP-10 implements)."""

    def plan(self, store: Store, job: Job, stage: StepType) -> list[StepSpec]: ...


class JobQueue(Protocol):
    """Job/step persistence used by services (enqueue, control) and the executor (WP-02)."""

    def create_job(self, job: Job) -> Job:
        """Stores a queued job. Steps are planned by the executor when it starts the stage."""
        ...

    def get_job(self, project_id: str, job_id: str) -> Job: ...
    def list_jobs(self, project_id: str, *, limit: int = 10) -> list[Job]: ...
    def stage_counts(self, job_id: str) -> dict[StepType, dict[str, int]]:
        """Per stage: counts by StepState value (get_job.stages)."""
        ...

    def request(
        self, project_id: str, job_id: str, action: Literal["pause", "resume", "cancel", "retry_failed"]
    ) -> Job:
        """State transition per 6.3 / 6.5. Invalid transition -> errors.InvalidState."""
        ...

    def steps(self, job_id: str, *, state: str | None = None) -> list[Step]: ...


class Executor(Protocol):
    executor_id: str

    def try_acquire(self) -> bool:
        """Take RC_HOME/executor.lock without blocking. True when this process is the executor."""
        ...

    @property
    def is_owner(self) -> bool: ...
    def start(self) -> None:
        """Start the background loop: own the lock (or poll for it every 5 s), recover, claim, run."""
        ...

    def stop(self, timeout: float = 10.0) -> None: ...
    async def run_until_idle(self) -> None:
        """Test helper: run steps until no claimable step remains."""
        ...


def snapshot_hash(snapshot: ConfigSnapshot) -> str:
    """Stable hash of a config snapshot (6.6)."""
    import hashlib

    return hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()


# =====================================================================================
# Extraction (WP-03)
# =====================================================================================


@dataclass(frozen=True)
class ExtractedEntry:
    text: str
    section_id: str | None
    label: str | None = None  # "[42]", "12." or None
    spans: list[Span] = field(default_factory=list)
    warnings: list[ExtractionWarning] = field(default_factory=list)


@dataclass(frozen=True)
class ExtractionResult:
    sections: list[SectionInfo]
    entries: list[ExtractedEntry]
    warnings: list[str] = field(default_factory=list)


class ExtractionAdapter(Protocol):
    name: str  # "builtin"
    version: str

    def preflight(self, data: bytes | str, kind: SourceType) -> Preflight:
        """Raises EncryptedDocument, OcrRequired, LimitExceeded, UnsupportedSource (8.1)."""
        ...

    def extract(
        self, data: bytes | str, kind: SourceType, sections: Sequence[SectionInfo] | None = None
    ) -> ExtractionResult:
        """`sections=None` = detect. Raises ReferenceSectionNotFound when nothing is found or inferred."""
        ...


# =====================================================================================
# Providers (WP-04)
# =====================================================================================


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str
    from_cache: bool = False

    def json(self) -> Any:
        import json

        return json.loads(self.body)


@dataclass(frozen=True)
class DownloadResult:
    final_url: str
    status: int
    content_type: str | None
    path: Path  # temp file in RC_HOME/tmp; the caller verifies, then commits or deletes it
    bytes: int


class NetGuard(Protocol):
    """Retrieval network policy (spec 13, 10.4 step 1). WP-08 implements."""

    async def check(self, url: str) -> None:
        """Raises errors.BlockedAddress / errors.NotAuthorized for non-HTTPS URLs and for hosts that
        resolve to private, loopback, link-local or metadata addresses."""
        ...


class HttpClient(Protocol):
    """One client per job, built from the job's ConfigSnapshot (D-08).

    - offline network mode: raises errors.NetworkModeDenied before any socket opens (AT-20)
    - User-Agent `refcollector/<version> (mailto:<email>)`
    - per-host aiolimiter; Retry-After honoured; Retry-After > 60 s -> host blocked for this
      client and errors.ProviderUnavailable (6.5)
    - 408/429/5xx/timeouts -> errors.TransientNetworkError / errors.RateLimited (retryable)
    """

    blocked_hosts: set[str]

    async def get(
        self,
        url: str,
        *,
        provider: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl_s: int | None = None,
    ) -> HttpResponse:
        """Metadata request through the response cache. 4xx other than 408/429 are returned, not raised."""
        ...

    async def download(
        self,
        url: str,
        *,
        dest: Path,
        max_bytes: int,
        timeout_s: float,
        max_redirects: int,
        guard: NetGuard,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        """Streamed retrieval (network mode `retrieval` only). Every redirect target passes
        `guard.check`. Partial files are deleted on any failure (AT-10a)."""
        ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class CitationQuery:
    text: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None


class MetadataProvider(Protocol):
    """Crossref, DataCite, doi.org, OpenAlex, Semantic Scholar, Open Library."""

    name: str

    async def by_identifier(
        self, kind: Literal["doi", "isbn", "arxiv", "pmid"], value: str
    ) -> ProviderRecord | None:
        """None when the provider has no record or does not support the identifier kind."""
        ...

    async def search(self, query: CitationQuery, rows: int = 5) -> list[ProviderRecord]:
        """[] when the provider has no text search."""
        ...


@dataclass(frozen=True)
class OALocationResult:
    """One source's work-specific locations and explicit restriction statements.

    Evidence is interpreted by the adapter from documented response fields.
    HTTP errors, absence of an OA result, licences and URL reachability are
    not restriction evidence. Discovery adds the consulted source name.
    """

    locations: list[AccessLocation] = field(default_factory=list)
    restriction_evidence: list[str] = field(default_factory=list)


class OAProvider(Protocol):
    """Unpaywall, OpenAlex locations (10.1).

    Legacy list-returning adapters remain supported. Adapters can additionally
    implement DetailedOAProvider; discovery then uses its single detailed call.
    """

    name: str

    async def locations(self, doi: str) -> list[AccessLocation]:
        """Raises errors.ProviderUnavailable when the provider did not answer."""
        ...


class DetailedOAProvider(OAProvider, Protocol):
    """Optional additive capability; the legacy locations method is unchanged."""

    async def locations_detailed(self, doi: str) -> OALocationResult:
        """Same failure contract as locations; never infer restriction from failure."""
        ...


# =====================================================================================
# Identity (WP-06)
# =====================================================================================


@dataclass
class ScoredCandidate:
    metadata: WorkMetadata
    features: dict[str, float | int | bool | None]
    reasons: list[ReasonCode]
    evidence: list[ProviderRecord]
    identifier_linked: bool
    score: float  # ranking value; not shown as a confidence


@dataclass
class ResolutionResult:
    state: IdentityState  # accepted_auto, needs_review or unresolved
    reasons: list[ReasonCode]
    candidates: list[ScoredCandidate]  # ranked, at most 5
    accepted_index: int | None  # index into candidates when state == accepted_auto
    providers_failed: list[str] = field(default_factory=list)
    policy_version: str = "identity-policy-v1"


class IdentityResolver(Protocol):
    async def resolve(
        self,
        citation: str | None,
        *,
        fields: CitationQuery | None = None,
        forced_doi: str | None = None,
        rejected: Sequence[WorkMetadata] = (),
    ) -> ResolutionResult:
        """Spec 9.1 steps 1-7, policy 9.2. `rejected` candidates are never re-accepted."""
        ...

    def evaluate(self, citation: str, metadata: WorkMetadata) -> ScoredCandidate:
        """Score one known candidate (select_candidate checks, set_doi, file identity checks)."""
        ...

    def merge_metadata(
        self, work: ProjectWork | None, accepted: WorkMetadata, *, source_rank: int
    ) -> WorkMetadata:
        """Metadata precedence 9.4: locks > identifier-linked > corroborating > parsed > agent.
        Locked fields of `work` never change (AT-11)."""
        ...


# =====================================================================================
# Access (WP-07), files (WP-08)
# =====================================================================================


@dataclass(frozen=True)
class ApprovedLocation:
    """Trusted internal input after approval verification for one project/work.

    This scope is checked by discovery, but is not itself proof of approval.
    Only a caller that verified the approval may construct this input. Never
    construct it from a tool argument, a provider claim or a location ID match.
    """

    project_id: str
    work_id: str
    location: AccessLocation


class AccessDiscoverer(Protocol):
    async def discover(
        self,
        project_id: str,
        work: ProjectWork,
        *,
        version_policy: VersionPolicy,
        now: datetime,
        approved_locations: Sequence[ApprovedLocation] = (),
    ) -> AccessCheck:
        """One discovery run (10.1, 7.5). Never raises for provider failures; they go to
        `sources_failed`. The result is not stored; the caller stores it as current.

        approved_locations is a trusted internal input, never a public tool
        flag. The caller must verify approval for this project and work first.
        Discovery rejects mismatched scope and marks these copies user_approved
        without changing their provider provenance, identity, version or licence.
        Approval does not bypass network, retrieval or file safety controls.
        """
        ...


@dataclass
class VerificationResult:
    """10.4 steps 2-6."""

    state: RetrievalState  # verified, needs_review or failed
    reasons: list[str]
    readable: bool
    identity_match: bool | None  # None = not enough evidence
    version: CopyVersion = CopyVersion.UNKNOWN
    evidence: dict[str, Any] = field(default_factory=dict)


class FileVerifier(Protocol):
    def verify(
        self, path: Path, work: WorkMetadata, *, content_type: str | None = None
    ) -> VerificationResult:
        """Content type + %PDF + full parse (fail -> state failed, FILE_VALIDATION_FAILED);
        first-two-page DOI/title evidence, compare, supplement/wrong-work detection
        (fail -> needs_review, never verified)."""
        ...


@dataclass
class RetrievalOutcome:
    state: RetrievalState
    work_file: WorkFile | None
    reasons: list[str]
    error: dict[str, Any] | None = None


class Retriever(Protocol):
    async def retrieve(
        self,
        project_id: str,
        work: ProjectWork,
        locations: Sequence[AccessLocation],
        *,
        http: HttpClient,
        is_cancelled: Callable[[], bool],
    ) -> RetrievalOutcome:
        """Try eligible locations in policy order; verify; commit to FileStore and grant the
        project only for verified or needs_review files. Returns an unsaved WorkFile."""
        ...


class LocalCopyChecker(Protocol):
    def evaluate(
        self,
        project_id: str,
        work: ProjectWork,
        *,
        version_policy: VersionPolicy,
        attached_path: Path | None = None,
        version_hint: CopyVersion | None = None,
    ) -> list[LocalCopy]:
        """Five checks (10.3) for each local-copy source: managed store files already verified
        for this work in this project, plus `attached_path` (origin user_attached, copied into the
        store). Returns unsaved LocalCopy rows; state usable only when all five pass."""
        ...


# =====================================================================================
# Outputs (WP-05)
# =====================================================================================


@dataclass(frozen=True)
class ExportRow:
    """One occurrence with its accepted work (if any), for renderers."""

    occurrence: Occurrence
    work: ProjectWork | None
    decision: Decision | None
    access: AccessCheck | None
    files: list[WorkFile] = field(default_factory=list)


@dataclass(frozen=True)
class RenderedFile:
    name: str  # file name inside the artifact folder
    media_type: str
    data: bytes


class Exporter(Protocol):
    def render(
        self, fmt: str, rows: Sequence[ExportRow], *, selection: str, project: Project
    ) -> RenderedFile: ...
    def manifest(
        self,
        project: Project,
        *,
        selection: str,
        files: Sequence[RenderedFile],
        counts: Mapping[str, int],
        policy_versions: Mapping[str, str],
    ) -> RenderedFile: ...


@dataclass(frozen=True)
class Sender:
    name: str
    affiliation: str | None = None
    purpose: str | None = None
    honorific_style: str | None = None


class DraftRenderer(Protocol):
    def render(
        self, rows: Sequence[ExportRow], *, sender: Sender, language: str, group_by_author: bool
    ) -> list[RenderedFile]:
        """One .txt bundle plus one .eml per draft; recipients blank (12.2)."""
        ...


# =====================================================================================
# Network-mode helper shared by services and pipeline
# =====================================================================================


def network_allows(mode: NetworkMode, need: Literal["metadata", "retrieval"]) -> bool:
    order = {NetworkMode.OFFLINE: 0, NetworkMode.METADATA: 1, NetworkMode.RETRIEVAL: 2}
    return order[mode] >= {"metadata": 1, "retrieval": 2}[need]
