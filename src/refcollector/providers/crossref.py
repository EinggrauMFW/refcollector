"""WP-04: Crossref MetadataProvider. Spec 9.1."""

from __future__ import annotations

from typing import Any, Literal

from refcollector.config import Limits
from refcollector.contracts.ports import CitationQuery, HttpClient
from refcollector.domain.models import ProviderRecord
from refcollector.providers import csl_metadata, doi_path, get_json, make_record, normalize_doi, query_text

BASE = "https://api.crossref.org/works"


class CrossrefProvider:
    name = "crossref"

    def __init__(self, http: HttpClient, limits: Limits | None = None) -> None:
        self._http = http
        self._search_ttl = (limits or Limits()).cache_ttl_search_s

    async def by_identifier(
        self, kind: Literal["doi", "isbn", "arxiv", "pmid"], value: str
    ) -> ProviderRecord | None:
        if kind != "doi" or not normalize_doi(value):
            return None
        data = await get_json(self._http, f"{BASE}/{doi_path(value)}", self.name)
        message = (data or {}).get("message")
        return self._record(message) if isinstance(message, dict) else None

    async def search(self, query: CitationQuery, rows: int = 5) -> list[ProviderRecord]:
        q = query_text(query)
        if not q:
            return []
        data = await get_json(
            self._http,
            BASE,
            self.name,
            params={"query.bibliographic": q[:400], "rows": rows},
            cache_ttl_s=self._search_ttl,
        )
        items = ((data or {}).get("message") or {}).get("items") or []
        return [r for item in items if (r := self._record(item))]

    def _record(self, item: dict[str, Any]) -> ProviderRecord | None:
        md = csl_metadata(item)
        return make_record(self.name, md.doi or "", md, item) if md.title else None
