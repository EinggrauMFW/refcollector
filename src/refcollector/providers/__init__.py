"""WP-04: provider access through cache and per-host limiter (spec 2 Retain, 6.5, 6.6, 9.1).

Shared mapping helpers for the adapters live here.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import quote

from refcollector.contracts.ports import CitationQuery, HttpClient
from refcollector.domain.enums import CopyVersion, LocationBasis
from refcollector.domain.models import AccessLocation, Person, ProviderRecord, WorkMetadata, utcnow
from refcollector.errors import ProviderUnavailable, RateLimited
from refcollector.ids import new_id

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)

CSL_TYPES = {"article-journal": "journal-article", "paper-conference": "proceedings-article"}

VERSIONS = {
    "submittedVersion": CopyVersion.SUBMITTED,
    "acceptedVersion": CopyVersion.ACCEPTED,
    "publishedVersion": CopyVersion.PUBLISHED,
}


def normalize_doi(value: str | None) -> str | None:
    """Lower-case DOI without URL or `doi:` prefix."""
    if not value:
        return None
    return _DOI_PREFIX.sub("", value.strip()).strip().lower() or None


def doi_path(doi: str) -> str:
    return quote(normalize_doi(doi) or "", safe="/()")


def text(value: Any) -> str | None:
    """First non-empty string of a str or list (CSL fields come as either); numbers as str."""
    if isinstance(value, list):
        return next((t for v in value if (t := text(v))), None)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value.strip() or None
    return None


def split_name(name: str | None) -> Person | None:
    """'Given Family' display name. A single word stays a literal (AT-07)."""
    parts = (name or "").split()
    if not parts:
        return None
    if len(parts) == 1:
        return Person(literal=parts[0])
    return Person(family=parts[-1], given=" ".join(parts[:-1]))


def csl_person(a: dict[str, Any]) -> Person | None:
    if text(a.get("family")):
        return Person(family=text(a.get("family")), given=text(a.get("given")))
    literal = text(a.get("literal")) or text(a.get("name"))  # corporate / single names (AT-07)
    return Person(literal=literal) if literal else None


def csl_year(item: dict[str, Any]) -> int | None:
    for key in ("issued", "published-print", "published-online", "published"):
        parts = ((item.get(key) or {}).get("date-parts") or [[None]])[0]
        if parts and isinstance(parts[0], int):
            return parts[0]
    return None


def csl_metadata(item: dict[str, Any]) -> WorkMetadata:
    """Crossref work items and CSL JSON share field names; lists and strings both accepted."""
    kind = text(item.get("type"))
    return WorkMetadata(
        title=text(item.get("title")),
        authors=[p for a in item.get("author") or [] if (p := csl_person(a))],
        year=csl_year(item),
        venue=text(item.get("container-title")),
        volume=text(item.get("volume")),
        issue=text(item.get("issue")),
        pages=text(item.get("page")),
        publisher=text(item.get("publisher")),
        type=CSL_TYPES.get(kind or "", kind),
        doi=normalize_doi(text(item.get("DOI"))),
        url=text(item.get("URL")),
        isbn=text(item.get("ISBN")),
    )


def query_text(query: CitationQuery) -> str:
    if query.text:
        return query.text.strip()
    return " ".join(p for p in (query.title, *query.authors, str(query.year or "")) if p).strip()


def make_record(
    provider: str, key: str, metadata: WorkMetadata, raw: dict[str, Any], oa_pdf_url: str | None = None
) -> ProviderRecord:
    payload_hash = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    return ProviderRecord(
        provider=provider,
        record_key=key,
        fetched_at=utcnow(),
        payload_hash=payload_hash,
        metadata=metadata,
        raw=raw,
        oa_pdf_url=oa_pdf_url,
    )


def oa_location(
    provider: str, url: str, *, is_pdf: bool, host_type: str | None, version: str | None, licence: str | None
) -> AccessLocation:
    """A location listed by an enabled OA provider is eligible (10.1, D-11)."""
    return AccessLocation(
        location_id=new_id("location"),
        url=url,
        provider=provider,
        version=VERSIONS.get(version or "", CopyVersion.UNKNOWN),
        licence=licence,
        basis=LocationBasis.REPOSITORY if host_type == "repository" else LocationBasis.OA_PROVIDER,
        host_type=host_type,
        eligible=True,
        is_pdf=is_pdf,
    )


async def get_json(http: HttpClient, url: str, provider: str, **kw: Any) -> Any:
    """Parsed 2xx body, else None (no record). Transport errors propagate."""
    resp = await http.get(url, provider=provider, **kw)
    if not 200 <= resp.status < 300:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def oa_json(http: HttpClient, url: str, provider: str, **kw: Any) -> Any:
    """Like get_json for OAProvider.locations: 404 -> None; no answer -> ProviderUnavailable."""
    try:
        resp = await http.get(url, provider=provider, **kw)
    except RateLimited as exc:
        raise ProviderUnavailable(exc.message, retry_after=exc.retry_after) from exc
    if resp.status == 404:
        return None
    if not 200 <= resp.status < 300:
        raise ProviderUnavailable(f"{provider}: HTTP {resp.status}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderUnavailable(f"{provider}: unreadable response") from exc
