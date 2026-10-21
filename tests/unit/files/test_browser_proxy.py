"""Offline proxy checks: local clients and injected public-address transport."""

import asyncio

import pytest

from refcollector.files.browser_proxy import BrowserProxy
from refcollector.files.netguard import DefaultNetGuard


class MemoryWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


async def client(proxy, payload):
    host, port = proxy.address.split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(payload)
    await writer.drain()
    return reader, writer


async def close(writer):
    writer.close()
    await writer.wait_closed()


def fixture_proxy(*, answers=("8.8.8.8",), max_bytes=100, cancelled=lambda: False):
    calls = []
    incoming = asyncio.StreamReader()
    outgoing = MemoryWriter()

    async def resolve(host, port):
        calls.append(("resolve", host, port))
        return answers

    async def connect(host, port):
        calls.append(("connect", host, port))
        return incoming, outgoing

    return (
        BrowserProxy(max_bytes, cancelled, guard=DefaultNetGuard(resolve=resolve), connect=connect),
        calls,
        incoming,
        outgoing,
    )


@pytest.mark.anyio
async def test_numeric_connection_and_bidirectional_tunnel_cleanup():
    proxy, calls, incoming, outgoing = fixture_proxy()
    async with proxy:
        reader, writer = await client(proxy, b"CONNECT paper.example:443 HTTP/1.1\r\n\r\n")
        assert b"200" in await reader.readuntil(b"\r\n\r\n")
        writer.write(b"request")
        await writer.drain()
        incoming.feed_data(b"response")
        assert await reader.readexactly(8) == b"response"
        for _ in range(100):
            if outgoing.data:
                break
            await asyncio.sleep(0.001)
        assert outgoing.data == b"request"
        assert calls == [("resolve", "paper.example", 443), ("connect", "8.8.8.8", 443)]
        assert proxy.bytes_transferred == 15
    assert outgoing.closed
    assert not proxy._tasks
    assert await reader.read() == b""
    await close(writer)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        b"GET https://paper.example/ HTTP/1.1\r\n\r\n",
        b"CONNECT paper.example:80 HTTP/1.1\r\n\r\n",
        b"CONNECT user@paper.example:443 HTTP/1.1\r\n\r\n",
        b"CONNECT paper.example/x:443 HTTP/1.1\r\n\r\n",
        b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n",
        b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n",
        b"CONNECT paper.example:443 HTTP/1.1\r\nX: " + b"a" * 17000 + b"\r\n\r\n",
    ],
)
async def test_rejects_invalid_or_private_request_without_connection(payload):
    proxy, calls, _, _ = fixture_proxy()
    async with proxy:
        reader, writer = await client(proxy, payload)
        assert b"403" in await reader.read()
        assert not any(call[0] == "connect" for call in calls)
        await close(writer)


@pytest.mark.anyio
async def test_resolves_each_connection_and_rejects_mixed_private_answers():
    proxy, calls, _, _ = fixture_proxy(answers=("8.8.8.8", "10.0.0.1"))
    async with proxy:
        for _ in range(2):
            reader, writer = await client(proxy, b"CONNECT paper.example:443 HTTP/1.1\r\n\r\n")
            assert b"403" in await reader.read()
            await close(writer)
    assert calls == [("resolve", "paper.example", 443)] * 2


@pytest.mark.anyio
async def test_aggregate_budget_counts_both_directions_and_closes():
    proxy, _, incoming, outgoing = fixture_proxy(max_bytes=5)
    async with proxy:
        reader, writer = await client(proxy, b"CONNECT paper.example:443 HTTP/1.1\r\n\r\n")
        await reader.readuntil(b"\r\n\r\n")
        incoming.feed_data(b"1234")
        assert await reader.readexactly(4) == b"1234"
        writer.write(b"xx")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert proxy.limit_exceeded
        assert proxy.bytes_transferred == 4
        assert outgoing.data == b""
        await close(writer)


@pytest.mark.anyio
async def test_cancel_closes_idle_tunnel():
    cancelled = False
    proxy, _, _, outgoing = fixture_proxy(cancelled=lambda: cancelled)
    async with proxy:
        reader, writer = await client(proxy, b"CONNECT paper.example:443 HTTP/1.1\r\n\r\n")
        await reader.readuntil(b"\r\n\r\n")
        cancelled = True
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert outgoing.closed
        await close(writer)


@pytest.mark.anyio
async def test_incomplete_header_has_bounded_wait():
    proxy, calls, _, _ = fixture_proxy()
    proxy.IO_TIMEOUT = 0.02
    async with proxy:
        reader, writer = await client(proxy, b"CONNECT paper.example:443 HTTP/1.1\r\n")
        assert b"403" in await asyncio.wait_for(reader.read(), 1)
        assert calls == []
        await close(writer)


@pytest.mark.anyio
async def test_budget_is_shared_across_connections():
    calls = []
    upstreams = []

    async def resolve(host, port):
        calls.append(host)
        return ("8.8.8.8",)

    async def connect(host, port):
        reader = asyncio.StreamReader()
        upstreams.append(reader)
        return reader, MemoryWriter()

    proxy = BrowserProxy(5, lambda: False, guard=DefaultNetGuard(resolve=resolve), connect=connect)
    async with proxy:
        reader1, writer1 = await client(proxy, b"CONNECT first.example:443 HTTP/1.1\r\n\r\n")
        await reader1.readuntil(b"\r\n\r\n")
        upstreams[0].feed_data(b"1234")
        assert await reader1.readexactly(4) == b"1234"
        reader2, writer2 = await client(proxy, b"CONNECT second.example:443 HTTP/1.1\r\n\r\n")
        await reader2.readuntil(b"\r\n\r\n")
        upstreams[1].feed_data(b"xx")
        assert await asyncio.wait_for(reader2.read(), 1) == b""
        assert proxy.limit_exceeded
        assert proxy.bytes_transferred == 4
        assert calls == ["first.example", "second.example"]
        await close(writer1)
        await close(writer2)


@pytest.mark.anyio
async def test_rejects_connections_above_active_limit():
    proxy, _, _, _ = fixture_proxy()
    proxy.MAX_CONNECTIONS = 1
    async with proxy:
        reader1, writer1 = await client(proxy, b"CONNECT paper.example:443 HTTP/1.1\r\n\r\n")
        await reader1.readuntil(b"\r\n\r\n")
        reader2, writer2 = await client(proxy, b"")
        assert await asyncio.wait_for(reader2.read(), 1) == b""
        await close(writer1)
        await close(writer2)
