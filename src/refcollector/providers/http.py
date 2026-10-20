"""WP-04: HttpClient implementation (httpx.AsyncClient). Spec 6.5, 13.

One client per job, built from the job's ConfigSnapshot (D-08). Metadata GETs go through the
response cache and a per-host limiter; downloads stream to a temp file with manual redirects,
each target checked by the NetGuard.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx

import refcollector
from refcollector.config import SECRET_NAMES, Limits, get_secret
from refcollector.contracts.ports import DownloadResult, HttpResponse, NetGuard, network_allows
from refcollector.domain.models import ConfigSnapshot
from refcollector.errors import (
    Cancelled,
    IntegrationUnavailable,
    LimitExceeded,
    NetworkModeDenied,
    ProviderUnavailable,
    RateLimited,
    TransientNetworkError,
)
from refcollector.files.netguard import validate_destination
from refcollector.providers import cache
from refcollector.providers.limiter import HostLimiters
from refcollector.storage.db import Database

from .retrieval_transport import PinnedDownloadTransport

MAX_429_ATTEMPTS = 3


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After as seconds or an HTTP date. None when absent or unreadable."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _load_secrets() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in SECRET_NAMES:
        try:
            value = get_secret(name)
        except Exception:  # noqa: S112 - no keyring backend: run without keys
            continue
        if value:
            out[name] = value
    return out


def _transient_status(status: int) -> bool:
    return status == 408 or status >= 500


class ProviderHttpClient:
    def __init__(
        self,
        snapshot: ConfigSnapshot,
        db: Database,
        limits: Limits,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        download_transport: httpx.MockTransport | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        secrets: Mapping[str, str] | None = None,
    ) -> None:
        if download_transport is not None and type(download_transport) is not httpx.MockTransport:
            raise IntegrationUnavailable("Download test transport must be an explicit httpx.MockTransport")
        self._download_transport = download_transport
        self._mode = snapshot.network_mode
        self._db = db
        self._limits = limits
        self._sleep = sleep
        self._secrets = dict(secrets) if secrets is not None else _load_secrets()
        self._limiters = HostLimiters()
        self.blocked_hosts: set[str] = set()
        ua = f"refcollector/{refcollector.__version__}"
        if snapshot.contact_email:
            ua += f" (mailto:{snapshot.contact_email})"
        self._user_agent = ua
        self._client = httpx.AsyncClient(
            transport=transport, headers={"User-Agent": ua}, timeout=30.0, follow_redirects=True
        )

    # ------------------------------------------------------------------ metadata

    async def get(
        self,
        url: str,
        *,
        provider: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl_s: int | None = None,
    ) -> HttpResponse:
        params = dict(params or {})
        headers = dict(headers or {})
        fp = cache.fingerprint("GET", url, params, headers.get("Accept"))
        hit = cache.lookup(self._db, fp)
        if hit is not None:
            return hit
        if not network_allows(self._mode, "metadata"):
            raise NetworkModeDenied(f"network mode {self._mode.value}: no request to {provider}")
        host = urlsplit(url).hostname or ""
        if host in self.blocked_hosts:
            raise ProviderUnavailable(f"{provider} is rate-limited for the rest of this job")

        # Secrets are added after the fingerprint and never reach the cache or the response URL.
        send_params, send_headers = dict(params), dict(headers)
        if host == "api.openalex.org" and self._secrets.get("openalex_key"):
            send_params["api_key"] = self._secrets["openalex_key"]
        if host == "api.semanticscholar.org" and self._secrets.get("s2_key"):
            send_headers["x-api-key"] = self._secrets["s2_key"]

        for attempt in range(MAX_429_ATTEMPTS):
            async with self._limiters.for_host(host):
                try:
                    r = await self._client.get(url, params=send_params, headers=send_headers)
                except httpx.TransportError as exc:  # timeouts, connect and read errors
                    # `from None`: the httpx error carries the request URL, which may hold a key.
                    raise TransientNetworkError(f"{provider}: {type(exc).__name__}") from None
            if r.status_code == 429:
                wait = parse_retry_after(r.headers.get("Retry-After"))
                if wait is not None and wait > self._limits.provider_exhausted_retry_after_s:
                    self.blocked_hosts.add(host)
                    raise ProviderUnavailable(
                        f"{provider} rate limit exhausted (Retry-After {wait:.0f} s)", retry_after=wait
                    )
                wait = 2.0**attempt if wait is None else wait
                if attempt == MAX_429_ATTEMPTS - 1:
                    raise RateLimited(f"{provider} kept answering 429", retry_after=wait)
                await self._sleep(wait)
                continue
            if _transient_status(r.status_code):
                raise TransientNetworkError(
                    f"{provider}: HTTP {r.status_code}",
                    retry_after=parse_retry_after(r.headers.get("Retry-After")),
                )
            resp = HttpResponse(
                r.status_code, dict(r.headers), r.content, str(httpx.URL(url, params=params or None))
            )
            if 200 <= r.status_code < 300 or r.status_code == 404:
                ttl = self._limits.cache_ttl_identifier_s if cache_ttl_s is None else cache_ttl_s
                cache.store(self._db, fp, provider, resp, ttl)
            return resp
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ retrieval

    async def download(
        self,
        url: str,
        *,
        dest: Path,
        max_bytes: int,
        timeout_s: float,
        max_redirects: int,
        guard: NetGuard,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        if not network_allows(self._mode, "retrieval"):
            raise NetworkModeDenied(f"network mode {self._mode.value}: downloads need retrieval")
        try:
            async with asyncio.timeout(timeout_s):
                # Metadata connections, cookies and proxy routes are never reused for retrieval.
                transport = self._download_transport or PinnedDownloadTransport(
                    guard, is_cancelled=is_cancelled
                )
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    headers={"User-Agent": self._user_agent},
                    follow_redirects=False,
                ) as client:
                    return await self._download(
                        url, dest, max_bytes, timeout_s, max_redirects, guard, is_cancelled, client
                    )
        except BaseException as exc:
            # AT-10a: no partial file survives. Sync on purpose: it must run even mid-cancellation.
            dest.unlink(missing_ok=True)  # noqa: ASYNC240
            if isinstance(exc, TimeoutError | httpx.TransportError):
                raise TransientNetworkError(f"download failed: {type(exc).__name__}") from None
            raise

    async def _download(
        self,
        url: str,
        dest: Path,
        max_bytes: int,
        timeout_s: float,
        max_redirects: int,
        guard: NetGuard,
        is_cancelled: Callable[[], bool] | None,
        client: httpx.AsyncClient,
    ) -> DownloadResult:
        current = url
        for _ in range(max_redirects + 1):
            if is_cancelled is not None and is_cancelled():
                raise Cancelled("download cancelled")
            validate_destination(current)
            if self._download_transport is not None:
                # Explicit socket-free fixtures retain URL checks. Real transports resolve
                # once inside PinnedDownloadTransport and connect only to checked IPs.
                await guard.check(current)
            async with client.stream("GET", current, follow_redirects=False, timeout=timeout_s) as r:
                if r.is_redirect:
                    current = str(r.url.join(r.headers["location"]))
                    continue
                if r.status_code == 429 or _transient_status(r.status_code):
                    raise TransientNetworkError(
                        f"download: HTTP {r.status_code}",
                        retry_after=parse_retry_after(r.headers.get("Retry-After")),
                    )
                length = r.headers.get("content-length", "")
                if length.isdigit() and int(length) > max_bytes:
                    raise LimitExceeded(f"download larger than {max_bytes} bytes")
                size = 0
                async with await anyio.open_file(dest, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        if is_cancelled is not None and is_cancelled():
                            raise Cancelled("download cancelled")
                        size += len(chunk)
                        if size > max_bytes:
                            raise LimitExceeded(f"download larger than {max_bytes} bytes")
                        await f.write(chunk)
                return DownloadResult(str(r.url), r.status_code, r.headers.get("content-type"), dest, size)
        raise LimitExceeded(f"more than {max_redirects} redirects")

    async def aclose(self) -> None:
        await self._client.aclose()
