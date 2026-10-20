"""WP-07 requirements 1-11; AT-08b/35/36/37 and W-20/21, all offline."""

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

import refcollector.access as access
from refcollector.domain.enums import CopyVersion, LocationBasis, VersionPolicy
from refcollector.domain.models import AccessLocation, Project, ProjectWork, WorkMetadata
from refcollector.errors import Cancelled, InvalidInput, NetworkModeDenied, ProviderUnavailable
from refcollector.storage.repos import SqliteStore

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
DOI = "10.1000/accepted"
pytestmark = pytest.mark.anyio


class Provider:
    def __init__(self, name, result=()):
        self.name = name
        self.result = result
        self.calls = []

    async def locations(self, doi):
        self.calls.append(doi)
        if isinstance(self.result, BaseException):
            raise self.result
        return list(self.result)


def work(**kwargs):
    return ProjectWork(
        work_id="wrk_a",
        project_id="prj_a",
        metadata=WorkMetadata(doi=DOI),
        created_at=NOW,
        updated_at=NOW,
        **kwargs,
    )


def location(provider="unpaywall", **kwargs):
    data = dict(
        location_id="loc_1",
        url="https://repo.example/paper",
        provider=provider,
        version=CopyVersion.ACCEPTED,
        basis=LocationBasis.OA_PROVIDER,
        eligible=True,
    )
    data.update(kwargs)
    return AccessLocation(**data)


async def discover(providers, *, item=None, policy=VersionPolicy.PREFER_PUBLISHED, **kwargs):
    return await access.DefaultAccessDiscoverer(providers, **kwargs).discover(
        "prj_a",
        item or work(),
        version_policy=policy,
        now=NOW,
    )


@pytest.mark.parametrize("left,right", list(product(["empty", "found", "failed"], repeat=2)))
async def test_r01_r05_all_source_outcomes_at08b_at35_at36_w20_w21(left, right):
    providers = []
    for name, outcome in zip(("unpaywall", "openalex"), (left, right), strict=True):
        result = {"empty": [], "found": [location(name)], "failed": ProviderUnavailable("source offline")}[
            outcome
        ]
        providers.append(Provider(name, result))
    check = await discover(providers)
    failures = [p.name for p, outcome in zip(providers, (left, right), strict=True) if outcome == "failed"]
    expected_discovery = {0: "complete", 1: "partial", 2: "failed"}[len(failures)]
    expected_class = (
        "eligible_found" if "found" in (left, right) else ("unknown" if failures else "none_found")
    )
    assert check.discovery == expected_discovery
    assert check.classification == expected_class
    assert check.sources_consulted == ["unpaywall", "openalex"]
    assert [(f.source, f.error) for f in check.sources_failed] == [(f, "source offline") for f in failures]
    assert check.checked_at == NOW
    assert check.project_id == "prj_a" and check.work_id == "wrk_a"
    assert check.eligibility_policy_version == "eligibility-policy-v1"
    assert [p.calls for p in providers] == [[DOI], [DOI]]
    assert {loc.provider for loc in check.locations} == {
        p.name for p, outcome in zip(providers, (left, right), strict=True) if outcome == "found"
    }


async def test_r02_effective_doi_override_not_stale_link_key():
    provider = Provider("unpaywall")
    item = work(overrides={"doi": "10.1000/override"}, link_keys={"doi": "10.1000/stale"})
    before = item.model_dump()
    await discover([provider], item=item)
    assert provider.calls == ["10.1000/override"]
    assert item.model_dump() == before


@pytest.mark.parametrize("doi", [None, "", "   "])
async def test_r02_r03_no_supported_identifier_is_not_empty_success(doi):
    provider = Provider("openalex", [location("openalex")])
    item = work(overrides={"doi": doi})
    item.metadata.openalex_id = "W123"
    item.metadata.isbn = "9780000000000"
    check = await discover([provider], item=item)
    assert check.discovery == "failed" and check.classification == "unknown"
    assert check.sources_consulted == [] and check.sources_failed == []
    assert check.locations == [] and provider.calls == []


async def test_r03_no_configured_source_is_not_none_found():
    check = await discover([])
    assert check.discovery == "failed" and check.classification == "unknown"
    assert check.sources_consulted == [] and check.sources_failed == []


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailable("down"),
        TimeoutError("timeout"),
        ValueError("bad response"),
        NetworkModeDenied("cache miss"),
    ],
)
async def test_r04_failures_preserve_other_source(error):
    check = await discover([Provider("unpaywall", error), Provider("openalex", [location("openalex")])])
    assert check.discovery == "partial" and check.classification == "eligible_found"
    assert check.sources_failed[0].source == "unpaywall"
    assert check.sources_failed[0].error == str(error)
    assert check.restriction_evidence == []


@pytest.mark.parametrize("error", [asyncio.CancelledError(), Cancelled("stop"), KeyboardInterrupt()])
async def test_r04_cancellation_and_process_control_propagate(error):
    with pytest.raises(type(error)):
        await discover([Provider("unpaywall", error)])


async def test_r06_disabled_and_non_p0_sources_not_queried():
    disabled = Provider("unpaywall", [location()])
    arbitrary = Provider("reachable-url", [location("reachable-url")])
    enabled = Provider("openalex")
    check = await discover([disabled, arbitrary, enabled], enabled_providers=["openalex", "reachable-url"])
    assert check.sources_consulted == ["openalex"]
    assert check.classification == "none_found" and check.locations == []
    assert disabled.calls == arbitrary.calls == []


async def test_r06_unrelated_source_cannot_claim_eligibility_or_approval():
    forged = location("openalex", basis=LocationBasis.USER_APPROVED)
    check = await discover([Provider("unpaywall", [forged])], enabled_providers=["unpaywall"])
    assert not check.locations[0].eligible
    assert check.classification == "none_found"
    assert check.locations[0].provider == "openalex"


async def test_r06_enabled_work_specific_oa_record_recomputes_eligibility():
    check = await discover([Provider("unpaywall", [location(eligible=False)])])
    assert check.classification == "eligible_found" and check.locations[0].eligible


async def test_r07_deduplicate_exact_records_without_losing_identity_or_evidence():
    loc = location(licence=None)
    variants = [
        loc,
        loc.model_copy(deep=True),
        loc.model_copy(update={"location_id": "loc_2"}),
        loc.model_copy(update={"version": CopyVersion.PUBLISHED}),
        loc.model_copy(update={"licence": "cc-by"}),
    ]
    other = location("openalex", location_id="loc_other")
    check = await discover([Provider("unpaywall", variants), Provider("openalex", [other])])
    assert len(check.locations) == 5
    assert check.locations[0].licence is None
    assert check.locations[0].version == CopyVersion.ACCEPTED
    assert [loc.location_id for loc in check.locations] == ["loc_1", "loc_2", "loc_1", "loc_1", "loc_other"]
    check.locations[0].url = "https://changed.example/"
    assert loc.url == "https://repo.example/paper"


@pytest.mark.parametrize("policy", list(VersionPolicy))
async def test_r08_version_policy_does_not_remove_discovery_evidence(policy):
    check = await discover([Provider("unpaywall", [location(version=CopyVersion.SUBMITTED)])], policy=policy)
    assert check.classification == "eligible_found"
    assert check.locations[0].version == CopyVersion.SUBMITTED


async def test_r10_cross_project_rejected_before_provider_call():
    provider = Provider("unpaywall")
    with pytest.raises(InvalidInput):
        await access.DefaultAccessDiscoverer([provider]).discover(
            "prj_other", work(), version_policy=VersionPolicy.ANY_ELIGIBLE, now=NOW
        )
    assert provider.calls == []


async def test_r10_at37_unsaved_checks_current_history_and_project_isolation(db, settings):
    store = SqliteStore(db, settings)
    for pid in ("prj_a", "prj_b"):
        store.projects.add(Project(project_id=pid, name=pid, created_at=NOW, updated_at=NOW))
    item = store.works.add(work())
    other = store.works.add(work().model_copy(update={"project_id": "prj_b", "work_id": "wrk_b"}))
    before = item.model_dump()
    discoverer = access.DefaultAccessDiscoverer([Provider("unpaywall")])
    old = await discoverer.discover("prj_a", item, version_policy=VersionPolicy.ANY_ELIGIBLE, now=NOW)
    assert store.access_checks.current("prj_a", "wrk_a") is None
    store.access_checks.add_current(old)
    new = await discoverer.discover(
        "prj_a", item, version_policy=VersionPolicy.ANY_ELIGIBLE, now=NOW + timedelta(days=1)
    )
    assert new.check_id != old.check_id and new.check_id.startswith("acc_")
    assert store.access_checks.current("prj_a", "wrk_a").check_id == old.check_id
    store.access_checks.add_current(new)
    assert store.access_checks.current("prj_a", "wrk_a").check_id == new.check_id
    assert [(c.check_id, c.current) for c in store.access_checks.history("prj_a", "wrk_a")] == [
        (new.check_id, True),
        (old.check_id, False),
    ]
    assert store.access_checks.history("prj_b", "wrk_a") == []
    assert store.access_checks.current("prj_b", "wrk_b") is None
    assert store.works.get("prj_b", "wrk_b") == other
    assert item.model_dump() == before and store.works.get("prj_a", "wrk_a") == item


async def test_r01_duplicate_provider_names_rejected():
    with pytest.raises(InvalidInput):
        access.DefaultAccessDiscoverer([Provider("unpaywall"), Provider("unpaywall")])
