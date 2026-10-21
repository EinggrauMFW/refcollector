"""Bounded HTML resolver enabled only by explicit trusted configuration.

The resolver reads the static #pdf element used by custom Zotero resolvers.
It does not establish open access, a licence, or user approval.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import quote, urldefrag, urljoin, urlsplit

from refcollector.config import Limits
from refcollector.contracts.ports import FileStore, HttpClient, NetGuard
from refcollector.domain.enums import LocationBasis
from refcollector.domain.models import AccessLocation
from refcollector.errors import InvalidInput, LimitExceeded, NotAuthorized
from refcollector.identity.identifiers import normalize_doi
from refcollector.ids import new_id

from .netguard import DefaultNetGuard, validate_destination
from .retrieval import _cancel, _require_connection_policy


def validate_template(template: str) -> str:
    """Require one DOI placeholder in an HTTPS path without resolving DNS."""
    try:
        if not isinstance(template, str) or template.count("{doi}") != 1:
            raise ValueError("one DOI placeholder is required")
        remainder = template.replace("{doi}", "")
        if "{" in remainder or "}" in remainder:
            raise ValueError("other placeholders are forbidden")
        parts = urlsplit(template)
        if "{doi}" not in parts.path or "?" in template or "#" in template:
            raise ValueError("placeholder must be in the path without query or fragment")
        validate_destination(template.replace("{doi}", "doi"))
    except (ValueError, NotAuthorized) as exc:
        raise InvalidInput("PDF fallback template must be HTTPS with one {doi} path placeholder") from exc
    return template


class _PdfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if self.url is None and tag in {"iframe", "embed", "object"} and values.get("id") == "pdf":
            self.url = values.get("src") or (values.get("data") if tag == "object" else None)
        if self.url is None and tag == "a":
            href = values.get("href") or ""
            if urlsplit(href).path.startswith("/storage/") and urlsplit(href).path.endswith(".pdf"):
                self.url = href


class DoiPdfFallback:
    def __init__(
        self, files: FileStore, *, limits: Limits | None = None, guard: NetGuard | None = None
    ) -> None:
        self._files = files
        self._limits = limits or Limits()
        self._guard = guard or DefaultNetGuard()

    async def resolve(
        self, template: str, doi: str, *, http: HttpClient, is_cancelled: Callable[[], bool]
    ) -> AccessLocation | None:
        _cancel(is_cancelled)
        validate_template(template)
        normalized = normalize_doi(doi)
        if normalized is None:
            raise InvalidInput("PDF fallback requires a valid DOI")
        _require_connection_policy(http)
        url = template.replace("{doi}", quote(normalized, safe=""))
        tmp = self._files.new_tmp_path(".html")
        max_bytes = min(1024 * 1024, self._limits.download_bytes)
        timeout = min(20.0, self._limits.download_timeout_s)
        try:
            async with asyncio.timeout(timeout):
                result = await http.download(
                    url,
                    dest=tmp,
                    max_bytes=max_bytes,
                    timeout_s=timeout,
                    max_redirects=self._limits.max_redirects,
                    guard=self._guard,
                    is_cancelled=is_cancelled,
                )
                _cancel(is_cancelled)
                if not 200 <= result.status < 300 or result.path != tmp:
                    return None
                if tmp.stat().st_size > max_bytes:
                    raise LimitExceeded("PDF fallback HTML exceeds byte limit")
                media_type = (result.content_type or "").split(";", 1)[0].strip().lower()
                if media_type not in {"text/html", "application/xhtml+xml"}:
                    return None
                parser = _PdfParser()
                parser.feed(tmp.read_bytes().decode("utf-8", errors="replace"))
                parser.close()
                if not parser.url:
                    return None
                pdf_url = urldefrag(urljoin(result.final_url, parser.url))[0]
                validate_destination(pdf_url)
                await self._guard.check(pdf_url)
                _cancel(is_cancelled)
                return AccessLocation(
                    location_id=new_id("location"),
                    url=pdf_url,
                    provider="configured_fallback",
                    basis=LocationBasis.CONFIGURED_FALLBACK,
                    eligible=True,
                    is_pdf=True,
                )
        finally:
            tmp.unlink(missing_ok=True)  # noqa: ASYNC240 - cleanup must survive cancellation
