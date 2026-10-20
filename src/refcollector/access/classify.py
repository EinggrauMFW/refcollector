"""WP-07: discovery and classification rules. Spec 10.1."""

from collections.abc import Sequence
from datetime import datetime

from refcollector.domain.enums import AccessClassification, DiscoveryState
from refcollector.domain.models import AccessLocation


def classify_access(
    discovery: DiscoveryState,
    locations: Sequence[AccessLocation],
    restriction_evidence: Sequence[str] = (),
) -> AccessClassification:
    """Apply evidence precedence to locations whose eligibility has been checked.

    Restriction evidence must be an explicit statement supplied by the caller.
    Failed lookups, missing locations and unknown licences are not evidence.
    """
    if any(location.eligible for location in locations):
        return AccessClassification.ELIGIBLE_FOUND
    if any(evidence.strip() for evidence in restriction_evidence):
        return AccessClassification.RESTRICTED
    if discovery == DiscoveryState.COMPLETE:
        return AccessClassification.NONE_FOUND
    return AccessClassification.UNKNOWN


def none_found_message(sources: Sequence[str], checked_at: datetime) -> str:
    """Use only for a complete check with classification none_found (10.1)."""
    return (
        f"No eligible copy was found in the configured sources ({', '.join(sources)}) "
        f"at {checked_at.isoformat()}."
    )
