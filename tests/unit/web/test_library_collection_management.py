"""Regression tests for Library collection management (rename, consequences, delete).

Uses real SQLite database and RefCollectorService (no mocked SQL).
Tests active job race protection, foreign-key safe cascade deletion, cross-project isolation,
sidecar cleanup, and disk file preservation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport

from refcollector.config import Settings
from refcollector.contracts.api import (
    CallContext,
    CreateProjectIn,
)
from refcollector.domain.enums import (
    AuditEventType,
    Channel,
    DecisionAction,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyState,
    Origin,
    RetrievalState,
    Scope,
    SourceType,
)
from refcollector.domain.models import (
    Candidate,
    Decision,
    Occurrence,
    Preflight,
    Provenance,
    Source,
    WorkMetadata,
    utcnow,
)
from refcollector.errors import ErrorCode, RcError
from refcollector.executor.queue import SqliteJobQueue
from refcollector.extraction.builtin import BuiltinExtractionAdapter
from refcollector.ids import new_id
from refcollector.services.library_folder import LibraryFolders
from refcollector.services.service import RefCollectorServiceImpl
from refcollector.storage.db import Database
from refcollector.storage.repos import SqliteStore
from refcollector.web.library_routes import router as library_router
from refcollector.web.session import SessionStore
from tests.unit.executor.helpers import new_job


class FakeLocalCopies:
    def evaluate(self, *args: Any, **kwargs: Any) -> Any:
        return None


def _create_real_service(db: Database, settings: Settings) -> RefCollectorServiceImpl:
    store = SqliteStore(db, settings)
    queue = SqliteJobQueue(db)
    return RefCollectorServiceImpl(
        settings=settings,
        store=store,
        queue=queue,
        extractor=BuiltinExtractionAdapter(settings.limits),
        identity_factory=lambda snap: None,
        local_copies=FakeLocalCopies(),
        clock=utcnow,
    )


def _create_test_app(
    svc: RefCollectorServiceImpl, session_store: SessionStore, folders: LibraryFolders
) -> FastAPI:
    app = FastAPI()
    app.state.service = svc
    app.state.session_store = session_store
    app.state.library_folders = folders

    @app.exception_handler(RcError)
    async def rc_error_handler(request: Request, exc: RcError) -> JSONResponse:
        status_code = 400
        if exc.code == ErrorCode.NOT_FOUND:
            status_code = 404
        elif exc.code == ErrorCode.NOT_AUTHORIZED:
            status_code = 403
        elif exc.code in (ErrorCode.REVISION_CONFLICT, ErrorCode.IDEMPOTENCY_CONFLICT):
            status_code = 409
        return JSONResponse(status_code=status_code, content=exc.to_payload())

    app.include_router(library_router)
    return app


@pytest.fixture
def test_env(tmp_path: Path):
    rc_home = tmp_path / "rc_home"
    rc_home.mkdir(parents=True, exist_ok=True)
    folder_dir = tmp_path / "user_library_folder"
    folder_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        rc_home=rc_home,
        email="dev@example.org",
        allow_folders=[folder_dir],
    )
    db = Database(settings.db_path)
    db.migrate()

    svc = _create_real_service(db, settings)
    folders = LibraryFolders(rc_home, lambda: [folder_dir])
    session_store = SessionStore()
    app = _create_test_app(svc, session_store, folders)

    ctx = CallContext(
        principal="test-user",
        channel=Channel.WEB_UI,
        scopes=frozenset(Scope),
        request_id="req_test_init",
    )

    yield {
        "db": db,
        "settings": settings,
        "svc": svc,
        "folders": folders,
        "session_store": session_store,
        "app": app,
        "ctx": ctx,
        "folder_dir": folder_dir,
    }

    db.close()


@pytest.fixture
def auth_client(test_env):
    session_store = test_env["session_store"]
    session = session_store.create_session()
    cookie = session.session_id

    headers = {
        "X-CSRF-Token": session.csrf_token,
        "Origin": "http://testserver",
    }
    cookies = {"rc_session": cookie}

    client = httpx.AsyncClient(
        transport=ASGITransport(app=test_env["app"]),
        base_url="http://testserver",
        headers=headers,
        cookies=cookies,
    )
    return client


# ----------------------------------------------------------------------------
# 1. Rename Collection Tests
# ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rename_collection_success_and_validation(test_env, auth_client):
    svc = test_env["svc"]
    ctx = test_env["ctx"]

    create_out = svc.create_project(
        ctx, CreateProjectIn(name="Old Collection Name", idempotency_key=new_id("request"))
    )
    cid = create_out.project_id

    # 1. Empty rename validation
    resp_empty = await auth_client.patch(f"/api/library/collections/{cid}", json={"name": "   "})
    assert resp_empty.status_code == 400
    assert resp_empty.json()["code"] == "INVALID_INPUT"

    # 2. Successful rename
    resp_ok = await auth_client.patch(f"/api/library/collections/{cid}", json={"name": "New Collection Name"})
    assert resp_ok.status_code == 200
    assert resp_ok.json()["name"] == "New Collection Name"

    # Verify project in DB was updated
    proj_in_db = svc.store.projects.get(cid)
    assert proj_in_db.name == "New Collection Name"

    # Verify audit event was written
    audit_events = test_env["db"].query(
        "SELECT type, data FROM audit_event WHERE project_id = ? AND type = ?",
        (cid, AuditEventType.FIELDS_SET.value),
    )
    assert len(audit_events) > 0


# ----------------------------------------------------------------------------
# 2. Collection Consequences Tests
# ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_collection_consequences_with_seeded_db(test_env, auth_client):
    svc = test_env["svc"]
    ctx = test_env["ctx"]
    folders = test_env["folders"]
    folder_dir = test_env["folder_dir"]

    create_out = svc.create_project(
        ctx, CreateProjectIn(name="Consequences Collection", idempotency_key=new_id("request"))
    )
    cid = create_out.project_id

    # Seed disk files in folder and sidecar record
    dummy_pdf = folder_dir / "paper1.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 test")
    rec = folders.create(cid, folder_dir)
    rec.files["sha_test"] = "paper1.pdf"
    folders._save_record(cid, rec)

    # Seed 1 source and 2 occurrences
    src_id = new_id("source")
    svc.store.sources.add(
        Source(
            source_id=src_id,
            project_id=cid,
            type=SourceType.PDF,
            label="paper1.pdf",
            sha256="abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcd",
            origin=str(dummy_pdf),
            preflight=Preflight(pages=1, text_coverage=1.0),
            extraction_state=ExtractionState.EXTRACTED,
            imported_at=utcnow(),
        )
    )

    occ1_id = new_id("occurrence")
    occ2_id = new_id("occurrence")
    occs = [
        Occurrence(
            occurrence_id=oid,
            project_id=cid,
            source_id=src_id,
            order=float(i),
            raw_text=f"Citation {i}",
            extraction=ExtractionState.EXTRACTED,
            identity=IdentityState.UNPROCESSED,
            local_copy=LocalCopyState.NONE,
            retrieval=RetrievalState.NOT_REQUESTED,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        for i, oid in enumerate((occ1_id, occ2_id), start=1)
    ]
    svc.store.occurrences.add_many(occs)

    # Call consequences API
    resp = await auth_client.get(f"/api/library/collections/{cid}/consequences")
    assert resp.status_code == 200
    data = resp.json()
    assert data["collection_id"] == cid
    assert data["name"] == "Consequences Collection"
    assert data["paper_count"] == 1
    assert data["citation_count"] == 2
    assert data["file_count"] == 1
    assert data["folder_path"] == str(folder_dir.resolve()).replace("\\", "/")
    assert "will NOT be deleted" in data["message"]
    assert data["has_active_jobs"] is False


# ----------------------------------------------------------------------------
# 3. Active Job Deletion Rejection Test
# ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_collection_rejects_active_jobs(test_env, auth_client):
    svc = test_env["svc"]
    ctx = test_env["ctx"]

    create_out = svc.create_project(
        ctx, CreateProjectIn(name="Active Job Project", idempotency_key=new_id("request"))
    )
    cid = create_out.project_id

    # Enqueue a running job in queue
    proj = svc.store.projects.get(cid)
    job = new_job(test_env["settings"], proj, {"extract": []})
    job.state = JobState.RUNNING
    svc.queue.create_job(job)

    # Consequences must proactively report has_active_jobs=True
    consequences_resp = await auth_client.get(f"/api/library/collections/{cid}/consequences")
    assert consequences_resp.status_code == 200
    assert consequences_resp.json()["has_active_jobs"] is True

    # Attempt delete -> must be rejected with 400 (INVALID_STATE)
    resp = await auth_client.delete(f"/api/library/collections/{cid}")
    assert resp.status_code == 400
    err = resp.json()
    assert err["code"] == "INVALID_STATE"
    assert "active job" in err["message"].lower()

    # Verify project still exists in DB
    assert svc.store.projects.get(cid).name == "Active Job Project"


# ----------------------------------------------------------------------------
# 4. Safe Cascade Deletion, FK Integrity & Cross-Project Isolation
# ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_collection_safe_cascade_and_isolation(test_env, auth_client):
    svc = test_env["svc"]
    ctx = test_env["ctx"]
    db = test_env["db"]
    folders = test_env["folders"]
    folder_dir = test_env["folder_dir"]

    # 1. Setup Project A (to be deleted)
    create_out_a = svc.create_project(
        ctx, CreateProjectIn(name="Project A", idempotency_key=new_id("request"))
    )
    cid_a = create_out_a.project_id

    # Disk file and sidecar in Project A
    pdf_a = folder_dir / "paper_a.pdf"
    pdf_a.write_bytes(b"%PDF-1.4 paper A contents")
    rec_a = folders.create(cid_a, folder_dir)
    rec_a.files["sha_a"] = "paper_a.pdf"
    folders._save_record(cid_a, rec_a)

    src_a = new_id("source")
    svc.store.sources.add(
        Source(
            source_id=src_a,
            project_id=cid_a,
            type=SourceType.PDF,
            label="paper_a.pdf",
            sha256="aaa1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcd",
            origin=str(pdf_a),
            preflight=Preflight(pages=1, text_coverage=1.0),
            extraction_state=ExtractionState.EXTRACTED,
            imported_at=utcnow(),
        )
    )
    occ_a = new_id("occurrence")
    svc.store.occurrences.add_many(
        [
            Occurrence(
                occurrence_id=occ_a,
                project_id=cid_a,
                source_id=src_a,
                order=1.0,
                raw_text="Citation A",
                extraction=ExtractionState.EXTRACTED,
                identity=IdentityState.UNPROCESSED,
                local_copy=LocalCopyState.NONE,
                retrieval=RetrievalState.NOT_REQUESTED,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        ]
    )
    cand_a = new_id("candidate")
    cand_obj = Candidate(
        candidate_id=cand_a,
        project_id=cid_a,
        occurrence_id=occ_a,
        rank=1,
        metadata=WorkMetadata(title="Cand A"),
    )
    svc.store.candidates.replace_for_occurrence(cid_a, occ_a, [cand_obj])
    dec_a = new_id("decision")
    svc.store.decisions.add(
        Decision(
            decision_id=dec_a,
            project_id=cid_a,
            occurrence_id=occ_a,
            action=DecisionAction.ACCEPT,
            identity_after=IdentityState.ACCEPTED_USER,
            provenance=Provenance(principal="tester", channel=Channel.CLI, origin=Origin.INTERACTIVE),
            occurrence_revision=1,
            created_at=utcnow(),
        )
    )

    # Add completed job in Project A
    proj_a = svc.store.projects.get(cid_a)
    job_a = svc.queue.create_job(new_job(test_env["settings"], proj_a, {"extract": []}))
    svc.queue.put_job(job_a.model_copy(update={"state": JobState.COMPLETED}))

    # 2. Setup Project B (must remain untouched!)
    create_out_b = svc.create_project(
        ctx, CreateProjectIn(name="Project B", idempotency_key=new_id("request"))
    )
    cid_b = create_out_b.project_id
    src_b = new_id("source")
    svc.store.sources.add(
        Source(
            source_id=src_b,
            project_id=cid_b,
            type=SourceType.PDF,
            label="paper_b.pdf",
            sha256="bbb1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcd",
            origin="test/paper_b.pdf",
            preflight=Preflight(pages=1, text_coverage=1.0),
            extraction_state=ExtractionState.EXTRACTED,
            imported_at=utcnow(),
        )
    )

    # 3. Delete Project A via API
    resp_del = await auth_client.delete(f"/api/library/collections/{cid_a}")
    assert resp_del.status_code == 204, resp_del.json()

    # 4. Assert Project A records are deleted from project, source, occurrence, candidate, decision, job
    assert db.query_one("SELECT count(*) FROM project WHERE id = ?", (cid_a,))[0] == 0
    assert db.query_one("SELECT count(*) FROM source WHERE project_id = ?", (cid_a,))[0] == 0
    assert db.query_one("SELECT count(*) FROM occurrence WHERE project_id = ?", (cid_a,))[0] == 0
    assert db.query_one("SELECT count(*) FROM candidate WHERE project_id = ?", (cid_a,))[0] == 0
    assert db.query_one("SELECT count(*) FROM decision WHERE project_id = ?", (cid_a,))[0] == 0
    assert db.query_one("SELECT count(*) FROM job WHERE project_id = ?", (cid_a,))[0] == 0

    # 5. Assert Audit trail is NOT deleted!
    audit_count_a = db.query_one("SELECT count(*) FROM audit_event WHERE project_id = ?", (cid_a,))[0]
    assert audit_count_a > 0, "Audit events should NOT be deleted on project deletion!"

    # 6. Assert disk file is NOT deleted!
    assert pdf_a.is_file(), "Disk PDF file should NEVER be deleted!"
    assert pdf_a.read_bytes() == b"%PDF-1.4 paper A contents"

    # 7. Assert sidecar JSON is deleted
    assert not folders._record_path(cid_a).is_file()

    # 8. Assert Project B is 100% intact!
    assert db.query_one("SELECT count(*) FROM project WHERE id = ?", (cid_b,))[0] == 1
    assert db.query_one("SELECT count(*) FROM source WHERE project_id = ?", (cid_b,))[0] == 1
    assert svc.store.projects.get(cid_b).name == "Project B"
