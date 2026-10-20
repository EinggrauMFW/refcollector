"""Additive detailed OA adapter results, with no invented access restrictions."""

import pytest

from refcollector.access import DefaultAccessDiscoverer
from refcollector.domain.enums import VersionPolicy
from refcollector.errors import ProviderUnavailable
from refcollector.providers.openalex import OpenAlexProvider
from refcollector.providers.unpaywall import UnpaywallProvider
from tests.unit.access.test_discovery import NOW, work
from tests.unit.providers.conftest import EMAIL, fixture_json

pytestmark = pytest.mark.anyio
DOI = "10.1000/abc.123"


def adapter(name, client):
    if name == "unpaywall":
        return UnpaywallProvider(client, EMAIL), f"https://api.unpaywall.org/v2/{DOI}"
    return OpenAlexProvider(client), f"https://api.openalex.org/works/doi:{DOI}"


@pytest.mark.parametrize("name,fixture", [("unpaywall", "doi.json"), ("openalex", "work.json")])
async def test_detailed_method_preserves_legacy_locations(name, fixture, client, respx_mock):
    provider, url = adapter(name, client)
    respx_mock.get(url).respond(200, json=fixture_json(name, fixture))
    legacy = await provider.locations(DOI)
    detailed = await provider.locations_detailed(DOI)
    assert legacy and detailed.locations
    assert [loc.model_dump(exclude={"location_id"}) for loc in detailed.locations] == [
        loc.model_dump(exclude={"location_id"}) for loc in legacy
    ]
    assert detailed.restriction_evidence == []


@pytest.mark.parametrize("name", ["unpaywall", "openalex"])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "is_oa": False,
            "oa_status": "closed",
            "oa_locations": [],
            "open_access": {"is_oa": False, "oa_status": "closed"},
            "locations": [],
        },
        {
            "oa_locations": [{"url": "https://publisher.example/landing", "license": None}],
            "locations": [
                {"landing_page_url": "https://publisher.example/landing", "is_oa": False, "license": "cc-by"}
            ],
        },
        {"restriction_evidence": ["Unrecognised field must not be trusted"]},
    ],
)
async def test_missing_oa_status_licence_or_unknown_field_is_not_restriction(
    name, payload, client, respx_mock
):
    provider, url = adapter(name, client)
    respx_mock.get(url).respond(200, json=payload)
    detailed = await provider.locations_detailed(DOI)
    assert detailed.restriction_evidence == []


@pytest.mark.parametrize("name", ["unpaywall", "openalex"])
async def test_detailed_404_is_empty_answer_not_restriction(name, client, respx_mock):
    provider, url = adapter(name, client)
    respx_mock.get(url).respond(404)
    detailed = await provider.locations_detailed(DOI)
    assert detailed.locations == [] and detailed.restriction_evidence == []


@pytest.mark.parametrize("name", ["unpaywall", "openalex"])
async def test_detailed_http_error_remains_provider_failure(name, client, respx_mock):
    provider, url = adapter(name, client)
    respx_mock.get(url).respond(403)
    with pytest.raises(ProviderUnavailable):
        await provider.locations_detailed(DOI)


@pytest.mark.parametrize("name,fixture", [("unpaywall", "doi.json"), ("openalex", "work.json")])
async def test_actual_detailed_adapter_discovery_keeps_eligible_evidence(name, fixture, client, respx_mock):
    provider, url = adapter(name, client)
    respx_mock.get(url).respond(200, json=fixture_json(name, fixture))
    item = work(overrides={"doi": DOI})
    found = await DefaultAccessDiscoverer([provider]).discover(
        "prj_a", item, version_policy=VersionPolicy.PREFER_PUBLISHED, now=NOW
    )
    assert found.discovery == "complete" and found.classification == "eligible_found"
    assert found.sources_consulted == [name] and found.sources_failed == []
    assert found.restriction_evidence == []
    assert all(loc.provider == name and loc.eligible for loc in found.locations)
