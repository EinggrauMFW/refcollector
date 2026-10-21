"""Offline version evidence from sanitized WP14 responses and documented API values."""

import json
from pathlib import Path

import pytest

from refcollector.domain.enums import CopyVersion
from refcollector.providers.openalex import OpenAlexProvider
from refcollector.providers.unpaywall import UnpaywallProvider
from tests.unit.providers.conftest import EMAIL

pytestmark = pytest.mark.anyio
DOI = "10.1063/1.4896850"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def adapter(name, client):
    if name == "openalex":
        return OpenAlexProvider(client), f"https://api.openalex.org/works/doi:{DOI}"
    return UnpaywallProvider(client, EMAIL), f"https://api.unpaywall.org/v2/{DOI}"


@pytest.mark.parametrize("name", ["openalex", "unpaywall"])
async def test_saved_wp14_submitted_evidence_is_not_promoted(name, client, respx_mock):
    payload = json.loads((FIXTURES / f"wp14_versions_{name}.json").read_text(encoding="utf-8"))
    provider, url = adapter(name, client)
    respx_mock.get(url).respond(200, json=payload)
    locations = await provider.locations(DOI)
    assert len(locations) == 1
    assert locations[0].version == CopyVersion.SUBMITTED
    assert locations[0].is_pdf


@pytest.mark.parametrize("name", ["openalex", "unpaywall"])
@pytest.mark.parametrize(
    "version,expected",
    [
        ("publishedVersion", CopyVersion.PUBLISHED),
        ("acceptedVersion", CopyVersion.ACCEPTED),
        ("submittedVersion", CopyVersion.SUBMITTED),
        ("futureVersion", CopyVersion.UNKNOWN),
        (None, CopyVersion.UNKNOWN),
    ],
)
@pytest.mark.parametrize("pdf", [True, False])
async def test_version_mapping_for_pdf_and_landing_page(name, version, expected, pdf, client, respx_mock):
    location = {"version": version}
    if name == "openalex":
        location.update(
            is_oa=True,
            pdf_url="https://repository.example/copy.pdf" if pdf else None,
            landing_page_url="https://repository.example/landing",
        )
        payload = {"locations": [location]}
    else:
        location.update(
            url_for_pdf="https://repository.example/copy.pdf" if pdf else None,
            url_for_landing_page="https://repository.example/landing",
        )
        payload = {"oa_locations": [location]}
    provider, url = adapter(name, client)
    respx_mock.get(url).respond(200, json=payload)
    locations = await provider.locations(DOI)
    assert len(locations) == 1
    assert locations[0].version == expected
    assert locations[0].is_pdf is pdf


async def test_openalex_best_location_fallback_keeps_version(client, respx_mock):
    provider, url = adapter("openalex", client)
    respx_mock.get(url).respond(
        200,
        json={
            "locations": [],
            "best_oa_location": {
                "is_oa": True,
                "landing_page_url": "https://repository.example/landing",
                "version": "acceptedVersion",
            },
        },
    )
    locations = await provider.locations(DOI)
    assert len(locations) == 1
    assert locations[0].version == CopyVersion.ACCEPTED
