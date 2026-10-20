"""ProviderHttpClient: cache, rate limits, network modes, downloads (spec 6.5, 6.6, 13)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

import refcollector
from refcollector.domain.enums import NetworkMode
from refcollector.errors import (
    BlockedAddress,
    Cancelled,
    LimitExceeded,
    NetworkModeDenied,
    ProviderUnavailable,
    RateLimited,
    TransientNetworkError,
)
from refcollector.providers.cache import fingerprint
from tests.unit.providers.conftest import EMAIL

pytestmark = pytest.mark.anyio

URL = "https://api.crossref.org/works/10.1000/x"


# ---------------------------------------------------------------- get()


async def test_user_agent_has_mailto(client, respx_mock) -> None:
    route = respx_mock.get(URL).respond(200, json={"ok": 1})
    await client.get(URL, provider="crossref")
    ua = route.calls.last.request.headers["user-agent"]
    assert ua == f"refcollector/{refcollector.__version__} (mailto:{EMAIL})"


async def test_at08a_429_then_success_honours_retry_after(client, respx_mock, fake_sleep) -> None:
    route = respx_mock.get(URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={"a": 1})]
    )
    resp = await client.get(URL, provider="crossref")
    assert resp.status == 200 and resp.json() == {"a": 1}
    assert fake_sleep.calls == [2]
    assert route.call_count == 2


async def test_429_until_attempts_run_out_raises_rate_limited(client, respx_mock) -> None:
    respx_mock.get(URL).respond(429, headers={"Retry-After": "5"})
    with pytest.raises(RateLimited) as err:
        await client.get(URL, provider="crossref")
    assert err.value.retryable and err.value.retry_after == 5


async def test_long_retry_after_blocks_host(client, respx_mock) -> None:
    route = respx_mock.get(URL).respond(429, headers={"Retry-After": "3600"})
    with pytest.raises(ProviderUnavailable) as err:
        await client.get(URL, provider="crossref")
    assert err.value.retry_after == 3600
    assert "api.crossref.org" in client.blocked_hosts
    with pytest.raises(ProviderUnavailable):
        await client.get(URL + "?again=1", provider="crossref")
    assert route.call_count == 1


async def test_retry_after_http_date(client, respx_mock) -> None:
    later = format_datetime(datetime.now(UTC) + timedelta(hours=2), usegmt=True)
    respx_mock.get(URL).respond(429, headers={"Retry-After": later})
    with pytest.raises(ProviderUnavailable):
        await client.get(URL, provider="crossref")
    assert "api.crossref.org" in client.blocked_hosts


async def test_at20_offline_makes_no_request(make_client, respx_mock, tmp_path) -> None:
    route = respx_mock.get(URL).respond(200, json={})
    offline = make_client(NetworkMode.OFFLINE)
    with pytest.raises(NetworkModeDenied):
        await offline.get(URL, provider="crossref")
    with pytest.raises(NetworkModeDenied):
        await _dl(offline, tmp_path / "a.pdf", url=URL)
    assert route.call_count == 0
    assert respx_mock.calls.call_count == 0


async def test_offline_serves_cache_only(make_client, respx_mock) -> None:
    route = respx_mock.get(URL).respond(200, json={"c": 1})
    await make_client().get(URL, provider="crossref")
    resp = await make_client(NetworkMode.OFFLINE).get(URL, provider="crossref")
    assert resp.from_cache and resp.json() == {"c": 1}
    assert route.call_count == 1


async def test_cache_hit_sends_no_second_request(client, respx_mock) -> None:
    route = respx_mock.get(URL).respond(200, json={"c": 1})
    first = await client.get(URL, provider="crossref", params={"b": 2, "a": 1})
    second = await client.get(URL, provider="crossref", params={"a": 1, "b": 2})
    assert not first.from_cache and second.from_cache
    assert second.json() == {"c": 1} and second.status == 200
    assert route.call_count == 1
    await client.get(URL, provider="crossref", params={"a": 2})
    await client.get(URL, provider="crossref", params={"a": 1, "b": 2}, headers={"Accept": "text/x"})
    assert route.call_count == 3


async def test_cache_expiry(client, respx_mock) -> None:
    route = respx_mock.get(URL).respond(200, json={})
    await client.get(URL, provider="crossref", cache_ttl_s=0)
    second = await client.get(URL, provider="crossref", cache_ttl_s=0)
    assert not second.from_cache
    assert route.call_count == 2


async def test_404_returned_and_cached_400_not_cached(client, respx_mock) -> None:
    r404 = respx_mock.get(URL).respond(404)
    assert (await client.get(URL, provider="crossref")).status == 404
    assert (await client.get(URL, provider="crossref")).from_cache
    assert r404.call_count == 1
    r400 = respx_mock.get(URL + "/bad").respond(400)
    assert (await client.get(URL + "/bad", provider="crossref")).status == 400
    await client.get(URL + "/bad", provider="crossref")
    assert r400.call_count == 2


@pytest.mark.parametrize("status", [408, 500, 503])
async def test_5xx_and_408_are_transient(client, respx_mock, status) -> None:
    route = respx_mock.get(URL).respond(status)
    with pytest.raises(TransientNetworkError) as err:
        await client.get(URL, provider="crossref")
    assert err.value.retryable
    with pytest.raises(TransientNetworkError):
        await client.get(URL, provider="crossref")
    assert route.call_count == 2  # not cached


async def test_connect_error_and_timeout_are_transient(client, respx_mock) -> None:
    respx_mock.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(TransientNetworkError):
        await client.get(URL, provider="crossref")
    respx_mock.get(URL + "/t").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(TransientNetworkError):
        await client.get(URL + "/t", provider="crossref")


async def test_api_keys_sent_but_not_cached_or_returned(make_client, respx_mock, db) -> None:
    client = make_client(secrets={"openalex_key": "OAKEY", "s2_key": "S2KEY"})
    oa = respx_mock.get("https://api.openalex.org/works/W1").respond(200, json={})
    s2 = respx_mock.get("https://api.semanticscholar.org/graph/v1/paper/X").respond(200, json={})
    resp = await client.get("https://api.openalex.org/works/W1", provider="openalex", params={"q": 1})
    await client.get("https://api.semanticscholar.org/graph/v1/paper/X", provider="semanticscholar")
    assert oa.calls.last.request.url.params["api_key"] == "OAKEY"
    assert s2.calls.last.request.headers["x-api-key"] == "S2KEY"
    assert "OAKEY" not in resp.url
    rows = db.query("SELECT fingerprint, url, headers FROM response_cache")
    assert rows and not any("KEY" in (r["url"] + r["headers"]) for r in rows)
    expected = fingerprint("GET", "https://api.openalex.org/works/W1", {"q": 1}, None)
    assert expected in {r["fingerprint"] for r in rows}


# ---------------------------------------------------------------- download()

PDF = "https://repo.example.org/paper.pdf"


class _Guard:
    def __init__(self, deny: str | None = None) -> None:
        self.checked: list[str] = []
        self.deny = deny

    async def check(self, url: str) -> None:
        self.checked.append(url)
        if url == self.deny:
            raise BlockedAddress("private address")


async def _chunks(*parts: bytes, delay: float = 0) -> AsyncIterator[bytes]:
    for part in parts:
        if delay:
            await asyncio.sleep(delay)
        yield part


class _Stream(httpx.AsyncByteStream):
    """Explicit MockTransport requires the public HTTPX stream interface."""

    def __init__(self, parts: AsyncIterator[bytes]) -> None:
        self.parts = parts

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for part in self.parts:
            yield part


def _dl(client, dest, guard=None, *, url=PDF, **kw):
    args = {"max_bytes": 1000, "timeout_s": 5.0, "max_redirects": 3, **kw}
    return client.download(url, dest=dest, guard=guard or _Guard(), **args)


async def test_download_success(make_client, respx_mock, tmp_path) -> None:
    respx_mock.get(PDF).respond(200, content=b"%PDF-1.7 body", headers={"content-type": "application/pdf"})
    guard = _Guard()
    dest = tmp_path / "a.pdf"
    res = await _dl(make_client(NetworkMode.RETRIEVAL), dest, guard)
    assert res.status == 200 and res.bytes == 13 and res.path == dest
    assert res.content_type == "application/pdf" and res.final_url == PDF
    assert dest.read_bytes() == b"%PDF-1.7 body"
    assert guard.checked == [PDF]


async def test_download_requires_retrieval_mode(client, respx_mock, tmp_path) -> None:
    route = respx_mock.get(PDF).respond(200, content=b"x")
    with pytest.raises(NetworkModeDenied):
        await _dl(client, tmp_path / "a.pdf")
    assert route.call_count == 0


async def test_download_too_large_leaves_no_file(make_client, respx_mock, tmp_path) -> None:
    respx_mock.get(PDF).respond(200, content=b"x" * 100)
    respx_mock.get(PDF + "?s").respond(200, stream=_Stream(_chunks(b"x" * 8, b"x" * 8)))
    client = make_client(NetworkMode.RETRIEVAL)
    dest = tmp_path / "a.pdf"
    with pytest.raises(LimitExceeded):
        await _dl(client, dest, max_bytes=10)
    assert not dest.exists()
    with pytest.raises(LimitExceeded):
        await _dl(client, dest, max_bytes=10, url=PDF + "?s")
    assert not dest.exists()


async def test_download_redirect_checked_by_guard(make_client, respx_mock, tmp_path) -> None:
    target = "https://cdn.example.org/files/paper.pdf"
    respx_mock.get(PDF).respond(302, headers={"location": target})
    respx_mock.get(target).respond(200, content=b"%PDF")
    guard = _Guard()
    res = await _dl(make_client(NetworkMode.RETRIEVAL), tmp_path / "a.pdf", guard)
    assert guard.checked == [PDF, target] and res.final_url == target


async def test_download_guard_blocks_redirect(make_client, respx_mock, tmp_path) -> None:
    target = "https://169.254.169.254/latest"
    respx_mock.get(PDF).respond(302, headers={"location": target})
    blocked = respx_mock.get(target).respond(200, content=b"secret")
    dest = tmp_path / "a.pdf"
    with pytest.raises(BlockedAddress):
        await _dl(make_client(NetworkMode.RETRIEVAL), dest, _Guard(deny=target))
    assert blocked.call_count == 0 and not dest.exists()


async def test_download_too_many_redirects(make_client, respx_mock, tmp_path) -> None:
    route = respx_mock.get(PDF).respond(302, headers={"location": PDF})
    dest = tmp_path / "a.pdf"
    with pytest.raises(LimitExceeded):
        await _dl(make_client(NetworkMode.RETRIEVAL), dest, max_redirects=2)
    assert route.call_count == 3 and not dest.exists()


async def test_download_cancelled_mid_stream(make_client, respx_mock, tmp_path) -> None:
    respx_mock.get(PDF).respond(200, stream=_Stream(_chunks(b"%PDF", b"more", b"end")))
    seen = iter([False, False, True])  # pre-request check, first chunk, second chunk
    dest = tmp_path / "a.pdf"
    with pytest.raises(Cancelled):
        await _dl(make_client(NetworkMode.RETRIEVAL), dest, is_cancelled=lambda: next(seen, True))
    assert not dest.exists()


async def test_download_timeout_is_transient(make_client, respx_mock, tmp_path) -> None:
    respx_mock.get(PDF).respond(200, stream=_Stream(_chunks(b"%PDF", b"late", delay=0.2)))
    dest = tmp_path / "a.pdf"
    with pytest.raises(TransientNetworkError):
        await _dl(make_client(NetworkMode.RETRIEVAL), dest, timeout_s=0.05)
    assert not dest.exists()


async def test_download_5xx_is_transient(make_client, respx_mock, tmp_path) -> None:
    respx_mock.get(PDF).respond(503)
    dest = tmp_path / "a.pdf"
    with pytest.raises(TransientNetworkError):
        await _dl(make_client(NetworkMode.RETRIEVAL), dest)
    assert not dest.exists()
