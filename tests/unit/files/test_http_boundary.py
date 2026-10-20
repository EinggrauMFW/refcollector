"""Real HTTP client over explicit socket-free fixtures: limits and redirects.

Actual pinning is tested below HTTPX/httpcore in providers/test_retrieval_transport.py.
"""

import asyncio

import httpx
import pytest

from refcollector.config import Limits
from refcollector.domain.enums import NetworkMode, RetrievalState
from refcollector.errors import BlockedAddress, Cancelled, LimitExceeded, TransientNetworkError
from refcollector.files import DefaultNetGuard, DefaultRetriever
from refcollector.providers.http import ProviderHttpClient
from tests.fixtures.pdfs import paper_pdf
from tests.unit.files.test_guard import resolver
from tests.unit.files.test_retrieval import DOI, TITLE, location, work
from tests.unit.providers.conftest import snapshot


async def chunks(*parts, delay=0):
    for part in parts:
        if delay:
            await asyncio.sleep(delay)
        yield part


class Stream(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        async for part in self.parts:
            yield part


def client(db, handler):
    return ProviderHttpClient(
        snapshot(NetworkMode.RETRIEVAL),
        db,
        Limits(),
        download_transport=httpx.MockTransport(handler),
        secrets={},
    )


async def download(http, dest, guard, **kw):
    args = {"max_bytes": 100 * 1024 * 1024, "timeout_s": 120, "max_redirects": 5, **kw}
    return await http.download("https://repo.example/one", dest=dest, guard=guard, **args)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "target,answers",
    [
        ("https://169.254.169.254/latest", ("8.8.8.8",)),
        ("https://[::ffff:127.0.0.1]/x", ("8.8.8.8",)),
        ("https://private.example/x", ("10.0.0.1",)),
        ("https://mixed.example/x", ("8.8.8.8", "::ffff:192.168.1.1")),
        ("http://public.example/x", ("8.8.8.8",)),
    ],
)
async def test_at15_redirect_private_mapped_mixed_and_http_blocked(db, tmp_path, target, answers):
    seen = []

    async def resolve(host, port):
        return ("8.8.8.8",) if host == "repo.example" else answers

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": target})

    http = client(db, handler)
    dest = tmp_path / "redirect.pdf"
    try:
        with pytest.raises((BlockedAddress,)) if target.startswith("https:") else pytest.raises(Exception):
            await download(http, dest, DefaultNetGuard(resolve=resolve))
        assert seen == ["https://repo.example/one"] and not dest.exists()
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_sixth_redirect_is_not_followed(db, tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": f"https://repo.example/{len(seen)}"})

    http = client(db, handler)
    dest = tmp_path / "redirect.pdf"
    try:
        with pytest.raises(LimitExceeded):
            await download(http, dest, DefaultNetGuard(resolve=resolver("8.8.8.8")))
        assert len(seen) == 6 and not dest.exists()
    finally:
        await http.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["bytes", "timeout", "cancel", "interrupt"])
async def test_real_download_stream_limits_cleanup(db, tmp_path, mode):
    async def body():
        yield b"%PDF-"
        if mode == "interrupt":
            raise httpx.ReadError("interrupted")
        if mode == "timeout":
            await asyncio.sleep(0.1)
        yield b"more"

    http = client(db, lambda request: httpx.Response(200, stream=Stream(body())))
    dest = tmp_path / "stream.pdf"
    states = iter([False, True])
    options = {
        "bytes": {"max_bytes": 6},
        "timeout": {"timeout_s": 0.01},
        "cancel": {"is_cancelled": lambda: next(states, True)},
        "interrupt": {},
    }[mode]
    error = {
        "bytes": LimitExceeded,
        "timeout": TransientNetworkError,
        "cancel": Cancelled,
        "interrupt": TransientNetworkError,
    }[mode]
    try:
        with pytest.raises(error):
            await download(http, dest, DefaultNetGuard(resolve=resolver("8.8.8.8")), **options)
        assert not dest.exists()
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_explicit_mock_fixture_does_not_depend_on_peer_inspection(db, tmp_path):
    """Explicit socket-free fixtures do not use peer inspection as a safety policy.

    MockTransport reports a private connection after DNS preflight approved public DNS.
    No sockets open. Real transport protection is proved in the provider backend tests.
    """

    class PrivatePeer:
        def get_extra_info(self, name):
            return ("127.0.0.1", 443) if name == "server_addr" else None

    data = paper_pdf(TITLE, [], DOI)
    http = client(
        db,
        lambda request: httpx.Response(
            200,
            content=data,
            headers={"content-type": "application/pdf"},
            extensions={"network_stream": PrivatePeer()},
        ),
    )
    dest = tmp_path / "gap.pdf"
    try:
        result = await download(http, dest, DefaultNetGuard(resolve=resolver("8.8.8.8")))
        assert result.status == 200 and dest.read_bytes() == data
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_retriever_actual_http_redirect_verified_and_limits(db, fake_store):
    data = paper_pdf(TITLE, [], DOI)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf"})

    http = client(db, handler)

    retriever = DefaultRetriever(fake_store.files, guard=DefaultNetGuard(resolve=resolver("8.8.8.8")))
    try:
        result = await retriever.retrieve(
            "prj_a", work(), [location()], http=http, is_cancelled=lambda: False
        )
        assert result.state == RetrievalState.VERIFIED
        assert result.work_file.final_url == "https://repo.example/final"
        assert fake_store.files.has_grant("prj_a", result.work_file.sha256)
    finally:
        await http.aclose()
