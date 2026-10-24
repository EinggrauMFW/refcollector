"""Focused tests for Wave 3 interactive review routes."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from httpx import ASGITransport

from refcollector.contracts.api import CallContext, ReviewDecisionIn
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


class MockReviewService(FakeService):
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        super().__init__(responses)
        self.prepare_calls: list[tuple[CallContext, Any]] = []
        self.confirm_calls: list[tuple[CallContext, Any, str]] = []

    def prepare_web_review(self, ctx: CallContext, req: Any) -> Any:
        self.prepare_calls.append((ctx, req))
        if "prepare_web_review" in self.responses:
            resp = self.responses["prepare_web_review"]
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                return resp(ctx, req)
            return resp
        return {
            "confirmation_token": "tok_rev_12345",
            "expires_at": "2026-10-21T19:00:00Z",
            "purpose": "review",
            "summary": {
                "project_id": req.project_id,
                "occurrence_id": req.occurrence_id,
                "action": req.action,
                "candidate_id": req.candidate_id,
                "expected_revision": req.expected_revision,
            },
        }

    def confirm_web_review(self, ctx: CallContext, req: Any, confirmation_token: str) -> Any:
        self.confirm_calls.append((ctx, req, confirmation_token))
        if "confirm_web_review" in self.responses:
            resp = self.responses["confirm_web_review"]
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                return resp(ctx, req, confirmation_token)
            return resp
        return {
            "occurrence_id": req.occurrence_id,
            "revision": req.expected_revision + 1,
            "updated": True,
        }


@pytest.fixture
def review_service() -> MockReviewService:
    return MockReviewService()


@pytest.mark.anyio
async def test_review_intent_requires_session(review_service: MockReviewService) -> None:
    handle = MockAppHandle(review_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review-intent",
            headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"},
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"
        assert len(review_service.prepare_calls) == 0


@pytest.mark.anyio
async def test_review_intent_requires_csrf(review_service: MockReviewService) -> None:
    handle = MockAppHandle(review_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review-intent",
            headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"},
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"
        assert len(review_service.prepare_calls) == 0


@pytest.mark.anyio
async def test_review_intent_path_body_mismatch(review_service: MockReviewService) -> None:
    handle = MockAppHandle(review_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        # Project ID mismatch
        r = await client.post(
            "/api/projects/prj_wrong/references/occ_1/review-intent",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # Occurrence ID mismatch
        r2 = await client.post(
            "/api/projects/prj_1/references/occ_wrong/review-intent",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r2.status_code == 400
        assert r2.json()["code"] == "INVALID_INPUT"
        assert len(review_service.prepare_calls) == 0


@pytest.mark.anyio
async def test_review_intent_success_passes_session_context(
    review_service: MockReviewService,
) -> None:
    handle = MockAppHandle(review_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        session_id = client.cookies.get("rc_session")

        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review-intent",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["confirmation_token"] == "tok_rev_12345"
        assert data["purpose"] == "review"

        assert len(review_service.prepare_calls) == 1
        ctx, req = review_service.prepare_calls[0]
        assert ctx.channel == Channel.WEB_UI
        assert ctx.tty is False
        if hasattr(ctx, "web_session_id"):
            assert ctx.web_session_id == session_id
        assert isinstance(req, ReviewDecisionIn)
        assert req.project_id == "prj_1"
        assert req.occurrence_id == "occ_1"
        assert req.action == "accept"


@pytest.mark.anyio
async def test_confirm_review_requires_confirmation_token_header(
    review_service: MockReviewService,
) -> None:
    handle = MockAppHandle(review_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        # Missing X-Confirmation-Token header
        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"
        assert "X-Confirmation-Token" in r.json()["message"]
        assert len(review_service.confirm_calls) == 0


@pytest.mark.anyio
async def test_confirm_review_success(review_service: MockReviewService) -> None:
    handle = MockAppHandle(review_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        session_id = client.cookies.get("rc_session")

        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
                "X-Confirmation-Token": "tok_rev_valid_token",
            },
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["occurrence_id"] == "occ_1"
        assert data["revision"] == 2

        assert len(review_service.confirm_calls) == 1
        ctx, req, token = review_service.confirm_calls[0]
        assert token == "tok_rev_valid_token"
        assert ctx.channel == Channel.WEB_UI
        if hasattr(ctx, "web_session_id"):
            assert ctx.web_session_id == session_id
        assert req.candidate_id == "cand_1"


@pytest.mark.anyio
async def test_confirm_review_stale_revision_conflict(review_service: MockReviewService) -> None:
    handle = MockAppHandle(review_service)
    review_service.responses["confirm_web_review"] = RevisionConflict(
        "expected revision 1 does not match current occurrence revision 2"
    )
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
                "X-Confirmation-Token": "tok_rev_stale",
            },
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r.status_code == 409
        assert r.json()["code"] == "REVISION_CONFLICT"


@pytest.mark.anyio
async def test_no_review_decision_competing_route(review_service: MockReviewService) -> None:
    """Brief freeze: Do not add /review-decision as a competing route."""
    handle = MockAppHandle(review_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]

        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review-decision",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
            },
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_123",
            },
        )
        assert r.status_code == 404
