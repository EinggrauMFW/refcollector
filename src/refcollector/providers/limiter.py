"""WP-04: per-host aiolimiter. Spec 6.5. Blocked hosts live on the client (`blocked_hosts`)."""

from __future__ import annotations

from aiolimiter import AsyncLimiter

# Requests per second. Semantic Scholar's shared unkeyed pool is the tightest.
# ponytail: static rates; read X-Rate-Limit-* headers if providers start throttling us.
HOST_RATES: dict[str, float] = {
    "api.semanticscholar.org": 1,
    "api.crossref.org": 10,
    "api.openalex.org": 10,
}
DEFAULT_RATE = 5.0


class HostLimiters:
    def __init__(self, rates: dict[str, float] | None = None) -> None:
        self._rates = HOST_RATES if rates is None else rates
        self._limiters: dict[str, AsyncLimiter] = {}

    def for_host(self, host: str) -> AsyncLimiter:
        if host not in self._limiters:
            self._limiters[host] = AsyncLimiter(self._rates.get(host, DEFAULT_RATE), 1)
        return self._limiters[host]
