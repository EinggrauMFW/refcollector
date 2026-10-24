"""Focused tests for Wave 2 PDF upload and source page preview routes."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from httpx import ASGITransport

from refcollector.contracts.api import CallContext
from refcollector.web.app import create_app
from tests.fakes.service import FakeService


class MockAppHandle:
    def __init__(self, service: Any) -> None:
        self.service = service

    def start_executor(self) -> bool:
        return True

    def close(self) -> None:
        pass


@dataclass
class FakePageRender:
    data: bytes
    media_type: str
    page_index: int
    width: int
    height: int
    span_overlay_supported: bool = True


class MockPdfService(FakeService):
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        super().__init__(responses)
        self.upload_calls: list[tuple[CallContext, Any, bytes]] = []
        self.render_calls: list[tuple[CallContext, Any]] = []

    def upload_pdf(self, ctx: CallContext, req: Any, body: Any) -> Any:
        content = body.read()
        self.upload_calls.append((ctx, req, content))
        if "upload_pdf" in self.responses:
            resp = self.responses["upload_pdf"]
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                return resp(ctx, req, body)
            return resp
        return {
            "source": {"source_id": "src_1", "reused": False},
            "attachment": None,
        }

    def render_source_page(self, ctx: CallContext, req: Any) -> Any:
        self.render_calls.append((ctx, req))
        if "render_source_page" in self.responses:
            resp = self.responses["render_source_page"]
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                return resp(ctx, req)
            return resp
        return FakePageRender(
            data=b"\x89PNG\r\n\x1a\nfake-png-data",
            media_type="image/png",
            page_index=getattr(req, "page_index", 0),
            width=612,
            height=792,
        )


@pytest.fixture
def pdf_service() -> MockPdfService:
    return MockPdfService()


@pytest.mark.anyio
async def test_upload_pdf_requires_session(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"},
            files={"file": ("test.pdf", b"%PDF-1.4 test", "application/pdf")},
            data={"purpose": "source", "declared_bytes": "13", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403
        assert len(pdf_service.upload_calls) == 0


@pytest.mark.anyio
async def test_upload_pdf_requires_csrf_before_parsing(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Establish session but do not send X-CSRF-Token
        await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"},
            files={"file": ("test.pdf", b"%PDF-1.4 test", "application/pdf")},
            data={"purpose": "source", "declared_bytes": "13", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "NOT_AUTHORIZED"
        assert len(pdf_service.upload_calls) == 0


@pytest.mark.anyio
async def test_upload_pdf_foreign_origin_rejected(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://attacker.com",
                "X-CSRF-Token": csrf,
            },
            files={"file": ("test.pdf", b"%PDF-1.4 test", "application/pdf")},
            data={"purpose": "source", "declared_bytes": "13", "idempotency_key": "12345678"},
        )
        assert r.status_code == 403
        assert len(pdf_service.upload_calls) == 0


@pytest.mark.anyio
async def test_upload_pdf_non_multipart_rejected(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": csrf,
                "Content-Type": "application/json",
            },
            content=b"{}",
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"
        assert len(pdf_service.upload_calls) == 0


@pytest.mark.anyio
async def test_upload_pdf_source_success(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf,
        }

        pdf_bytes = b"%PDF-1.4 dummy valid PDF content %%EOF"
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("sample.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            data={
                "purpose": "source",
                "name": "sample.pdf",
                "declared_bytes": str(len(pdf_bytes)),
                "idempotency_key": "upload-idem-key-1",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["source"]["source_id"] == "src_1"
        assert data["attachment"] is None

        assert len(pdf_service.upload_calls) == 1
        ctx, req, body_bytes = pdf_service.upload_calls[0]
        assert body_bytes == pdf_bytes
        req_dict = req if isinstance(req, dict) else req.__dict__
        assert req_dict["project_id"] == "prj_1"
        assert req_dict["purpose"] == "source"
        assert req_dict["name"] == "sample.pdf"
        assert req_dict["declared_bytes"] == len(pdf_bytes)
        assert req_dict["idempotency_key"] == "upload-idem-key-1"


@pytest.mark.anyio
async def test_upload_pdf_local_copy_attachment_success(pdf_service: MockPdfService) -> None:
    pdf_service.responses["upload_pdf"] = {
        "source": None,
        "attachment": {"local_copy_id": "lc_1", "local_copy": "usable"},
    }
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf,
        }

        pdf_bytes = b"%PDF-1.4 attachment file content %%EOF"
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("attachment.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            data={
                "purpose": "local_copy",
                "name": "attachment.pdf",
                "declared_bytes": str(len(pdf_bytes)),
                "occurrence_id": "occ_123",
                "version_hint": "published",
                "idempotency_key": "upload-idem-key-2",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["source"] is None
        assert data["attachment"]["local_copy_id"] == "lc_1"

        ctx, req, body_bytes = pdf_service.upload_calls[0]
        assert body_bytes == pdf_bytes
        req_dict = req if isinstance(req, dict) else req.__dict__
        assert req_dict["purpose"] == "local_copy"
        assert req_dict["occurrence_id"] == "occ_123"
        assert req_dict["version_hint"] == "published"


@pytest.mark.anyio
async def test_upload_pdf_validation_rejections(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf,
        }

        valid_pdf = b"%PDF-1.4 dummy %%EOF"

        # 1. Non-PDF MIME type
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("test.txt", valid_pdf, "text/plain")},
            data={"purpose": "source", "declared_bytes": str(len(valid_pdf)), "idempotency_key": "12345678"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 2. Path traversal filename
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("../../evil.pdf", valid_pdf, "application/pdf")},
            data={"purpose": "source", "declared_bytes": str(len(valid_pdf)), "idempotency_key": "12345678"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 3. Control character in filename
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("null\x00byte.pdf", valid_pdf, "application/pdf")},
            data={"purpose": "source", "declared_bytes": str(len(valid_pdf)), "idempotency_key": "12345678"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 4. declared_bytes mismatch
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("valid.pdf", valid_pdf, "application/pdf")},
            data={"purpose": "source", "declared_bytes": "9999", "idempotency_key": "12345678"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 5. Missing occurrence_id for local_copy
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("valid.pdf", valid_pdf, "application/pdf")},
            data={
                "purpose": "local_copy",
                "declared_bytes": str(len(valid_pdf)),
                "idempotency_key": "12345678",
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 6. occurrence_id forbidden for source
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("valid.pdf", valid_pdf, "application/pdf")},
            data={
                "purpose": "source",
                "declared_bytes": str(len(valid_pdf)),
                "occurrence_id": "occ_1",
                "idempotency_key": "12345678",
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 7. Unexpected extra form field
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("valid.pdf", valid_pdf, "application/pdf")},
            data={
                "purpose": "source",
                "declared_bytes": str(len(valid_pdf)),
                "idempotency_key": "12345678",
                "unauthorized_field": "injected",
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 8. Short idempotency key (< 8 chars)
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("valid.pdf", valid_pdf, "application/pdf")},
            data={"purpose": "source", "declared_bytes": str(len(valid_pdf)), "idempotency_key": "short"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"


@pytest.mark.anyio
async def test_render_source_page_requires_session(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        r = await client.get(
            "/api/projects/prj_1/sources/src_1/pages/0",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 403
        assert len(pdf_service.render_calls) == 0


@pytest.mark.anyio
async def test_render_source_page_success(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        r = await client.get(
            "/api/projects/prj_1/sources/src_1/pages/0?scale=1.5",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "no-store" in r.headers["cache-control"]
        assert r.headers["x-page-index"] == "0"
        assert r.headers["x-page-width"] == "612"
        assert r.headers["x-page-height"] == "792"
        assert r.content == b"\x89PNG\r\n\x1a\nfake-png-data"

        assert len(pdf_service.render_calls) == 1
        ctx, req = pdf_service.render_calls[0]
        req_dict = req if isinstance(req, dict) else req.__dict__
        assert req_dict["project_id"] == "prj_1"
        assert req_dict["source_id"] == "src_1"
        assert req_dict["page_index"] == 0
        assert req_dict["scale"] == 1.5


@pytest.mark.anyio
async def test_render_source_page_bounds_validation(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})

        # 1. Negative page_index
        r = await client.get(
            "/api/projects/prj_1/sources/src_1/pages/-1",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 2. Scale too small (< 0.5)
        r = await client.get(
            "/api/projects/prj_1/sources/src_1/pages/0?scale=0.2",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 3. Scale too large (> 2.0)
        r = await client.get(
            "/api/projects/prj_1/sources/src_1/pages/0?scale=2.5",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"


@pytest.mark.anyio
async def test_upload_pdf_spooled_cleanup_on_validation_failure(
    pdf_service: MockPdfService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.datastructures import UploadFile

    closed_files: list[UploadFile] = []
    orig_close = UploadFile.close

    async def tracking_close(self: UploadFile) -> None:
        closed_files.append(self)
        await orig_close(self)

    monkeypatch.setattr(UploadFile, "close", tracking_close)

    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf,
        }

        valid_pdf = b"%PDF-1.4 sample content for spooled file test %%EOF"

        # 1. Validation failure: extra field
        r1 = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("valid.pdf", valid_pdf, "application/pdf")},
            data={
                "purpose": "source",
                "declared_bytes": str(len(valid_pdf)),
                "idempotency_key": "12345678",
                "extra_evil": "injected",
            },
        )
        assert r1.status_code == 400
        assert r1.json()["code"] == "INVALID_INPUT"
        assert len(closed_files) == 1
        assert closed_files[0].file.closed

        # 2. Validation failure: duplicate field
        closed_files.clear()
        r2 = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("valid.pdf", valid_pdf, "application/pdf")},
            data={
                "purpose": ["source", "local_copy"],
                "declared_bytes": str(len(valid_pdf)),
                "idempotency_key": "12345678",
            },
        )
        assert r2.status_code == 400
        assert r2.json()["code"] == "INVALID_INPUT"
        assert len(closed_files) == 1
        assert closed_files[0].file.closed


@pytest.mark.anyio
async def test_upload_pdf_oversize_streaming_returns_413(pdf_service: MockPdfService) -> None:
    from refcollector.config import load_settings

    # Configure a tiny body limit to test streaming chunk enforcement
    settings = load_settings()
    settings.limits.pdf_bytes = 200

    handle = MockAppHandle(pdf_service)
    # BodyLimitMiddleware sets max_bytes = 200 + 64 * 1024 = 65736 bytes
    app = create_app(settings=settings, handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf,
        }

        # Send body larger than max_bytes (70,000 bytes)
        big_content = b"%PDF-1.4 " + b"X" * 70000
        r = await client.post(
            "/api/projects/prj_1/uploads/pdf",
            headers=headers,
            files={"file": ("big.pdf", big_content, "application/pdf")},
            data={
                "purpose": "source",
                "declared_bytes": str(len(big_content)),
                "idempotency_key": "12345678",
            },
        )
        assert r.status_code == 413
        assert r.json()["code"] == "LIMIT_EXCEEDED"
        assert len(pdf_service.upload_calls) == 0


@pytest.mark.anyio
async def test_render_source_page_span_overlay_supported_header(pdf_service: MockPdfService) -> None:
    handle = MockAppHandle(pdf_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})

        # 1. Overlay supported is True -> X-Span-Overlay-Supported: 1
        pdf_service.responses["render_source_page"] = FakePageRender(
            data=b"\x89PNG\r\n\x1a\nnormal",
            media_type="image/png",
            page_index=0,
            width=600,
            height=800,
            span_overlay_supported=True,
        )
        r = await client.get(
            "/api/projects/prj_1/sources/src_1/pages/0",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 200
        assert r.headers["x-span-overlay-supported"] == "1"

        # 2. Overlay supported is False (e.g. rotated page) -> X-Span-Overlay-Supported: 0
        pdf_service.responses["render_source_page"] = FakePageRender(
            data=b"\x89PNG\r\n\x1a\nrotated",
            media_type="image/png",
            page_index=0,
            width=800,
            height=600,
            span_overlay_supported=False,
        )
        r = await client.get(
            "/api/projects/prj_1/sources/src_1/pages/0",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 200
        assert r.headers["x-span-overlay-supported"] == "0"
