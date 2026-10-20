"""Fake HttpClient for unit tests of identity, access and files before WP-04 lands.

Routes are matched on the URL without query string. A route value is an HttpResponse,
an exception to raise, or a list of them (consumed in order). Unrouted URLs return 404.
Provider adapter tests (WP-04) use respx with recorded fixtures instead.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from refcollector.contracts.ports import DownloadResult, HttpResponse, NetGuard
from refcollector.errors import Cancelled, LimitExceeded, NetworkModeDenied

Route = HttpResponse | Exception | list[HttpResponse | Exception]


def json_response(url: str, data: Any, status: int = 200) -> HttpResponse:
    import json

    return HttpResponse(status, {"content-type": "application/json"}, json.dumps(data).encode(), url)


class FakeHttpClient:
    def __init__(self, routes: Mapping[str, Route] | None = None, *, offline: bool = False) -> None:
        self.routes: dict[str, Route] = dict(routes or {})
        self.offline = offline
        self.calls: list[tuple[str, str, dict[str, Any]]] = []  # (kind, url, params)
        self.blocked_hosts: set[str] = set()

    def _next(self, url: str) -> HttpResponse:
        route = self.routes.get(url.split("?", 1)[0])
        if isinstance(route, list):
            route = route.pop(0) if route else None
        if route is None:
            return HttpResponse(404, {}, b"", url)
        if isinstance(route, Exception):
            raise route
        return route

    async def get(
        self,
        url: str,
        *,
        provider: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl_s: int | None = None,
    ) -> HttpResponse:
        if self.offline:
            raise NetworkModeDenied("offline mode")
        self.calls.append(("get", url, dict(params or {})))
        return self._next(url)

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
        if self.offline:
            raise NetworkModeDenied("offline mode")
        self.calls.append(("download", url, {}))
        await guard.check(url)
        if is_cancelled and is_cancelled():
            raise Cancelled()
        resp = self._next(url)
        if len(resp.body) > max_bytes:
            raise LimitExceeded("download too large")
        dest.write_bytes(resp.body)  # noqa: ASYNC240 - small test fake
        return DownloadResult(resp.url, resp.status, resp.headers.get("content-type"), dest, len(resp.body))

    async def aclose(self) -> None:
        return None


class AllowAllGuard:
    async def check(self, url: str) -> None:
        return None
