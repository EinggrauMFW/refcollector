"""WP-07: version policy and VERSION_DIFFERENCE. Spec 10.2."""

from collections.abc import Sequence
from dataclasses import dataclass

from refcollector.domain.enums import CopyVersion, ReasonCode, VersionPolicy
from refcollector.domain.models import AccessLocation


@dataclass(frozen=True)
class VersionSelection:
    """Retrieval choices and warnings, separate from access classification."""

    locations: list[AccessLocation]
    reasons: list[ReasonCode]


def select_versions(
    locations: Sequence[AccessLocation],
    policy: VersionPolicy,
    *,
    cited_version: CopyVersion = CopyVersion.UNKNOWN,
) -> VersionSelection:
    """Select eligible copies without changing the check or provider locations.

    Unknown versions cannot prove either a match or a difference. Warnings
    cover all eligible copies, including those excluded by the policy, so
    an empty selection can still explain a version difference. P0 metadata
    has no cited-version field; the caller supplies it when known.
    """
    eligible = [location for location in locations if location.eligible]
    if policy == VersionPolicy.PREFER_PUBLISHED:
        published = [loc for loc in eligible if loc.version == CopyVersion.PUBLISHED]
        selected = published or [loc for loc in eligible if loc.version == CopyVersion.ACCEPTED]
    elif policy == VersionPolicy.CITED_VERSION_ONLY:
        selected = [
            loc for loc in eligible if cited_version != CopyVersion.UNKNOWN and loc.version == cited_version
        ]
    elif policy == VersionPolicy.ANY_ELIGIBLE:
        selected = eligible
    else:
        raise ValueError(f"Unknown version policy: {policy}")
    difference = cited_version != CopyVersion.UNKNOWN and any(
        loc.version not in (CopyVersion.UNKNOWN, cited_version) for loc in eligible
    )
    return VersionSelection(
        locations=[loc.model_copy(deep=True) for loc in selected],
        reasons=[ReasonCode.VERSION_DIFFERENCE] if difference else [],
    )
