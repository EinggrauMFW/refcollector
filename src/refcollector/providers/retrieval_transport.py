"""Pin retrieval connections with public HTTPX APIs; no direct httpcore dependency.

HTTPX documents numeric-URL pinning with Host and sni_hostname, including certificate
hostname verification: https://www.python-httpx.org/advanced/extensions/#sni_hostname
Its installed 0.28.1 transport passes that name to httpcore 1.0.9 start_tls().

Resolution happens at the transport send boundary. Only validated numeric addresses
reach AsyncHTTPTransport, so its TCP backend never receives a DNS hostname. Each
download owns a separate transport; no proxy, HTTP/2 multiplexing or idle pool reuse
can bypass destination validation or reuse another hostname's TLS connection.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from refcollector.contracts.ports import NetGuard
from refcollector.errors import Cancelled
from refcollector.files.netguard import DefaultNetGuard


class PinnedDownloadTransport(httpx.AsyncBaseTransport):
    def __init__(self, guard: NetGuard, *, is_cancelled: Callable[[], bool] | None = None) -> None:
        self._guard = guard
        self._is_cancelled = is_cancelled
        self._resolver = guard if type(guard) is DefaultNetGuard else DefaultNetGuard()
        self._transport = httpx.AsyncHTTPTransport(
            verify=True,
            trust_env=False,
            retries=0,
            http2=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if type(self._guard) is not DefaultNetGuard:
            # Custom URL policies cannot replace the built-in address safety policy.
            await self._guard.check(str(request.url))
        addresses = await self._resolver.resolve_addresses(str(request.url))
        headers = httpx.Headers(request.headers)
        headers["Host"] = request.url.netloc.decode("ascii")
        extensions = {"sni_hostname": request.url.raw_host.decode("ascii")}
        if "timeout" in request.extensions:
            extensions["timeout"] = request.extensions["timeout"]
        # Do not forward caller-controlled target/SNI extensions. HTTPX assigns the
        # original request to the response, retaining original URLs and redirects.
        for index, address in enumerate(addresses):
            if self._is_cancelled is not None and self._is_cancelled():
                raise Cancelled("download cancelled before connection")
            pinned = httpx.Request(
                request.method,
                request.url.copy_with(host=address),
                headers=headers,
                stream=request.stream,
                extensions=extensions,
            )
            try:
                return await self._transport.handle_async_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise
        raise AssertionError("checked resolution returned no addresses")  # pragma: no cover

    async def aclose(self) -> None:
        await self._transport.aclose()
