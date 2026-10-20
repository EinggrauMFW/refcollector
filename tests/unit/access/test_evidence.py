"""Restriction evidence and trusted internal approvals, using offline providers."""

import asyncio

import pytest

from refcollector.access import DefaultAccessDiscoverer
from refcollector.contracts import ports
from refcollector.domain.enums import LocationBasis, VersionPolicy
from refcollector.errors import Cancelled, InvalidInput, ProviderUnavailable
from tests.unit.access.test_discovery import DOI, NOW, Provider, location, work

pytestmark = pytest.mark.anyio


class DetailedProvider(Provider):
    async def locations_detailed(self, doi):
        self.calls.append(doi)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def locations(self, doi):
        raise AssertionError("Discovery must use the detailed response without a second lookup")


def result(locations=(), evidence=()):
    return ports.OALocationResult(locations=list(locations), restriction_evidence=list(evidence))


def approval(project_id="prj_a", work_id="wrk_a", loc=None):
    return ports.ApprovedLocation(project_id=project_id, work_id=work_id, location=loc or location("user"))


async def check(providers=(), *, approvals=(), item=None, **kwargs):
    return await DefaultAccessDiscoverer(providers, **kwargs).discover(
        "prj_a",
        item or work(),
        version_policy=VersionPolicy.PREFER_PUBLISHED,
        now=NOW,
        approved_locations=approvals,
    )


async def test_detailed_restriction_is_preserved_with_source_provenance():
    provider = DetailedProvider(
        "unpaywall", result(evidence=["Publisher explicitly requires a subscription"])
    )
    found = await check([provider])
    assert found.discovery == "complete" and found.classification == "restricted"
    assert found.sources_consulted == ["unpaywall"]
    assert found.sources_failed == [] and provider.calls == [DOI]
    assert found.restriction_evidence == ["unpaywall: Publisher explicitly requires a subscription"]


async def test_multiple_detailed_sources_keep_evidence_attribution():
    found = await check(
        [DetailedProvider(name, result(evidence=["Explicit denial"])) for name in ("unpaywall", "openalex")]
    )
    assert found.restriction_evidence == ["unpaywall: Explicit denial", "openalex: Explicit denial"]
    assert found.classification == "restricted"


@pytest.mark.parametrize("has_copy", [False, True])
async def test_legacy_locations_and_detailed_restriction_precedence(has_copy):
    legacy = Provider("openalex", [location("openalex")] if has_copy else [])
    detailed = DetailedProvider("unpaywall", result(evidence=["Explicit denial"]))
    found = await check([detailed, legacy])
    assert found.classification == ("eligible_found" if has_copy else "restricted")
    assert found.discovery == "complete" and found.sources_consulted == ["unpaywall", "openalex"]
    assert found.restriction_evidence == ["unpaywall: Explicit denial"]


async def test_restriction_survives_another_source_outage():
    found = await check(
        [
            DetailedProvider("unpaywall", result(evidence=["Explicit denial"])),
            Provider("openalex", ProviderUnavailable("offline")),
        ]
    )
    assert found.discovery == "partial" and found.classification == "restricted"
    assert [(f.source, f.error) for f in found.sources_failed] == [("openalex", "offline")]
    assert found.restriction_evidence == ["unpaywall: Explicit denial"]


async def test_whitespace_evidence_does_not_create_restriction():
    found = await check([DetailedProvider("unpaywall", result(evidence=["", "  "]))])
    assert found.classification == "none_found" and found.restriction_evidence == []


async def test_disabled_detailed_provider_cannot_supply_restrictions():
    provider = DetailedProvider("unpaywall", result(evidence=["Explicit denial"]))
    found = await check([provider], enabled_providers=[])
    assert found.classification == "unknown" and found.restriction_evidence == []
    assert found.sources_consulted == [] and provider.calls == []


@pytest.mark.parametrize("error", [ProviderUnavailable("down"), TimeoutError("timeout")])
async def test_detailed_failure_is_not_restriction_and_does_not_erase_legacy_copy(error):
    found = await check([DetailedProvider("unpaywall", error), Provider("openalex", [location("openalex")])])
    assert found.discovery == "partial" and found.classification == "eligible_found"
    assert found.restriction_evidence == []
    assert [(f.source, f.error) for f in found.sources_failed] == [("unpaywall", str(error))]


@pytest.mark.parametrize("error", [asyncio.CancelledError(), Cancelled("stop"), KeyboardInterrupt()])
async def test_detailed_cancellation_propagates_even_with_approved_copy(error):
    with pytest.raises(type(error)):
        await check([DetailedProvider("unpaywall", error)], approvals=[approval()])


@pytest.mark.parametrize("without_doi", [False, True])
async def test_approval_with_no_providers_or_no_doi_still_establishes_eligibility(without_doi):
    loc = location("internal-user-record", eligible=False, licence=None)
    item = work(overrides={"doi": None}) if without_doi else work()
    providers = [Provider("unpaywall")] if without_doi else []
    found = await check(providers, approvals=[approval(loc=loc)], item=item)
    assert found.discovery == "failed" and found.classification == "eligible_found"
    assert found.sources_consulted == [] and found.sources_failed == []
    assert len(found.locations) == 1
    approved = found.locations[0]
    assert approved.eligible and approved.basis == LocationBasis.USER_APPROVED
    assert approved.provider == "internal-user-record"
    assert approved.location_id == "loc_1" and approved.url == loc.url
    assert approved.licence is None and approved.version == loc.version


async def test_approved_copy_outranks_restriction_and_outage():
    found = await check(
        [
            DetailedProvider("unpaywall", result(evidence=["Explicit denial"])),
            Provider("openalex", ProviderUnavailable("offline")),
        ],
        approvals=[approval()],
    )
    assert found.discovery == "partial" and found.classification == "eligible_found"
    assert found.restriction_evidence == ["unpaywall: Explicit denial"]
    assert found.sources_failed[0].source == "openalex"


async def test_approved_copy_outranks_all_source_outages():
    found = await check(
        [Provider(name, ProviderUnavailable("offline")) for name in ("unpaywall", "openalex")],
        approvals=[approval()],
    )
    assert found.discovery == "failed" and found.classification == "eligible_found"
    assert len(found.sources_failed) == 2 and len(found.locations) == 1


@pytest.mark.parametrize("project_id,work_id", [("prj_b", "wrk_a"), ("prj_a", "wrk_b")])
async def test_approval_scope_mismatch_rejected_before_lookup(project_id, work_id):
    provider = Provider("unpaywall")
    with pytest.raises(InvalidInput):
        await check([provider], approvals=[approval(project_id, work_id)])
    assert provider.calls == []


async def test_raw_location_id_is_not_approval_evidence():
    with pytest.raises(InvalidInput):
        await check(approvals=[location(basis=LocationBasis.USER_APPROVED)])


async def test_provider_cannot_smuggle_approval_by_matching_location_id():
    unapproved = location("unpaywall", basis=LocationBasis.USER_APPROVED, url="https://other.example/copy")
    approved = location("user", url="https://approved.example/copy")
    found = await check(
        [DetailedProvider("unpaywall", result([unapproved]))], approvals=[approval(loc=approved)]
    )
    assert found.classification == "eligible_found"
    assert [(loc.url, loc.eligible) for loc in found.locations] == [
        ("https://approved.example/copy", True),
        ("https://other.example/copy", False),
    ]


async def test_detailed_inputs_approvals_and_work_are_not_mutated_or_aliased():
    loc = location("unpaywall", eligible=False)
    response = result([loc], ["Explicit denial"])
    approved = approval(loc=location("user", eligible=False))
    item = work()
    before_work = item.model_dump()
    before_response = loc.model_dump()
    before_approval = approved.location.model_dump()
    found = await check([DetailedProvider("unpaywall", response)], approvals=[approved], item=item)
    assert item.model_dump() == before_work
    assert loc.model_dump() == before_response and approved.location.model_dump() == before_approval
    found.locations[0].url = "https://changed.example/approved"
    found.locations[1].eligible = False
    found.restriction_evidence.append("Changed")
    assert approved.location.model_dump() == before_approval
    assert loc.model_dump() == before_response and response.restriction_evidence == ["Explicit denial"]


async def test_repeated_approval_only_collapses_exact_location_evidence():
    approved = approval()
    distinct = approval(loc=location("user", location_id="loc_other"))
    found = await check(approvals=[approved, approved, distinct])
    assert [loc.location_id for loc in found.locations] == ["loc_1", "loc_other"]
