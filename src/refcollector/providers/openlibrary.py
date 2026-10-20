"""WP-04: Open Library ISBN MetadataProvider (grey literature). Spec 9.1 step 7."""

from __future__ import annotations

import re
from typing import Literal

from refcollector.contracts.ports import CitationQuery, HttpClient
from refcollector.domain.models import ProviderRecord, WorkMetadata
from refcollector.providers import get_json, make_record, split_name, text


class OpenLibraryProvider:
    name = "openlibrary"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    async def by_identifier(
        self, kind: Literal["doi", "isbn", "arxiv", "pmid"], value: str
    ) -> ProviderRecord | None:
        isbn = re.sub(r"[^0-9X]", "", value.upper()) if kind == "isbn" else ""
        if not isbn:
            return None
        key = f"ISBN:{isbn}"
        data = await get_json(
            self._http,
            "https://openlibrary.org/api/books",
            self.name,
            params={"bibkeys": key, "format": "json", "jscmd": "data"},
        )
        book = (data or {}).get(key)
        if not isinstance(book, dict) or not text(book.get("title")):
            return None
        title = text(book.get("title"))
        if text(book.get("subtitle")):
            title = f"{title}: {text(book.get('subtitle'))}"
        year = re.search(r"\b(\d{4})\b", text(book.get("publish_date")) or "")
        md = WorkMetadata(
            title=title,
            authors=[p for a in book.get("authors") or [] if (p := split_name(a.get("name")))],
            year=int(year.group(1)) if year else None,
            publisher=text([p.get("name") for p in book.get("publishers") or []]),
            type="book",
            isbn=isbn,
            url=text(book.get("url")),
        )
        return make_record(self.name, key.lower(), md, book)

    async def search(self, query: CitationQuery, rows: int = 5) -> list[ProviderRecord]:
        return []
