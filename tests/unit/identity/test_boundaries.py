"""Identifier, comparison and policy boundary regressions."""

import pytest

from refcollector.contracts.ports import ScoredCandidate
from refcollector.domain.enums import IdentityState as State
from refcollector.domain.enums import ReasonCode as R
from refcollector.domain.models import WorkMetadata
from refcollector.errors import Cancelled
from refcollector.identity import DefaultIdentityResolver
from refcollector.identity.identifiers import read_identifiers
from refcollector.identity.policy_v1 import close_candidates

from .test_resolver import CITATION, DOI, TITLE, Provider, metadata, record


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://doi.org/10.1234/Waves?utm_source=example#part", {"doi": DOI}),
        ("doi:10.1234/waves.\npart", {"doi": DOI + ".part"}),
        ("doi:10.1234/waves-\npart", {"doi": DOI + "-part"}),
        ("ISBN: 978-0-262-03384-8 2020", {"isbn": "9780262033848"}),
        ("ISBN: 0-306-40615-2 2020", {"isbn": "0306406152"}),
        ("arXiv: 2301.12345v2", {"arxiv": "2301.12345v2"}),
        ("https://doi.org/10.1234/waves\n-part", {"doi": DOI + "-part"}),
        ("https://doi.org/10.1234/\nwaves", {"doi": DOI}),
    ],
)
def test_r02_identifier_boundaries(text, expected):
    assert read_identifiers(text) == expected


@pytest.mark.parametrize(("margin", "expected"), [(0.049, True), (0.05, True), (0.051, False)])
def test_r05_r07_closeness_margin_boundary(margin, expected):
    # Both token-set ratios are 100. The combined score gap is 0.9 * base gap.
    best = ScoredCandidate(WorkMetadata(), {"token_set_ratio": 100.0}, [], [], False, 1.0)
    other = ScoredCandidate(WorkMetadata(), {"token_set_ratio": 100.0}, [], [], False, 1.0 - margin / 0.9)
    assert close_candidates(best, other) is expected


def test_r05_rapidfuzz_changes_only_closeness_gate():
    best = ScoredCandidate(WorkMetadata(), {"token_set_ratio": 100.0}, [], [], False, 1.0)
    other = ScoredCandidate(WorkMetadata(), {"token_set_ratio": 100.0}, [], [], False, 0.96)
    assert close_candidates(best, other)
    other.features["token_set_ratio"] = 0.0
    assert not close_candidates(best, other)
    assert other.score == 0.96


@pytest.mark.anyio
async def test_r03_cancellation_is_not_a_provider_outage():
    with pytest.raises(Cancelled):
        await DefaultIdentityResolver([Provider(failure=Cancelled())]).resolve(CITATION)


def test_r06_unrelated_year_digits_do_not_agree_with_volume_or_issue():
    md = metadata(volume="20", issue="20", pages="2020")
    candidate = DefaultIdentityResolver([]).evaluate(f"Smith (2020). {TITLE}.", md)
    assert candidate.features["volume_agreement"] is None
    assert candidate.features["issue_agreement"] is None
    assert candidate.features["pages_agreement"] is None


def test_r06_explicit_bibliographic_conflicts_are_visible():
    md = metadata(volume="13", issue="4", pages="99-101")
    candidate = DefaultIdentityResolver([]).evaluate(CITATION, md)
    assert candidate.features["volume_agreement"] is False
    assert candidate.features["issue_agreement"] is False
    assert candidate.features["pages_agreement"] is False


@pytest.mark.anyio
async def test_r09_identifier_linked_bibliographic_conflict_needs_review():
    md = metadata(doi=DOI, volume="13", issue="4", pages="99-101")
    result = await DefaultIdentityResolver([Provider(linked=[(("doi", DOI), record(md=md))])]).resolve(
        CITATION + " doi:" + DOI
    )
    assert result.state == State.NEEDS_REVIEW and R.PROVIDER_DISAGREEMENT in result.reasons


def test_r11_evaluate_flags_explicit_wrong_doi_even_with_matching_title():
    candidate = DefaultIdentityResolver([]).evaluate(CITATION + " doi:" + DOI, metadata(doi="10.1234/other"))
    assert candidate.features["doi_agreement"] is False
    assert R.PROVIDER_DISAGREEMENT in candidate.reasons


@pytest.mark.anyio
async def test_r10_ambiguous_ordinary_candidates_still_run_isbn_fallback():
    isbn = "9780262033848"
    rows = [record(key=str(i), md=metadata(doi=f"10.1234/{i}")) for i in range(2)]
    book = metadata(isbn=isbn, type="book")
    provider = Provider("openlibrary", linked=[(("isbn", isbn), record("openlibrary", md=book))])
    result = await DefaultIdentityResolver([Provider(results=rows), provider]).resolve(
        CITATION + " ISBN:" + isbn
    )
    assert any(c.metadata.isbn == isbn for c in result.candidates)
    assert result.state == State.NEEDS_REVIEW


@pytest.mark.anyio
async def test_r02_r09_mismatched_isbn_is_not_accepted():
    md = metadata(isbn="0306406152", type="book")
    result = await DefaultIdentityResolver([Provider(results=[record(md=md)])]).resolve(
        CITATION + " ISBN:9780262033848"
    )
    assert result.state == State.NEEDS_REVIEW and R.PROVIDER_DISAGREEMENT in result.reasons
    assert result.candidates[0].features["isbn_agreement"] is False


def test_r06_missing_identifier_evidence_is_not_agreement():
    candidate = DefaultIdentityResolver([]).evaluate(CITATION, metadata())
    assert candidate.features["isbn_agreement"] is None
    assert candidate.features["pmid_agreement"] is None
    assert candidate.features["arxiv_agreement"] is None


@pytest.mark.anyio
async def test_r09_mismatched_pmid_is_not_accepted():
    result = await DefaultIdentityResolver(
        [Provider(results=[record(md=metadata(pmid="99999999"))])]
    ).resolve(CITATION + " PMID:12345678")
    assert result.state == State.NEEDS_REVIEW and R.PROVIDER_DISAGREEMENT in result.reasons


@pytest.mark.anyio
async def test_r06_linked_year_plus_one_is_agreement_not_conflict():
    md = metadata(doi=DOI).model_copy(update={"year": 2019})
    result = await DefaultIdentityResolver([Provider(linked=[(("doi", DOI), record(md=md))])]).resolve(
        CITATION + " doi:" + DOI
    )
    assert result.state == State.ACCEPTED_AUTO and R.YEAR_CONFLICT not in result.reasons
    assert result.candidates[0].features["year_delta"] == 1


@pytest.mark.anyio
async def test_r06_short_personal_author_conflict_still_blocks_acceptance():
    from refcollector.domain.models import Person

    md = WorkMetadata(title=TITLE, year=2020, authors=[Person(family="Wu")])
    result = await DefaultIdentityResolver([Provider(results=[record(md=md)])]).resolve(
        f"Li (2020). {TITLE}."
    )
    assert result.state == State.NEEDS_REVIEW and R.AUTHOR_CONFLICT in result.reasons
    assert result.candidates[0].features["author_overlap"] == 0
