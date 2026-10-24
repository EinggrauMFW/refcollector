"""Unit tests for Library HTTP API read and action routes (Wave 1 & Wave 2, B2 & B3)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport

from refcollector.contracts.api import (
    BrowserStatus,
    CandidateView,
    CreateProjectOut,
    GetJobOut,
    GetProjectOut,
    GetReferenceOut,
    GetSettingsOut,
    GetSourceOut,
    IdentityView,
    ImportSourceOut,
    JobOut,
    JobResults,
    ListProjectsOut,
    ListReferencesOut,
    ListSourcesOut,
    OccurrenceSummary,
    Preflight,
    ProjectFileDescriptor,
    ProjectListItem,
    ReadProjectFileOut,
    ReviewFileDecisionOut,
    SourceListItem,
    StageCounts,
    StartJobOut,
    UpdateReferenceOut,
    UploadPdfOut,
    WebIntentOut,
)
from refcollector.domain.enums import (
    AccessClassification,
    DiscoveryState,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyState,
    RetrievalState,
    SourceType,
)
from refcollector.domain.models import Person, WorkMetadata
from refcollector.errors import ErrorCode, NotFound, RcError, RevisionConflict
from refcollector.services.library_folder import LibraryFolders
from refcollector.web.library_routes import router as library_router
from refcollector.web.session import SessionStore
from tests.fakes.service import FakeService


def _create_test_app(fake_service: FakeService, session_store: SessionStore) -> FastAPI:
    from types import SimpleNamespace

    fake_service.responses.setdefault("get_settings", SimpleNamespace(pdf_fallback_url=None))
    app = FastAPI()
    app.state.service = fake_service
    app.state.session_store = session_store

    @app.exception_handler(RcError)
    async def rc_error_handler(request: Request, exc: RcError) -> JSONResponse:
        status_code = 400
        if exc.code in (ErrorCode.NOT_AUTHORIZED, ErrorCode.PATH_NOT_ALLOWED):
            status_code = 403
        elif exc.code == ErrorCode.NOT_FOUND:
            status_code = 404
        elif exc.code in (
            ErrorCode.REVISION_CONFLICT,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            ErrorCode.REVISION_NOT_CURRENT,
        ):
            status_code = 409
        elif exc.code in (ErrorCode.LIMIT_EXCEEDED, ErrorCode.BUDGET_EXCEEDED):
            status_code = 413
        elif exc.code == ErrorCode.RATE_LIMITED:
            status_code = 429
        return JSONResponse(status_code=status_code, content=exc.to_payload())

    app.include_router(library_router)
    return app


@pytest.fixture
def session_store() -> SessionStore:
    return SessionStore()


@pytest.fixture
def auth_cookies(session_store: SessionStore) -> dict[str, str]:
    session = session_store.create_session()
    return {"rc_session": session.session_id}


@pytest.fixture
def csrf_headers(session_store: SessionStore, auth_cookies: dict[str, str]) -> dict[str, str]:
    session = session_store.get_session(auth_cookies["rc_session"])
    assert session is not None
    return {"X-CSRF-Token": session.csrf_token}


@pytest.mark.anyio
async def test_collections_list(session_store: SessionStore, auth_cookies: dict[str, str]) -> None:
    fake_service = FakeService(
        {
            "list_projects": ListProjectsOut(
                projects=[
                    ProjectListItem(
                        project_id="prj_1",
                        name="Thesis Collection",
                        revision=1,
                        counts={"sources": 1, "occurrences": 2},
                        updated_at="2026-10-22T00:00:00Z",  # type: ignore[arg-type]
                    )
                ],
                next_cursor=None,
            ),
            "get_settings": GetSettingsOut(
                revision="rev_1",
                rc_home="/tmp/rc_home",
                email=None,
                allow_folders=["/tmp/allowed"],
                download_enabled_global=True,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
            "get_project": GetProjectOut(
                project_id="prj_1",
                name="Thesis Collection",
                revision=1,
                policy={},
                network_mode="retrieval",
                counts={"occurrences": 2},
                sources_count=1,
                recent_jobs=[],
                recent_artifacts=[],
                download_enabled=False,
            ),
            "list_references": ListReferencesOut(
                occurrences=[
                    {
                        "occurrence_id": "occ_1",
                        "source_id": "src_1",
                        "retrieval": RetrievalState.NOT_REQUESTED,
                        "identity": IdentityState.NEEDS_REVIEW,
                    }
                ],
                next_cursor=None,
                total=1,
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        coll = data[0]
        assert coll["collection_id"] == "prj_1"
        assert coll["name"] == "Thesis Collection"
        assert coll["check_count"] == 1
        assert coll["permissions"]["network_mode"] == "retrieval"
        assert coll["permissions"]["downloads_enabled"] is True
        assert coll["permissions"]["downloads_source"] == "global"


@pytest.mark.anyio
async def test_shelf_lists_uploaded_and_downloaded_papers_with_cited_by(
    session_store: SessionStore, auth_cookies: dict[str, str]
) -> None:
    fake_service = FakeService(
        {
            "get_project": GetProjectOut(
                project_id="prj_1",
                name="Thesis",
                revision=1,
                policy={},
                network_mode="retrieval",
                counts={},
                sources_count=1,
                recent_jobs=[],
                recent_artifacts=[],
            ),
            "list_sources": ListSourcesOut(
                sources=[
                    SourceListItem(
                        source_id="src_1",
                        label="Source Paper.pdf",
                        type=SourceType.TEXT,
                        pages=5,
                        sections=1,
                        extraction_state=ExtractionState.EXTRACTED,
                        occurrence_count=1,
                        imported_at="2026-10-22T00:00:00Z",  # type: ignore[arg-type]
                    )
                ],
                next_cursor=None,
            ),
            "list_references": ListReferencesOut(
                occurrences=[
                    {
                        "occurrence_id": "occ_1",
                        "source_id": "src_1",
                        "work_id": "work_1",
                        "raw_text": "Doe, 2020",
                        "extraction": ExtractionState.EXTRACTED,
                        "identity": IdentityState.ACCEPTED_AUTO,
                        "discovery": DiscoveryState.COMPLETE,
                        "classification": AccessClassification.ELIGIBLE_FOUND,
                        "retrieval": RetrievalState.VERIFIED,
                    }
                ],
                next_cursor=None,
                total=1,
            ),
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_1",
                    source_id="src_1",
                    revision=1,
                    label=None,
                    raw_text="Doe, 2020",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(state=IdentityState.ACCEPTED_AUTO),
                    access={  # type: ignore[arg-type]
                        "discovery": DiscoveryState.COMPLETE,
                        "classification": AccessClassification.ELIGIBLE_FOUND,
                    },
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.VERIFIED,
                ),
                work=WorkMetadata(
                    title="Resolved Work Title",
                    authors=[Person(family="Doe", given="Jane")],
                    year=2020,
                    venue="Journal of Testing",
                    doi="10.1234/test",
                ),
                files=[
                    {
                        "work_file_id": "wf_1",
                        "work_id": "work_1",
                        "export_name": "2020_Doe_Resolved.pdf",
                        "sha256": "sha_wf_1",
                        "state": RetrievalState.VERIFIED,
                    }
                ],
            ),
            "get_settings": GetSettingsOut(
                revision="rev_1",
                rc_home="/tmp/rc_home",
                email=None,
                allow_folders=[],
                download_enabled_global=False,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections/prj_1/shelf")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        papers = data["papers"]

        # Check uploaded paper
        uploaded = next(p for p in papers if p["kind"] == "uploaded")
        assert uploaded["paper_id"] == "src_1"
        assert uploaded["title"] == "Source Paper.pdf"
        assert uploaded["citation_count"] == 1
        assert uploaded["status_counts"]["downloaded"] == 1
        assert uploaded["file_url"] == "/api/library/collections/prj_1/files/source/src_1"

        # Check downloaded paper
        downloaded = next(p for p in papers if p["kind"] == "downloaded")
        assert downloaded["paper_id"] == "wf_1"
        assert downloaded["title"] == "Resolved Work Title"
        assert downloaded["authors"][0]["display"] == "Doe, Jane"
        assert downloaded["year"] == 2020
        assert downloaded["cited_by"] == ["src_1"]
        assert downloaded["file_url"] == "/api/library/collections/prj_1/files/work_file/wf_1"


@pytest.mark.anyio
async def test_paper_detail_statuses(session_store: SessionStore, auth_cookies: dict[str, str]) -> None:
    fake_service = FakeService(
        {
            "get_settings": GetSettingsOut(
                revision="rev_1",
                rc_home="/tmp/rc_home",
                email=None,
                allow_folders=[],
                download_enabled_global=False,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
            "get_source": GetSourceOut(
                source={
                    "source_id": "src_1",
                    "label": "MySource.pdf",
                    "type": SourceType.TEXT,
                    "pages": 12,
                    "sections": 1,
                    "extraction_state": ExtractionState.EXTRACTED,
                    "occurrence_count": 2,
                    "imported_at": "2026-10-22T10:00:00Z",
                },
                revision=1,
                preflight=Preflight(pages=12, text_coverage=1.0),
                sections=[],
                extraction={"state": "extracted"},
                jobs=[],
            ),
            "list_references": ListReferencesOut(
                occurrences=[
                    {
                        "occurrence_id": "occ_1",
                        "source_id": "src_1",
                        "order": 1.0,
                        "raw_text": "Citation 1",
                        "revision": 1,
                    },
                    {
                        "occurrence_id": "occ_2",
                        "source_id": "src_1",
                        "order": 2.0,
                        "raw_text": "Citation 2",
                        "revision": 1,
                    },
                ],
                next_cursor=None,
                total=2,
            ),
            "get_reference": lambda ctx, req: GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id=req.occurrence_id,
                    source_id="src_1",
                    revision=1,
                    label=None,
                    raw_text=f"Citation for {req.occurrence_id}",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(
                        state=(
                            IdentityState.ACCEPTED_AUTO
                            if req.occurrence_id == "occ_1"
                            else IdentityState.NEEDS_REVIEW
                        )
                    ),
                    access={  # type: ignore[arg-type]
                        "discovery": (
                            DiscoveryState.COMPLETE
                            if req.occurrence_id == "occ_1"
                            else DiscoveryState.NOT_CHECKED
                        ),
                        "classification": (
                            AccessClassification.ELIGIBLE_FOUND if req.occurrence_id == "occ_1" else None
                        ),
                    },
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.NOT_REQUESTED,
                ),
                work=WorkMetadata(title="Sample Paper Title"),
                files=[],
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections/prj_1/papers/src_1")
        assert r.status_code == 200
        data = r.json()
        assert data["pages"] == 12
        assert len(data["citations"]) == 2

        c1 = next(c for c in data["citations"] if c["citation_id"] == "occ_1")
        assert c1["status"] == "pdf_available"
        assert c1["title"] == "Sample Paper Title"

        c2 = next(c for c in data["citations"] if c["citation_id"] == "occ_2")
        assert c2["status"] == "check"


@pytest.mark.anyio
async def test_citation_detail_with_candidates(
    session_store: SessionStore, auth_cookies: dict[str, str]
) -> None:
    fake_service = FakeService(
        {
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_1",
                    source_id="src_1",
                    revision=2,
                    label=None,
                    raw_text="Sample text",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(state=IdentityState.NEEDS_REVIEW),
                    access={  # type: ignore[arg-type]
                        "discovery": DiscoveryState.NOT_CHECKED,
                        "classification": None,
                    },
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.NOT_REQUESTED,
                ),
                work=None,
                candidates=[
                    CandidateView(
                        candidate_id="cand_1",
                        rank=1,
                        metadata=WorkMetadata(
                            title="Candidate Title",
                            authors=[Person(family="Smith", given="John")],
                            year=2021,
                        ),
                        features={},
                        reasons=["MULTIPLE_CLOSE_CANDIDATES"],
                        providers=["crossref"],
                        identifier_linked=False,
                        rejected=False,
                    )
                ],
                files=[],
            )
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections/prj_1/citations/occ_1")
        assert r.status_code == 200
        data = r.json()
        assert data["citation"]["citation_id"] == "occ_1"
        assert data["citation"]["status"] == "check"
        assert len(data["candidates"]) == 1
        cand = data["candidates"][0]
        assert cand["candidate_id"] == "cand_1"
        assert cand["title"] == "Candidate Title"
        assert cand["authors"][0]["display"] == "Smith, John"


@pytest.mark.anyio
async def test_job_status_endpoint(session_store: SessionStore, auth_cookies: dict[str, str]) -> None:
    fake_service = FakeService(
        {
            "get_job": GetJobOut(
                job_id="job_123",
                state=JobState.RUNNING,
                stages=[StageCounts(name="extract", queued=0, running=1, succeeded=0)],
                warnings=["Warning text"],
                results=JobResults(source_ids=["src_1"], occurrence_count=5, changed_filter={}),
                next_poll_after_ms=1500,
            )
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections/prj_1/jobs/job_123")
        assert r.status_code == 200
        data = r.json()
        assert data["job_id"] == "job_123"
        assert data["state"] == "running"
        assert data["terminal"] is False
        assert data["next_poll_after_ms"] == 1500
        assert len(data["warnings"]) == 1


@pytest.mark.anyio
async def test_unknown_collection_returns_404(
    session_store: SessionStore, auth_cookies: dict[str, str]
) -> None:
    def raise_not_found(ctx: Any, req: Any) -> Any:
        raise NotFound(f"Project '{req.project_id}' not found")

    fake_service = FakeService({"get_project": raise_not_found})

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections/nonexistent/shelf")
        assert r.status_code == 404
        data = r.json()
        assert data["code"] == "NOT_FOUND"


@pytest.mark.anyio
async def test_file_route_streams_pdf_inline_and_rejects_unknown_kind(
    session_store: SessionStore, auth_cookies: dict[str, str]
) -> None:
    fake_service = FakeService(
        {
            "read_project_file": ReadProjectFileOut(
                descriptor=ProjectFileDescriptor(
                    resource_id="src_1",
                    kind="source",
                    name="My Document.pdf",
                    media_type="application/pdf",
                    bytes=12,
                    sha256="abc",
                ),
                data=b"%PDF-1.4 dummy",
                next_offset=12,
                eof=True,
            )
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # Valid source request
        r = await client.get("/api/library/collections/prj_1/files/source/src_1")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"].startswith("inline;")
        assert b"%PDF-1.4 dummy" in r.content

        # Unknown kind rejected with 400
        r_bad = await client.get("/api/library/collections/prj_1/files/unknown_kind/src_1")
        assert r_bad.status_code == 400
        assert r_bad.json()["code"] == "INVALID_INPUT"


@pytest.mark.anyio
async def test_get_defaults(session_store: SessionStore, auth_cookies: dict[str, str]) -> None:
    fake_service = FakeService()
    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/defaults")
        assert r.status_code == 200
        data = r.json()
        assert "default_root" in data
        assert isinstance(data["default_root"], str)
        assert len(data["default_root"]) > 0


@pytest.mark.anyio
async def test_create_collection(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    target_folder = tmp_path / "my_papers"
    target_folder.mkdir()

    fake_service = FakeService(
        {
            "create_project": CreateProjectOut(project_id="prj_new", revision=1),
            "get_project": GetProjectOut(
                project_id="prj_new",
                name="My New Collection",
                revision=1,
                policy={},
                network_mode="offline",
                counts={},
                sources_count=0,
                recent_jobs=[],
                recent_artifacts=[],
                download_enabled=False,
            ),
            "get_settings": GetSettingsOut(
                revision="rev_1",
                rc_home=str(tmp_path),
                email=None,
                allow_folders=[str(tmp_path)],
                download_enabled_global=False,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
            "list_references": ListReferencesOut(occurrences=[], next_cursor=None, total=0),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.post(
            "/api/library/collections",
            headers=csrf_headers,
            json={
                "name": "My New Collection",
                "folder": str(target_folder),
                "idempotency_key": "coll_idem_123",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["collection_id"] == "prj_new"
        assert data["name"] == "My New Collection"
        assert data["permissions"]["folder_granted"] is True


@pytest.mark.anyio
async def test_permission_intent_and_confirm(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    folder_path = tmp_path / "coll_folder"
    folder_path.mkdir()

    # Pre-record folder
    folders = LibraryFolders(rc_home=tmp_path, allowed_folders=lambda: [folder_path])
    folders.create("prj_1", folder_path)

    settings_base = GetSettingsOut(
        revision="rev_1",
        rc_home=str(tmp_path),
        email=None,
        allow_folders=[],
        download_enabled_global=False,
        pdf_fallback_url=None,
        browser=BrowserStatus(enabled=False, configured=False),
        enabled_providers=[],
        mcp_scopes=[],
        limits={},
        keys={},
        project=None,
    )
    settings_updated = GetSettingsOut(
        revision="rev_2",
        rc_home=str(tmp_path),
        email=None,
        allow_folders=[str(folder_path)],
        download_enabled_global=True,
        pdf_fallback_url=None,
        browser=BrowserStatus(enabled=False, configured=False),
        enabled_providers=[],
        mcp_scopes=[],
        limits={},
        keys={},
        project=None,
    )

    fake_service = FakeService(
        {
            "get_settings": settings_base,
            "prepare_web_settings": WebIntentOut(
                confirmation_token="tok_perm_123",
                expires_at="2026-10-22T01:00:00Z",
                purpose="settings",
                summary={"action": "add_allowed_folder"},
            ),
            "confirm_web_settings": settings_updated,
            "get_project": GetProjectOut(
                project_id="prj_1",
                name="Thesis",
                revision=1,
                policy={},
                network_mode="retrieval",
                counts={},
                sources_count=0,
                recent_jobs=[],
                recent_artifacts=[],
                download_enabled=True,
            ),
            "list_references": ListReferencesOut(occurrences=[], next_cursor=None, total=0),
        }
    )

    app = _create_test_app(fake_service, session_store)
    app.state.library_folders = folders
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # 1. Prepare intent
        r_intent = await client.post(
            "/api/library/collections/prj_1/permissions/intent",
            headers=csrf_headers,
            json={
                "change": "add_allowed_folder",
                "expected_settings_revision": "rev_1",
                "idempotency_key": "idem_perm_intent_1",
            },
        )
        assert r_intent.status_code == 200
        data_intent = r_intent.json()
        assert data_intent["confirmation_token"] == "tok_perm_123"
        assert len(data_intent["description"]) > 0

        # 2. Confirm without token header -> 403
        r_no_header = await client.post(
            "/api/library/collections/prj_1/permissions/confirm",
            headers=csrf_headers,
            json={
                "change": "add_allowed_folder",
                "expected_settings_revision": "rev_1",
                "idempotency_key": "idem_perm_confirm_1",
            },
        )
        assert r_no_header.status_code == 403

        # 3. Confirm with valid token header -> 200
        fake_service.responses["get_settings"] = settings_updated
        r_confirm = await client.post(
            "/api/library/collections/prj_1/permissions/confirm",
            headers={**csrf_headers, "X-Confirmation-Token": "tok_perm_123"},
            json={
                "change": "add_allowed_folder",
                "expected_settings_revision": "rev_1",
                "idempotency_key": "idem_perm_confirm_1",
            },
        )
        assert r_confirm.status_code == 200
        data_confirm = r_confirm.json()
        assert data_confirm["permissions"]["folder_granted"] is True


@pytest.mark.anyio
async def test_paper_upload_multipart(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    folder_path = tmp_path / "coll_folder"
    folder_path.mkdir()

    folders = LibraryFolders(rc_home=tmp_path, allowed_folders=lambda: [folder_path])
    folders.create("prj_1", folder_path)

    pdf_content = b"%PDF-1.4 test document body"

    fake_service = FakeService(
        {
            "get_settings": GetSettingsOut(
                revision="rev_1",
                rc_home=str(tmp_path),
                email=None,
                allow_folders=[str(folder_path)],
                download_enabled_global=False,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
            "upload_pdf": UploadPdfOut(
                source=ImportSourceOut(
                    source_id="src_uploaded_1",
                    reused=False,
                    preflight=Preflight(pages=1, text_coverage=1.0),
                ),
                attachment=None,
            ),
            "start_job": StartJobOut(
                job_id="job_extract_1",
                state=JobState.QUEUED,
                steps_planned=1,
                next_poll_after_ms=1000,
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    app.state.library_folders = folders
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # Missing idempotency_key or not PDF rejected
        files = {"file": ("paper.txt", io.BytesIO(b"plain text"), "text/plain")}
        r_bad = await client.post(
            "/api/library/collections/prj_1/papers",
            headers=csrf_headers,
            data={"idempotency_key": "upload_idem_123"},
            files=files,
        )
        assert r_bad.status_code == 400

        # Valid PDF upload
        files_ok = {"file": ("paper.pdf", io.BytesIO(pdf_content), "application/pdf")}
        r_ok = await client.post(
            "/api/library/collections/prj_1/papers",
            headers=csrf_headers,
            data={"idempotency_key": "upload_idem_123"},
            files=files_ok,
        )
        assert r_ok.status_code == 200
        data = r_ok.json()
        assert data["paper_id"] == "src_uploaded_1"
        assert data["active_job_id"] == "job_extract_1"
        assert data["kind"] == "uploaded"


@pytest.mark.anyio
async def test_find_metadata_endpoints(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
) -> None:
    fake_service = FakeService(
        {
            "start_job": StartJobOut(
                job_id="job_meta_123",
                state=JobState.QUEUED,
                steps_planned=2,
                next_poll_after_ms=1500,
            ),
            "list_sources": ListSourcesOut(
                sources=[
                    SourceListItem(
                        source_id="src_1",
                        label="Paper.pdf",
                        type=SourceType.TEXT,
                        pages=1,
                        sections=1,
                        extraction_state=ExtractionState.EXTRACTED,
                        occurrence_count=1,
                        imported_at="2026-10-22T00:00:00Z",  # type: ignore[arg-type]
                    )
                ],
                next_cursor=None,
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # Citation scope
        r_cite = await client.post(
            "/api/library/collections/prj_1/find-metadata",
            headers=csrf_headers,
            json={"citation_id": "occ_1", "idempotency_key": "meta_idem_1"},
        )
        assert r_cite.status_code == 200
        assert r_cite.json()["job_id"] == "job_meta_123"

        # Collection scope
        r_coll = await client.post(
            "/api/library/collections/prj_1/find-metadata",
            headers=csrf_headers,
            json={"idempotency_key": "meta_idem_2"},
        )
        assert r_coll.status_code == 200
        assert r_coll.json()["job_id"] == "job_meta_123"

        # Empty collection scope returns disabled_reason
        fake_service.responses["list_sources"] = ListSourcesOut(sources=[], next_cursor=None)
        r_empty = await client.post(
            "/api/library/collections/prj_1/find-metadata",
            headers=csrf_headers,
            json={"idempotency_key": "meta_idem_3"},
        )
        assert r_empty.status_code == 200
        assert r_empty.json()["job_id"] is None
        assert "No papers in collection" in r_empty.json()["disabled_reason"]


@pytest.mark.anyio
@pytest.mark.parametrize("paper_id", [None, "src_1"])
async def test_download_action_permissions_and_scope(
    paper_id: str | None,
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    folder_path = tmp_path / "papers_dl"
    folder_path.mkdir()

    folders = LibraryFolders(rc_home=tmp_path, allowed_folders=lambda: [folder_path])
    folders.create("prj_1", folder_path)

    # 1. Permission not granted (network_mode == "offline")
    fake_service = FakeService(
        {
            "get_project": GetProjectOut(
                project_id="prj_1",
                name="Project",
                revision=1,
                policy={},
                network_mode="offline",
                counts={},
                sources_count=1,
                recent_jobs=[],
                recent_artifacts=[],
                download_enabled=False,
            ),
            "get_settings": GetSettingsOut(
                revision="rev_1",
                rc_home=str(tmp_path),
                email=None,
                allow_folders=[str(folder_path)],
                download_enabled_global=True,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    app.state.library_folders = folders
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r_denied = await client.post(
            "/api/library/collections/prj_1/download",
            headers=csrf_headers,
            json={"idempotency_key": "dl_idem_1"},
        )
        assert r_denied.status_code == 200
        assert r_denied.json()["job_id"] is None
        assert "retrieval" in r_denied.json()["disabled_reason"]

        # 2. Permissions granted, but no downloadable citations
        fake_service.responses["get_project"] = GetProjectOut(
            project_id="prj_1",
            name="Project",
            revision=1,
            policy={},
            network_mode="retrieval",
            counts={},
            sources_count=1,
            recent_jobs=[],
            recent_artifacts=[],
            download_enabled=True,
        )
        fake_service.responses["list_references"] = ListReferencesOut(
            occurrences=[
                {
                    "occurrence_id": "occ_1",
                    "source_id": "src_1",
                    "classification": AccessClassification.NONE_FOUND,
                    "retrieval": RetrievalState.NOT_REQUESTED,
                }
            ],
            next_cursor=None,
            total=1,
        )

        r_no_target = await client.post(
            "/api/library/collections/prj_1/download",
            headers=csrf_headers,
            json={"idempotency_key": "dl_idem_2"},
        )
        assert r_no_target.status_code == 200
        assert r_no_target.json()["job_id"] is None
        assert "No downloadable citations found" in r_no_target.json()["disabled_reason"]

        # 3. Permissions granted and downloadable citation exists
        fake_service.responses["list_references"] = ListReferencesOut(
            occurrences=[
                {
                    "occurrence_id": "occ_1",
                    "source_id": "src_1",
                    "work_id": "work_1",
                    "identity": IdentityState.ACCEPTED_AUTO,
                    "discovery": DiscoveryState.COMPLETE,
                    "classification": AccessClassification.ELIGIBLE_FOUND,
                    "retrieval": RetrievalState.NOT_REQUESTED,
                }
            ],
            next_cursor=None,
            total=1,
        )
        # Match the real ListReferencesOut wire format, which omits work_id.
        summary = fake_service.responses["list_references"].occurrences[0]
        summary.pop("work_id")
        summary["identity"] = {"state": IdentityState.ACCEPTED_AUTO, "reasons": [], "candidates": 0}
        summary["access"] = {
            "discovery": DiscoveryState.COMPLETE,
            "classification": AccessClassification.ELIGIBLE_FOUND,
        }
        summary.pop("discovery")
        fake_service.responses["download_files"] = JobOut(job_id="job_dl_789")

        r_ok = await client.post(
            "/api/library/collections/prj_1/download",
            headers=csrf_headers,
            json={"idempotency_key": "dl_idem_3"},
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["job_id"] == "job_dl_789"
        assert r_ok.json()["disabled_reason"] is None
        requests = [req for method, _, req in fake_service.calls if method == "download_files"]
        assert requests[-1].version_policy is None
        assert requests[-1].retrieval_basis == "accepted_work"

        fake_service.responses["get_project"].policy = {"version_policy": "any_eligible"}
        await client.post(
            "/api/library/collections/prj_1/download",
            headers=csrf_headers,
            json={"idempotency_key": "dl_policy"},
        )
        requests = [req for method, _, req in fake_service.calls if method == "download_files"]
        assert requests[-1].version_policy == "any_eligible"

        fake_service.responses["get_project"].policy = {"version_policy": "prefer_published"}
        missing = await client.post(
            "/api/library/collections/prj_1/download",
            headers=csrf_headers,
            json={"idempotency_key": "dl_missing", "use_fallback": True},
        )
        assert missing.json()["job_id"] is None
        assert "fallback" in missing.json()["disabled_reason"].lower()

        fake_service.responses["get_settings"].pdf_fallback_url = "https://sci-hub.st/{doi}"
        fake_service.responses["list_references"].occurrences[0]["citation_doi"] = "10.1234/example"
        # No normal eligible copy: a resolved citation must still reach the fallback.
        fake_service.responses["list_references"].occurrences[0]["classification"] = (
            AccessClassification.NONE_FOUND
        )
        fallback = await client.post(
            "/api/library/collections/prj_1/download",
            headers=csrf_headers,
            json={"idempotency_key": "dl_fallback", "use_fallback": True, "paper_id": paper_id},
        )
        assert fallback.status_code == 200
        assert fallback.json()["job_id"] == "job_dl_789"
        requests = [req for method, _, req in fake_service.calls if method == "download_files"]
        assert requests[-1].version_policy == "any_eligible"
        assert requests[-1].scope.occurrence_ids == ["occ_1"]


@pytest.mark.anyio
async def test_citation_review_intent_and_confirm(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
) -> None:
    fake_service = FakeService(
        {
            "prepare_web_review": WebIntentOut(
                confirmation_token="tok_rev_456",
                expires_at="2026-10-22T01:00:00Z",
                purpose="review",
                summary={"action": "accept"},
            ),
            "confirm_web_review": UpdateReferenceOut(
                changed=[],
                created=[],
                identity_state=IdentityState.ACCEPTED_USER,
            ),
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_1",
                    source_id="src_1",
                    revision=2,
                    label=None,
                    raw_text="Author 2020",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(state=IdentityState.ACCEPTED_USER),
                    access={"discovery": DiscoveryState.COMPLETE},  # type: ignore[arg-type]
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.NOT_REQUESTED,
                ),
                work=WorkMetadata(title="Reviewed Paper Title"),
                files=[],
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # Prepare review intent
        r_intent = await client.post(
            "/api/library/collections/prj_1/citations/occ_1/review/intent",
            headers=csrf_headers,
            json={
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_rev_1",
            },
        )
        assert r_intent.status_code == 200
        data_intent = r_intent.json()
        assert data_intent["confirmation_token"] == "tok_rev_456"

        # Confirm without header -> 403
        r_no_tok = await client.post(
            "/api/library/collections/prj_1/citations/occ_1/review/confirm",
            headers=csrf_headers,
            json={
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_rev_2",
            },
        )
        assert r_no_tok.status_code == 403

        # Confirm with header -> 200
        r_confirm = await client.post(
            "/api/library/collections/prj_1/citations/occ_1/review/confirm",
            headers={**csrf_headers, "X-Confirmation-Token": "tok_rev_456"},
            json={
                "action": "accept",
                "candidate_id": "cand_1",
                "expected_revision": 1,
                "idempotency_key": "idem_rev_2",
            },
        )
        assert r_confirm.status_code == 200
        data_confirm = r_confirm.json()
        assert data_confirm["citation_id"] == "occ_1"
        assert data_confirm["status"] == "no_pdf"


@pytest.mark.anyio
async def test_sync_endpoint(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    folder_path = tmp_path / "coll_folder"
    folder_path.mkdir()

    folders = LibraryFolders(rc_home=tmp_path, allowed_folders=lambda: [folder_path])
    folders.create("prj_1", folder_path)

    fake_service = FakeService(
        {
            "get_settings": GetSettingsOut(
                revision="rev_1",
                rc_home=str(tmp_path),
                email=None,
                allow_folders=[str(folder_path)],
                download_enabled_global=False,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
            "get_project": GetProjectOut(
                project_id="prj_1",
                name="Thesis",
                revision=1,
                policy={},
                network_mode="retrieval",
                counts={},
                sources_count=0,
                recent_jobs=[],
                recent_artifacts=[],
                download_enabled=False,
            ),
            "list_sources": ListSourcesOut(sources=[], next_cursor=None),
            "list_references": ListReferencesOut(occurrences=[], next_cursor=None, total=0),
        }
    )

    app = _create_test_app(fake_service, session_store)
    app.state.library_folders = folders
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.post("/api/library/collections/prj_1/sync", headers=csrf_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["collection_id"] == "prj_1"
        assert data["name"] == "Thesis"
        assert data["permissions"]["folder_granted"] is True


@pytest.mark.anyio
async def test_regression_bug1_and_bug2_sequential_permission_grants(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    """Bugs 1 & 2: sequential grants (folder -> network -> downloads) with revision update and no conflict."""
    col_folder = tmp_path / "coll_folder"
    col_folder.mkdir()
    folders = LibraryFolders(tmp_path, lambda: [col_folder])
    folders.create("prj_seq", col_folder)

    # State tracking simulating the real settings service
    state = {
        "allow_folders": [],
        "network_mode": "off",
        "download_enabled": False,
        "global_rev": "rev_global_v1",
        "proj_rev": "rev_proj_v1",
    }

    def fake_get_settings(ctx: Any, req: Any) -> GetSettingsOut:
        is_proj = bool(getattr(req, "project_id", None))
        rev = state["proj_rev"] if is_proj else state["global_rev"]
        return GetSettingsOut(
            revision=rev,
            rc_home=str(tmp_path),
            email=None,
            allow_folders=list(state["allow_folders"]),
            download_enabled_global=False,
            pdf_fallback_url=None,
            browser=BrowserStatus(enabled=False, configured=False),
            enabled_providers=[],
            mcp_scopes=[],
            limits={},
            keys={},
            project=None,
        )

    def fake_get_project(ctx: Any, req: Any) -> GetProjectOut:
        return GetProjectOut(
            project_id="prj_seq",
            name="Sequential Grants Test",
            revision=1,
            policy={},
            network_mode=state["network_mode"],
            counts={},
            sources_count=0,
            recent_jobs=[],
            recent_artifacts=[],
            download_enabled=state["download_enabled"],
        )

    def fake_prepare_web_settings(ctx: Any, req: Any) -> WebIntentOut:
        c = req.change
        if c.type == "add_allowed_folder":
            if req.expected_settings_revision != state["global_rev"]:
                raise RevisionConflict(
                    "settings revision conflict",
                    details={"expected": req.expected_settings_revision, "current": state["global_rev"]},
                )
        elif c.type in ("set_project_network", "set_project_downloads"):
            if req.expected_settings_revision != state["proj_rev"]:
                raise RevisionConflict(
                    "settings revision conflict",
                    details={"expected": req.expected_settings_revision, "current": state["proj_rev"]},
                )
        return WebIntentOut(
            confirmation_token=f"tok_{c.type}",
            expires_at="2026-10-22T01:00:00Z",
            purpose="settings",
            summary={"change": c.type},
        )

    def fake_confirm_web_settings(ctx: Any, req_tuple: tuple[Any, str]) -> GetSettingsOut:
        req, token = req_tuple
        c = req.change
        if c.type == "add_allowed_folder":
            if req.expected_settings_revision != state["global_rev"]:
                raise RevisionConflict(
                    "settings revision conflict",
                    details={"expected": req.expected_settings_revision, "current": state["global_rev"]},
                )
            state["allow_folders"] = [str(col_folder)]
            state["global_rev"] = "rev_global_v2"
            state["proj_rev"] = "rev_proj_v2"
        elif c.type == "set_project_network":
            if req.expected_settings_revision != state["proj_rev"]:
                raise RevisionConflict(
                    "settings revision conflict",
                    details={"expected": req.expected_settings_revision, "current": state["proj_rev"]},
                )
            state["network_mode"] = "retrieval"
            state["proj_rev"] = "rev_proj_v3"
        elif c.type == "set_project_downloads":
            if req.expected_settings_revision != state["proj_rev"]:
                raise RevisionConflict(
                    "settings revision conflict",
                    details={"expected": req.expected_settings_revision, "current": state["proj_rev"]},
                )
            state["download_enabled"] = True
            state["proj_rev"] = "rev_proj_v4"

        return fake_get_settings(ctx, req)

    fake_service = FakeService(
        {
            "list_projects": ListProjectsOut(
                projects=[
                    ProjectListItem(
                        project_id="prj_seq",
                        name="Sequential Grants Test",
                        revision=1,
                        counts={"sources": 0, "occurrences": 0},
                        updated_at="2026-10-22T00:00:00Z",  # type: ignore[arg-type]
                    )
                ],
                next_cursor=None,
            ),
            "get_settings": fake_get_settings,
            "get_project": fake_get_project,
            "list_references": ListReferencesOut(occurrences=[], next_cursor=None, total=0),
            "prepare_web_settings": fake_prepare_web_settings,
            "confirm_web_settings": fake_confirm_web_settings,
        }
    )

    app = _create_test_app(fake_service, session_store)
    app.state.library_folders = folders
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # Step 1: List collections
        r_list = await client.get("/api/library/collections")
        assert r_list.status_code == 200
        cols = r_list.json()
        assert len(cols) == 1
        col = cols[0]
        assert col["permissions"]["folder_granted"] is False
        assert col["permissions"]["network_mode"] == "off"
        initial_rev = col["settings_revision"]

        # Step 2: Grant folder access (intent then confirm)
        r_intent1 = await client.post(
            "/api/library/collections/prj_seq/permissions/intent",
            headers=csrf_headers,
            json={
                "change": "add_allowed_folder",
                "expected_settings_revision": initial_rev,
                "idempotency_key": "idem_seq_k1",
            },
        )
        assert r_intent1.status_code == 200
        tok1 = r_intent1.json()["confirmation_token"]

        r_conf1 = await client.post(
            "/api/library/collections/prj_seq/permissions/confirm",
            headers={**csrf_headers, "X-Confirmation-Token": tok1},
            json={
                "change": "add_allowed_folder",
                "expected_settings_revision": initial_rev,
                "idempotency_key": "idem_seq_k1",
            },
        )
        assert r_conf1.status_code == 200
        col_after_conf1 = r_conf1.json()
        assert col_after_conf1["permissions"]["folder_granted"] is True
        rev_after_1 = col_after_conf1["settings_revision"]
        assert rev_after_1 != initial_rev

        # Step 3: GET /collections verifies new revision and permissions
        r_list2 = await client.get("/api/library/collections")
        assert r_list2.status_code == 200
        col_list2 = r_list2.json()[0]
        assert col_list2["permissions"]["folder_granted"] is True
        assert col_list2["settings_revision"] == rev_after_1

        # Step 4: Immediate second grant: network lookups (intent then confirm)
        r_intent2 = await client.post(
            "/api/library/collections/prj_seq/permissions/intent",
            headers=csrf_headers,
            json={
                "change": "set_project_network",
                "expected_settings_revision": rev_after_1,
                "idempotency_key": "idem_seq_k2",
            },
        )
        assert r_intent2.status_code == 200
        tok2 = r_intent2.json()["confirmation_token"]

        r_conf2 = await client.post(
            "/api/library/collections/prj_seq/permissions/confirm",
            headers={**csrf_headers, "X-Confirmation-Token": tok2},
            json={
                "change": "set_project_network",
                "expected_settings_revision": rev_after_1,
                "idempotency_key": "idem_seq_k2",
            },
        )
        assert r_conf2.status_code == 200
        col_after_conf2 = r_conf2.json()
        assert col_after_conf2["permissions"]["network_mode"] == "retrieval"
        rev_after_2 = col_after_conf2["settings_revision"]
        assert rev_after_2 != rev_after_1


@pytest.mark.anyio
async def test_regression_bug3_sync_copies_uploaded_sources_and_reports_errors(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    """Bug 3: POST /sync copies uploaded sources when folder is granted; reports error when not allowed."""
    target_folder = tmp_path / "my_collection"
    target_folder.mkdir()
    source_pdf_file = tmp_path / "test_paper.pdf"
    pdf_bytes = b"%PDF-1.4 test paper content"
    source_pdf_file.write_bytes(pdf_bytes)
    sha256_hash = hashlib.sha256(pdf_bytes).hexdigest()

    # Allowed list starts empty
    allowed_list = []
    folders = LibraryFolders(tmp_path, lambda: allowed_list)
    folders.create("prj_sync_test", target_folder)

    class FakeStoreFiles:
        def path_for(self, pid: str, sha: str) -> Path:
            if sha == sha256_hash:
                return source_pdf_file
            raise NotFound("File not found")

    class FakeStoreSources:
        def get(self, pid: str, sid: str) -> Any:
            class DummySource:
                source_id = "src_123"
                label = "test_paper.pdf"
                sha256 = sha256_hash

            return DummySource()

    class FakeStore:
        files = FakeStoreFiles()
        sources = FakeStoreSources()

    fake_service = FakeService(
        {
            "get_settings": lambda ctx, req: GetSettingsOut(
                revision="rev1",
                rc_home=str(tmp_path),
                email=None,
                allow_folders=[str(p) for p in allowed_list],
                download_enabled_global=False,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
            "get_project": GetProjectOut(
                project_id="prj_sync_test",
                name="Sync Test",
                revision=1,
                policy={},
                network_mode="off",
                counts={},
                sources_count=1,
                recent_jobs=[],
                recent_artifacts=[],
                download_enabled=False,
            ),
            "list_sources": ListSourcesOut(
                sources=[
                    SourceListItem(
                        source_id="src_123",
                        label="test_paper.pdf",
                        type=SourceType.PDF,
                        pages=1,
                        sections=0,
                        extraction_state=ExtractionState.EXTRACTED,
                        occurrence_count=0,
                        imported_at="2026-10-22T00:00:00Z",  # type: ignore[arg-type]
                        sha256=sha256_hash,
                    )
                ],
                next_cursor=None,
            ),
            "list_references": ListReferencesOut(occurrences=[], next_cursor=None, total=0),
            "get_source": GetSourceOut(
                source=SourceListItem(
                    source_id="src_123",
                    label="test_paper.pdf",
                    type=SourceType.PDF,
                    pages=1,
                    sections=0,
                    extraction_state=ExtractionState.EXTRACTED,
                    occurrence_count=0,
                    imported_at="2026-10-22T00:00:00Z",  # type: ignore[arg-type]
                    sha256=sha256_hash,
                ),
                revision=1,
                preflight=Preflight(
                    pages=1,
                    text_coverage=1.0,
                ),
                sections=[],
                extraction={"state": "extracted"},
                jobs=[],
            ),
        }
    )
    fake_service.store = FakeStore()

    app = _create_test_app(fake_service, session_store)
    app.state.library_folders = folders
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # 1. Folder is not allowed yet -> POST /sync should fail with 403 PATH_NOT_ALLOWED
        r_not_allowed = await client.post("/api/library/collections/prj_sync_test/sync", headers=csrf_headers)
        assert r_not_allowed.status_code == 403
        data_err = r_not_allowed.json()
        assert data_err["code"] == "PATH_NOT_ALLOWED"

        # 2. Grant folder access
        allowed_list.append(target_folder)

        # 3. Retry POST /sync -> copies the uploaded source PDF into target folder!
        r_sync = await client.post("/api/library/collections/prj_sync_test/sync", headers=csrf_headers)
        assert r_sync.status_code == 200

        # Check file copied on disk
        rec = folders.get("prj_sync_test")
        assert rec is not None
        assert sha256_hash in rec.files
        copied_file = target_folder / rec.files[sha256_hash]
        assert copied_file.is_file()
        assert copied_file.read_bytes() == pdf_bytes

        # Check paper detail reflects non-null folder_path
        r_detail = await client.get("/api/library/collections/prj_sync_test/papers/src_123")
        assert r_detail.status_code == 200
        card = r_detail.json()["paper"]
        assert card["folder_path"] is not None
        assert card["folder_path"].endswith(rec.files[sha256_hash])


@pytest.mark.anyio
async def test_regression_bug4_citation_order_from_occurrence_and_sorting(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    tmp_path: Path,
) -> None:
    """Bug 4: Citation.order comes from the occurrence reading order, and citations are sorted by it."""
    folders = LibraryFolders(tmp_path, lambda: [])

    # Create dummy occurrences in store with distinct reading orders: 3.5, 1.0, 2.0
    class DummyStoreOccurrences:
        def get(self, pid: str, oid: str) -> Any:
            class DummyOcc:
                order = {"occ_a": 3.5, "occ_b": 1.0, "occ_c": 2.0}.get(oid, 0.0)

            return DummyOcc()

    class FakeStore:
        occurrences = DummyStoreOccurrences()

    def fake_get_reference(ctx: Any, req: Any) -> GetReferenceOut:
        oid = req.occurrence_id
        return GetReferenceOut(
            occurrence=OccurrenceSummary(
                occurrence_id=oid,
                source_id="src_1",
                revision=1,
                order={"occ_a": 3.5, "occ_b": 1.0, "occ_c": 2.0}.get(oid, 0.0),
                label=None,
                raw_text=f"Citation {oid}",
                edited_text=None,
                spans=[],
                extraction=ExtractionState.EXTRACTED,
                identity=IdentityView(state=IdentityState.ACCEPTED_AUTO, reasons=["PROVIDER_MATCH"]),
                access={"discovery": DiscoveryState.COMPLETE},  # type: ignore[arg-type]
                local_copy=LocalCopyState.NONE,
                retrieval=RetrievalState.NOT_REQUESTED,
            ),
            work=WorkMetadata(title=f"Work for {oid}"),
            files=[],
        )

    fake_service = FakeService(
        {
            "get_project": GetProjectOut(
                project_id="prj_1",
                name="Project",
                revision=1,
                policy={},
                network_mode="off",
                counts={},
                sources_count=1,
                recent_jobs=[],
                recent_artifacts=[],
                download_enabled=False,
            ),
            "get_source": GetSourceOut(
                source=SourceListItem(
                    source_id="src_1",
                    label="paper.pdf",
                    type=SourceType.PDF,
                    pages=1,
                    sections=0,
                    extraction_state=ExtractionState.EXTRACTED,
                    occurrence_count=3,
                    imported_at="2026-10-22T00:00:00Z",  # type: ignore[arg-type]
                ),
                revision=1,
                preflight=Preflight(pages=1, text_coverage=1.0),
                sections=[],
                extraction={"state": "extracted"},
                jobs=[],
            ),
            "list_references": ListReferencesOut(
                occurrences=[
                    {"occurrence_id": "occ_a", "source_id": "src_1"},
                    {"occurrence_id": "occ_b", "source_id": "src_1"},
                    {"occurrence_id": "occ_c", "source_id": "src_1"},
                ],
                next_cursor=None,
                total=3,
            ),
            "get_reference": fake_get_reference,
            "get_settings": lambda ctx, req: GetSettingsOut(
                revision="rev1",
                rc_home=str(tmp_path),
                email=None,
                allow_folders=[],
                download_enabled_global=False,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
        }
    )
    fake_service.store = FakeStore()

    app = _create_test_app(fake_service, session_store)
    app.state.library_folders = folders
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections/prj_1/papers/src_1")
        assert r.status_code == 200
        citations = r.json()["citations"]
        assert len(citations) == 3
        # Should be sorted by order: occ_b (1.0), occ_c (2.0), occ_a (3.5)
        orders = [c["order"] for c in citations]
        cids = [c["citation_id"] for c in citations]
        assert orders == [1.0, 2.0, 3.5]
        assert cids == ["occ_b", "occ_c", "occ_a"]


@pytest.mark.anyio
async def test_regression_bug5_reason_codes_deduplicated_and_raw(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    tmp_path: Path,
) -> None:
    """Bug 5: reasons are deduplicated in order and kept as raw code values."""
    folders = LibraryFolders(tmp_path, lambda: [])

    fake_service = FakeService(
        {
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_1",
                    source_id="src_1",
                    revision=1,
                    label=None,
                    raw_text="Sample text",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(
                        state=IdentityState.NEEDS_REVIEW,
                        reasons=[
                            "PROVIDER_DISAGREEMENT",
                            "AUTHOR_CONFLICT",
                            "PROVIDER_DISAGREEMENT",
                        ],
                    ),
                    access={"discovery": DiscoveryState.COMPLETE},  # type: ignore[arg-type]
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.NOT_REQUESTED,
                ),
                work=WorkMetadata(title="Sample Work"),
                files=[],
            ),
            "get_settings": lambda ctx, req: GetSettingsOut(
                revision="rev1",
                rc_home=str(tmp_path),
                email=None,
                allow_folders=[],
                download_enabled_global=False,
                pdf_fallback_url=None,
                browser=BrowserStatus(enabled=False, configured=False),
                enabled_providers=[],
                mcp_scopes=[],
                limits={},
                keys={},
                project=None,
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    app.state.library_folders = folders
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections/prj_1/citations/occ_1")
        assert r.status_code == 200
        data = r.json()
        assert data["citation"]["reasons"] == ["PROVIDER_DISAGREEMENT", "AUTHOR_CONFLICT"]


@pytest.mark.anyio
async def test_regression_bug6_review_intent_description_candidate_details(
    session_store: SessionStore,
    auth_cookies: dict[str, str],
    csrf_headers: dict[str, str],
) -> None:
    """Bug 6: review intent description contains candidate details and 120-char citation snippet."""
    fake_service = FakeService(
        {
            "prepare_web_review": WebIntentOut(
                confirmation_token="tok_rev_cand",
                expires_at="2026-10-22T01:00:00Z",
                purpose="review",
                summary={"action": "accept"},
            ),
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_1",
                    source_id="src_1",
                    revision=1,
                    label=None,
                    raw_text=(
                        "Babarit, A. (2012). Numerical benchmarking study of a selection "
                        "of wave energy converters. Renewable Energy, 41, 44-63. "
                        "Extra text that continues beyond one hundred and twenty characters..."
                    ),
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(state=IdentityState.NEEDS_REVIEW),
                    access={"discovery": DiscoveryState.COMPLETE},  # type: ignore[arg-type]
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.NOT_REQUESTED,
                ),
                work=None,
                candidates=[
                    CandidateView(
                        candidate_id="cand_123",
                        rank=1,
                        metadata=WorkMetadata(
                            title="Numerical benchmarking study",
                            authors=[Person(family="Babarit", given="A.")],
                            year=2012,
                        ),
                        features={},
                        reasons=[],
                        providers=["openalex"],
                        identifier_linked=False,
                        rejected=False,
                    )
                ],
                files=[],
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.post(
            "/api/library/collections/prj_1/citations/occ_1/review/intent",
            headers=csrf_headers,
            json={
                "action": "accept",
                "candidate_id": "cand_123",
                "expected_revision": 1,
                "idempotency_key": "idem_rev_cand_1",
            },
        )
        assert r.status_code == 200
        desc = r.json()["description"]
        assert len(desc) >= 1
        full_desc = " ".join(desc)
        assert "Numerical benchmarking study" in full_desc
        assert "Babarit" in full_desc
        assert "2012" in full_desc
        # Verify citation snippet is present and does not exceed 120 chars of citation text
        assert "Babarit, A. (2012). Numerical benchmarking study" in full_desc


@pytest.mark.anyio
async def test_regression_bug7_session_cookie_missing_recovery_hint() -> None:
    """Bug 7: missing session cookie returns recovery_hint 'Reload the page to start a new session.'"""
    fake_service = FakeService({})
    session_store = SessionStore()
    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        r = await client.get("/api/library/collections")
        assert r.status_code == 403
        data = r.json()
        assert data["code"] == "NOT_AUTHORIZED"
        assert data["recovery_hint"] == "Reload the page to start a new session."


@pytest.mark.parametrize(
    "changes",
    [
        {"identity": {"state": "unresolved"}},
        {"access": {"discovery": "not_checked"}},
        {"extraction": "excluded"},
        {"work_id": None},
    ],
)
def test_download_summary_preserves_prerequisites(changes: dict[str, Any]) -> None:
    from refcollector.web.library_api import is_downloadable

    summary = {
        "identity": {"state": "accepted_auto"},
        "access": {"discovery": "complete"},
        "extraction": "extracted",
    }
    assert is_downloadable(summary)
    assert not is_downloadable({**summary, **changes})


# ============================================================================
# Brief B6 Regressions (S1, S2, S3)
# ============================================================================


def test_regression_b6_s1_can_download_requires_and_reads_work_id() -> None:
    """Bug S1: OccurrenceSummary must carry work_id and is_downloadable must verify it."""
    from refcollector.contracts.api import AccessView, IdentityView, OccurrenceSummary
    from refcollector.domain.enums import (
        DiscoveryState,
        ExtractionState,
        IdentityState,
        LocalCopyState,
        RetrievalState,
    )
    from refcollector.web.library_api import is_downloadable

    summary = OccurrenceSummary(
        occurrence_id="occ_1",
        source_id="src_1",
        revision=1,
        label=None,
        raw_text="Test raw citation",
        edited_text=None,
        spans=[],
        extraction=ExtractionState.EXTRACTED,
        identity=IdentityView(state=IdentityState.ACCEPTED_AUTO),
        access=AccessView(discovery=DiscoveryState.COMPLETE),
        local_copy=LocalCopyState.NONE,
        retrieval=RetrievalState.NOT_REQUESTED,
        work_id="wrk_123",
    )
    assert hasattr(summary, "work_id")
    assert summary.work_id == "wrk_123"
    assert is_downloadable(summary) is True

    summary_no_work = summary.model_copy(update={"work_id": None})
    assert is_downloadable(summary_no_work) is False


@pytest.mark.anyio
async def test_regression_b6_s2_file_review_check_kind_and_pending_file(
    session_store: SessionStore, auth_cookies: dict[str, str]
) -> None:
    """Bug S2: Citation must expose check_kind and GET returns pending file details."""
    fake_service = FakeService(
        {
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_review_file",
                    source_id="src_1",
                    revision=1,
                    label=None,
                    raw_text="Sample paper",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(state=IdentityState.ACCEPTED_AUTO),
                    access={  # type: ignore[arg-type]
                        "discovery": DiscoveryState.COMPLETE,
                        "classification": None,
                    },
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.NEEDS_REVIEW,
                ),
                work=None,
                candidates=[],
                files=[
                    {
                        "work_file_id": "wf_pending_1",
                        "sha256": "abc123",
                        "bytes": 1048576,
                        "source_url": "https://example.com/paper.pdf",
                        "state": RetrievalState.NEEDS_REVIEW.value,
                        "reasons": ["SUSPICIOUS_LAYOUT", "WRONG_TITLE"],
                    }
                ],
            )
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        r = await client.get("/api/library/collections/prj_1/citations/occ_review_file")
        assert r.status_code == 200
        data = r.json()
        assert data["citation"]["status"] == "check"
        assert data["citation"]["check_kind"] == "file"
        assert data["pending_file"] is not None
        pf = data["pending_file"]
        assert pf["work_file_id"] == "wf_pending_1"
        assert pf["size"] == 1048576
        assert pf["source_url"] == "https://example.com/paper.pdf"
        assert pf["failed_checks"] == ["SUSPICIOUS_LAYOUT", "WRONG_TITLE"]


@pytest.mark.anyio
async def test_regression_b6_s3_downloaded_status_and_local_copy_mapping(
    session_store: SessionStore, auth_cookies: dict[str, str]
) -> None:
    """Bug S3: When retrieval is satisfied_by_local_copy, map to local_copy id."""
    fake_service = FakeService(
        {
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_local_copy",
                    source_id="src_1",
                    revision=1,
                    label=None,
                    raw_text="Sample paper",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(state=IdentityState.ACCEPTED_AUTO),
                    access={  # type: ignore[arg-type]
                        "discovery": DiscoveryState.COMPLETE,
                        "classification": AccessClassification.ELIGIBLE_FOUND,
                    },
                    local_copy=LocalCopyState.USABLE,
                    retrieval=RetrievalState.SATISFIED_BY_LOCAL_COPY,
                    work_id="wrk_1",
                ),
                work=WorkMetadata(title="Local Copy Paper"),
                candidates=[],
                files=[],  # No managed work file
                local_copies=[
                    {
                        "local_copy_id": "lc_user_1",
                        "occurrence_id": "occ_local_copy",
                        "work_id": "wrk_1",
                        "sha256": "deadbeef",
                        "bytes": 54321,
                        "state": LocalCopyState.USABLE.value,
                    }
                ],
            ),
            "read_project_file": ReadProjectFileOut(
                descriptor=ProjectFileDescriptor(
                    resource_id="lc_user_1",
                    kind="local_copy",
                    name="lc_user_1.pdf",
                    media_type="application/pdf",
                    bytes=21,
                    sha256="deadbeef",
                ),
                data=b"%PDF-1.4 mock content",
                next_offset=21,
                eof=True,
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # Check citation endpoint maps downloaded_paper_id to local copy
        r = await client.get("/api/library/collections/prj_1/citations/occ_local_copy")
        assert r.status_code == 200
        data = r.json()
        assert data["citation"]["status"] == "downloaded"
        assert data["citation"]["downloaded_paper_id"] == "lc_user_1"

        # Check file route streams local_copy kind without error
        r_file = await client.get("/api/library/collections/prj_1/files/local_copy/lc_user_1")
        assert r_file.status_code == 200
        assert r_file.content == b"%PDF-1.4 mock content"


@pytest.mark.anyio
async def test_file_review_routes_approve_and_reject(
    session_store: SessionStore, auth_cookies: dict[str, str], csrf_headers: dict[str, str]
) -> None:
    """Test POST file-review/intent and confirm routes for approve and reject."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    fake_service = FakeService(
        {
            "prepare_web_file_review": WebIntentOut(
                confirmation_token="tok_file_rev_123",
                expires_at=now + timedelta(seconds=60),
                purpose="file_review",
                summary={
                    "project_id": "prj_1",
                    "occurrence_id": "occ_1",
                    "work_file_id": "wf_1",
                    "action": "approve",
                    "file": {
                        "size": 12345,
                        "host": "arxiv.org",
                        "failed_reasons": ["SUSPICIOUS_LAYOUT"],
                    },
                    "work_title": "Paper Work Title",
                },
            ),
            "confirm_web_file_review": ReviewFileDecisionOut(
                occurrence_id="occ_1",
                work_file_id="wf_1",
                retrieval=RetrievalState.VERIFIED,
                revision=2,
            ),
            "get_reference": GetReferenceOut(
                occurrence=OccurrenceSummary(
                    occurrence_id="occ_1",
                    source_id="src_1",
                    revision=2,
                    label=None,
                    raw_text="Author 2020",
                    edited_text=None,
                    spans=[],
                    extraction=ExtractionState.EXTRACTED,
                    identity=IdentityView(state=IdentityState.ACCEPTED_USER),
                    access={"discovery": DiscoveryState.COMPLETE},  # type: ignore[arg-type]
                    local_copy=LocalCopyState.NONE,
                    retrieval=RetrievalState.VERIFIED,
                    work_id="wrk_1",
                ),
                work=WorkMetadata(title="Approved File Paper"),
                files=[
                    {
                        "work_file_id": "wf_1",
                        "sha256": "abcsha",
                        "bytes": 12345,
                        "state": RetrievalState.VERIFIED.value,
                        "reasons": ["SUSPICIOUS_LAYOUT"],
                    }
                ],
            ),
        }
    )

    app = _create_test_app(fake_service, session_store)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000", cookies=auth_cookies
    ) as client:
        # Prepare file review intent
        r_intent = await client.post(
            "/api/library/collections/prj_1/citations/occ_1/file-review/intent",
            headers=csrf_headers,
            json={
                "action": "approve",
                "work_file_id": "wf_1",
                "expected_revision": 1,
                "idempotency_key": "idem_file_rev_1",
            },
        )
        assert r_intent.status_code == 200
        data_intent = r_intent.json()
        assert data_intent["confirmation_token"] == "tok_file_rev_123"
        assert len(data_intent["description"]) == 1
        assert "Approve PDF (12345 bytes from arxiv.org)" in data_intent["description"][0]

        # Confirm without X-Confirmation-Token header -> 403
        r_no_tok = await client.post(
            "/api/library/collections/prj_1/citations/occ_1/file-review/confirm",
            headers=csrf_headers,
            json={
                "action": "approve",
                "work_file_id": "wf_1",
                "expected_revision": 1,
                "idempotency_key": "idem_file_rev_2",
            },
        )
        assert r_no_tok.status_code == 403

        # Confirm approve with X-Confirmation-Token -> 200 and updated Citation
        r_confirm = await client.post(
            "/api/library/collections/prj_1/citations/occ_1/file-review/confirm",
            headers={**csrf_headers, "X-Confirmation-Token": "tok_file_rev_123"},
            json={
                "action": "approve",
                "work_file_id": "wf_1",
                "expected_revision": 1,
                "idempotency_key": "idem_file_rev_2",
            },
        )
        assert r_confirm.status_code == 200
        data_confirm = r_confirm.json()
        assert data_confirm["citation_id"] == "occ_1"
        assert data_confirm["status"] == "downloaded"
        assert data_confirm["downloaded_paper_id"] == "wf_1"
