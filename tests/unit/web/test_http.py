"""Focused HTTP tests for lifecycle, routes, and error mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from httpx import ASGITransport

from refcollector.contracts.api import (
    AddReferenceOut,
    ArtifactDescriptor,
    AttachFileOut,
    ControlJobOut,
    CreateProjectOut,
    GetCapabilitiesOut,
    GetJobOut,
    GetProjectOut,
    GetReferenceOut,
    GetSourceOut,
    IdentityView,
    ImportSourceOut,
    JobOut,
    JobResults,
    ListArtifactsOut,
    ListProjectsOut,
    ListReferencesOut,
    ListSourcesOut,
    OccurrenceSummary,
    Preflight,
    ReadArtifactOut,
    ResolveCitationOut,
    SetSourceSectionsOut,
    StartJobOut,
    UpdateReferenceOut,
)
from refcollector.domain.enums import (
    AccessClassification,
    Channel,
    DiscoveryState,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyState,
    RetrievalState,
    SourceType,
)
from refcollector.errors import (
    FeatureNotAvailable,
    LimitExceeded,
    NotFound,
    RateLimited,
    RevisionConflict,
)
from refcollector.web.app import create_app
from tests.fakes.service import FakeService


class ServiceWithoutWebFeatures:
    """Service that does not implement web review or settings methods."""

    def __init__(self) -> None:
        pass


class MockAppHandle:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.started = 0
        self.closed = 0

    def start_executor(self) -> bool:
        self.started += 1
        return True

    def close(self) -> None:
        self.closed += 1


@dataclass
class Chunk:
    descriptor: dict[str, Any]
    data: bytes
    next_offset: int
    eof: bool


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
            "list_projects": ListProjectsOut(projects=[], next_cursor=None),
            "create_project": CreateProjectOut(project_id="prj_1", revision=1),
            "get_project": GetProjectOut(
                project_id="prj_1",
                name="Thesis",
                revision=1,
                policy={},
                network_mode="metadata",
                counts={"occurrences": 0},
                sources_count=0,
                recent_jobs=[],
                recent_artifacts=[],
            ),
            "list_sources": ListSourcesOut(sources=[], next_cursor=None),
            "import_source": ImportSourceOut(
                source_id="src_1", reused=False, preflight=Preflight(pages=1, text_coverage=1.0)
            ),
            "get_source": GetSourceOut(
                source={
                    "source_id": "src_1",
                    "label": "Source 1",
                    "type": SourceType.TEXT,
                    "pages": 1,
                    "sections": 1,
                    "extraction_state": ExtractionState.EXTRACTED,
                    "occurrence_count": 0,
                    "imported_at": "2026-10-21T00:00:00Z",
                },
                revision=1,
                preflight=Preflight(pages=1, text_coverage=1.0),
                sections=[],
                extraction={"state": "extracted"},
                jobs=[],
            ),
            "set_source_sections": SetSourceSectionsOut(sections=[], requires_reextract=True),
            "start_job": StartJobOut(job_id="job_1", steps_planned=1, next_poll_after_ms=1000),
            "get_job": GetJobOut(
                job_id="job_1",
                state=JobState.QUEUED,
                stages=[],
                warnings=[],
                results=JobResults(source_ids=[], occurrence_count=0, changed_filter={}, artifact_ids=[]),
            ),
            "control_job": ControlJobOut(job_id="job_1", state=JobState.PAUSED),
            "list_references": ListReferencesOut(occurrences=[], next_cursor=None, total=0),
            "add_reference": AddReferenceOut(occurrence_id="occ_1", revision=1),
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_1",
                    source_id="src_1",
                    revision=1,
                    label="Ref 1",
                    raw_text="Example Citation",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(state=IdentityState.UNPROCESSED),
                    access={
                        "discovery": DiscoveryState.NOT_CHECKED,
                        "classification": AccessClassification.UNKNOWN,
                    },
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.NOT_REQUESTED,
                )
            ),
            "update_reference": UpdateReferenceOut(changed=[], created=[]),
            "resolve_citation": ResolveCitationOut(candidates=[], decision_hint="unresolved"),
            "attach_file": AttachFileOut(local_copy_id="lc_1", checks={}, local_copy=LocalCopyState.USABLE),
            "download_files": JobOut(job_id="job_dl"),
            "create_export": JobOut(job_id="job_exp"),
            "draft_requests": JobOut(job_id="job_draft"),
            "list_artifacts": ListArtifactsOut(artifacts=[], next_cursor=None),
            "read_artifact": ReadArtifactOut(
                descriptor=ArtifactDescriptor(
                    artifact_id="art_1",
                    kind="export",
                    media_type="text/plain",
                    bytes=10,
                    sha256="abc",
                    project_revision=1,
                    name="export.txt",
                ),
                text="hello",
                eof=True,
            ),
        }
    )


@pytest.mark.anyio
async def test_app_lifecycle_starts_and_closes_executor(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)

    async with app.router.lifespan_context(app):
        assert handle.started == 1
        assert handle.closed == 0

    assert handle.closed == 1


@pytest.mark.anyio
async def test_session_and_capabilities_flow(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Without session, capabilities is forbidden
        r = await client.get("/api/capabilities", headers={"Host": "127.0.0.1:8000"})
        assert r.status_code == 403

        # Bootstrap session
        sess_resp = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        assert sess_resp.status_code == 200
        data = sess_resp.json()
        assert "session_id" not in data
        assert "csrf_token" in data
        assert "principal" in data

        # Repeated session request reuses existing session
        sess_resp2 = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        assert sess_resp2.status_code == 200
        assert sess_resp2.json()["csrf_token"] == data["csrf_token"]

        # Now capabilities succeeds
        r = await client.get("/api/capabilities", headers={"Host": "127.0.0.1:8000"})
        assert r.status_code == 200
        assert r.json()["release"] == "P0"

        # Check call context
        assert len(fake_service.calls) >= 1
        method, ctx, _ = fake_service.calls[-1]
        assert method == "get_capabilities"
        assert ctx.channel == Channel.WEB_UI
        assert ctx.origin == "agent"
        assert ctx.tty is False


@pytest.mark.anyio
async def test_mutating_project_routes_enforce_csrf_and_path_consistency(
    fake_service: FakeService,
) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess_resp = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf_token = sess_resp.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf_token,
        }

        # Create project
        create_resp = await client.post(
            "/api/projects",
            headers=headers,
            json={"name": "New Project", "idempotency_key": "12345678"},
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["project_id"] == "prj_1"

        # Import source with matching project ID
        import_resp = await client.post(
            "/api/projects/prj_1/sources/import",
            headers=headers,
            json={
                "project_id": "prj_1",
                "input": {"type": "text", "text": "Some citation text"},
                "idempotency_key": "12345678",
            },
        )
        assert import_resp.status_code == 200
        assert import_resp.json()["source_id"] == "src_1"

        # Import source with MISMATCHED project ID returns 400 INVALID_INPUT
        mismatch_resp = await client.post(
            "/api/projects/prj_other/sources/import",
            headers=headers,
            json={
                "project_id": "prj_1",
                "input": {"type": "text", "text": "Some citation text"},
                "idempotency_key": "12345678",
            },
        )
        assert mismatch_resp.status_code == 400
        assert mismatch_resp.json()["code"] == "INVALID_INPUT"


@pytest.mark.anyio
async def test_binary_file_streaming(fake_service: FakeService) -> None:
    # Setup streaming response in fake service
    chunk1 = Chunk(
        descriptor={"name": "paper.pdf", "media_type": "application/pdf"},
        data=b"%PDF-1.4 first chunk ",
        next_offset=21,
        eof=False,
    )
    chunk2 = Chunk(
        descriptor={"name": "paper.pdf", "media_type": "application/pdf"},
        data=b"second chunk",
        next_offset=33,
        eof=True,
    )

    def fake_read_file(ctx: Any, req: Any) -> Chunk:
        offset = req.offset if hasattr(req, "offset") else req.get("offset", 0)
        return chunk1 if offset == 0 else chunk2

    fake_service.responses["read_project_file"] = fake_read_file

    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        r = await client.get(
            "/api/projects/prj_1/files/work_file/wf_1",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert 'attachment; filename="paper.pdf"' in r.headers["content-disposition"]
        assert r.content == b"%PDF-1.4 first chunk second chunk"

        # Local copy file stream
        r_lc = await client.get(
            "/api/projects/prj_1/files/local_copy/lcp_1",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r_lc.status_code == 200
        assert r_lc.headers["content-type"] == "application/pdf"
        assert r_lc.content == b"%PDF-1.4 first chunk second chunk"

        # Invalid file kind returns 400 INVALID_INPUT
        r_invalid = await client.get(
            "/api/projects/prj_1/files/invalid_kind/wf_1",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r_invalid.status_code == 400
        assert r_invalid.json()["code"] == "INVALID_INPUT"


@pytest.mark.anyio
async def test_wave_2_stubs_explicitly_fail() -> None:
    """Verify routes return FEATURE_NOT_AVAILABLE when service lacks optional methods."""
    handle = MockAppHandle(ServiceWithoutWebFeatures())
    app = create_app(handle=handle, host="127.0.0.1", port=8000)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess_resp = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf_token = sess_resp.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf_token,
        }

        # Guard: Missing X-Confirmation-Token returns 403
        r_no_token = await client.post(
            "/api/projects/prj_1/references/occ_1/review",
            headers=headers,
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_12345",
            },
        )
        assert r_no_token.status_code == 403
        assert r_no_token.json()["code"] == "NOT_AUTHORIZED"

        # Review decision unavailable when service lacks method
        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review",
            headers={**headers, "X-Confirmation-Token": "tok_123"},
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_12345",
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "FEATURE_NOT_AVAILABLE"

        # Web settings read unavailable when service lacks method
        r = await client.get("/api/settings", headers={"Host": "127.0.0.1:8000"})
        assert r.status_code == 400
        assert r.json()["code"] == "FEATURE_NOT_AVAILABLE"

        # Guard: Missing X-Confirmation-Token on settings write returns 403
        r_no_token_settings = await client.post(
            "/api/settings",
            headers=headers,
            json={
                "expected_settings_revision": "rev_1",
                "change": {"type": "set_email", "value": "a@b.com"},
                "idempotency_key": "idem_12345",
            },
        )
        assert r_no_token_settings.status_code == 403
        assert r_no_token_settings.json()["code"] == "NOT_AUTHORIZED"

        # Web settings write unavailable when service lacks method
        r = await client.post(
            "/api/settings",
            headers={**headers, "X-Confirmation-Token": "tok_123"},
            json={
                "expected_settings_revision": "rev_1",
                "change": {"type": "set_email", "value": "a@b.com"},
                "idempotency_key": "idem_12345",
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "FEATURE_NOT_AVAILABLE"

        # Project settings write stub remains unavailable
        r = await client.post("/api/projects/prj_1/settings", headers=headers)
        assert r.status_code == 400
        assert r.json()["code"] == "FEATURE_NOT_AVAILABLE"


@pytest.mark.anyio
async def test_configured_feature_not_available_responses(fake_service: FakeService) -> None:
    """Verify configured FeatureNotAvailable exceptions map to HTTP 400."""
    fake_service.responses["confirm_web_review"] = FeatureNotAvailable(
        "Interactive review is disabled in backend"
    )
    fake_service.responses["get_settings"] = FeatureNotAvailable("Web settings are disabled")
    fake_service.responses["confirm_web_settings"] = FeatureNotAvailable("Web settings update is disabled")

    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess_resp = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf_token = sess_resp.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf_token,
        }

        # Configured review unavailable
        r = await client.post(
            "/api/projects/prj_1/references/occ_1/review",
            headers={**headers, "X-Confirmation-Token": "tok_123"},
            json={
                "project_id": "prj_1",
                "occurrence_id": "occ_1",
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_12345",
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "FEATURE_NOT_AVAILABLE"

        # Configured settings read unavailable
        r = await client.get("/api/settings", headers={"Host": "127.0.0.1:8000"})
        assert r.status_code == 400
        assert r.json()["code"] == "FEATURE_NOT_AVAILABLE"

        # Configured settings write unavailable
        r = await client.post(
            "/api/settings",
            headers={**headers, "X-Confirmation-Token": "tok_123"},
            json={
                "expected_settings_revision": "rev_1",
                "change": {"type": "set_email", "value": "a@b.com"},
                "idempotency_key": "idem_12345",
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "FEATURE_NOT_AVAILABLE"


@pytest.mark.anyio
async def test_error_translation_mappings(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess_resp = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf_token = sess_resp.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf_token,
        }

        # NotFound -> 404
        fake_service.responses["get_project"] = NotFound("Project does not exist")
        r = await client.get("/api/projects/missing", headers={"Host": "127.0.0.1:8000"})
        assert r.status_code == 404
        assert r.json()["code"] == "NOT_FOUND"

        # RevisionConflict -> 409
        fake_service.responses["create_project"] = RevisionConflict("Concurrent revision collision")
        r = await client.post(
            "/api/projects",
            headers=headers,
            json={"name": "Collision", "idempotency_key": "12345678"},
        )
        assert r.status_code == 409
        assert r.json()["code"] == "REVISION_CONFLICT"

        # LimitExceeded -> 413
        fake_service.responses["create_project"] = LimitExceeded("Payload limit exceeded")
        r = await client.post(
            "/api/projects",
            headers=headers,
            json={"name": "TooLarge", "idempotency_key": "12345678"},
        )
        assert r.status_code == 413
        assert r.json()["code"] == "LIMIT_EXCEEDED"

        # Unexpected Exception -> 500 INTERNAL with request_id
        fake_service.responses["create_project"] = RuntimeError("Database disk failure")
        r = await client.post(
            "/api/projects",
            headers=headers,
            json={"name": "Error", "idempotency_key": "12345678"},
        )
        assert r.status_code == 500
        assert r.json()["code"] == "INTERNAL"
        assert "request_id" in r.json()

        # RequestValidationError -> 400 INVALID_INPUT with sanitized details (no raw 'input')
        val_resp = await client.post(
            "/api/projects",
            headers=headers,
            json={"name": 12345, "idempotency_key": "short"},
        )
        assert val_resp.status_code == 400
        assert val_resp.json()["code"] == "INVALID_INPUT"
        details = val_resp.json()["details"]
        assert isinstance(details, list)
        assert len(details) > 0
        for err in details:
            assert "input" not in err


def test_session_store_capacity_bound() -> None:
    from refcollector.web.session import SessionStore

    store = SessionStore(ttl_seconds=3600.0, max_sessions=3)
    s1 = store.create_session()
    s2 = store.create_session()
    s3 = store.create_session()
    # Access s1 to make s2 the oldest accessed
    assert store.get_session(s1.session_id) is not None
    # 4th session creation evicts oldest (s2)
    s4 = store.create_session()
    assert store.get_session(s2.session_id) is None
    assert store.get_session(s1.session_id) is not None
    assert store.get_session(s3.session_id) is not None
    assert store.get_session(s4.session_id) is not None


def test_cli_serve_web_invokes_run_web(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from refcollector.cli.main import app

    called: list[tuple[Any, str, int]] = []

    def mock_run_web(settings: Any, host: str, port: int) -> None:
        called.append((settings, host, port))

    monkeypatch.setattr("refcollector.web.server.run_web", mock_run_web)

    runner = CliRunner()
    result = runner.invoke(app, ["serve", "web", "--host", "127.0.0.1", "--port", "8080"])
    assert result.exit_code == 0
    assert len(called) == 1
    assert called[0][1] == "127.0.0.1"
    assert called[0][2] == 8080


@pytest.mark.anyio
async def test_references_query_filters_and_validation(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})

        # 1. classification_is_null=true
        r = await client.get(
            "/api/projects/prj_1/references?classification_is_null=true",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 200
        req = fake_service.calls[-1][2]
        assert req.filter is not None
        assert req.filter.classification_is_null is True

        # 2. classification_is_null=false (false semantics)
        r = await client.get(
            "/api/projects/prj_1/references?classification_is_null=false",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 200
        req = fake_service.calls[-1][2]
        assert req.filter is not None
        assert req.filter.classification_is_null is False

        # 3. Conflicting classification and classification_is_null -> 400 INVALID_INPUT
        r = await client.get(
            "/api/projects/prj_1/references?classification=unknown&classification_is_null=true",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 400
        data = r.json()
        assert data["code"] == "INVALID_INPUT"
        for err in data["details"]:
            assert "input" not in err
            assert "ctx" not in err

        # 4. Invalid limit -> 400 INVALID_INPUT, not 500 INTERNAL
        r = await client.get(
            "/api/projects/prj_1/references?limit=999",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"

        # 5. Invalid sort -> 400 INVALID_INPUT, not 500 INTERNAL
        r = await client.get(
            "/api/projects/prj_1/references?sort=invalid_sort",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_INPUT"


@pytest.mark.anyio
async def test_content_disposition_sanitization(fake_service: FakeService) -> None:
    cases = [
        ('thesis "draft".pdf', 'filename="thesis__draft_.pdf"', "filename*=UTF-8''thesis%20_draft_.pdf"),
        ("../../secret.pdf", 'filename="secret.pdf"', "filename*=UTF-8''secret.pdf"),
        (
            "paper\r\nEvil: injected.pdf",
            'filename="paper__Evil__injected.pdf"',
            "filename*=UTF-8''paper__Evil%3A%20injected.pdf",
        ),
        ("論文.pdf", 'filename="__.pdf"', "filename*=UTF-8''%E8%AB%96%E6%96%87.pdf"),
    ]

    for raw_name, exp_ascii, exp_star in cases:
        chunk = Chunk(
            descriptor={"name": raw_name, "media_type": "application/pdf"},
            data=b"test data",
            next_offset=9,
            eof=True,
        )
        fake_service.responses["read_project_file"] = lambda ctx, req, c=chunk: c

        handle = MockAppHandle(fake_service)
        app = create_app(handle=handle, host="127.0.0.1", port=8000)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
            r = await client.get(
                "/api/projects/prj_1/files/artifact/art_1",
                headers={"Host": "127.0.0.1:8000"},
            )
            assert r.status_code == 200
            cd = r.headers["content-disposition"]
            assert exp_ascii in cd
            assert exp_star in cd
            assert "\r" not in cd
            assert "\n" not in cd


@pytest.mark.anyio
async def test_rc_error_payload_contract_and_rate_limiting(fake_service: FakeService) -> None:
    handle = MockAppHandle(fake_service)
    app = create_app(handle=handle, host="127.0.0.1", port=8000)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        sess = await client.get("/api/session", headers={"Host": "127.0.0.1:8000"})
        csrf = sess.json()["csrf_token"]
        headers = {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": csrf,
        }

        fake_service.responses["create_project"] = RateLimited(
            "Rate limit reached for provider",
            retry_after=30.0,
        )
        r = await client.post(
            "/api/projects",
            headers=headers,
            json={"name": "RateLimitTest", "idempotency_key": "12345678"},
        )
        assert r.status_code == 429
        data = r.json()
        assert data["code"] == "RATE_LIMITED"
        assert data["retryable"] is True
        assert data["recovery_hint"] == "Wait for Retry-After."
        assert data["retry_after"] == 30.0
        assert r.headers["retry-after"] == "30"


def test_session_store_thread_safety_and_absolute_expiry() -> None:
    import concurrent.futures
    import time

    from refcollector.web.session import SessionStore

    with pytest.raises(ValueError):
        SessionStore(ttl_seconds=0)
    with pytest.raises(ValueError):
        SessionStore(ttl_seconds=-10)
    with pytest.raises(ValueError):
        SessionStore(max_sessions=0)
    with pytest.raises(ValueError):
        SessionStore(max_lifetime_seconds=0)

    # Absolute expiry test
    store = SessionStore(ttl_seconds=10.0, max_lifetime_seconds=0.1)
    s = store.create_session()
    sid = s.session_id
    assert store.get_session(sid) is not None
    time.sleep(0.15)
    assert store.get_session(sid) is None

    # Idle expiry test
    store_idle = SessionStore(ttl_seconds=0.1, max_lifetime_seconds=10.0)
    s2 = store_idle.create_session()
    time.sleep(0.15)
    assert store_idle.get_session(s2.session_id) is None

    # Concurrent thread safety test
    stress_store = SessionStore(ttl_seconds=3600.0, max_sessions=50)

    def worker(idx: int) -> None:
        for _ in range(20):
            sess = stress_store.create_session()
            stress_store.get_session(sess.session_id)
            stress_store.validate_csrf(sess.session_id, sess.csrf_token)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(8)]
        for f in futures:
            f.result()

    assert len(stress_store._sessions) <= 50
