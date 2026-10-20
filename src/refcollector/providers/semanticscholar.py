"""WP-04: Semantic Scholar MetadataProvider. Spec 9.1."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from refcollector.config import Limits
from refcollector.contracts.ports import CitationQuery, HttpClient
from refcollector.domain.models import ProviderRecord, WorkMetadata
from refcollector.providers import get_json, make_record, normalize_doi, split_name, text

BASE = "https://api.semanticscholar.org/graph/v1/paper"
FIELDS = "title,year,authors,externalIds,openAccessPdf,venue,publicationTypes"
ID_PREFIX = {"doi": "DOI", "arxiv": "ARXIV", "pmid": "PMID"}


class SemanticScholarProvider:
    name = "semanticscholar"

    def __init__(self, http: HttpClient, limits: Limits | None = None) -> None:
        self._http = http
        self._search_ttl = (limits or Limits()).cache_ttl_search_s

    async def by_identifier(
        self, kind: Literal["doi", "isbn", "arxiv", "pmid"], value: str
    ) -> ProviderRecord | None:
        ident = normalize_doi(value) if kind == "doi" else value.strip()
        if kind not in ID_PREFIX or not ident:
            return None
        paper = await get_json(
            self._http,
            f"{BASE}/{ID_PREFIX[kind]}:{quote(ident, safe='/()')}",
            self.name,
            params={"fields": FIELDS},
        )
        return self._record(paper) if isinstance(paper, dict) else None

    async def search(self, query: CitationQuery, rows: int = 5) -> list[ProviderRecord]:
        """Title match endpoint: at most one record (404 when nothing matches)."""
        q = query.title or query.text
        if not q:
            return []
        data = await get_json(
            self._http,
            f"{BASE}/search/match",
            self.name,
            params={"query": q[:300], "fields": FIELDS},
            cache_ttl_s=self._search_ttl,
        )
        return [r for p in (data or {}).get("data") or [] if (r := self._record(p))][:rows]

    def _record(self, p: dict[str, Any]) -> ProviderRecord | None:
        title = text(p.get("title"))
        if not title:
            return None
        ids = p.get("externalIds") or {}
        types = p.get("publicationTypes") or []
        pid = text(p.get("paperId"))
        md = WorkMetadata(
            title=title,
            authors=[a for x in p.get("authors") or [] if (a := split_name(x.get("name")))],
            year=p.get("year"),
            venue=text(p.get("venue")),
            type="journal-article"
            if "JournalArticle" in types
            else "proceedings-article"
            if "Conference" in types
            else None,
            doi=normalize_doi(text(ids.get("DOI"))),
            arxiv=text(ids.get("ArXiv")),
            pmid=text(ids.get("PubMed")),
            url=f"https://www.semanticscholar.org/paper/{pid}" if pid else None,
        )
        oa_pdf = text((p.get("openAccessPdf") or {}).get("url"))
        return make_record(self.name, pid or md.doi or "", md, p, oa_pdf)
