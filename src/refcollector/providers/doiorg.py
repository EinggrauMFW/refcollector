"""WP-04: doi.org CSL content negotiation MetadataProvider. Spec 9.1.

Covers registration agencies without their own adapter (mEDRA, JaLC, ...).
"""

from __future__ import annotations

from typing import Literal

from refcollector.contracts.ports import CitationQuery, HttpClient
from refcollector.domain.models import ProviderRecord
from refcollector.providers import csl_metadata, doi_path, get_json, make_record, normalize_doi

CSL_JSON = "application/vnd.citationstyle.csl+json"


class DoiOrgProvider:
    name = "doiorg"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    async def by_identifier(
        self, kind: Literal["doi", "isbn", "arxiv", "pmid"], value: str
    ) -> ProviderRecord | None:
        doi = normalize_doi(value) if kind == "doi" else None
        if not doi:
            return None
        data = await get_json(
            self._http, f"https://doi.org/{doi_path(doi)}", self.name, headers={"Accept": CSL_JSON}
        )
        if not isinstance(data, dict):
            return None
        md = csl_metadata(data)
        if not md.title:
            return None
        md.doi = md.doi or doi
        return make_record(self.name, md.doi, md, data)

    async def search(self, query: CitationQuery, rows: int = 5) -> list[ProviderRecord]:
        return []
