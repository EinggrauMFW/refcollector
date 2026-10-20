"""WP-04: Unpaywall OAProvider. Spec 10.1."""

from __future__ import annotations

from refcollector.config import Limits
from refcollector.contracts.ports import HttpClient
from refcollector.domain.models import AccessLocation
from refcollector.errors import ConfigMissingEmail
from refcollector.providers import doi_path, oa_json, oa_location, text


class UnpaywallProvider:
    name = "unpaywall"

    def __init__(self, http: HttpClient, email: str | None, limits: Limits | None = None) -> None:
        self._http = http
        self._email = email
        self._ttl = (limits or Limits()).cache_ttl_search_s

    async def locations(self, doi: str) -> list[AccessLocation]:
        if not self._email:
            raise ConfigMissingEmail("Unpaywall needs a contact email")
        data = await oa_json(
            self._http,
            f"https://api.unpaywall.org/v2/{doi_path(doi)}",
            self.name,
            params={"email": self._email},
            cache_ttl_s=self._ttl,
        )
        out: list[AccessLocation] = []
        seen: set[str] = set()
        for loc in (data or {}).get("oa_locations") or []:
            pdf = text(loc.get("url_for_pdf"))
            url = pdf or text(loc.get("url")) or text(loc.get("url_for_landing_page"))
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(
                oa_location(
                    self.name,
                    url,
                    is_pdf=bool(pdf),
                    host_type=text(loc.get("host_type")),
                    version=loc.get("version"),
                    licence=text(loc.get("license")),
                )
            )
        return out
