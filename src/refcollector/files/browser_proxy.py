"""Bounded CONNECT-only browser proxy with checked, pinned TCP destinations."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress

from refcollector.errors import BlockedAddress, NotAuthorized
from refcollector.files.netguard import DefaultNetGuard, validate_destination

Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class BrowserProxy:
    """Tunnel HTTPS without TLS interception; one byte budget covers all tunnels."""

    HEADER_LIMIT = 16 * 1024
    IO_TIMEOUT = 10.0
    MAX_CONNECTIONS = 32

    def __init__(
        self,
        max_bytes: int,
        is_cancelled: Callable[[], bool],
        *,
        guard: DefaultNetGuard | None = None,
        connect: Connector | None = None,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._is_cancelled = is_cancelled
        self._guard = guard or DefaultNetGuard()
        self._connect = connect or asyncio.open_connection
        self._server: asyncio.Server | None = None
        self._monitor: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self.address = ""
        self.bytes_transferred = 0
        self.limit_exceeded = False

    async def __aenter__(self) -> BrowserProxy:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=self.HEADER_LIMIT)
        self.address = f"127.0.0.1:{self._server.sockets[0].getsockname()[1]}"
        self._monitor = asyncio.create_task(self._watch_cancel())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            await asyncio.gather(self._monitor, return_exceptions=True)
        await self._close()

    async def _close(self) -> None:
        if self._server is not None:
            self._server.close()
        for writer in tuple(self._writers):
            writer.close()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)
        if self._server is not None:
            await self._server.wait_closed()

    async def _watch_cancel(self) -> None:
        # The parent supplies a synchronous callback, not an awaitable event.
        while not self._is_cancelled() and not self.limit_exceeded:  # noqa: ASYNC110
            await asyncio.sleep(0.05)
        await self._close()

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._tasks) >= self.MAX_CONNECTIONS or self._is_cancelled() or self.limit_exceeded:
            writer.close()
            return
        task = asyncio.create_task(self._handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        upstream = None
        pumps: list[asyncio.Task] = []
        established = False
        try:
            async with asyncio.timeout(self.IO_TIMEOUT):
                header = await reader.readuntil(b"\r\n\r\n")
                if len(header) > self.HEADER_LIMIT:
                    raise ValueError("header too large")
                line = header.split(b"\r\n", 1)[0].decode("ascii")
                match = re.fullmatch(r"CONNECT (\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+):443 HTTP/1\.[01]", line)
                if match is None:
                    raise ValueError("CONNECT to HTTPS port 443 required")
                url = f"https://{match[1]}:443"
                validate_destination(url)
                addresses = await self._guard.resolve_addresses(url)
                # Never pass the original hostname to the TCP connector.
                remote, upstream = await self._connect(addresses[0], 443)
                self._writers.add(upstream)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                established = True
            pumps = [
                asyncio.create_task(self._pump(reader, upstream)),
                asyncio.create_task(self._pump(remote, writer)),
            ]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        except (
            OSError,
            ValueError,
            UnicodeError,
            BlockedAddress,
            NotAuthorized,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
        ):
            if not established:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                with suppress(OSError, TimeoutError):
                    async with asyncio.timeout(self.IO_TIMEOUT):
                        await writer.drain()
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (writer, upstream):
                if stream is not None:
                    stream.close()
                    with suppress(OSError, TimeoutError):
                        async with asyncio.timeout(self.IO_TIMEOUT):
                            await stream.wait_closed()
                    self._writers.discard(stream)

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while not self._is_cancelled() and not self.limit_exceeded:
            async with asyncio.timeout(self.IO_TIMEOUT):
                data = await reader.read(64 * 1024)
                if not data:
                    return
                if self.bytes_transferred + len(data) > self._max_bytes:
                    self.limit_exceeded = True
                    return
                self.bytes_transferred += len(data)
                writer.write(data)
                await writer.drain()
