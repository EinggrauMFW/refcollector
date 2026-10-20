"""WP-04: OpenAlex MetadataProvider and OAProvider. Spec 9.1, 10.1."""

from __future__ import annotations

from typing import Any, Literal

from refcollector.config import Limits
from refcollector.contracts.ports import CitationQuery, HttpClient
from refcollector.domain.models import AccessLocation, ProviderRecord, WorkMetadata
from refcollector.providers import (
    doi_path,
    get_json,
    make_record,
    normalize_doi,
    oa_json,
    oa_location,
    query_text,
    split_name,
    text,
)

BASE = "https://api.openalex.org/works"


def _tail(url: Any) -> str | None:
    """'https://openalex.org/W123' -> 'W123'."""
    return text(url).rsplit("/", 1)[-1] if text(url) else None


class OpenAlexProvider:
    name = "openalex"

    def __init__(self, http: HttpClient, limits: Limits | None = None) -> None:
        self._http = http
        self._search_ttl = (limits or Limits()).cache_ttl_search_s

    async def by_identifier(
        self, kind: Literal["doi", "isbn", "arxiv", "pmid"], value: str
    ) -> ProviderRecord | None:
        if kind != "doi" or not normalize_doi(value):
            return None
        work = await get_json(self._http, f"{BASE}/doi:{doi_path(value)}", self.name)
        return self._record(work) if isinstance(work, dict) else None

    async def search(self, query: CitationQuery, rows: int = 5) -> list[ProviderRecord]:
        q = query.title or query_text(query)
        if not q:
            return []
        data = await get_json(
            self._http,
            BASE,
            self.name,
            params={"search": q[:300], "per-page": rows},
            cache_ttl_s=self._search_ttl,
        )
        return [r for w in (data or {}).get("results") or [] if (r := self._record(w))]

    async def locations(self, doi: str) -> list[AccessLocation]:
        work = await oa_json(
            self._http, f"{BASE}/doi:{doi_path(doi)}", self.name, cache_ttl_s=self._search_ttl
        )
        if not isinstance(work, dict):
            return []
        listed = work.get("locations") or [work.get("best_oa_location") or {}]
        out: list[AccessLocation] = []
        seen: set[str] = set()
        for loc in listed:
            url = text(loc.get("pdf_url")) or text(loc.get("landing_page_url"))
            if not loc.get("is_oa") or not url or url in seen:
                continue
            seen.add(url)
            source_type = (loc.get("source") or {}).get("type")
            out.append(
                oa_location(
                    self.name,
                    url,
                    is_pdf=bool(text(loc.get("pdf_url"))),
                    host_type="repository" if source_type == "repository" else "publisher",
                    version=loc.get("version"),
                    licence=text(loc.get("license")),
                )
            )
        return out

    def _record(self, w: dict[str, Any]) -> ProviderRecord | None:
        title = text(w.get("title")) or text(w.get("display_name"))
        if not title:
            return None
        bib = w.get("biblio") or {}
        pages = "-".join(p for p in (text(bib.get("first_page")), text(bib.get("last_page"))) if p)
        oid = _tail(w.get("id"))
        md = WorkMetadata(
            title=title,
            authors=[
                p
                for a in w.get("authorships") or []
                if (p := split_name((a.get("author") or {}).get("display_name")))
            ],
            year=w.get("publication_year"),
            venue=text(((w.get("primary_location") or {}).get("source") or {}).get("display_name")),
            volume=text(bib.get("volume")),
            issue=text(bib.get("issue")),
            pages=pages or None,
            type=text(w.get("type_crossref")) or text(w.get("type")),
            doi=normalize_doi(text(w.get("doi"))),
            openalex_id=oid,
            pmid=_tail((w.get("ids") or {}).get("pmid")),
        )
        oa_pdf = text((w.get("best_oa_location") or {}).get("pdf_url"))
        return make_record(self.name, oid or md.doi or "", md, w, oa_pdf)
