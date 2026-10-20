"""Eligibility, classification and version policy tests against frozen value shapes."""

import pytest

from refcollector.access import classify, eligibility, versions
from refcollector.domain.enums import CopyVersion, LocationBasis, VersionPolicy
from tests.unit.access.test_discovery import NOW, location


@pytest.mark.parametrize(
    "state,eligible,evidence,want",
    [
        ("failed", False, [], "unknown"),
        ("partial", False, [], "unknown"),
        ("complete", False, [], "none_found"),
        ("not_checked", False, [], "unknown"),
        ("failed", False, ["Publisher explicitly denies access"], "restricted"),
        ("partial", False, ["Publisher explicitly denies access"], "restricted"),
        ("complete", False, ["Publisher explicitly denies access"], "restricted"),
        ("partial", True, ["Publisher explicitly denies access"], "eligible_found"),
        ("complete", True, [], "eligible_found"),
    ],
)
def test_r05_classification_precedence(state, eligible, evidence, want):
    assert classify.classify_access(state, [location(eligible=eligible)], evidence) == want


def test_r09_none_found_wording_has_sources_and_time():
    assert classify.none_found_message(["unpaywall", "openalex"], NOW) == (
        "No eligible copy was found in the configured sources (unpaywall, openalex) "
        "at 2026-10-20T12:00:00+00:00."
    )


@pytest.mark.parametrize(
    "basis,host,want",
    [
        (LocationBasis.OA_PROVIDER, "publisher", True),
        (LocationBasis.REPOSITORY, "repository", True),
        (LocationBasis.REPOSITORY, "publisher", False),
        (LocationBasis.USER_APPROVED, None, False),
    ],
)
def test_r06_basis_requires_work_specific_evidence(basis, host, want):
    loc = location(basis=basis, host_type=host)
    assert (
        eligibility.is_eligible(
            loc, work_id="wrk_a", enabled_providers=["unpaywall"], consulted_provider="unpaywall"
        )
        is want
    )


@pytest.mark.parametrize(
    "provider,consulted", [("unknown", "unknown"), ("openalex", "unpaywall"), ("unpaywall", None)]
)
def test_r06_reachable_licensed_or_preflagged_copy_is_not_evidence(provider, consulted):
    loc = location(provider, licence="cc-by", eligible=True, host_type="repository")
    assert not eligibility.is_eligible(
        loc, work_id="wrk_a", enabled_providers=["unpaywall"], consulted_provider=consulted
    )


@pytest.mark.parametrize("approved_work,want", [("wrk_a", True), ("wrk_b", False), (None, False)])
def test_r06_user_approval_is_work_scoped(approved_work, want):
    loc = location("user", basis=LocationBasis.USER_APPROVED)
    assert (
        eligibility.is_eligible(loc, work_id="wrk_a", enabled_providers=[], approved_work_id=approved_work)
        is want
    )


@pytest.mark.parametrize(
    "policy,cited,want",
    [
        (VersionPolicy.PREFER_PUBLISHED, CopyVersion.PUBLISHED, ["pub"]),
        (VersionPolicy.ANY_ELIGIBLE, CopyVersion.PUBLISHED, ["pre", "acc", "pub", "unknown"]),
        (VersionPolicy.CITED_VERSION_ONLY, CopyVersion.ACCEPTED, ["acc"]),
        (VersionPolicy.CITED_VERSION_ONLY, CopyVersion.UNKNOWN, []),
    ],
)
def test_r08_version_policies(policy, cited, want):
    locs = [
        location(location_id=lid, version=ver)
        for lid, ver in [
            ("pre", CopyVersion.SUBMITTED),
            ("acc", CopyVersion.ACCEPTED),
            ("pub", CopyVersion.PUBLISHED),
            ("unknown", CopyVersion.UNKNOWN),
        ]
    ]
    locs.append(location(location_id="ineligible", eligible=False))
    result = versions.select_versions(locs, policy, cited_version=cited)
    assert [loc.location_id for loc in result.locations] == want
    assert all(loc.eligible for loc in result.locations)


@pytest.mark.parametrize(
    "available,want",
    [
        ([CopyVersion.ACCEPTED, CopyVersion.SUBMITTED], [CopyVersion.ACCEPTED]),
        ([CopyVersion.SUBMITTED, CopyVersion.UNKNOWN], []),
        ([], []),
    ],
)
def test_r08_prefer_published_falls_back_only_to_accepted(available, want):
    result = versions.select_versions(
        [location(version=v) for v in available], VersionPolicy.PREFER_PUBLISHED
    )
    assert [loc.version for loc in result.locations] == want


@pytest.mark.parametrize(
    "actual,cited,want",
    [
        (CopyVersion.ACCEPTED, CopyVersion.PUBLISHED, ["VERSION_DIFFERENCE"]),
        (CopyVersion.SUBMITTED, CopyVersion.ACCEPTED, ["VERSION_DIFFERENCE"]),
        (CopyVersion.PUBLISHED, CopyVersion.PUBLISHED, []),
        (CopyVersion.UNKNOWN, CopyVersion.PUBLISHED, []),
        (CopyVersion.ACCEPTED, CopyVersion.UNKNOWN, []),
    ],
)
def test_r08_version_difference_requires_known_different_versions(actual, cited, want):
    result = versions.select_versions(
        [location(version=actual)], VersionPolicy.ANY_ELIGIBLE, cited_version=cited
    )
    assert result.reasons == want


@pytest.mark.parametrize("policy", [VersionPolicy.CITED_VERSION_ONLY, VersionPolicy.PREFER_PUBLISHED])
def test_r08_excluded_version_difference_is_still_explained(policy):
    locs = [
        location(location_id="accepted", version=CopyVersion.ACCEPTED),
        location(location_id="published", version=CopyVersion.PUBLISHED),
    ]
    result = versions.select_versions(locs, policy, cited_version=CopyVersion.PUBLISHED)
    assert [loc.location_id for loc in result.locations] == ["published"]
    assert result.reasons == ["VERSION_DIFFERENCE"]


def test_r08_cited_version_mismatch_explains_empty_selection():
    result = versions.select_versions(
        [location(version=CopyVersion.ACCEPTED)],
        VersionPolicy.CITED_VERSION_ONLY,
        cited_version=CopyVersion.PUBLISHED,
    )
    assert result.locations == []
    assert result.reasons == ["VERSION_DIFFERENCE"]


def test_r08_selection_does_not_mutate_location_evidence():
    loc = location()
    result = versions.select_versions([loc], VersionPolicy.ANY_ELIGIBLE)
    result.locations[0].eligible = False
    assert loc.eligible
