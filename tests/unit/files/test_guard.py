"""Offline URL/DNS policy tests; connection-boundary limitation is tested separately."""

import socket

import pytest

from refcollector.errors import BlockedAddress, NotAuthorized
from refcollector.files import DefaultNetGuard


def resolver(*addresses):
    async def resolve(host, port):
        return addresses

    return resolve


@pytest.mark.anyio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "100.64.0.1",
        "192.0.2.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
async def test_non_public_literal_and_dns_blocked(address):
    literal = f"[{address}]" if ":" in address else address
    with pytest.raises(BlockedAddress):
        await DefaultNetGuard(resolve=resolver("8.8.8.8")).check(f"https://{literal}/x")
    with pytest.raises(BlockedAddress):
        await DefaultNetGuard(resolve=resolver(address)).check("https://repo.example/x")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "answers", [("8.8.8.8", "127.0.0.1"), ("2606:4700:4700::1111", "::ffff:192.168.1.1"), (), ("not-an-ip",)]
)
async def test_mixed_empty_malformed_dns_fail_closed(answers):
    with pytest.raises(BlockedAddress):
        await DefaultNetGuard(resolve=resolver(*answers)).check("https://repo.example/x")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "file:///x",
        "https:///x",
        "https://u:p@example.org",
        "https://u@example.org",
        "https://example.org:bad/x",
        "https://example.org:0/x",
        "https://example.org:99999",
        "https://[::1",
        "https://example.org\\@evil.org",
        "https://example.org/\nsecret",
        " https://example.org",
        "https://example.org%2f.evil",
        "https://[fe80::1%25eth0]/",
        "https://a..org/",
    ],
)
async def test_malformed_or_unauthorized_destination(url):
    with pytest.raises(NotAuthorized):
        await DefaultNetGuard(resolve=resolver("8.8.8.8")).check(url)


@pytest.mark.anyio
async def test_public_v4_v6_and_mapped_public_allowed():
    guard = DefaultNetGuard(resolve=resolver("8.8.8.8", "2606:4700:4700::1111"))
    for url in ("https://repo.example/x", "https://8.8.8.8/x", "https://[::ffff:8.8.8.8]/x"):
        await guard.check(url)


@pytest.mark.anyio
async def test_dns_failure_is_typed():
    async def fail(host, port):
        raise socket.gaierror("no record")

    with pytest.raises(BlockedAddress):
        await DefaultNetGuard(resolve=fail).check("https://repo.example/x")


@pytest.mark.anyio
@pytest.mark.parametrize("url", ["https://[2606:4700:4700::1111]suffix/x", "https://repo.example:/x"])
async def test_malformed_authority_not_silently_reinterpreted(url):
    with pytest.raises(NotAuthorized):
        await DefaultNetGuard(resolve=resolver("8.8.8.8")).check(url)
