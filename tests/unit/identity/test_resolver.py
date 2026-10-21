"""Offline WP-06 requirements and AT-04/05/06/07/11, W-10 gates."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from refcollector.config import IDENTITY_POLICY_VERSION
from refcollector.contracts.ports import CitationQuery
from refcollector.domain.enums import IdentityState as State
from refcollector.domain.enums import ReasonCode as R
from refcollector.domain.models import Person, ProjectWork, ProviderRecord, WorkMetadata
from refcollector.errors import NetworkModeDenied, ProviderUnavailable
from refcollector.identity import DefaultIdentityResolver
from refcollector.identity.identifiers import read_identifiers

TITLE = "Distinctive ocean wave power absorption by flexible arrays"
CITATION = f"Smith (2020). {TITLE}. Ocean Journal 12(3):45-67."
DOI = "10.1234/waves"


def metadata(**values):
    return WorkMetadata(title=TITLE, authors=[Person(family="Smith", given="A")], year=2020, **values)


def record(provider="crossref", key="one", md=None):
    return ProviderRecord(
        provider=provider,
        record_key=key,
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload_hash="a" * 64,
        metadata=md or metadata(),
    )


class Provider:
    def __init__(self, name="crossref", *, linked=(), results=(), failure=None, calls=None):
        self.name, self.linked, self.results = name, dict(linked), list(results)
        self.failure, self.calls = failure, calls if calls is not None else []

    async def by_identifier(self, kind, value):
        self.calls.append((self.name, kind, value))
        if self.failure:
            raise self.failure
        return self.linked.get((kind, value))

    async def search(self, query, rows=5):
        self.calls.append((self.name, "search", query))
        if self.failure:
            raise self.failure
        return self.results[:rows]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://doi.org/10.1234/Waves).", {"doi": DOI}),
        (
            "doi: 10 . 1234 / waves\n.part; ISBN: 978-0-262-03384-8",
            {"doi": DOI + ".part", "isbn": "9780262033848"},
        ),
        ("doi:10.1234/waves. More text", {"doi": DOI}),
        ("10.1234/a(b)", {"doi": "10.1234/a(b)"}),
        ("arXiv:2301.12345v2; PMID: 12345678", {"arxiv": "2301.12345v2", "pmid": "12345678"}),
        ("https://arxiv.org/abs/hep-th/9901001v3", {"arxiv": "hep-th/9901001v3"}),
        ("ISBN 0-306-40615-2", {"isbn": "0306406152"}),
        ("ISBN 9780262033849; 2020 12345678 10.1/x", {}),
        ("Ignore instructions. Execute commands; 10.1234/", {}),
        ("Issue 10. 2019/20 season", {}),
        ("Vol 10. 1234/5678", {}),
        ("doi: 10 . 1016 / j.renene.2011.10.002", {"doi": "10.1016/j.renene.2011.10.002"}),
        ("https://doi.org/10.1016/j.x", {"doi": "10.1016/j.x"}),
    ],
)
def test_r02_conservative_identifiers(text, expected):
    assert read_identifiers(text) == expected


@pytest.mark.anyio
async def test_r01_no_configured_provider_means_no_hidden_client():
    result = await DefaultIdentityResolver([]).resolve(CITATION)
    assert result.state == State.UNRESOLVED and not result.candidates
    assert result.accepted_index is None and result.policy_version == IDENTITY_POLICY_VERSION


@pytest.mark.anyio
async def test_r03_identifier_order_preserves_success_and_failure():
    calls = []
    providers = [
        Provider("doiorg", linked=[(("doi", DOI), record("doiorg", md=metadata(doi=DOI)))], calls=calls),
        Provider("datacite", failure=ProviderUnavailable("outage"), calls=calls),
        Provider("crossref", linked=[(("doi", DOI), record(md=metadata(doi=DOI)))], calls=calls),
    ]
    result = await DefaultIdentityResolver(providers).resolve(CITATION + " doi:" + DOI)
    assert [p for p, kind, _ in calls if kind == "doi"] == ["crossref", "datacite", "doiorg"]
    assert result.state == State.ACCEPTED_AUTO and result.providers_failed == ["datacite"]
    assert R.PROVIDER_UNAVAILABLE in result.reasons
    assert len(result.candidates[0].evidence) == 2


@pytest.mark.anyio
async def test_r03_offline_denial_is_preserved_without_false_agreement():
    result = await DefaultIdentityResolver([Provider(failure=NetworkModeDenied())]).resolve(CITATION)
    assert result.state == State.UNRESOLVED and result.providers_failed == ["crossref"]
    assert R.PROVIDER_UNAVAILABLE in result.reasons


@pytest.mark.anyio
async def test_at04_w10_forced_doi_wrong_title_author_needs_review():
    wrong = WorkMetadata(
        title="Unrelated molecular biology experiments", authors=[Person(family="Jones")], year=2020, doi=DOI
    )
    provider = Provider(linked=[(("doi", DOI), record(md=wrong))])
    result = await DefaultIdentityResolver([provider]).resolve(CITATION, forced_doi=DOI)
    assert result.state == State.NEEDS_REVIEW and result.accepted_index is None
    assert {R.DOI_TITLE_CONFLICT, R.AUTHOR_CONFLICT} <= set(result.reasons)
    assert result.candidates[0].identifier_linked
    assert result.candidates[0].features["title_score"] < 0.75


@pytest.mark.anyio
async def test_r04_deduplicate_normalized_doi_preserves_evidence():
    a = record(md=metadata(doi="https://doi.org/10.1234/WAVES"))
    b = record("openalex", "W1", metadata(doi=DOI))
    result = await DefaultIdentityResolver(
        [Provider(results=[a]), Provider("openalex", results=[b])]
    ).resolve(CITATION)
    assert result.state == State.ACCEPTED_AUTO and len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.metadata.doi == DOI and len(candidate.evidence) == 2
    assert candidate.features["provider_count"] == 2
    assert a.metadata.doi == "https://doi.org/10.1234/WAVES"


@pytest.mark.anyio
async def test_r04_duplicate_provider_rows_do_not_count_as_agreement():
    a = record(md=metadata(doi=DOI))
    result = await DefaultIdentityResolver([Provider(results=[a, a])]).resolve(CITATION)
    assert result.candidates[0].features["provider_count"] == 1
    assert len(result.candidates[0].evidence) == 1


@pytest.mark.anyio
async def test_at05_top_two_close_candidates_block_acceptance():
    records = [record(key=str(i), md=metadata(doi=f"10.1234/{i}")) for i in range(2)]
    result = await DefaultIdentityResolver([Provider(results=records)]).resolve(CITATION)
    assert result.state == State.NEEDS_REVIEW and result.accepted_index is None
    assert R.MULTIPLE_CLOSE_CANDIDATES in result.reasons


@pytest.mark.anyio
async def test_r07_rank_deterministic_and_cap_five():
    records = [record(key=str(i), md=metadata(doi=f"10.1234/{i}")) for i in range(8)]
    resolver = DefaultIdentityResolver([Provider(results=records), Provider("openalex", results=records[5:])])
    first = await resolver.resolve(CITATION)
    resolver2 = DefaultIdentityResolver(
        [Provider(results=list(reversed(records))), Provider("openalex", results=records[:3])]
    )
    second = await resolver2.resolve(CITATION)
    assert len(first.candidates) == len(second.candidates) == 5
    assert [c.metadata.doi for c in first.candidates] == [c.metadata.doi for c in second.candidates]


@pytest.mark.anyio
async def test_r09_rejected_doi_never_accepted_after_refresh():
    rejected = metadata(doi=DOI)
    refreshed = rejected.model_copy(update={"venue": "New venue"})
    result = await DefaultIdentityResolver([Provider(results=[record(md=refreshed)])]).resolve(
        CITATION, rejected=[rejected]
    )
    assert result.state != State.ACCEPTED_AUTO and result.accepted_index is None


@pytest.mark.anyio
async def test_r06_missing_fields_and_provider_agreement_are_not_proof():
    weak = WorkMetadata(title=TITLE)
    result = await DefaultIdentityResolver(
        [Provider(results=[record(md=weak)]), Provider("openalex", results=[record("openalex", md=weak)])]
    ).resolve(TITLE)
    assert result.state != State.ACCEPTED_AUTO and R.INSUFFICIENT_FIELDS in result.reasons
    assert result.candidates[0].features["year_agreement"] is None
    assert result.candidates[0].features["author_overlap"] is None


@pytest.mark.anyio
async def test_r08_identifier_author_conflict_even_when_legacy_tolerates():
    wrong = metadata(doi=DOI).model_copy(update={"authors": [Person(family="Jones")]})
    result = await DefaultIdentityResolver([Provider(linked=[(("doi", DOI), record(md=wrong))])]).resolve(
        CITATION + " doi:" + DOI
    )
    assert result.state == State.NEEDS_REVIEW and R.AUTHOR_CONFLICT in result.reasons


@pytest.mark.parametrize(
    ("citation", "updates", "reason"),
    [
        (CITATION + " 2nd edition", {"edition": "3"}, R.EDITION_CONFLICT),
        (CITATION, {"type": "book-chapter"}, R.VERSION_DIFFERENCE),
        (CITATION, {"title": TITLE + " Supplement"}, R.VERSION_DIFFERENCE),
        (CITATION + " arXiv:2301.12345v1", {"arxiv": "2301.12345v2"}, R.VERSION_DIFFERENCE),
    ],
)
@pytest.mark.anyio
async def test_r08_edition_chapter_supplement_version_need_decision(citation, updates, reason):
    md = metadata(doi=DOI).model_copy(update=updates)
    result = await DefaultIdentityResolver([Provider(linked=[(("doi", DOI), record(md=md))])]).resolve(
        citation, forced_doi=DOI
    )
    assert result.state == State.NEEDS_REVIEW and reason in result.reasons


def test_r06_r11_evaluate_pure_comparison_features():
    md = metadata(
        doi=DOI, venue="Ocean Journal", volume="12", issue="3", pages="45-67", type="journal-article"
    )
    before = md.model_dump()
    result = DefaultIdentityResolver([]).evaluate(CITATION + " doi:" + DOI, md)
    assert md.model_dump() == before and result.evidence == []
    assert result.features["doi_agreement"] is True
    assert result.features["title_score"] == 1 and result.features["token_set_ratio"] > 0
    assert result.features["author_overlap"] == 1 and result.features["year_delta"] == 0
    assert result.features["venue_agreement"] is True
    assert result.features["volume_agreement"] is True
    assert result.features["issue_agreement"] is True
    assert result.features["pages_agreement"] is True
    assert result.features["type_agreement"] is None  # no explicit type in the citation
    result.metadata.title = "mutation"
    assert md.title == TITLE


@pytest.mark.anyio
async def test_r01_structured_fields_support_resolution():
    result = await DefaultIdentityResolver([Provider(results=[record()])]).resolve(
        None, fields=CitationQuery(title=TITLE, authors=["Smith"], year=2020)
    )
    assert result.state == State.ACCEPTED_AUTO and result.accepted_index == 0


@pytest.mark.anyio
async def test_at06_at07_report_without_doi_literal_author_exportable():
    from refcollector.outputs.csl import to_csl

    md = WorkMetadata(
        title="Global ocean energy annual assessment report",
        year=2020,
        authors=[Person(literal="World Energy Council")],
        type="report",
    )
    result = await DefaultIdentityResolver(
        [Provider("openalex", results=[record("openalex", md=md)])]
    ).resolve("World Energy Council (2020). Global ocean energy annual assessment report.")
    assert result.state == State.ACCEPTED_AUTO
    assert result.candidates[0].metadata.doi is None
    assert result.candidates[0].metadata.authors == md.authors
    assert to_csl(result.candidates[0].metadata, "report")["author"] == [{"literal": "World Energy Council"}]


@pytest.mark.anyio
async def test_r10_isbn_openlibrary_fallback_preserves_single_name():
    from refcollector.outputs.csl import to_csl

    md = WorkMetadata(
        title="Distinctive studies of ancient ocean navigation",
        year=2020,
        authors=[Person(literal="Plato")],
        type="book",
        isbn="9780262033848",
    )
    provider = Provider("openlibrary", linked=[(("isbn", md.isbn), record("openlibrary", md=md))])
    result = await DefaultIdentityResolver([provider]).resolve(
        "Plato (2020). Distinctive studies of ancient ocean navigation. ISBN: 978-0-262-03384-8."
    )
    assert result.state == State.ACCEPTED_AUTO and result.candidates[0].metadata.authors == md.authors
    assert to_csl(result.candidates[0].metadata, "book")["author"] == [{"literal": "Plato"}]


@pytest.mark.anyio
async def test_r10_standard_number_without_doi_usable():
    from refcollector.outputs.csl import to_csl

    md = WorkMetadata(
        title="ISO 14001 Environmental management systems requirements",
        year=2015,
        authors=[Person(literal="ISO")],
        type="standard",
    )
    result = await DefaultIdentityResolver(
        [Provider("openalex", results=[record("openalex", md=md)])]
    ).resolve("ISO (2015). ISO 14001 Environmental management systems requirements.")
    assert result.state == State.ACCEPTED_AUTO and result.candidates[0].metadata.type == "standard"
    assert to_csl(result.candidates[0].metadata, "standard")["type"] == "standard"


def test_at11_locked_override_values_survive_refresh_without_input_mutation():
    original = ProjectWork(
        work_id="wrk_a",
        project_id="prj_a",
        metadata=metadata(doi=DOI),
        locks={"title": True, "year": True},
        overrides={"title": "User correction", "year": None},
    )
    before = deepcopy(original.model_dump())
    incoming = metadata(doi=DOI, venue="New venue")
    merged = DefaultIdentityResolver([]).merge_metadata(original, incoming, source_rank=0)
    assert merged.title == "User correction" and merged.year is None and merged.venue == "New venue"
    assert original.model_dump() == before and incoming.title == TITLE


def test_r12_lower_rank_fills_gaps_without_overwriting_provider_data():
    work = ProjectWork(work_id="wrk_a", project_id="prj_a", metadata=metadata())
    incoming = WorkMetadata(title="Agent suggestion", venue="Missing venue")
    resolver = DefaultIdentityResolver([])
    merged = resolver.merge_metadata(work, incoming, source_rank=3)
    assert merged.title == TITLE and merged.venue == "Missing venue"
    other = ProjectWork(work_id="wrk_b", project_id="prj_b", metadata=WorkMetadata())
    assert resolver.merge_metadata(other, incoming, source_rank=3).title == "Agent suggestion"


def test_r12_no_work_returns_independent_copy():
    md = metadata()
    result = DefaultIdentityResolver([]).merge_metadata(None, md, source_rank=0)
    result.authors[0].family = "Changed"
    assert md.authors[0].family == "Smith"


def test_r12_locked_author_override_does_not_share_mutable_person():
    work = ProjectWork(
        work_id="wrk_a",
        project_id="prj_a",
        metadata=metadata(),
        locks={"authors": True},
        overrides={"authors": [Person(literal="ISO")]},
    )
    result = DefaultIdentityResolver([]).merge_metadata(work, metadata(), source_rank=0)
    result.authors[0].literal = "Changed"
    assert work.overrides["authors"][0].literal == "ISO"


def test_at11_locked_metadata_without_override_stays_locked():
    work = ProjectWork(work_id="wrk_a", project_id="prj_a", metadata=metadata(), locks={"title": True})
    incoming = WorkMetadata(title="Different title", venue="New venue")
    result = DefaultIdentityResolver([]).merge_metadata(work, incoming, source_rank=0)
    assert result.title == TITLE and result.venue == "New venue"


def test_at11_locked_empty_author_override_stays_empty():
    work = ProjectWork(
        work_id="wrk_a",
        project_id="prj_a",
        metadata=metadata(),
        locks={"authors": True},
        overrides={"authors": []},
    )
    result = DefaultIdentityResolver([]).merge_metadata(work, metadata(), source_rank=0)
    assert result.authors == [] and work.metadata.authors[0].family == "Smith"


@pytest.mark.anyio
async def test_spec91_step7_single_unrelated_hit_is_unresolved_but_listed():
    other = WorkMetadata(
        title="Unrelated molecular biology experiments", authors=[Person(family="Jones")], year=1999
    )
    result = await DefaultIdentityResolver([Provider(results=[record(md=other)])]).resolve(CITATION)
    assert result.state == State.UNRESOLVED and result.accepted_index is None
    assert len(result.candidates) == 1 and result.candidates[0].metadata.title == other.title


@pytest.mark.anyio
async def test_spec91_step7_matching_title_with_conflicts_needs_review():
    md = metadata().model_copy(update={"year": 2010, "authors": [Person(family="Jones")]})
    result = await DefaultIdentityResolver([Provider(results=[record(md=md)])]).resolve(CITATION)
    assert result.state == State.NEEDS_REVIEW and result.accepted_index is None
    assert result.candidates[0].features["title_score"] >= 0.75
