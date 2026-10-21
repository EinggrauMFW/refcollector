"""Bounded browser link resolution followed by the normal pinned PDF download."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote, urldefrag, urljoin, urlsplit

from refcollector.domain.enums import LocationBasis
from refcollector.domain.models import AccessLocation
from refcollector.errors import IntegrationUnavailable, LimitExceeded
from refcollector.identity.identifiers import normalize_doi
from refcollector.ids import new_id

from .browser_proxy import BrowserProxy
from .netguard import DefaultNetGuard, validate_destination
from .pdf_fallback import _PdfParser, validate_template
from .process_tree import ProcessTree
from .retrieval import _cancel


class BrowserPdfFallback:
    def __init__(self, *, python: str, binary: str, limits, guard=None):
        self.python, self.binary, self.limits = python, binary, limits
        self.guard = guard or DefaultNetGuard()
        self.user_agent = None
        if not Path(self.python).is_file() or not Path(self.binary).is_file():
            raise IntegrationUnavailable("Configured browser Python or Chromium binary is unavailable")

    async def resolve(self, template, doi, *, is_cancelled):
        _cancel(is_cancelled)
        validate_template(template)
        normalized = normalize_doi(doi)
        if normalized is None:
            raise IntegrationUnavailable("Browser resolver requires a valid DOI")
        url = template.replace("{doi}", quote(normalized, safe=""))
        validate_destination(url)
        await self.guard.check(url)
        timeout = min(90.0, self.limits.download_timeout_s)
        with tempfile.TemporaryDirectory(prefix="rc_pdf_browser_") as folder:
            root = Path(folder)
            async with BrowserProxy(max_bytes=self.limits.download_bytes, is_cancelled=is_cancelled) as proxy:
                request = root / "request.json"
                request.write_text(
                    json.dumps({"url": url, "binary": self.binary, "proxy": proxy.address}), encoding="utf-8"
                )
                await self._run(request, timeout, is_cancelled)
                if proxy.limit_exceeded:
                    raise LimitExceeded("Browser network byte limit exceeded")
                _cancel(is_cancelled)
                result_path = root / "result.json"
                if not result_path.is_file():
                    raise IntegrationUnavailable("Browser resolver produced no result")
                if result_path.stat().st_size > 2 * 1024 * 1024:
                    raise LimitExceeded("Browser result exceeds byte limit")
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    if not isinstance(result, dict) or not all(
                        isinstance(result.get(key), str) for key in ("url", "html")
                    ):
                        raise ValueError("invalid browser result")
                except (ValueError, UnicodeError) as error:
                    raise IntegrationUnavailable("Browser resolver returned invalid data") from error
        # Browser navigation cannot authorize a different origin or a private PDF.
        validate_destination(result["url"])
        if urlsplit(result["url"]).netloc != urlsplit(url).netloc:
            raise IntegrationUnavailable("Browser resolver left the configured origin")
        parser = _PdfParser()
        parser.feed(result["html"])
        if not parser.url:
            return None
        pdf_url = urldefrag(urljoin(result["url"], parser.url))[0]
        validate_destination(pdf_url)
        await self.guard.check(pdf_url)
        _cancel(is_cancelled)
        self.user_agent = result.get("user_agent")
        return AccessLocation(
            location_id=new_id("location"),
            url=pdf_url,
            provider="configured_browser_fallback",
            basis=LocationBasis.CONFIGURED_FALLBACK,
            eligible=True,
            is_pdf=True,
        )

    async def _run(self, request, seconds, is_cancelled):
        process = None
        tree = ProcessTree()
        try:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.python,
                    "-I",
                    str(Path(__file__).with_name("browser_worker.py")),
                    str(request),
                    cwd=str(request.parent),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=(os.name != "nt"),
                )
            except OSError as error:
                raise IntegrationUnavailable("Configured browser Python could not start") from error
            async with asyncio.timeout(seconds):
                ready = request.parent / "ready"
                while not await asyncio.to_thread(ready.is_file):
                    _cancel(is_cancelled)
                    if process.returncode is not None:
                        raise IntegrationUnavailable("Browser worker failed before ownership handshake")
                    await asyncio.sleep(0.05)
                if ready.stat().st_size > 4096:
                    raise IntegrationUnavailable("Browser worker process identity exceeds byte limit")
                try:
                    identity = json.loads(ready.read_text(encoding="ascii"))
                    pid, runtime = identity["pid"], identity["runtime"]
                    if type(pid) is not int or pid <= 0 or not isinstance(runtime, str):
                        raise ValueError("invalid process identity")
                except (ValueError, KeyError, TypeError) as error:
                    raise IntegrationUnavailable(
                        "Browser worker returned an invalid process identity"
                    ) from error
                if os.name == "nt" and "windowsapps" in runtime.replace("\\", "/").lower().split("/"):
                    raise IntegrationUnavailable(
                        "Browser requires standalone Python; Microsoft Store Python cannot contain Chromium"
                    )
                # Windows venv Python is a launcher; contain the actual interpreter.
                tree.attach(pid if os.name == "nt" else process.pid)
                (request.parent / "start").write_text("ready", encoding="utf-8")
                while process.returncode is None:
                    _cancel(is_cancelled)
                    await asyncio.sleep(0.1)
                if process.returncode:
                    raise IntegrationUnavailable(
                        "Browser resolver failed; install SeleniumBase 4.56.2 "
                        "in the configured Python environment"
                    )
        finally:
            tree.close()
            if process is not None:
                if process.returncode is None:
                    if os.name == "nt":
                        killer = await asyncio.create_subprocess_exec(
                            "taskkill",
                            "/PID",
                            str(process.pid),
                            "/T",
                            "/F",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await killer.wait()
                    else:
                        process.kill()
                await process.wait()
