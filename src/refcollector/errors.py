"""Error codes (spec 11.5) as typed exceptions (spec 6.6).

Every library failure is an `RcError` subclass. Interfaces map it to the wire shape with
`to_payload()`. No `SystemExit` below the CLI entry point.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    ENCRYPTED_DOCUMENT = "ENCRYPTED_DOCUMENT"
    OCR_REQUIRED = "OCR_REQUIRED"
    REFERENCE_SECTION_NOT_FOUND = "REFERENCE_SECTION_NOT_FOUND"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    PREREQUISITE_NOT_MET = "PREREQUISITE_NOT_MET"
    INVALID_STATE = "INVALID_STATE"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    REVISION_NOT_CURRENT = "REVISION_NOT_CURRENT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    NETWORK_MODE_DENIED = "NETWORK_MODE_DENIED"
    CONFIG_MISSING_EMAIL = "CONFIG_MISSING_EMAIL"
    FEATURE_NOT_AVAILABLE = "FEATURE_NOT_AVAILABLE"
    ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
    INTEGRATION_UNAVAILABLE = "INTEGRATION_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    FILE_VALIDATION_FAILED = "FILE_VALIDATION_FAILED"
    CANCELLED = "CANCELLED"


class RcError(Exception):
    """Base error. Subclasses fix `code`, `retryable` and a default `recovery_hint`."""

    code: ErrorCode = ErrorCode.INVALID_INPUT
    retryable: bool = False
    recovery_hint: str = ""

    def __init__(
        self,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
        recovery_hint: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message or self.code.value)
        self.message = message or self.code.value
        self.details = details or {}
        if recovery_hint is not None:
            self.recovery_hint = recovery_hint
        self.retry_after = retry_after  # seconds; set by providers from Retry-After

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "recovery_hint": self.recovery_hint,
        }
        if self.details:
            out["details"] = self.details
        return out


def _error(name: str, code: ErrorCode, hint: str, retryable: bool = False) -> type[RcError]:
    return type(name, (RcError,), {"code": code, "recovery_hint": hint, "retryable": retryable})


InvalidInput = _error("InvalidInput", ErrorCode.INVALID_INPUT, "Fix the request and send it again.")
NotFound = _error("NotFound", ErrorCode.NOT_FOUND, "Check the ID and the project.")
NotAuthorized = _error("NotAuthorized", ErrorCode.NOT_AUTHORIZED, "Grant the scope in the config.")
PathNotAllowed = _error(
    "PathNotAllowed", ErrorCode.PATH_NOT_ALLOWED, "Add the folder: refcollector config allow-folder PATH."
)
UnsupportedSource = _error("UnsupportedSource", ErrorCode.UNSUPPORTED_SOURCE, "Use a PDF or pasted text.")
EncryptedDocument = _error(
    "EncryptedDocument", ErrorCode.ENCRYPTED_DOCUMENT, "Remove the password, then import again."
)
OcrRequired = _error("OcrRequired", ErrorCode.OCR_REQUIRED, "The pages have no text layer. OCR is P1.")
ReferenceSectionNotFound = _error(
    "ReferenceSectionNotFound",
    ErrorCode.REFERENCE_SECTION_NOT_FOUND,
    "Set the page range with set_source_sections / refcollector sections.",
)
LimitExceeded = _error("LimitExceeded", ErrorCode.LIMIT_EXCEEDED, "Reduce the input below the limit.")
PrerequisiteNotMet = _error(
    "PrerequisiteNotMet", ErrorCode.PREREQUISITE_NOT_MET, "Run the earlier stage first."
)
InvalidState = _error("InvalidState", ErrorCode.INVALID_STATE, "Read the current state and try again.")
RevisionConflict = _error(
    "RevisionConflict", ErrorCode.REVISION_CONFLICT, "Read the current revisions and apply the edit again."
)
RevisionNotCurrent = _error(
    "RevisionNotCurrent", ErrorCode.REVISION_NOT_CURRENT, "Use the current project revision."
)
IdempotencyConflict = _error(
    "IdempotencyConflict", ErrorCode.IDEMPOTENCY_CONFLICT, "Use a new idempotency_key for a new request."
)
NetworkModeDenied = _error(
    "NetworkModeDenied", ErrorCode.NETWORK_MODE_DENIED, "Change the project network mode."
)
ConfigMissingEmail = _error(
    "ConfigMissingEmail", ErrorCode.CONFIG_MISSING_EMAIL, "Set it: refcollector config set email ADDRESS."
)
FeatureNotAvailable = _error(
    "FeatureNotAvailable", ErrorCode.FEATURE_NOT_AVAILABLE, "This feature is not in this release."
)
AdapterUnavailable = _error("AdapterUnavailable", ErrorCode.ADAPTER_UNAVAILABLE, "Use the built-in adapter.")
IntegrationUnavailable = _error(
    "IntegrationUnavailable", ErrorCode.INTEGRATION_UNAVAILABLE, "Start or configure the integration."
)
ProviderUnavailable = _error(
    "ProviderUnavailable", ErrorCode.PROVIDER_UNAVAILABLE, "Retry later.", retryable=True
)
RateLimited = _error("RateLimited", ErrorCode.RATE_LIMITED, "Wait for Retry-After.", retryable=True)
BudgetExceeded = _error("BudgetExceeded", ErrorCode.BUDGET_EXCEEDED, "Increase the budget or narrow scope.")
FileValidationFailed = _error(
    "FileValidationFailed", ErrorCode.FILE_VALIDATION_FAILED, "The file is not the expected PDF."
)
Cancelled = _error("Cancelled", ErrorCode.CANCELLED, "The job was cancelled.")


class BlockedAddress(NotAuthorized):  # type: ignore[misc, valid-type]
    """Retrieval target resolves to a private, loopback, link-local or metadata address (spec 13)."""


class TransientNetworkError(ProviderUnavailable):  # type: ignore[misc, valid-type]
    """Timeout, connection error, HTTP 408/5xx: retryable step failure (spec 6.5)."""


ERRORS: dict[ErrorCode, type[RcError]] = {
    cls.code: cls
    for cls in (
        InvalidInput,
        NotFound,
        NotAuthorized,
        PathNotAllowed,
        UnsupportedSource,
        EncryptedDocument,
        OcrRequired,
        ReferenceSectionNotFound,
        LimitExceeded,
        PrerequisiteNotMet,
        InvalidState,
        RevisionConflict,
        RevisionNotCurrent,
        IdempotencyConflict,
        NetworkModeDenied,
        ConfigMissingEmail,
        FeatureNotAvailable,
        AdapterUnavailable,
        IntegrationUnavailable,
        ProviderUnavailable,
        RateLimited,
        BudgetExceeded,
        FileValidationFailed,
        Cancelled,
    )
}
