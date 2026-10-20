"""WP-08: NetGuard implementation. Spec 13, 10.4 step 1.

Destination preflight policy. This is NOT connection-boundary rebinding protection.

The frozen NetGuard.check() cannot select or validate the socket opened by HttpClient.
The HTTP owner must enforce this policy again at connection time, preserving TLS SNI
and certificate validation and preventing proxy/pool bypasses. See WP-08's report.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urlsplit

from refcollector.errors import BlockedAddress, NotAuthorized

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def _resolve(host: str, port: int) -> Sequence[str]:
    rows = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [row[4][0] for row in rows]


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None or ip.teredo is not None:
            return False
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


class DefaultNetGuard:
    """Reject malformed destinations and any non-public DNS answer; no DNS cache."""

    def __init__(self, *, resolve: Resolver = _resolve) -> None:
        self._resolve = resolve

    async def check(self, url: str) -> None:
        try:
            if not url or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url:
                raise ValueError("control characters, whitespace or backslash")
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port
            if (
                parts.scheme != "https"
                or not host
                or parts.username is not None
                or parts.password is not None
            ):
                raise ValueError("HTTPS without credentials is required")
            if "%" in host or port == 0:
                raise ValueError("scoped or encoded host, or invalid port")
            if parts.netloc.endswith(":"):
                raise ValueError("empty port")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                host = host.encode("idna").decode("ascii")
                labels = host.rstrip(".").split(".")
                if len(host) > 253 or any(
                    not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in labels
                ):
                    raise ValueError("invalid DNS hostname") from None
                literal = False
            else:
                literal = True
        except (ValueError, UnicodeError) as exc:
            raise NotAuthorized(
                "Malformed or unauthorized retrieval destination",
                recovery_hint="Use an HTTPS URL with a valid public hostname and no credentials.",
            ) from exc
        try:
            addresses = [host] if literal else await self._resolve(host, port or 443)
            if not addresses or not all(_public(address) for address in addresses):
                raise ValueError("destination has a non-public or empty DNS answer")
        except (OSError, ValueError) as exc:
            raise BlockedAddress(
                "Retrieval destination is not exclusively public",
                recovery_hint="Use a public HTTPS destination; check all DNS A and AAAA answers.",
            ) from exc
