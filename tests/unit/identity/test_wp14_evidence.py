"""Offline regressions for recorded WP14 decision evidence."""

import json
from pathlib import Path

import pytest

from refcollector.domain.enums import IdentityState as State
from refcollector.domain.enums import ReasonCode as R
from refcollector.domain.models import WorkMetadata
from refcollector.identity import DefaultIdentityResolver

from .test_resolver import CITATION, DOI, Provider, metadata, record


@pytest.mark.anyio
async def test_recorded_fleming_abbreviated_pages_are_agreement():
    data = json.loads((Path(__file__).parents[2] / "fixtures/wp14_identity_fleming.json").read_text())
    rows = [record(key=str(i), md=WorkMetadata.model_validate(md)) for i, md in enumerate(data["metadata"])]
    result = await DefaultIdentityResolver([Provider(results=rows)]).resolve(data["citation"])
    assert result.state == State.ACCEPTED_AUTO
    assert result.candidates[0].features["pages_agreement"] is True
    assert result.reasons == []
    assert R.PROVIDER_DISAGREEMENT in result.candidates[1].reasons


@pytest.mark.parametrize(
    ("cited", "recorded", "expected"),
    [
        ("108-25", "108-125", True),
        ("199-203", "199-203", True),
        ("199-03", "199-203", True),
        ("108-25", "108-225", False),
        ("108-25", "109-125", False),
        ("45-67", "45-68", False),
    ],
)
def test_page_range_equivalence_preserves_real_conflicts(cited, recorded, expected):
    candidate = DefaultIdentityResolver([]).evaluate(
        CITATION.replace("45-67", cited), metadata(pages=recorded)
    )
    assert candidate.features["pages_agreement"] is expected


@pytest.mark.anyio
async def test_low_ranked_conflicts_remain_on_candidate_only():
    wrong = WorkMetadata(title="Unrelated biology", year=1980, doi="10.1234/other")
    result = await DefaultIdentityResolver(
        [Provider(results=[record(md=metadata(doi=DOI)), record(key="weak", md=wrong)])]
    ).resolve(CITATION)
    assert result.state == State.ACCEPTED_AUTO
    assert result.reasons == []
    assert R.YEAR_CONFLICT in result.candidates[1].reasons
    assert R.INSUFFICIENT_FIELDS in result.candidates[1].reasons


@pytest.mark.anyio
async def test_close_strong_distinct_competitor_still_blocks():
    rows = [record(key=str(i), md=metadata(doi=f"10.1234/{i}")) for i in range(2)]
    result = await DefaultIdentityResolver([Provider(results=rows)]).resolve(CITATION)
    assert result.state == State.NEEDS_REVIEW
    assert R.MULTIPLE_CLOSE_CANDIDATES in result.reasons


@pytest.mark.anyio
async def test_same_work_contradictory_pages_still_block():
    result = await DefaultIdentityResolver(
        [
            Provider(results=[record(md=metadata(doi=DOI, pages="45-67"))]),
            Provider("openalex", results=[record("openalex", md=metadata(doi=DOI, pages="45-68"))]),
        ]
    ).resolve(CITATION)
    assert result.state == State.NEEDS_REVIEW
    assert R.PROVIDER_DISAGREEMENT in result.reasons
    assert result.candidates[0].features["provider_conflict_pages"] is True
    assert len(result.candidates[0].evidence) == 2
