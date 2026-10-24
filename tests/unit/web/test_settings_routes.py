"""Focused tests for Wave 3 web settings routes."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from httpx import ASGITransport
from pydantic import BaseModel, Field

from refcollector.contracts import api
from refcollector.contracts.api import CallContext
from refcollector.domain.enums import Channel
from refcollector.errors import RevisionConflict
from refcollector.web.app import create_app
from tests.fakes.service import FakeService


class MockAppHandle:
    def __init__(self, service: Any) -> None:
        self.service = service

    def start_executor(self) -> bool:
        return True

    def close(self) -> None:
        pass


# Fallback / mock DTOs conforming to frozen brief section 2
class MockEmailChange(BaseModel):
    type: Literal["set_email"] = "set_email"
    value: str


class MockKeyChange(BaseModel):
    type: Literal["set_provider_key"] = "set_provider_key"
    provider: Literal["openalex_key", "s2_key"]
    value: str


class MockFolderChange(BaseModel):
    type: Literal["add_allowed_folder"] = "add_allowed_folder"
    path: str


class MockUpdateWebSettingsIn(BaseModel):
    expected_settings_revision: str
    change: Annotated[
        MockEmailChange | MockKeyChange | MockFolderChange,
        Field(discriminator="type"),
    ]
    idempotency_key: str


class MockSettingsService(FakeService):
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        super().__init__(responses)
        self.get_settings_calls: list[tuple[CallContext, Any]] = []
        self.prepare_calls: list[tuple[CallContext, Any]] = []
        self.confirm_calls: list[tuple[CallContext, Any, str]] = []

    def get_settings(self, ctx: CallContext, req: Any) -> Any:
        self.get_settings_calls.append((ctx, req))
        if "get_settings" in self.responses:
            resp = self.responses["get_settings"]
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                return resp(ctx, req)
            return resp
        return {
            "revision": "rev_abc123",
            "rc_home": "D:/test/rc_home",
            "email": "test@example.com",
            "allow_folders": ["D:/allowed"],
            "download_enabled_global": False,
            "pdf_fallback_url": None,
            "browser": {"enabled": False, "configured": False},
            "enabled_providers": ["crossref", "openalex"],
            "mcp_scopes": ["projects:read"],
            "limits": {"pdf_bytes": 104857600},
            "keys": {"openalex_key": True, "s2_key": False},
            "project": (
                {
                    "project_id": req.project_id,
                    "network_mode": "metadata",
                    "download_enabled": False,
                    "version_policy": "prefer_published",
                }
                if req and req.project_id
                else None
            ),
        }

    def prepare_web_settings(self, ctx: CallContext, req: Any) -> Any:
        self.prepare_calls.append((ctx, req))
        if "prepare_web_settings" in self.responses:
            resp = self.responses["prepare_web_settings"]
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                return resp(ctx, req)
            return resp
        return {
            "confirmation_token": "tok_set_98765",
            "expires_at": "2026-10-21T19:05:00Z",
            "purpose": "settings",
            "summary": {
                "change_type": (
                    req.change.type if hasattr(req.change, "type") else req.change.get("type", "unknown")
                ),
                "expected_settings_revision": req.expected_settings_revision,
            },
        }

    def confirm_web_settings(self, ctx: CallContext, req: Any, confirmation_token: str) -> Any:
        self.confirm_calls.append((ctx, req, confirmation_token))
        if "confirm_web_settings" in self.responses:
            resp = self.responses["confirm_web_settings"]
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                return resp(ctx, req, confirmation_token)
            return resp
        return {
            "revision": "rev_updated_456",
            "rc_home": "D:/test/rc_home",
            "email": "updated@example.com",
            "allow_folders": ["D:/allowed"],
            "download_enabled_global": False,
            "pdf_fallback_url": None,
            "browser": {"enabled": False, "configured": False},
            "enabled_providers": ["crossref", "openalex"],
            "mcp_scopes": ["projects:read"],
            "limits": {"pdf_bytes": 104857600},
            "keys": {"openalex_key": True, "s2_key": False},
            "project": None,
        }


@pytest.fixture(autouse=True)
def setup_api_dtos(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(api, "UpdateWebSettingsIn"):
        monkeypatch.setattr(api, "UpdateWebSettingsIn", MockUpdateWebSettingsIn)


@pytest.fixture
def settings_service() -> MockSettingsService:
    return MockSettingsService()


@pytest.mark.anyio
async def test_get_settings_requires_session(settings_service: MockSettingsService) -> None:
    handle = MockAppHandle(settings_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        r = await client.get("/api/settings", headers={"Host": "127.0.0.1:8000"})
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"
        assert len(settings_service.get_settings_calls) == 0


@pytest.mark.anyio
async def test_get_settings_success(settings_service: MockSettingsService) -> None:
    handle = MockAppHandle(settings_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        session_id = client.cookies.get("rc_session")

        r = await client.get(
            "/api/settings?project_id=prj_42",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["revision"] == "rev_abc123"
        assert data["email"] == "test@example.com"
        assert data["keys"] == {"openalex_key": True, "s2_key": False}
        assert data["browser"] == {"enabled": False, "configured": False}
        assert data["project"]["project_id"] == "prj_42"

        assert len(settings_service.get_settings_calls) == 1
        ctx, req = settings_service.get_settings_calls[0]
        assert ctx.channel == Channel.WEB_UI
        if hasattr(ctx, "web_session_id"):
            assert ctx.web_session_id == session_id
        if req is not None:
            assert req.project_id == "prj_42"


@pytest.mark.anyio
async def test_prepare_settings_intent_requires_session_and_csrf(
    settings_service: MockSettingsService,
) -> None:
    handle = MockAppHandle(settings_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # No session
        r = await client.post(
            "/api/settings/intent",
            headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"},
            json={
                "expected_settings_revision": "rev_abc123",
                "change": {"type": "set_email", "value": "new@example.com"},
                "idempotency_key": "idem_set_1",
            },
        )
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"

        # Session present, but no CSRF
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        r2 = await client.post(
            "/api/settings/intent",
            headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"},
            json={
                "expected_settings_revision": "rev_abc123",
                "change": {"type": "set_email", "value": "new@example.com"},
                "idempotency_key": "idem_set_1",
            },
        )
        assert r2.status_code == 403
        assert r2.json()["code"] == "NOT_AUTHORIZED"
        assert len(settings_service.prepare_calls) == 0

        # With CSRF
        r3 = await client.post(
            "/api/settings/intent",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={
                "expected_settings_revision": "rev_abc123",
                "change": {"type": "set_email", "value": "new@example.com"},
                "idempotency_key": "idem_set_1",
            },
        )
        assert r3.status_code == 200
        data = r3.json()
        assert data["confirmation_token"] == "tok_set_98765"
        assert data["purpose"] == "settings"
        assert len(settings_service.prepare_calls) == 1


@pytest.mark.anyio
async def test_confirm_settings_requires_confirmation_token_header(
    settings_service: MockSettingsService,
) -> None:
    handle = MockAppHandle(settings_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        # Missing X-Confirmation-Token header
        r = await client.post(
            "/api/settings",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={
                "expected_settings_revision": "rev_abc123",
                "change": {"type": "set_email", "value": "new@example.com"},
                "idempotency_key": "idem_set_1",
            },
        )
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"
        assert "X-Confirmation-Token" in r.json()["message"]
        assert len(settings_service.confirm_calls) == 0


@pytest.mark.anyio
async def test_confirm_settings_success(settings_service: MockSettingsService) -> None:
    handle = MockAppHandle(settings_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        session_id = client.cookies.get("rc_session")

        r = await client.post(
            "/api/settings",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
                "X-Confirmation-Token": "tok_set_valid_token",
            },
            json={
                "expected_settings_revision": "rev_abc123",
                "change": {"type": "set_email", "value": "new@example.com"},
                "idempotency_key": "idem_set_1",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["revision"] == "rev_updated_456"

        assert len(settings_service.confirm_calls) == 1
        ctx, req, token = settings_service.confirm_calls[0]
        assert token == "tok_set_valid_token"
        assert ctx.channel == Channel.WEB_UI
        if hasattr(ctx, "web_session_id"):
            assert ctx.web_session_id == session_id


@pytest.mark.anyio
async def test_confirm_settings_revision_conflict(settings_service: MockSettingsService) -> None:
    handle = MockAppHandle(settings_service)
    settings_service.responses["confirm_web_settings"] = RevisionConflict(
        "expected revision rev_old does not match current settings revision rev_new"
    )
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        r = await client.post(
            "/api/settings",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
                "X-Confirmation-Token": "tok_set_stale",
            },
            json={
                "expected_settings_revision": "rev_old",
                "change": {"type": "set_email", "value": "new@example.com"},
                "idempotency_key": "idem_set_1",
            },
        )
        assert r.status_code == 409
        assert r.json()["code"] == "REVISION_CONFLICT"


@pytest.mark.anyio
async def test_secret_redaction_on_validation_error(settings_service: MockSettingsService) -> None:
    """Brief freeze: Ensure secret-bearing validation errors do not expose input values."""
    handle = MockAppHandle(settings_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        secret_input = "super_secret_openalex_key_string_9999"
        # Invalid provider triggers a validation error on change
        r = await client.post(
            "/api/settings",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
                "X-Confirmation-Token": "tok_123",
            },
            json={
                "expected_settings_revision": "rev_abc123",
                "change": {
                    "type": "set_provider_key",
                    "provider": "unsupported_provider",
                    "value": secret_input,
                },
                "idempotency_key": "idem_set_1",
            },
        )
        assert r.status_code == 400
        resp_text = r.text
        # Assert raw secret is NOT leaked anywhere in the response body
        assert secret_input not in resp_text


@pytest.mark.anyio
async def test_direct_project_settings_stub_remains_unavailable(
    settings_service: MockSettingsService,
) -> None:
    """Brief freeze: Keep raw project-settings stub unavailable."""
    handle = MockAppHandle(settings_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        r = await client.post(
            "/api/projects/prj_1/settings",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={"network_mode": "retrieval"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "FEATURE_NOT_AVAILABLE"


@pytest.mark.anyio
async def test_confirm_all_setting_change_variants(settings_service: MockSettingsService) -> None:
    """Verify that all enumerated SettingChange variants parse and reach service."""
    handle = MockAppHandle(settings_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    variants = [
        {"type": "set_email", "value": "person@example.com"},
        {"type": "add_allowed_folder", "path": "D:/papers"},
        {"type": "set_provider_key", "provider": "openalex_key", "value": "secret123"},
        {"type": "set_project_network", "project_id": "prj_1", "mode": "retrieval"},
        {"type": "set_project_downloads", "project_id": "prj_1", "enabled": True},
        {"type": "set_pdf_fallback", "value": "box"},
    ]

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        for idx, chg in enumerate(variants):
            r = await client.post(
                "/api/settings",
                headers={
                    "Host": "127.0.0.1:8000",
                    "Origin": "http://127.0.0.1:8000",
                    "X-CSRF-Token": csrf,
                    "X-Confirmation-Token": f"tok_{idx}",
                },
                json={
                    "expected_settings_revision": "rev_abc",
                    "change": chg,
                    "idempotency_key": f"idem_key_{idx:04d}",
                },
            )
            assert r.status_code == 200, f"Variant failed: {chg}: {r.text}"
            assert len(settings_service.confirm_calls) == idx + 1
