"""Connection-boundary proof: real HTTPX/httpcore, fake TCP/TLS only.

No HTTP response mock replaces the address policy or HTTP transport here. httpcore
is HTTPX's installed backend; patch only its final connector to record the numeric
destination and TLS inputs, then serve wire-format HTTP through its real parser.
"""

import asyncio
import ssl
from collections import deque

import httpcore
import httpx
import pytest

from refcollector.config import Limits
from refcollector.domain.enums import NetworkMode, RetrievalState
from refcollector.errors import (
    BlockedAddress,
    Cancelled,
    IntegrationUnavailable,
    LimitExceeded,
    NotAuthorized,
    TransientNetworkError,
)
from refcollector.files import DefaultNetGuard, DefaultRetriever
from refcollector.providers.http import ProviderHttpClient
from refcollector.providers.retrieval_transport import PinnedDownloadTransport
from tests.fixtures.pdfs import paper_pdf
from tests.unit.files.test_retrieval import DOI, TITLE, location, work
from tests.unit.providers.conftest import snapshot


def wire(body=b"%PDF-1.7", *, status=200, headers=()):
    lines = [
        f"HTTP/1.1 {status} Test",
        "Content-Type: application/pdf",
        f"Content-Length: {len(body)}",
        *headers,
    ]
    return "\r\n".join(lines).encode() + b"\r\n\r\n" + body


class TcpStream(httpcore.AsyncNetworkStream):
    def __init__(self, response, *, tls_error=False):
        self.response = response
        self.writes = []
        self.tls = []
        self.closed = False
        self.tls_error = tls_error

    async def read(self, max_bytes, timeout=None):  # noqa: ASYNC109 - httpcore stream interface
        if isinstance(self.response, BaseException):
            raise self.response
        response, self.response = self.response, b""
        return response

    async def write(self, buffer, timeout=None):  # noqa: ASYNC109 - httpcore stream interface
        self.writes.append(buffer)

    async def aclose(self):
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):  # noqa: ASYNC109
        self.tls.append((server_hostname, ssl_context.check_hostname, ssl_context.verify_mode))
        if self.tls_error:
            raise httpcore.ConnectError("certificate verification failed")
        return self

    def get_extra_info(self, info):
        return None


@pytest.fixture
def tcp(monkeypatch):
    class Network:
        def __init__(self):
            self.responses = deque()
            self.connects = []
            self.streams = []
            self.tls_error = False
            self.connect_error = None

    network = Network()

    async def connect(backend, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ASYNC109
        network.connects.append((host, port))
        if network.connect_error:
            error = (
                network.connect_error.popleft()
                if isinstance(network.connect_error, deque)
                else network.connect_error
            )
            if error:
                raise error
        stream = TcpStream(
            network.responses.popleft() if network.responses else wire(), tls_error=network.tls_error
        )
        network.streams.append(stream)
        return stream

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    return network


def dns(*answers):
    calls = []

    async def resolve(host, port):
        calls.append((host, port))
        return answers

    return resolve, calls


@pytest.mark.anyio
async def test_rebinding_cannot_change_tcp_address_and_preserves_tls_host(tcp):
    calls = []

    async def rebind(host, port):
        calls.append((host, port))
        return ["8.8.8.8"] if len(calls) == 1 else ["127.0.0.1"]

    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=rebind))
    ) as client:
        response = await client.get("https://repo.example:8443/paper?q=one")
        assert response.status_code == 200 and str(response.url) == "https://repo.example:8443/paper?q=one"
    assert calls == [("repo.example", 8443)]
    assert tcp.connects == [("8.8.8.8", 8443)]
    assert tcp.streams[0].tls == [("repo.example", True, ssl.CERT_REQUIRED)]
    sent = b"".join(tcp.streams[0].writes)
    assert b"GET /paper?q=one HTTP/1.1\r\n" in sent
    assert b"Host: repo.example:8443\r\n" in sent
    assert tcp.streams[0].closed


@pytest.mark.anyio
@pytest.mark.parametrize(
    "answers",
    [
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("::ffff:10.0.0.1",),
        ("8.8.8.8", "127.0.0.1"),
        ("2606:4700:4700::1111", "fc00::1"),
        (),
        ("invalid",),
    ],
)
async def test_every_dns_answer_checked_before_any_connect(tcp, answers):
    resolve, calls = dns(*answers)
    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=resolve))
    ) as client:
        with pytest.raises(BlockedAddress):
            await client.get("https://repo.example/a")
    assert calls == [("repo.example", 443)] and tcp.connects == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1/x", "https://[::ffff:127.0.0.1]/x", "http://8.8.8.8/x", "https://u:p@repo.example/x"],
)
async def test_invalid_original_destination_blocked_before_tcp(tcp, url):
    resolve, calls = dns("8.8.8.8")
    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=resolve))
    ) as client:
        with pytest.raises(NotAuthorized):
            await client.get(url)
    assert calls == [] and tcp.connects == []


@pytest.mark.anyio
async def test_public_ipv6_pin_and_idna_tls_name(tcp):
    resolve, calls = dns("2606:4700:4700::1111")
    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=resolve))
    ) as client:
        await client.get("https://b\u00fccher.example/a")
    assert tcp.connects == [("2606:4700:4700::1111", 443)]
    assert calls == [("xn--bcher-kva.example", 443)]
    assert tcp.streams[0].tls == [("xn--bcher-kva.example", True, ssl.CERT_REQUIRED)]
    assert b"Host: xn--bcher-kva.example\r\n" in b"".join(tcp.streams[0].writes)


@pytest.mark.anyio
async def test_second_request_rechecks_dns_instead_of_reusing_connection(tcp):
    calls = []

    async def resolve(host, port):
        calls.append(host)
        return ["8.8.8.8"] if len(calls) == 1 else ["10.0.0.1"]

    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=resolve))
    ) as client:
        await client.get("https://repo.example/first")
        assert tcp.streams[0].closed
        with pytest.raises(BlockedAddress):
            await client.get("https://repo.example/second")
    assert calls == ["repo.example", "repo.example"] and tcp.connects == [("8.8.8.8", 443)]


@pytest.mark.anyio
async def test_same_ip_different_hosts_cannot_share_tls_connection(tcp):
    resolve, calls = dns("8.8.8.8")
    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=resolve))
    ) as client:
        await client.get("https://first.example/a")
        await client.get("https://second.example/b")
    assert len(tcp.connects) == 2 and all(stream.closed for stream in tcp.streams)
    assert [stream.tls[0][0] for stream in tcp.streams] == ["first.example", "second.example"]


@pytest.mark.anyio
async def test_caller_sni_host_and_proxy_target_cannot_override_original_url(tcp):
    resolve, calls = dns("8.8.8.8")
    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=resolve))
    ) as client:
        await client.get(
            "https://repo.example/safe",
            headers={"Host": "evil.example"},
            extensions={"sni_hostname": "evil.example", "target": b"https://127.0.0.1/private"},
        )
    assert tcp.streams[0].tls[0][0] == "repo.example"
    sent = b"".join(tcp.streams[0].writes)
    assert b"Host: repo.example\r\n" in sent and b"GET /safe HTTP/1.1" in sent
    assert b"evil.example" not in sent and b"127.0.0.1" not in sent


@pytest.mark.anyio
async def test_failed_certificate_cannot_send_http_or_disable_verification(tcp):
    tcp.tls_error = True
    resolve, _ = dns("8.8.8.8")
    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=resolve))
    ) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://repo.example/a")
    assert tcp.streams[0].tls == [("repo.example", True, ssl.CERT_REQUIRED)]
    assert tcp.streams[0].writes == []


@pytest.mark.anyio
async def test_safe_default_retriever_no_authorization_callback(db, fake_store):
    data = paper_pdf(TITLE, [], DOI)
    mocked = httpx.MockTransport(
        lambda request: httpx.Response(200, content=data, headers={"content-type": "application/pdf"})
    )
    resolve, _ = dns("8.8.8.8")
    http = ProviderHttpClient(
        snapshot(NetworkMode.RETRIEVAL), db, Limits(), download_transport=mocked, secrets={}
    )
    try:
        result = await DefaultRetriever(fake_store.files, guard=DefaultNetGuard(resolve=resolve)).retrieve(
            "prj_a", work(), [location()], http=http, is_cancelled=lambda: False
        )
        assert result.state == RetrievalState.VERIFIED
        assert fake_store.files.has_grant("prj_a", result.work_file.sha256)
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_boolean_claim_and_overriding_subclass_do_not_authorize_client(db, fake_store):
    class ClaimedSafe:
        connection_safe = True

        async def download(self, *args, **kwargs):
            pytest.fail("Untrusted download must not run")

    class UnsafeSubclass(ProviderHttpClient):
        connection_safe = True

        async def download(self, *args, **kwargs):
            pytest.fail("Overridden download must not run")

    subclass = UnsafeSubclass(snapshot(NetworkMode.RETRIEVAL), db, Limits(), secrets={})
    try:
        for http in (ClaimedSafe(), subclass):
            result = await DefaultRetriever(fake_store.files).retrieve(
                "prj_a", work(), [location()], http=http, is_cancelled=lambda: False
            )
            assert result.error["code"] == "INTEGRATION_UNAVAILABLE" and result.work_file is None
    finally:
        await subclass.aclose()


def test_download_test_transport_must_be_explicit_known_mock(db):
    class Arbitrary(httpx.AsyncBaseTransport):
        connection_safe = True

    with pytest.raises(IntegrationUnavailable):
        ProviderHttpClient(
            snapshot(NetworkMode.RETRIEVAL), db, Limits(), download_transport=Arbitrary(), secrets={}
        )


@pytest.mark.anyio
async def test_default_download_uses_pinned_transport_not_metadata_pool(db, tcp, tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    monkeypatch.setenv("ALL_PROXY", "http://169.254.169.254:9999")
    monkeypatch.setenv("NO_PROXY", "")
    resolve, calls = dns("8.8.8.8")
    metadata_calls = []

    def metadata(request):
        metadata_calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    http = ProviderHttpClient(
        snapshot(NetworkMode.RETRIEVAL), db, Limits(), transport=httpx.MockTransport(metadata), secrets={}
    )
    path = tmp_path / "safe.pdf"
    try:
        assert (await http.get("https://repo.example/metadata", provider="test")).status == 200
        result = await http.download(
            "https://repo.example/a",
            dest=path,
            max_bytes=1000,
            timeout_s=5,
            max_redirects=5,
            guard=DefaultNetGuard(resolve=resolve),
        )
        assert result.final_url == "https://repo.example/a" and path.read_bytes() == b"%PDF-1.7"
    finally:
        await http.aclose()
    assert metadata_calls == ["https://repo.example/metadata"]
    assert calls == [("repo.example", 443)] and tcp.connects == [("8.8.8.8", 443)]
    assert tcp.streams[0].closed


@pytest.mark.anyio
async def test_redirect_rebinding_cannot_reuse_original_connection(db, tcp, tmp_path):
    tcp.responses.append(wire(b"", status=302, headers=["Location: /next"]))
    calls = []

    async def resolve(host, port):
        calls.append(host)
        return ["8.8.8.8"] if len(calls) == 1 else ["127.0.0.1"]

    http = ProviderHttpClient(snapshot(NetworkMode.RETRIEVAL), db, Limits(), secrets={})
    dest = tmp_path / "no.pdf"
    try:
        with pytest.raises(BlockedAddress):
            await http.download(
                "https://repo.example/a",
                dest=dest,
                max_bytes=1000,
                timeout_s=5,
                max_redirects=5,
                guard=DefaultNetGuard(resolve=resolve),
            )
    finally:
        await http.aclose()
    assert calls == ["repo.example", "repo.example"] and tcp.connects == [("8.8.8.8", 443)]
    assert tcp.streams[0].closed and not dest.exists()


@pytest.mark.anyio
async def test_cancellation_during_boundary_resolution_never_connects(db, tcp, tmp_path):
    started = asyncio.Event()

    async def resolve(host, port):
        started.set()
        await asyncio.Event().wait()

    http = ProviderHttpClient(snapshot(NetworkMode.RETRIEVAL), db, Limits(), secrets={})
    dest = tmp_path / "cancel.pdf"
    task = asyncio.create_task(
        http.download(
            "https://repo.example/a",
            dest=dest,
            max_bytes=1000,
            timeout_s=5,
            max_redirects=5,
            guard=DefaultNetGuard(resolve=resolve),
        )
    )
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert tcp.connects == [] and not dest.exists()
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_default_retriever_runs_actual_pinning_without_callback(db, fake_store, tcp):
    tcp.responses.append(wire(paper_pdf(TITLE, [], DOI)))
    resolve, calls = dns("8.8.8.8")
    http = ProviderHttpClient(snapshot(NetworkMode.RETRIEVAL), db, Limits(), secrets={})
    try:
        result = await DefaultRetriever(fake_store.files, guard=DefaultNetGuard(resolve=resolve)).retrieve(
            "prj_a", work(), [location()], http=http, is_cancelled=lambda: False
        )
        assert result.state == RetrievalState.VERIFIED
        assert fake_store.files.has_grant("prj_a", result.work_file.sha256)
        assert result.work_file.source_url == result.work_file.final_url == "https://repo.example/one"
    finally:
        await http.aclose()
    assert calls == [("repo.example", 443)] and tcp.connects == [("8.8.8.8", 443)]
    assert tcp.streams[0].closed and list(fake_store.files.tmp.iterdir()) == []


@pytest.mark.anyio
async def test_custom_allow_guard_cannot_bypass_address_policy(tcp):
    class AllowAll:
        async def check(self, url):
            pass

    async with httpx.AsyncClient(transport=PinnedDownloadTransport(AllowAll())) as client:
        with pytest.raises(BlockedAddress):
            await client.get("https://127.0.0.1/a")
    assert tcp.connects == []


@pytest.mark.anyio
async def test_connect_failure_falls_back_only_within_checked_addresses(tcp):
    tcp.connect_error = deque([httpcore.ConnectError("first unavailable"), None])
    resolve, calls = dns("8.8.8.8", "1.1.1.1")
    async with httpx.AsyncClient(
        transport=PinnedDownloadTransport(DefaultNetGuard(resolve=resolve))
    ) as client:
        assert (await client.get("https://repo.example/a")).status_code == 200
    assert calls == [("repo.example", 443)]
    assert tcp.connects == [("8.8.8.8", 443), ("1.1.1.1", 443)]


@pytest.mark.anyio
async def test_changed_cancel_flag_after_dns_prevents_connect(db, tcp, tmp_path):
    cancelled = False

    async def resolve(host, port):
        nonlocal cancelled
        cancelled = True
        return ["8.8.8.8"]

    http = ProviderHttpClient(snapshot(NetworkMode.RETRIEVAL), db, Limits(), secrets={})
    dest = tmp_path / "cancelled.pdf"
    try:
        with pytest.raises(Cancelled):
            await http.download(
                "https://repo.example/a",
                dest=dest,
                max_bytes=1000,
                timeout_s=5,
                max_redirects=5,
                guard=DefaultNetGuard(resolve=resolve),
                is_cancelled=lambda: cancelled,
            )
    finally:
        await http.aclose()
    assert tcp.connects == [] and not dest.exists()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["connect", "read", "oversized", "dns_timeout"])
async def test_real_transport_failure_cleans_download(db, tcp, tmp_path, failure):
    if failure == "connect":
        tcp.connect_error = httpcore.ConnectError("connect failed")
    if failure == "read":
        tcp.responses.append(httpcore.ReadError("interrupted"))
    if failure == "oversized":
        tcp.responses.append(wire(b"x" * 11))

    async def resolve(host, port):
        if failure == "dns_timeout":
            await asyncio.sleep(0.1)
        return ["8.8.8.8"]

    http = ProviderHttpClient(snapshot(NetworkMode.RETRIEVAL), db, Limits(), secrets={})
    dest = tmp_path / "failure.pdf"
    try:
        expected = LimitExceeded if failure == "oversized" else TransientNetworkError
        with pytest.raises(expected):
            await http.download(
                "https://repo.example/a",
                dest=dest,
                max_bytes=10,
                timeout_s=0.02 if failure == "dns_timeout" else 5,
                max_redirects=5,
                guard=DefaultNetGuard(resolve=resolve),
            )
    finally:
        await http.aclose()
    assert not dest.exists() and all(stream.closed for stream in tcp.streams)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "target,answers",
    [
        ("https://127.0.0.1/private", ["8.8.8.8"]),
        ("https://[::ffff:127.0.0.1]/private", ["8.8.8.8"]),
        ("https://mixed.example/a", ["8.8.8.8", "10.0.0.1"]),
        ("http://8.8.8.8/a", ["8.8.8.8"]),
    ],
)
async def test_redirect_disallowed_target_never_reaches_tcp(db, tcp, tmp_path, target, answers):
    tcp.responses.append(wire(b"", status=302, headers=["Location: " + target]))

    async def resolve(host, port):
        return ["8.8.8.8"] if host == "repo.example" else answers

    http = ProviderHttpClient(snapshot(NetworkMode.RETRIEVAL), db, Limits(), secrets={})
    dest = tmp_path / "redirect.pdf"
    try:
        with pytest.raises(NotAuthorized):
            await http.download(
                "https://repo.example/a",
                dest=dest,
                max_bytes=1000,
                timeout_s=5,
                max_redirects=5,
                guard=DefaultNetGuard(resolve=resolve),
            )
    finally:
        await http.aclose()
    assert tcp.connects == [("8.8.8.8", 443)]
    assert tcp.streams[0].closed and not dest.exists()
