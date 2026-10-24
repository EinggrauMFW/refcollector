"""Unit tests for Library UI serving, framing, and CSP (Wave 2, B3)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from refcollector.config import Settings
from refcollector.contracts.api import (
    GetCapabilitiesOut,
    ProjectFileDescriptor,
    ReadProjectFileOut,
)
from refcollector.domain.enums import SourceType
from refcollector.web.app import create_app
from tests.fakes.service import FakeService


class MockAppHandle:
    def __init__(self, service: FakeService) -> None:
        self.service = service

    def start_executor(self) -> bool:
        return True

    def close(self) -> None:
        pass


@pytest.fixture
def fake_service() -> FakeService:
    return FakeService(
        {
            "get_capabilities": GetCapabilitiesOut(
                server_version="0.2.0.dev0",
                release="P0",
                source_types=[SourceType.TEXT],
                stages=[],
                adapters={},
                providers=[],
                local_copies=[],
                export_formats=[],
                approval_surfaces=[],
                limits={},
                scopes_granted=[],
                config={},
            ),
            "read_project_file": ReadProjectFileOut(
                descriptor=ProjectFileDescriptor(
                    resource_id="src_1",
                    kind="source",
                    name="paper.pdf",
                    media_type="application/pdf",
                    bytes=10,
                    sha256="abc",
                ),
                data=b"%PDF-test",
                next_offset=10,
                eof=True,
            ),
        }
    )


@pytest.mark.anyio
async def test_root_serves_library_index_and_advanced_serves_legacy_index(
    fake_service: FakeService,
    tmp_path: Path,
) -> None:
    settings = Settings(rc_home=tmp_path)
    handle = MockAppHandle(fake_service)
    app = create_app(settings=settings, handle=handle, host="127.0.0.1", port=8000)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # GET / serves the library index.html
        resp_root = await client.get("/", headers={"Host": "127.0.0.1:8000"})
        assert resp_root.status_code == 200
        # HTML content from web-ui build / static/library/index.html
        assert "text/html" in resp_root.headers["content-type"]
        assert '<div id="root">' in resp_root.text or "html" in resp_root.text.lower()

        # GET /advanced serves the old static/index.html
        resp_adv = await client.get("/advanced", headers={"Host": "127.0.0.1:8000"})
        assert resp_adv.status_code == 200
        assert "text/html" in resp_adv.headers["content-type"]
        # Legacy dashboard includes specific dashboard elements or title
        assert "RefCollector" in resp_adv.text

        # Unknown non-API path serves library SPA
        resp_spa = await client.get("/collections/prj_123", headers={"Host": "127.0.0.1:8000"})
        assert resp_spa.status_code == 200
        assert "text/html" in resp_spa.headers["content-type"]


@pytest.mark.anyio
async def test_framing_and_csp_headers(fake_service: FakeService, tmp_path: Path) -> None:
    settings = Settings(rc_home=tmp_path)
    handle = MockAppHandle(fake_service)
    app = create_app(settings=settings, handle=handle, host="127.0.0.1", port=8000)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # 1. Establish session
        sess_resp = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        assert sess_resp.status_code == 200
        cookies = dict(sess_resp.cookies)

        # 2. Library page CSP allows frame-src 'self', and keeps X-Frame-Options: DENY
        root_resp = await client.get("/", headers={"Host": "127.0.0.1:8000"})
        assert root_resp.headers["x-frame-options"] == "DENY"
        csp_root = root_resp.headers["content-security-policy"]
        assert "frame-src 'self'" in csp_root
        assert "frame-ancestors 'none'" in csp_root

        # 3. File route allows same-origin framing: X-Frame-Options: SAMEORIGIN and frame-ancestors 'self'
        file_resp = await client.get(
            "/api/library/collections/prj_1/files/source/src_1",
            headers={"Host": "127.0.0.1:8000"},
            cookies=cookies,
        )
        assert file_resp.status_code == 200
        assert file_resp.headers["x-frame-options"] == "SAMEORIGIN"
        csp_file = file_resp.headers["content-security-policy"]
        assert "frame-ancestors 'self'" in csp_file

        # 4. Other API routes maintain X-Frame-Options: DENY and frame-ancestors 'none'
        cap_resp = await client.get(
            "/api/capabilities",
            headers={"Host": "127.0.0.1:8000"},
            cookies=cookies,
        )
        assert cap_resp.status_code == 200
        assert cap_resp.headers["x-frame-options"] == "DENY"
        csp_cap = cap_resp.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp_cap
        assert "frame-src" not in csp_cap
