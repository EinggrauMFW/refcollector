"""Provider adapters map recorded responses to ProviderRecord / AccessLocation (spec 9.1, 10.1)."""

from __future__ import annotations

import pytest

from refcollector.contracts.ports import CitationQuery
from refcollector.domain.enums import CopyVersion, LocationBasis
from refcollector.domain.models import Person
from refcollector.errors import ProviderUnavailable
from refcollector.providers import normalize_doi
from refcollector.providers.crossref import CrossrefProvider
from refcollector.providers.datacite import DataCiteProvider
from refcollector.providers.doiorg import DoiOrgProvider
from refcollector.providers.openalex import OpenAlexProvider
from refcollector.providers.openlibrary import OpenLibraryProvider
from refcollector.providers.semanticscholar import SemanticScholarProvider
from refcollector.providers.unpaywall import UnpaywallProvider
from tests.unit.providers.conftest import EMAIL, fixture_json

pytestmark = pytest.mark.anyio

DOI = "10.1000/abc.123"


def test_normalize_doi() -> None:
    assert normalize_doi("https://doi.org/10.1000/ABC.123") == DOI
    assert normalize_doi(" doi:10.1000/ABC.123 ") == DOI
    assert normalize_doi("http://dx.doi.org/10.1000/abc.123") == DOI
    assert normalize_doi(None) is None


# ---------------------------------------------------------------- crossref


async def test_crossref_by_doi(client, respx_mock) -> None:
    respx_mock.get(f"https://api.crossref.org/works/{DOI}").respond(
        200, json=fixture_json("crossref", "work.json")
    )
    rec = await CrossrefProvider(client).by_identifier("doi", "https://doi.org/10.1000/ABC.123")
    assert rec is not None
    m = rec.metadata
    assert rec.provider == "crossref" and rec.record_key == DOI and len(rec.payload_hash) == 64
    assert m.title == "Deep Learning for Reference Parsing"
    assert m.authors == [Person(family="Lovelace", given="Ada"), Person(literal="World Health Organization")]
    assert (m.year, m.venue, m.volume, m.issue, m.pages) == (2021, "Journal of Examples", "12", "3", "45-67")
    assert (m.publisher, m.type, m.doi) == ("Example Press", "journal-article", DOI)
    assert rec.raw["DOI"] == "10.1000/ABC.123"


async def test_crossref_missing_and_unsupported(client, respx_mock) -> None:
    respx_mock.get(f"https://api.crossref.org/works/{DOI}").respond(404)
    provider = CrossrefProvider(client)
    assert await provider.by_identifier("doi", DOI) is None
    assert await provider.by_identifier("isbn", "9780262033848") is None
    assert respx_mock.calls.call_count == 1


async def test_crossref_search(client, respx_mock) -> None:
    route = respx_mock.get("https://api.crossref.org/works").respond(
        200, json=fixture_json("crossref", "search.json")
    )
    recs = await CrossrefProvider(client).search(CitationQuery(text="Lovelace 2021 Deep learning"), rows=2)
    params = route.calls.last.request.url.params
    assert params["query.bibliographic"] == "Lovelace 2021 Deep learning" and params["rows"] == "2"
    assert [r.metadata.doi for r in recs] == [DOI, "10.1000/xyz.9"]
    assert recs[1].metadata.year == 2019  # issued empty -> published-online
    assert await CrossrefProvider(client).search(CitationQuery()) == []


# ---------------------------------------------------------------- datacite / doi.org


async def test_datacite_by_doi(client, respx_mock) -> None:
    respx_mock.get("https://api.datacite.org/dois/10.5281/zenodo.42").respond(
        200, json=fixture_json("datacite", "doi.json")
    )
    rec = await DataCiteProvider(client).by_identifier("doi", "10.5281/ZENODO.42")
    assert rec is not None
    m = rec.metadata
    assert m.title == "Example Dataset of Citations"
    assert m.authors == [Person(family="Hopper", given="Grace"), Person(literal="CERN")]
    assert (m.year, m.publisher, m.type, m.doi) == (2020, "Zenodo", "dataset", "10.5281/zenodo.42")
    assert m.url == "https://zenodo.org/records/42"
    assert await DataCiteProvider(client).search(CitationQuery(text="x")) == []


async def test_doiorg_csl(client, respx_mock) -> None:
    route = respx_mock.get("https://doi.org/10.3000/medra.7").respond(
        200, json=fixture_json("doiorg", "csl.json")
    )
    rec = await DoiOrgProvider(client).by_identifier("doi", "10.3000/MEDRA.7")
    assert route.calls.last.request.headers["accept"] == "application/vnd.citationstyle.csl+json"
    assert rec is not None
    m = rec.metadata
    assert m.authors == [Person(family="Noether", given="Emmy"), Person(literal="Plato")]
    assert (m.title, m.venue, m.type, m.year) == (
        "An mEDRA Registered Article",
        "Rivista di Esempi",
        "journal-article",
        2018,
    )
    assert m.doi == "10.3000/medra.7"


# ---------------------------------------------------------------- openalex


async def test_openalex_by_doi_and_search(client, respx_mock) -> None:
    respx_mock.get(f"https://api.openalex.org/works/doi:{DOI}").respond(
        200, json=fixture_json("openalex", "work.json")
    )
    search = respx_mock.get("https://api.openalex.org/works").respond(
        200, json=fixture_json("openalex", "search.json")
    )
    provider = OpenAlexProvider(client)
    rec = await provider.by_identifier("doi", DOI)
    assert rec is not None
    m = rec.metadata
    assert rec.record_key == "W2741809807" and m.openalex_id == "W2741809807"
    assert m.authors == [Person(family="Lovelace", given="Ada"), Person(literal="Unesco")]
    assert (m.doi, m.year, m.type, m.pages, m.pmid) == (DOI, 2021, "journal-article", "45-67", "29456894")
    assert m.venue == "Journal of Examples"
    assert rec.oa_pdf_url == "https://repo.example.org/record/1/files/paper.pdf"

    recs = await provider.search(CitationQuery(title="A report without DOI"), rows=3)
    assert search.calls.last.request.url.params["search"] == "A report without DOI"
    assert search.calls.last.request.url.params["per-page"] == "3"
    assert len(recs) == 1 and recs[0].metadata.doi is None and recs[0].metadata.type == "report"


async def test_openalex_locations(client, respx_mock) -> None:
    respx_mock.get(f"https://api.openalex.org/works/doi:{DOI}").respond(
        200, json=fixture_json("openalex", "work.json")
    )
    locs = await OpenAlexProvider(client).locations(DOI)
    assert [loc.url for loc in locs] == [
        "https://repo.example.org/record/1/files/paper.pdf",
        "https://journal.example.org/article/1",
    ]
    repo, pub = locs
    assert (repo.basis, repo.host_type, repo.version, repo.licence, repo.is_pdf) == (
        LocationBasis.REPOSITORY,
        "repository",
        CopyVersion.ACCEPTED,
        "cc-by",
        True,
    )
    assert (pub.basis, pub.host_type, pub.version, pub.licence, pub.is_pdf) == (
        LocationBasis.OA_PROVIDER,
        "publisher",
        CopyVersion.PUBLISHED,
        None,
        False,
    )
    assert all(loc.eligible and loc.provider == "openalex" for loc in locs)
    assert all(loc.location_id.startswith("loc_") for loc in locs)


async def test_openalex_locations_missing_and_down(client, respx_mock) -> None:
    respx_mock.get("https://api.openalex.org/works/doi:10.1/none").respond(404)
    respx_mock.get("https://api.openalex.org/works/doi:10.1/down").respond(503)
    provider = OpenAlexProvider(client)
    assert await provider.locations("10.1/none") == []
    with pytest.raises(ProviderUnavailable):
        await provider.locations("10.1/down")


# ---------------------------------------------------------------- semantic scholar


async def test_semanticscholar_by_doi(client, respx_mock) -> None:
    route = respx_mock.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{DOI}").respond(
        200, json=fixture_json("semanticscholar", "paper.json")
    )
    rec = await SemanticScholarProvider(client).by_identifier("doi", DOI)
    assert "title" in route.calls.last.request.url.params["fields"]
    assert rec is not None
    m = rec.metadata
    assert rec.record_key == "649def34f8be52c8b66281af98ae884c09aef38b"
    assert m.authors == [Person(family="Lovelace", given="Ada"), Person(literal="Aristotle")]
    assert (m.doi, m.arxiv, m.year, m.type) == (DOI, "2101.00001", 2021, "journal-article")
    assert rec.oa_pdf_url == "https://arxiv.org/pdf/2101.00001"
    assert await SemanticScholarProvider(client).by_identifier("isbn", "123") is None


async def test_semanticscholar_match(client, respx_mock) -> None:
    route = respx_mock.get("https://api.semanticscholar.org/graph/v1/paper/search/match").respond(
        200, json=fixture_json("semanticscholar", "match.json")
    )
    recs = await SemanticScholarProvider(client).search(CitationQuery(title="Parsing references at scale"))
    assert route.calls.last.request.url.params["query"] == "Parsing references at scale"
    assert len(recs) == 1
    m = recs[0].metadata
    assert m.authors == [Person(family="Turing", given="Alan Mathison")]
    assert (m.type, m.doi, m.year) == ("proceedings-article", None, 2019)


async def test_semanticscholar_no_match(client, respx_mock) -> None:
    respx_mock.get("https://api.semanticscholar.org/graph/v1/paper/search/match").respond(404)
    assert await SemanticScholarProvider(client).search(CitationQuery(title="nothing")) == []


# ---------------------------------------------------------------- unpaywall


async def test_unpaywall_locations(client, respx_mock) -> None:
    route = respx_mock.get(f"https://api.unpaywall.org/v2/{DOI}").respond(
        200, json=fixture_json("unpaywall", "doi.json")
    )
    locs = await UnpaywallProvider(client, EMAIL).locations("https://doi.org/10.1000/ABC.123")
    assert route.calls.last.request.url.params["email"] == EMAIL
    assert [(loc.basis, loc.version, loc.is_pdf) for loc in locs] == [
        (LocationBasis.REPOSITORY, CopyVersion.ACCEPTED, True),
        (LocationBasis.OA_PROVIDER, CopyVersion.PUBLISHED, False),
        (LocationBasis.REPOSITORY, CopyVersion.SUBMITTED, True),
    ]
    assert locs[0].url == "https://repo.example.org/record/1/files/paper.pdf" and locs[0].licence == "cc-by"
    assert locs[2].url == "https://arxiv.org/pdf/2101.00001"
    assert all(loc.eligible and loc.provider == "unpaywall" for loc in locs)


async def test_unpaywall_unavailable(client, respx_mock) -> None:
    respx_mock.get(f"https://api.unpaywall.org/v2/{DOI}").respond(500)
    with pytest.raises(ProviderUnavailable):
        await UnpaywallProvider(client, EMAIL).locations(DOI)
    respx_mock.get("https://api.unpaywall.org/v2/10.1/none").respond(404)
    assert await UnpaywallProvider(client, EMAIL).locations("10.1/none") == []


# ---------------------------------------------------------------- open library


async def test_openlibrary_isbn(client, respx_mock) -> None:
    route = respx_mock.get("https://openlibrary.org/api/books").respond(
        200, json=fixture_json("openlibrary", "isbn.json")
    )
    rec = await OpenLibraryProvider(client).by_identifier("isbn", "978-0-262-03384-8")
    params = route.calls.last.request.url.params
    assert params["bibkeys"] == "ISBN:9780262033848" and params["jscmd"] == "data"
    assert rec is not None
    m = rec.metadata
    assert m.title == "Introduction to Algorithms: Third Edition"
    assert m.authors[0] == Person(family="Cormen", given="Thomas H.")
    assert (m.year, m.publisher, m.type, m.isbn) == (2009, "MIT Press", "book", "9780262033848")
    assert await OpenLibraryProvider(client).by_identifier("doi", DOI) is None


async def test_openlibrary_no_record(client, respx_mock) -> None:
    respx_mock.get("https://openlibrary.org/api/books").respond(200, json={})
    assert await OpenLibraryProvider(client).by_identifier("isbn", "0000000000") is None
