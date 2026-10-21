"""Configured PDF fallback stays bounded and uses guarded retrieval only."""

import asyncio

import httpx
import pytest

from refcollector.config import Limits
from refcollector.domain.enums import LocationBasis, NetworkMode
from refcollector.errors import (
    BlockedAddress,
    Cancelled,
    InvalidInput,
    LimitExceeded,
    NotAuthorized,
    TransientNetworkError,
)
from refcollector.files.netguard import DefaultNetGuard
from refcollector.files.pdf_fallback import DoiPdfFallback, validate_template
from refcollector.providers.http import ProviderHttpClient
from tests.unit.files.test_guard import resolver
from tests.unit.providers.conftest import snapshot

TEMPLATE = "https://resolver.example/paper/{doi}"


@pytest.mark.parametrize(
    "template",
    [
        "http://resolver.example/{doi}",
        "https://{doi}.example/",
        "https://user@host.example/{doi}",
        "https://host.example/{doi}?x=1",
        "https://host.example/{doi}#x",
        "https://host.example/{doi}/{doi}",
        "https://host.example/{other}",
    ],
)
def test_invalid_templates(template):
    with pytest.raises(InvalidInput):
        validate_template(template)


def test_template_does_not_require_dns():
    assert validate_template(TEMPLATE) == TEMPLATE


async def run(fake_store, db, handler, *, limits=None, cancelled=lambda: False):
    client = ProviderHttpClient(
        snapshot(NetworkMode.RETRIEVAL),
        db,
        Limits(),
        download_transport=httpx.MockTransport(handler),
        secrets={},
    )
    try:
        return await DoiPdfFallback(
            fake_store.files, limits=limits, guard=DefaultNetGuard(resolve=resolver("8.8.8.8"))
        ).resolve(TEMPLATE, "10.1234/a/../b", http=client, is_cancelled=cancelled)
    finally:
        await client.aclose()
        assert list(fake_store.files.tmp.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tag,attribute,src,expected",
    [
        ("iframe", "src", "../paper.pdf#page=2", "https://resolver.example/paper.pdf"),
        ("embed", "src", "//cdn.example/a.pdf?a=1&amp;b=2", "https://cdn.example/a.pdf?a=1&b=2"),
        ("object", "data", "/a.pdf", "https://resolver.example/a.pdf"),
    ],
)
async def test_selectors_and_encoded_doi(fake_store, db, tag, attribute, src, expected):
    def handler(request):
        assert request.url.raw_path == b"/paper/10.1234%2Fa%2F..%2Fb"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=f'<{tag} id="pdf" {attribute}="{src}"></{tag}>',
        )

    result = await run(fake_store, db, handler)
    assert result.url == expected and result.eligible and result.is_pdf
    assert result.provider == "configured_fallback"
    assert result.basis == LocationBasis.CONFIGURED_FALLBACK and result.licence is None


@pytest.mark.anyio
async def test_redirected_base(fake_store, db):
    def handler(request):
        if request.url.path.startswith("/paper/"):
            return httpx.Response(302, headers={"Location": "/new/base/"})
        return httpx.Response(
            200, headers={"Content-Type": "text/html"}, text='<iframe id="pdf" src="a.pdf">'
        )

    result = await run(fake_store, db, handler)
    assert result.url == "https://resolver.example/new/base/a.pdf"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,content_type,body",
    [
        (404, "text/html", '<iframe id="pdf" src="a.pdf">'),
        (200, None, '<iframe id="pdf" src="a.pdf">'),
        (200, "application/pdf", '<iframe id="pdf" src="a.pdf">'),
        (200, "text/html", '<h1>CAPTCHA</h1><script>pdf.src="a.pdf"</script>'),
        (200, "text/html", '<iframe id="other" src="a.pdf">'),
    ],
)
async def test_no_pdf(fake_store, db, status, content_type, body):
    headers = {"Content-Type": content_type} if content_type else {}
    assert (
        await run(fake_store, db, lambda _: httpx.Response(status, headers=headers, content=body.encode()))
        is None
    )


@pytest.mark.anyio
@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://example.org/a.pdf"])
async def test_bad_pdf_scheme(fake_store, db, url):
    with pytest.raises(NotAuthorized):
        await run(
            fake_store,
            db,
            lambda _: httpx.Response(
                200, headers={"Content-Type": "text/html"}, text=f'<iframe id="pdf" src="{url}">'
            ),
        )


@pytest.mark.anyio
async def test_private_redirect(fake_store, db):
    with pytest.raises(BlockedAddress):
        await run(fake_store, db, lambda _: httpx.Response(302, headers={"Location": "https://127.0.0.1/"}))


@pytest.mark.anyio
async def test_size_cancel_and_timeout_cleanup(fake_store, db):
    with pytest.raises(LimitExceeded):
        await run(
            fake_store, db, lambda _: httpx.Response(200, content=b"x" * 11), limits=Limits(download_bytes=10)
        )
    with pytest.raises(Cancelled):
        await run(fake_store, db, lambda _: pytest.fail("unexpected network"), cancelled=lambda: True)
    state = [False]

    def handler(_):
        state[0] = True
        return httpx.Response(200, text="ok")

    with pytest.raises(Cancelled):
        await run(fake_store, db, handler, cancelled=lambda: state[0])

    async def slow(_):
        await asyncio.sleep(1)
        return httpx.Response(200)

    with pytest.raises((TimeoutError, TransientNetworkError)):
        await run(fake_store, db, slow, limits=Limits(download_timeout_s=0.01))


@pytest.mark.anyio
async def test_private_pdf_endpoint(fake_store, db):
    with pytest.raises(BlockedAddress):
        await run(
            fake_store,
            db,
            lambda _: httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text='<iframe id="pdf" src="https://127.0.0.1/a.pdf">',
            ),
        )


@pytest.mark.anyio
async def test_default_html_size_cap(fake_store, db):
    with pytest.raises(LimitExceeded):
        await run(fake_store, db, lambda _: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))
