"""WP-06: plain-language sentences for reason codes. Spec 9.3."""

from refcollector.domain.enums import ReasonCode
from refcollector.domain.models import WorkMetadata

EXPLANATIONS = {
    ReasonCode.DOI_TITLE_CONFLICT: "The identifier-linked title differs from the cited title.",
    ReasonCode.AUTHOR_CONFLICT: "The cited authors do not agree with the record authors.",
    ReasonCode.YEAR_CONFLICT: "The cited year differs from the record year by more than one year.",
    ReasonCode.EDITION_CONFLICT: "The record edition differs from the cited edition or is not specified.",
    ReasonCode.VERSION_DIFFERENCE: "The record names a different version, chapter, or supplement.",
    ReasonCode.GENERIC_TITLE: "The title is too generic to identify a work without further evidence.",
    ReasonCode.MULTIPLE_CLOSE_CANDIDATES: "The two highest ranked candidates are too close to choose safely.",
    ReasonCode.INSUFFICIENT_FIELDS: "The citation and record do not contain enough matching fields.",
    ReasonCode.PROVIDER_DISAGREEMENT: "Provider records for the same identifier contain conflicting fields.",
    ReasonCode.PROVIDER_UNAVAILABLE: "An enabled provider did not answer; its absence is not agreement.",
    ReasonCode.POSSIBLE_MERGED_REFERENCE: "The citation may contain more than one reference.",
    ReasonCode.POSSIBLE_TRUNCATED_REFERENCE: "The citation may be incomplete.",
    ReasonCode.APPROVAL_STALE: "The reference changed after the recorded approval.",
}


def explain(reason: ReasonCode, *, citation: str | None = None, metadata: WorkMetadata | None = None) -> str:
    """Observed field values are in ScoredCandidate.features and provider evidence."""
    sentence = EXPLANATIONS[reason]
    if citation is not None:
        sentence += f" Citation: {citation}"
    if metadata is not None:
        sentence += f" Observed record: {metadata.model_dump_json(exclude_none=True)}"
    return sentence
