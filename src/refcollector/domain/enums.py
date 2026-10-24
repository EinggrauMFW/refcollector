"""State and value enums (spec 3, 6.3, 7.3, 7.4, 9.3, 10, 11.2, 12).

P0 values only. P1 values (for example source type "doi", origin "zotero_attachment",
reason "RETRACTED", stage "contexts") must not appear here (spec 3).
"""

from __future__ import annotations

from enum import StrEnum

# ---- 7.3 independent state dimensions ----


class ExtractionState(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    NEEDS_REVIEW = "needs_review"
    CORRECTED = "corrected"
    EXCLUDED = "excluded"
    FAILED = "failed"


class IdentityState(StrEnum):
    UNPROCESSED = "unprocessed"
    ACCEPTED_AUTO = "accepted_auto"
    ACCEPTED_AGENT = "accepted_agent"
    ACCEPTED_USER = "accepted_user"
    NEEDS_REVIEW = "needs_review"
    UNRESOLVED = "unresolved"
    REJECTED = "rejected"


ACCEPTED_IDENTITIES = frozenset(
    {IdentityState.ACCEPTED_AUTO, IdentityState.ACCEPTED_AGENT, IdentityState.ACCEPTED_USER}
)


class DiscoveryState(StrEnum):
    NOT_CHECKED = "not_checked"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class AccessClassification(StrEnum):
    ELIGIBLE_FOUND = "eligible_found"
    NONE_FOUND = "none_found"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class LocalCopyState(StrEnum):
    NONE = "none"
    CANDIDATE = "candidate"
    USABLE = "usable"
    UNUSABLE = "unusable"


class RetrievalState(StrEnum):
    NOT_REQUESTED = "not_requested"
    QUEUED = "queued"
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    SATISFIED_BY_LOCAL_COPY = "satisfied_by_local_copy"


# ---- 7.4 decision origin ----


class Channel(StrEnum):
    MCP = "mcp"
    CLI = "cli"
    CLI_TTY_REVIEW = "cli_tty_review"
    EXECUTOR = "executor"
    # "web_ui" is P1 (spec 3) and absent here in the original P0 baseline.
    WEB_UI = "web_ui"


class Origin(StrEnum):
    AUTOMATION = "automation"
    AGENT = "agent"
    INTERACTIVE = "interactive"


class ApprovalSurface(StrEnum):
    CLI_TTY = "cli_tty"
    WEB_UI = "web_ui"


class DecisionAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    MARK_UNRESOLVED = "mark_unresolved"
    SET_DOI = "set_doi"


# ---- 6.3 jobs and steps ----


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


TERMINAL_JOB_STATES = frozenset(
    {JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS, JobState.FAILED, JobState.CANCELLED}
)


class StepState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


TERMINAL_STEP_STATES = frozenset(
    {
        StepState.SUCCEEDED,
        StepState.FAILED_RETRYABLE,
        StepState.FAILED_PERMANENT,
        StepState.CANCELLED,
        StepState.SKIPPED,
    }
)


class Stage(StrEnum):
    """Public stages for start_job (spec 11.4 tool 9). "contexts" is P1."""

    EXTRACT = "extract"
    RESOLVE = "resolve"
    DISCOVER_ACCESS = "discover_access"


class StepType(StrEnum):
    """Executor step types (spec 6.2): the public stages plus internal ones."""

    EXTRACT = "extract"
    RESOLVE = "resolve"
    DISCOVER_ACCESS = "discover_access"
    RETRIEVE = "retrieve"
    RETRIEVE_DOI = "retrieve_doi"
    EXPORT = "export"
    DRAFT = "draft"


class SkipReason(StrEnum):
    OUTPUT_REUSED = "OUTPUT_REUSED"
    PREREQUISITE_FAILED = "PREREQUISITE_FAILED"
    SATISFIED_BY_LOCAL_COPY = "SATISFIED_BY_LOCAL_COPY"


# ---- 9.3 reason codes and extraction warnings ----


class ReasonCode(StrEnum):
    DOI_TITLE_CONFLICT = "DOI_TITLE_CONFLICT"
    AUTHOR_CONFLICT = "AUTHOR_CONFLICT"
    YEAR_CONFLICT = "YEAR_CONFLICT"
    EDITION_CONFLICT = "EDITION_CONFLICT"
    VERSION_DIFFERENCE = "VERSION_DIFFERENCE"
    GENERIC_TITLE = "GENERIC_TITLE"
    MULTIPLE_CLOSE_CANDIDATES = "MULTIPLE_CLOSE_CANDIDATES"
    INSUFFICIENT_FIELDS = "INSUFFICIENT_FIELDS"
    PROVIDER_DISAGREEMENT = "PROVIDER_DISAGREEMENT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    POSSIBLE_MERGED_REFERENCE = "POSSIBLE_MERGED_REFERENCE"
    POSSIBLE_TRUNCATED_REFERENCE = "POSSIBLE_TRUNCATED_REFERENCE"
    APPROVAL_STALE = "APPROVAL_STALE"
    FILE_REJECTED_BY_USER = "FILE_REJECTED_BY_USER"


class ExtractionWarning(StrEnum):
    POSSIBLE_MERGED_REFERENCE = "POSSIBLE_MERGED_REFERENCE"
    POSSIBLE_TRUNCATED_REFERENCE = "POSSIBLE_TRUNCATED_REFERENCE"
    COLUMN_ORDER_SUSPECT = "COLUMN_ORDER_SUSPECT"


class AuditEventType(StrEnum):
    PROJECT_CREATED = "PROJECT_CREATED"
    SOURCE_IMPORTED = "SOURCE_IMPORTED"
    SECTIONS_SET = "SECTIONS_SET"
    OCCURRENCES_EXTRACTED = "OCCURRENCES_EXTRACTED"
    OCCURRENCE_ADDED = "OCCURRENCE_ADDED"
    OCCURRENCE_EDITED = "OCCURRENCE_EDITED"
    OCCURRENCE_SPLIT = "OCCURRENCE_SPLIT"
    OCCURRENCES_MERGED = "OCCURRENCES_MERGED"
    OCCURRENCE_EXCLUDED = "OCCURRENCE_EXCLUDED"
    OCCURRENCE_RESTORED = "OCCURRENCE_RESTORED"
    CANDIDATES_UPDATED = "CANDIDATES_UPDATED"
    DECISION_RECORDED = "DECISION_RECORDED"
    APPROVAL_RECORDED = "APPROVAL_RECORDED"
    APPROVAL_STALE = "APPROVAL_STALE"
    FILE_APPROVED = "FILE_APPROVED"
    FILE_REJECTED = "FILE_REJECTED"
    FIELDS_SET = "FIELDS_SET"
    LOCKS_SET = "LOCKS_SET"
    ACCESS_CHECKED = "ACCESS_CHECKED"
    LOCAL_COPY_EVALUATED = "LOCAL_COPY_EVALUATED"
    FILE_RETRIEVED = "FILE_RETRIEVED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    JOB_CREATED = "JOB_CREATED"
    JOB_CONTROLLED = "JOB_CONTROLLED"
    PROJECT_SETTINGS_CHANGED = "PROJECT_SETTINGS_CHANGED"
    RECOVERED_AFTER_EXECUTOR_LOSS = "RECOVERED_AFTER_EXECUTOR_LOSS"


# ---- sources, policies, network ----


class SourceType(StrEnum):
    PDF = "pdf"
    TEXT = "text"


class PolicyProfile(StrEnum):
    DEFAULT = "default"
    STRICT = "strict"


class VersionPolicy(StrEnum):
    PREFER_PUBLISHED = "prefer_published"
    ANY_ELIGIBLE = "any_eligible"
    CITED_VERSION_ONLY = "cited_version_only"


class NetworkMode(StrEnum):
    OFFLINE = "offline"
    METADATA = "metadata"
    RETRIEVAL = "retrieval"


class Scope(StrEnum):
    PROJECTS_READ = "projects:read"
    PROJECTS_WRITE = "projects:write"
    SOURCES_IMPORT = "sources:import"
    REFERENCES_READ = "references:read"
    REFERENCES_WRITE = "references:write"
    METADATA_RESOLVE = "metadata:resolve"
    ACCESS_DISCOVER = "access:discover"
    FILES_DOWNLOAD = "files:download"
    FILES_ATTACH = "files:attach"
    EXPORTS_CREATE = "exports:create"
    DRAFTS_CREATE = "drafts:create"


# ---- 10 access, local copies, files ----


class CopyVersion(StrEnum):
    SUBMITTED = "submitted"  # preprint
    ACCEPTED = "accepted"  # accepted manuscript
    PUBLISHED = "published"  # version of record
    UNKNOWN = "unknown"


class LocationBasis(StrEnum):
    CONFIGURED_FALLBACK = "configured_fallback"
    OA_PROVIDER = "oa_provider"
    REPOSITORY = "repository"
    USER_APPROVED = "user_approved"


class LocalCopyOrigin(StrEnum):
    MANAGED_RETRIEVED = "managed_retrieved"
    USER_ATTACHED = "user_attached"
    # "zotero_attachment" is P1.


class LocalCopyCheck(StrEnum):
    RECORD = "record"
    ATTACHMENT = "attachment"
    READABLE = "readable"
    IDENTITY = "identity"
    VERSION = "version"


class CheckOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 - check outcome, not a password
    FAIL = "fail"
    SKIPPED = "skipped"


# ---- 12 outputs ----


class ExportFormat(StrEnum):
    BIBTEX = "bibtex"
    CSL_JSON = "csl_json"
    CSV = "csv"
    JSON = "json"
    UNRESOLVED = "unresolved"
    AUDIT_REPORT = "audit_report"
    # "ris" and "obsidian" are P1. manifest.json is always added (spec 11.4 tool 19).


class Selection(StrEnum):
    ACCEPTED = "accepted"
    REVIEWED = "reviewed"
    COMPLETE_AUDIT = "complete_audit"


class ArtifactKind(StrEnum):
    EXPORT = "export"
    MANIFEST = "manifest"
    DRAFT_BUNDLE = "draft_bundle"
    DRAFT_EML = "draft_eml"


class DraftLanguage(StrEnum):
    EN = "en"
    ID = "id"
