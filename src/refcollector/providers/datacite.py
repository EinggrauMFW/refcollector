"""WP-04: DataCite MetadataProvider. Spec 9.1."""

from __future__ import annotations

from typing import Any, Literal

from refcollector.contracts.ports import CitationQuery, HttpClient
from refcollector.domain.models import Person, ProviderRecord, WorkMetadata
from refcollector.providers import doi_path, get_json, make_record, normalize_doi, text


def _creator(c: dict[str, Any]) -> Person | None:
    name = text(c.get("name"))
    if c.get("nameType") == "Organizational":
        return Person(literal=name) if name else None
    if text(c.get("familyName")):
        return Person(family=text(c.get("familyName")), given=text(c.get("givenName")))
    if name and "," in name:
        family, given = name.split(",", 1)
        return Person(family=family.strip(), given=given.strip() or None)
    return Person(literal=name) if name else None  # unknown shape: keep the name whole (AT-07)


class DataCiteProvider:
    name = "datacite"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    async def by_identifier(
        self, kind: Literal["doi", "isbn", "arxiv", "pmid"], value: str
    ) -> ProviderRecord | None:
        doi = normalize_doi(value) if kind == "doi" else None
        if not doi:
            return None
        data = await get_json(self._http, f"https://api.datacite.org/dois/{doi_path(doi)}", self.name)
        attrs = ((data or {}).get("data") or {}).get("attributes")
        if not isinstance(attrs, dict):
            return None
        title = text([t.get("title") for t in attrs.get("titles") or []])
        if not title:
            return None
        publisher = attrs.get("publisher")
        year = text(attrs.get("publicationYear"))
        md = WorkMetadata(
            title=title,
            authors=[p for c in attrs.get("creators") or [] if (p := _creator(c))],
            year=int(year) if year and year.isdigit() else None,
            venue=text((attrs.get("container") or {}).get("title")),
            publisher=text(publisher.get("name") if isinstance(publisher, dict) else publisher),
            type=(text((attrs.get("types") or {}).get("resourceTypeGeneral")) or "").lower() or None,
            doi=normalize_doi(text(attrs.get("doi"))) or doi,
            url=text(attrs.get("url")),
        )
        return make_record(self.name, md.doi or doi, md, data)

    async def search(self, query: CitationQuery, rows: int = 5) -> list[ProviderRecord]:
        return []
