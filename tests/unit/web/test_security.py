"""Focused security tests for Host, Origin, Fetch-Metadata, Session, and CSRF."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from httpx import ASGITransport

from refcollector.contracts.api import CreateProjectOut
from refcollector.web.app import create_app
from tests.fakes.service import FakeService


class MockAppHandle:
    def __init__(self, service: Any) -> None:
        self.service = service

    def start_executor(self) -> bool:
        return True

    def close(self) -> None:
        pass


@pytest.fixture
def fake_service() -> FakeService:
    return FakeService(
        {
            "create_project": CreateProjectOut(project_id="prj_1", revision=1),
        }
    )


@pytest.mark.anyio
async def test_exact_host_and_port_matching(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Wrong domain
        r = await client.get("/api/session", headers={"Host": "evil.com:8000"})
        assert r.status_code == 400

        # Wrong port
        r = await client.get("/api/session", headers={"Host": "127.0.0.1:9000"})
        assert r.status_code == 400

        # Discarded port (localhost without port when 8000 required)
        r = await client.get("/api/session", headers={"Host": "127.0.0.1"})
        assert r.status_code == 400

        # Exact match
        r = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        assert r.status_code == 200


@pytest.mark.anyio
async def test_origin_header_enforcement(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Obtain valid session and CSRF token
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        # 1. Missing Origin on mutating POST -> 403
        r = await client.post(
            "/api/projects",
            headers={"Host": "127.0.0.1:8000", "X-CSRF-Token": csrf},
            json={"name": "Project", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"

        # 2. Foreign Origin on POST -> 403
        r = await client.post(
            "/api/projects",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://evil.com:8000",
                "X-CSRF-Token": csrf,
            },
            json={"name": "Project", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403

        # 3. Origin: null -> 403
        r = await client.post(
            "/api/projects",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "null",
                "X-CSRF-Token": csrf,
            },
            json={"name": "Project", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403

        # 4. Foreign Origin on GET -> 403
        r = await client.get(
            "/api/session",
            headers={"Host": "127.0.0.1:8000", "Origin": "http://attacker.com"},
        )
        assert r.status_code == 403

        # 5. Exact matching Origin -> 200
        r = await client.post(
            "/api/projects",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={"name": "Project", "idempotency_key": "12345678"},
        )
        assert r.status_code == 200


@pytest.mark.anyio
async def test_fetch_metadata_cross_site_rejection(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Cross-site session bootstrap attempt
        r = await client.get(
            "/api/session",
            headers={"Host": "127.0.0.1:8000", "Sec-Fetch-Site": "cross-site"},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"

        # Same-origin session bootstrap
        r = await client.get(
            "/api/session",
            headers={"Host": "127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"},
        )
        assert r.status_code == 200


@pytest.mark.anyio
async def test_session_and_csrf_validation(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Request with no session cookie
        r = await client.post(
            "/api/projects",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": "some-token",
            },
            json={"name": "NoSession", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403

        # Obtain valid session
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        real_csrf = sess.json()["csrf_token"]

        # Missing CSRF header
        r = await client.post(
            "/api/projects",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
            },
            json={"name": "NoToken", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403

        # Invalid CSRF header
        r = await client.post(
            "/api/projects",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": "wrong-token-value",
            },
            json={"name": "WrongToken", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403

        # Matching session + CSRF token
        r = await client.post(
            "/api/projects",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": real_csrf,
            },
            json={"name": "Valid", "idempotency_key": "12345678"},
        )
        assert r.status_code == 200


@pytest.mark.anyio
async def test_security_headers_present(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        r = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        assert r.status_code == 200
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["referrer-policy"] == "no-referrer"
