"""WP-07: eligibility policy v1. Spec 10.1, D-11."""

from collections.abc import Collection

from refcollector.domain.enums import LocationBasis
from refcollector.domain.models import AccessLocation

P0_OA_PROVIDERS = frozenset({"unpaywall", "openalex"})


def is_eligible(
    location: AccessLocation,
    *,
    work_id: str,
    enabled_providers: Collection[str],
    consulted_provider: str | None = None,
    approved_work_id: str | None = None,
) -> bool:
    """Require a work-specific lookup or trusted approval for the current work.

    `consulted_provider` is set by the code that performed this work's lookup,
    never by the location itself. `approved_work_id` must come from a verified
    approval in the caller's project, never from provider data. The frozen
    OAProvider cannot supply user approvals. URL reachability, licence and the
    incoming eligible flag do not establish eligibility.
    """
    if location.basis == LocationBasis.USER_APPROVED:
        return bool(work_id) and approved_work_id == work_id
    if not (
        location.provider == consulted_provider
        and consulted_provider in P0_OA_PROVIDERS
        and consulted_provider in enabled_providers
    ):
        return False
    if location.basis == LocationBasis.REPOSITORY:
        return location.host_type == "repository"
    return location.basis == LocationBasis.OA_PROVIDER
