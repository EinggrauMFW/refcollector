"""Browser fallback proof without SeleniumBase or external sockets."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from refcollector.config import Limits
from refcollector.errors import BlockedAddress, Cancelled, IntegrationUnavailable
from refcollector.files import browser_fallback as module
from refcollector.files.browser_worker import resolve_page
from refcollector.files.netguard import DefaultNetGuard
from refcollector.files.pdf_fallback import _PdfParser

CHALLENGE = '<h1>Are you a robot?</h1><input type="checkbox" id="altcha_checkbox_123"><label>No</label>'
ARTICLE = '<a href="/storage/2024/hash/paper.pdf">download</a>'
URL = "https://resolver.example/10.1234/test"


class Browser:
    def __init__(self, *, title="Sci-Hub: are you are robot?", html=CHALLENGE, clears=True):
        self.title, self.html, self.clears = title, html, clears
        self.clicks = []

    def get_page_source(self):
        return self.html

    def get_title(self):
        return self.title

    def get_user_agent(self):
        return "Mozilla/5.0 TestBrowser"

    def get_current_url(self):
        return URL

    def click(self, selector):
        self.clicks.append(selector)
        if self.clears:
            self.html, self.title = ARTICLE, "Sci-Hub. Expected article"


def test_inspected_dynamic_checkbox_clicks_once_and_waits():
    browser = Browser()
    waits = []
    result = resolve_page(browser, sleep=waits.append)
    assert browser.clicks == ["#altcha_checkbox_123"]
    assert waits == [12]
    assert result["clicked"] and result["html"] == ARTICLE


def test_unsolved_checkbox_is_not_clicked_repeatedly():
    browser = Browser(clears=False)
    result = resolve_page(browser, sleep=lambda _: None)
    assert browser.clicks == ["#altcha_checkbox_123"]
    assert result["html"] == CHALLENGE


@pytest.mark.parametrize(
    "browser", [Browser(title="Different challenge"), Browser(html=CHALLENGE + CHALLENGE)]
)
def test_unknown_or_ambiguous_control_is_not_clicked(browser):
    resolve_page(browser, sleep=lambda _: None)
    assert browser.clicks == []


def test_existing_article_needs_no_verification():
    browser = Browser(title="Article", html=ARTICLE)
    assert resolve_page(browser, sleep=lambda _: None)["clicked"] is False
    assert not browser.clicks


def test_storage_link_parser_ignores_unrelated_links():
    parser = _PdfParser()
    parser.feed('<a href="/meta/paper">metadata</a>' + ARTICLE)
    assert parser.url == "/storage/2024/hash/paper.pdf"


class Proxy:
    address = "127.0.0.1:9999"
    limit_exceeded = False

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr(module, "BrowserProxy", Proxy)

    async def dns(host, port):
        return ["127.0.0.1"] if host == "private.example" else ["8.8.8.8"]

    return module.BrowserPdfFallback(
        python=sys.executable, binary=sys.executable, limits=Limits(), guard=DefaultNetGuard(resolve=dns)
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "html,expected", [(ARTICLE, "https://resolver.example/storage/2024/hash/paper.pdf"), (CHALLENGE, None)]
)
async def test_result_returns_validated_unknown_version_location(resolver, html, expected):
    async def child(request, seconds, cancel):
        (request.parent / "result.json").write_text(json.dumps({"url": URL, "html": html}))

    resolver._run = child
    result = await resolver.resolve(
        "https://resolver.example/{doi}", "10.1234/test", is_cancelled=lambda: False
    )
    assert (result.url if result else None) == expected
    if result:
        assert result.provider == "configured_browser_fallback"
        assert result.version == "unknown" and result.licence is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url,html,error",
    [
        ("https://other.example/", ARTICLE, IntegrationUnavailable),
        (URL, '<iframe id="pdf" src="https://private.example/p.pdf">', BlockedAddress),
    ],
)
async def test_result_cannot_authorize_other_origin_or_private_pdf(resolver, url, html, error):
    async def child(request, seconds, cancel):
        (request.parent / "result.json").write_text(json.dumps({"url": url, "html": html}))

    resolver._run = child
    with pytest.raises(error):
        await resolver.resolve("https://resolver.example/{doi}", "10.1234/test", is_cancelled=lambda: False)


@pytest.mark.anyio
async def test_subprocess_protocol_uses_isolated_python_and_removes_scratch(resolver, tmp_path, monkeypatch):
    worker = tmp_path / "browser_worker.py"
    worker.write_text(
        "import sys,json,os,time;from pathlib import Path;p=Path(sys.argv[1]);"
        '(p.parent/"ready").write_text(json.dumps({"pid":os.getpid(),"runtime":"test-native"}));'
        '\nwhile not (p.parent/"start").exists():time.sleep(.01)\n'
        '(p.parent/"result.json").write_text(json.dumps({"url":"'
        + URL
        + '","html":'
        + repr(ARTICLE)
        + "}));(Path("
        + repr(str(tmp_path / "scratch_path"))
        + ")).write_text(str(p.parent))"
    )
    monkeypatch.setattr(module, "__file__", str(tmp_path / "browser_fallback.py"))
    result = await resolver.resolve(
        "https://resolver.example/{doi}", "10.1234/test", is_cancelled=lambda: False
    )
    assert result.url.endswith("paper.pdf")
    scratch = Path((tmp_path / "scratch_path").read_text())
    assert not await asyncio.to_thread(scratch.exists)


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timed_out_or_cancelled_child_is_reaped(resolver, tmp_path, monkeypatch, cancel):
    (tmp_path / "browser_worker.py").write_text("import time;time.sleep(60)")
    monkeypatch.setattr(module, "__file__", str(tmp_path / "browser_fallback.py"))
    request = tmp_path / "request.json"
    request.write_text("{}")
    with pytest.raises(Cancelled if cancel else TimeoutError):
        await resolver._run(request, 0.3, lambda: cancel)


@pytest.mark.anyio
async def test_existing_nonexecutable_python_has_typed_failure(resolver, tmp_path):
    invalid = tmp_path / "not_python.txt"
    invalid.write_text("not an executable")
    resolver.python = str(invalid)
    with pytest.raises(IntegrationUnavailable, match="could not start"):
        await resolver._run(tmp_path / "request.json", 1, lambda: False)


@pytest.mark.anyio
async def test_child_stops_even_when_worker_exits_nonzero(resolver, tmp_path, monkeypatch):
    marker = tmp_path / "survived"
    payload = (
        "import time;from pathlib import Path;time.sleep(1);Path("
        + repr(str(marker))
        + ').write_text("orphan")'
    )
    code = """import sys,time,subprocess,os,json
from pathlib import Path
(Path(sys.argv[1]).parent/'ready').write_text(json.dumps({'pid':os.getpid(),'runtime':'test-native'}))
while not (Path(sys.argv[1]).parent/'start').exists():time.sleep(.01)
subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c',PAYLOAD])
sys.exit(1)
""".replace("PAYLOAD", repr(payload))
    (tmp_path / "browser_worker.py").write_text(code)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "browser_fallback.py"))
    with pytest.raises(IntegrationUnavailable):
        await resolver._run(tmp_path / "request.json", 3, lambda: False)
    await asyncio.sleep(1.3)
    assert not await asyncio.to_thread(marker.exists)


@pytest.mark.anyio
async def test_browser_user_agent_is_scoped_to_one_pdf_and_restored(db, tmp_path):
    import httpx

    from refcollector.domain.enums import NetworkMode
    from refcollector.providers.http import ProviderHttpClient
    from tests.unit.providers.conftest import snapshot

    seen = []

    def response(request):
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, content=b"%PDF", headers={"Content-Type": "application/pdf"})

    async def dns(host, port):
        return ["8.8.8.8"]

    client = ProviderHttpClient(
        snapshot(NetworkMode.RETRIEVAL),
        db,
        Limits(),
        download_transport=httpx.MockTransport(response),
        secrets={},
    )

    async def download(url):
        await client.download(
            url,
            dest=tmp_path / "p.pdf",
            max_bytes=100,
            timeout_s=2,
            max_redirects=1,
            guard=DefaultNetGuard(resolve=dns),
        )

    try:
        with client.browser_pdf_profile(URL, "Mozilla/5.0 Browser"):
            await download(URL)
            await download("https://other.example/p.pdf")
        await download(URL)
        with pytest.raises(RuntimeError):
            with client.browser_pdf_profile(URL, "Mozilla/5.0 Browser"):
                raise RuntimeError("cleanup")
        await download(URL)
        assert seen[0] == "Mozilla/5.0 Browser"
        assert all(agent.startswith("refcollector/") for agent in seen[1:])
        with pytest.raises(IntegrationUnavailable):
            with client.browser_pdf_profile(URL, "bad\r\nHeader: injected"):
                pass
    finally:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows packaged-runtime restriction")
async def test_store_runtime_rejected_before_browser_start(resolver, tmp_path, monkeypatch):
    code = """import json,sys,os,time
from pathlib import Path
p=Path(sys.argv[1]).parent
runtime='C:/Program Files/WindowsApps/Python/python.exe'
(p/'ready').write_text(json.dumps({'pid':os.getpid(),'runtime':runtime}))
while not (p/'start').exists():time.sleep(.01)
(p/'should_not_start').write_text('unsafe')
"""
    (tmp_path / "browser_worker.py").write_text(code)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "browser_fallback.py"))
    with pytest.raises(IntegrationUnavailable, match="Microsoft Store"):
        await resolver._run(tmp_path / "request.json", 3, lambda: False)
    assert not await asyncio.to_thread((tmp_path / "should_not_start").exists)
