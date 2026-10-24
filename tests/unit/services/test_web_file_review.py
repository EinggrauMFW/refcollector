"""Tests for trusted web file review (B7, W2 file approval).

Verifies prepare_web_file_review, confirm_web_file_review, ApprovalSurface.WEB_UI,
provenance, token expiry, revision conflicts, input guards, idempotent replay,
and inclusion of approved files in exports.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from refcollector.contracts import api
from refcollector.contracts.api import CallContext, CreateExportIn, ReviewFileDecisionIn
from refcollector.domain.enums import (
    ApprovalSurface,
    AuditEventType,
    Channel,
    ExportFormat,
    IdentityState,
    ReasonCode,
    RetrievalState,
    Scope,
    Selection,
)
from refcollector.domain.models import ProjectWork, WorkFile, WorkMetadata
from refcollector.errors import (
    InvalidInput,
    NotAuthorized,
    RevisionConflict,
)
from refcollector.ids import new_id
from refcollector.pipeline.outputs import ExportHandler
from refcollector.services.support import fingerprint
from refcollector.storage.repos import SqliteStore


def web_ctx(
    session_id: str = "sess-file-123",
    principal: str = "file-reviewer",
    scopes: frozenset[Scope] | None = None,
) -> CallContext:
    return CallContext(
        principal=principal,
        channel=Channel.WEB_UI,
        scopes=scopes if scopes is not None else frozenset(Scope),
        request_id="req-web-file",
        web_session_id=session_id,
    )


@pytest.fixture
def file_review_setup(env: Any) -> dict[str, Any]:
    svc = env.svc
    store: SqliteStore = env.store
    pid = env.project("file-review-project")
    sid = env.text_source(pid, "Author. File Review Test. 2020.")
    occ = env.occ(pid, sid, "Author. File Review Test. 2020.")

    work = ProjectWork(
        work_id=new_id("work"),
        project_id=pid,
        metadata=WorkMetadata(title="Accepted Work For File Review", year=2020, doi="10.1000/182"),
    )
    store.works.add(work)

    # Link occurrence to accepted work and set retrieval to needs_review
    occ.work_id = work.work_id
    occ.identity = IdentityState.ACCEPTED_USER
    occ.retrieval = RetrievalState.NEEDS_REVIEW
    occ = store.occurrences.update(occ)

    # Commit a PDF file into store.files
    pdf_bytes = b"%PDF-1.4 mock pdf content for review"
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    tmp_path = store.files.new_tmp_path(".pdf")
    tmp_path.write_bytes(pdf_bytes)
    store.files.commit(tmp_path, "application/pdf")
    store.files.grant(pid, pdf_sha)

    wf = WorkFile(
        work_file_id=new_id("work_file"),
        project_id=pid,
        work_id=work.work_id,
        sha256=pdf_sha,
        bytes=len(pdf_bytes),
        source_url="https://arxiv.org/pdf/2001.00001.pdf",
        final_url="https://arxiv.org/pdf/2001.00001.pdf",
        state=RetrievalState.NEEDS_REVIEW,
        reasons=["SUSPICIOUS_LAYOUT", "WRONG_TITLE"],
        export_name="2020_author_title.pdf",
    )
    store.work_files.add(wf)

    return {
        "svc": svc,
        "store": store,
        "queue": env.queue,
        "clock": env.clock,
        "project_id": pid,
        "occurrence_id": occ.occurrence_id,
        "work_id": work.work_id,
        "work_file_id": wf.work_file_id,
        "sha256": pdf_sha,
        "bytes": len(pdf_bytes),
        "reasons": list(wf.reasons),
        "revision": occ.revision,
    }


def test_prepare_web_file_review_success(file_review_setup: dict[str, Any]) -> None:
    svc = file_review_setup["svc"]
    ctx = web_ctx()
    req = ReviewFileDecisionIn(
        project_id=file_review_setup["project_id"],
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=file_review_setup["work_file_id"],
        action="approve",
        expected_revision=file_review_setup["revision"],
        note="Approving file",
        idempotency_key="idem-prep-file-001",
    )

    out = svc.prepare_web_file_review(ctx, req)
    assert isinstance(out, api.WebIntentOut)
    assert out.purpose == "file_review"
    assert out.confirmation_token
    assert out.expires_at > file_review_setup["clock"]()
    assert out.summary["project_id"] == req.project_id
    assert out.summary["occurrence_id"] == req.occurrence_id
    assert out.summary["work_file_id"] == req.work_file_id
    assert out.summary["action"] == "approve"
    assert out.summary["file"]["size"] == file_review_setup["bytes"]
    assert out.summary["file"]["host"] == "arxiv.org"
    assert out.summary["file"]["failed_reasons"] == ["SUSPICIOUS_LAYOUT", "WRONG_TITLE"]
    assert out.summary["work_title"] == "Accepted Work For File Review"


def test_prepare_web_file_review_auth_and_channel_guards(file_review_setup: dict[str, Any]) -> None:
    svc = file_review_setup["svc"]
    req = ReviewFileDecisionIn(
        project_id=file_review_setup["project_id"],
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=file_review_setup["work_file_id"],
        action="approve",
        expected_revision=file_review_setup["revision"],
        idempotency_key="idem-prep-file-002",
    )

    # Without web session
    ctx_no_sess = CallContext(
        principal="file-reviewer",
        channel=Channel.WEB_UI,
        scopes=frozenset(Scope),
        request_id="req-1",
        web_session_id=None,
    )
    with pytest.raises(NotAuthorized, match="active web session"):
        svc.prepare_web_file_review(ctx_no_sess, req)

    # Non-web channel (CLI / MCP)
    ctx_mcp = CallContext(
        principal="file-reviewer",
        channel=Channel.MCP,
        scopes=frozenset(Scope),
        request_id="req-2",
        web_session_id="sess-123",
    )
    with pytest.raises(NotAuthorized, match="Channel.WEB_UI"):
        svc.prepare_web_file_review(ctx_mcp, req)


def test_prepare_web_file_review_validation_guards(file_review_setup: dict[str, Any]) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    ctx = web_ctx()
    pid = file_review_setup["project_id"]

    # Stale revision conflict
    req_stale = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=file_review_setup["work_file_id"],
        action="approve",
        expected_revision=file_review_setup["revision"] + 1,
        idempotency_key="idem-prep-stale",
    )
    with pytest.raises(RevisionConflict):
        svc.prepare_web_file_review(ctx, req_stale)

    # Work file belonging to another work
    other_work = ProjectWork(
        work_id=new_id("work"),
        project_id=pid,
        metadata=WorkMetadata(title="Other Work", year=2021),
    )
    store.works.add(other_work)
    other_wf = WorkFile(
        work_file_id=new_id("work_file"),
        project_id=pid,
        work_id=other_work.work_id,
        sha256="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        bytes=100,
        source_url="https://other.org/file.pdf",
        final_url="https://other.org/file.pdf",
        state=RetrievalState.NEEDS_REVIEW,
        reasons=[],
        export_name="other.pdf",
    )
    store.work_files.add(other_wf)

    req_other_work = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=other_wf.work_file_id,
        action="approve",
        expected_revision=file_review_setup["revision"],
        idempotency_key="idem-prep-other-work",
    )
    with pytest.raises(InvalidInput, match="does not belong to occurrence"):
        svc.prepare_web_file_review(ctx, req_other_work)

    # Work file not in needs_review state
    verified_wf = WorkFile(
        work_file_id=new_id("work_file"),
        project_id=pid,
        work_id=file_review_setup["work_id"],
        sha256="abcdefabcdef1234567890abcdef1234567890abcdef1234567890abcdef12",
        bytes=200,
        source_url="https://verified.org/file.pdf",
        final_url="https://verified.org/file.pdf",
        state=RetrievalState.VERIFIED,
        reasons=[],
        export_name="verified.pdf",
    )
    store.work_files.add(verified_wf)

    req_verified = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=verified_wf.work_file_id,
        action="approve",
        expected_revision=file_review_setup["revision"],
        idempotency_key="idem-prep-already-verified",
    )
    with pytest.raises(InvalidInput, match="needs_review state"):
        svc.prepare_web_file_review(ctx, req_verified)


def test_confirm_web_file_review_approve_happy_path(file_review_setup: dict[str, Any]) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    ctx = web_ctx()
    req = ReviewFileDecisionIn(
        project_id=file_review_setup["project_id"],
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=file_review_setup["work_file_id"],
        action="approve",
        expected_revision=file_review_setup["revision"],
        idempotency_key="idem-conf-approve-001",
    )

    intent = svc.prepare_web_file_review(ctx, req)
    res = svc.confirm_web_file_review(ctx, req, intent.confirmation_token)

    assert isinstance(res, api.ReviewFileDecisionOut)
    assert res.occurrence_id == req.occurrence_id
    assert res.work_file_id == req.work_file_id
    assert res.retrieval == RetrievalState.VERIFIED
    assert res.revision == file_review_setup["revision"] + 1

    # Verify WorkFile state updated to verified while keeping reasons
    wf = store.work_files.get(req.project_id, req.work_file_id)
    assert wf.state == RetrievalState.VERIFIED
    assert wf.reasons == ["SUSPICIOUS_LAYOUT", "WRONG_TITLE"]

    # Verify occurrence retrieval updated to verified and revision bumped
    occ = store.occurrences.get(req.project_id, req.occurrence_id)
    assert occ.retrieval == RetrievalState.VERIFIED
    assert occ.revision == file_review_setup["revision"] + 1

    # Verify audit event FILE_APPROVED
    events = store.audit.list(req.project_id, subject_id=req.occurrence_id)
    approved_events = [e for e in events if e.type == AuditEventType.FILE_APPROVED]
    assert len(approved_events) == 1
    ev = approved_events[0]
    assert ev.payload["surface"] == ApprovalSurface.WEB_UI
    assert ev.payload["principal"] == ctx.principal
    assert ev.payload["sha256"] == file_review_setup["sha256"]
    assert ev.payload["overridden_reasons"] == ["SUSPICIOUS_LAYOUT", "WRONG_TITLE"]


def test_confirm_web_file_review_reject_happy_path(file_review_setup: dict[str, Any]) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    ctx = web_ctx()
    req = ReviewFileDecisionIn(
        project_id=file_review_setup["project_id"],
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=file_review_setup["work_file_id"],
        action="reject",
        expected_revision=file_review_setup["revision"],
        idempotency_key="idem-conf-reject-001",
    )

    intent = svc.prepare_web_file_review(ctx, req)
    res = svc.confirm_web_file_review(ctx, req, intent.confirmation_token)

    assert isinstance(res, api.ReviewFileDecisionOut)
    assert res.occurrence_id == req.occurrence_id
    assert res.work_file_id == req.work_file_id
    assert res.retrieval == RetrievalState.FAILED
    assert res.revision == file_review_setup["revision"] + 1

    # WorkFile stays needs_review
    wf = store.work_files.get(req.project_id, req.work_file_id)
    assert wf.state == RetrievalState.NEEDS_REVIEW

    # Occurrence retrieval is failed and reason is recorded
    occ = store.occurrences.get(req.project_id, req.occurrence_id)
    assert occ.retrieval == RetrievalState.FAILED
    assert ReasonCode.FILE_REJECTED_BY_USER in occ.identity_reasons
    assert occ.revision == file_review_setup["revision"] + 1

    # Audit event FILE_REJECTED
    events = store.audit.list(req.project_id, subject_id=req.occurrence_id)
    rejected_events = [e for e in events if e.type == AuditEventType.FILE_REJECTED]
    assert len(rejected_events) == 1
    ev = rejected_events[0]
    assert ev.payload["surface"] == ApprovalSurface.WEB_UI
    assert ev.payload["principal"] == ctx.principal
    assert ev.payload["reason"] == ReasonCode.FILE_REJECTED_BY_USER


def test_confirm_web_file_review_token_validation(file_review_setup: dict[str, Any]) -> None:
    svc = file_review_setup["svc"]
    ctx = web_ctx()
    req = ReviewFileDecisionIn(
        project_id=file_review_setup["project_id"],
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=file_review_setup["work_file_id"],
        action="approve",
        expected_revision=file_review_setup["revision"],
        idempotency_key="idem-conf-tok-001",
    )

    intent = svc.prepare_web_file_review(ctx, req)

    # Missing token
    with pytest.raises(NotAuthorized, match="confirmation token required"):
        svc.confirm_web_file_review(ctx, req, "")

    # Unknown token
    with pytest.raises(NotAuthorized, match="invalid or unknown"):
        svc.confirm_web_file_review(ctx, req, "invalid-token-xyz")

    # Wrong web session
    ctx_wrong_sess = web_ctx(session_id="wrong-sess-999")
    with pytest.raises(NotAuthorized, match="belongs to another session"):
        svc.confirm_web_file_review(ctx_wrong_sess, req, intent.confirmation_token)

    # Wrong principal
    ctx_wrong_user = web_ctx(principal="other-user")
    with pytest.raises(NotAuthorized, match="belongs to another principal"):
        svc.confirm_web_file_review(ctx_wrong_user, req, intent.confirmation_token)

    # Expired token
    file_review_setup["clock"].advance(seconds=65)
    with pytest.raises(NotAuthorized, match="expired"):
        svc.confirm_web_file_review(ctx, req, intent.confirmation_token)


def test_confirm_web_file_review_idempotent_replay(file_review_setup: dict[str, Any]) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    ctx = web_ctx()
    req = ReviewFileDecisionIn(
        project_id=file_review_setup["project_id"],
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=file_review_setup["work_file_id"],
        action="approve",
        expected_revision=file_review_setup["revision"],
        idempotency_key="idem-conf-replay-001",
    )

    intent = svc.prepare_web_file_review(ctx, req)
    res1 = svc.confirm_web_file_review(ctx, req, intent.confirmation_token)

    # Audit events count
    events_1 = [
        e
        for e in store.audit.list(req.project_id, subject_id=req.occurrence_id)
        if e.type == AuditEventType.FILE_APPROVED
    ]
    assert len(events_1) == 1

    # Idempotent replay with same token and request
    res2 = svc.confirm_web_file_review(ctx, req, intent.confirmation_token)
    assert res1.occurrence_id == res2.occurrence_id
    assert res1.revision == res2.revision
    assert res1.retrieval == res2.retrieval

    # Ensure NO second audit event was recorded
    events_2 = [
        e
        for e in store.audit.list(req.project_id, subject_id=req.occurrence_id)
        if e.type == AuditEventType.FILE_APPROVED
    ]
    assert len(events_2) == 1


@pytest.mark.anyio
async def test_approved_file_appears_in_export(file_review_setup: dict[str, Any]) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    queue = file_review_setup["queue"]
    ctx = web_ctx()
    pid = file_review_setup["project_id"]

    req = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=file_review_setup["occurrence_id"],
        work_file_id=file_review_setup["work_file_id"],
        action="approve",
        expected_revision=file_review_setup["revision"],
        idempotency_key="idem-conf-export-001",
    )

    intent = svc.prepare_web_file_review(ctx, req)
    svc.confirm_web_file_review(ctx, req, intent.confirmation_token)

    # Read current project revision after approve
    proj = store.projects.get(pid)

    # Run export job
    exp_req = CreateExportIn(
        project_id=pid,
        project_revision=proj.revision,
        formats=[ExportFormat.JSON],
        selection=Selection.ACCEPTED,
        idempotency_key="idem-export-job-001",
    )
    job_out = svc.create_export(ctx, exp_req)
    job = queue.get_job(pid, job_out.job_id)

    # Run the export step handler
    from refcollector.contracts.ports import StepContext
    from refcollector.domain.enums import StepType
    from refcollector.pipeline.registry import JobPlanner

    specs = JobPlanner().plan(store, job, StepType.EXPORT)
    with store.transaction(immediate=True):
        steps = queue.add_steps(job, specs, max_attempts=1)

    handler = ExportHandler()
    step_ctx = StepContext(steps[0], job, store, lambda: False)
    res = await handler.run(step_ctx)
    assert res is not None
    with store.transaction():
        res.commit(store)

    # Verify that the PDF is included in the exported artifacts
    artifacts = store.artifacts.list(pid, limit=10).items
    pdf_artifacts = [a for a in artifacts if a.media_type == "application/pdf"]
    assert len(pdf_artifacts) >= 1
    assert any(a.sha256 == file_review_setup["sha256"] for a in pdf_artifacts)


@pytest.mark.parametrize(
    ("mutation_field", "mutation_val"),
    [
        ("project_id", "proj-other"),
        ("occurrence_id", "occ-other"),
        ("work_file_id", "wf-other"),
        ("action", "reject"),
        ("expected_revision", 999),
        ("idempotency_key", "diff-idem-key"),
        ("note", "tampered note"),
    ],
)
def test_confirm_web_file_review_adversarial_request_binding(
    file_review_setup: dict[str, Any], mutation_field: str, mutation_val: Any
) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    ctx = web_ctx()
    pid = file_review_setup["project_id"]
    oid = file_review_setup["occurrence_id"]
    wfid = file_review_setup["work_file_id"]
    rev = file_review_setup["revision"]

    base_req = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=oid,
        work_file_id=wfid,
        action="approve",
        expected_revision=rev,
        idempotency_key="idem-adv-001",
        note="initial note",
    )
    intent = svc.prepare_web_file_review(ctx, base_req)
    token = intent.confirmation_token

    mutated_kwargs = base_req.model_dump()
    mutated_kwargs[mutation_field] = mutation_val
    mutated_req = ReviewFileDecisionIn(**mutated_kwargs)

    initial_audit_count = len(store.audit.list(pid, subject_id=oid))

    with pytest.raises(NotAuthorized, match="request does not match confirmed intent"):
        svc.confirm_web_file_review(ctx, mutated_req, token)

    # 0 mutations
    occ_after = store.occurrences.get(pid, oid)
    assert occ_after.revision == rev
    wf_after = store.work_files.get(pid, wfid)
    assert wf_after.state == RetrievalState.NEEDS_REVIEW
    after_audit_count = len(store.audit.list(pid, subject_id=oid))
    assert after_audit_count == initial_audit_count


def test_confirm_web_file_review_wrong_purpose_and_other_file_token(
    file_review_setup: dict[str, Any],
) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    ctx = web_ctx()
    pid = file_review_setup["project_id"]
    oid = file_review_setup["occurrence_id"]
    wfid = file_review_setup["work_file_id"]
    rev = file_review_setup["revision"]

    req = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=oid,
        work_file_id=wfid,
        action="approve",
        expected_revision=rev,
        idempotency_key="idem-wrong-purp-001",
    )

    # 1. Token with wrong purpose ("review")
    fp = fingerprint(req)
    wrong_intent = svc._intent_registry.register(
        purpose="review",
        principal=ctx.principal,
        web_session_id=ctx.web_session_id,
        project_id=pid,
        target_id=oid,
        candidate_id=wfid,
        action="approve",
        expected_revision=rev,
        idempotency_key=req.idempotency_key,
        fingerprint=fp,
        summary={},
        now=file_review_setup["clock"](),
    )
    with pytest.raises(NotAuthorized, match="token purpose mismatch"):
        svc.confirm_web_file_review(ctx, req, wrong_intent.token)

    # 2. Token created for another work file (one-file binding)
    pdf_bytes2 = b"%PDF-1.4 second mock pdf"
    pdf_sha2 = hashlib.sha256(pdf_bytes2).hexdigest()
    tmp2 = store.files.new_tmp_path(".pdf")
    tmp2.write_bytes(pdf_bytes2)
    store.files.commit(tmp2, "application/pdf")
    store.files.grant(pid, pdf_sha2)

    wf2 = WorkFile(
        work_file_id=new_id("work_file"),
        project_id=pid,
        work_id=file_review_setup["work_id"],
        sha256=pdf_sha2,
        bytes=len(pdf_bytes2),
        source_url="https://example.org/second.pdf",
        final_url="https://example.org/second.pdf",
        state=RetrievalState.NEEDS_REVIEW,
        reasons=["WRONG_TITLE"],
        export_name="second.pdf",
    )
    store.work_files.add(wf2)

    req2 = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=oid,
        work_file_id=wf2.work_file_id,
        action="approve",
        expected_revision=rev,
        idempotency_key="idem-wf2-001",
    )

    intent1 = svc.prepare_web_file_review(ctx, req)
    intent2 = svc.prepare_web_file_review(ctx, req2)

    # Confirm req with intent2's token -> reject
    with pytest.raises(NotAuthorized, match="request does not match confirmed intent"):
        svc.confirm_web_file_review(ctx, req, intent2.confirmation_token)

    # Confirm req2 with intent1's token -> reject
    with pytest.raises(NotAuthorized, match="request does not match confirmed intent"):
        svc.confirm_web_file_review(ctx, req2, intent1.confirmation_token)

    # 0 mutations
    assert store.work_files.get(pid, wfid).state == RetrievalState.NEEDS_REVIEW
    assert store.work_files.get(pid, wf2.work_file_id).state == RetrievalState.NEEDS_REVIEW


def test_confirm_web_file_review_channel_guards_and_no_mcp_exposure(
    file_review_setup: dict[str, Any],
) -> None:
    svc = file_review_setup["svc"]
    pid = file_review_setup["project_id"]
    oid = file_review_setup["occurrence_id"]
    wfid = file_review_setup["work_file_id"]
    rev = file_review_setup["revision"]

    valid_web = web_ctx()
    req = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=oid,
        work_file_id=wfid,
        action="approve",
        expected_revision=rev,
        idempotency_key="idem-chan-guard-001",
    )
    intent = svc.prepare_web_file_review(valid_web, req)
    token = intent.confirmation_token

    # 1. MCP channel cannot prepare or confirm
    mcp_ctx = CallContext(
        principal="mcp-agent",
        channel=Channel.MCP,
        scopes=frozenset(Scope),
        request_id="req-mcp-001",
        web_session_id=None,
    )
    with pytest.raises(NotAuthorized, match="Channel.WEB_UI"):
        svc.prepare_web_file_review(mcp_ctx, req)
    with pytest.raises(NotAuthorized, match="Channel.WEB_UI"):
        svc.confirm_web_file_review(mcp_ctx, req, token)

    # 2. CLI channel cannot prepare or confirm
    cli_ctx = CallContext(
        principal="cli-user",
        channel=Channel.CLI,
        scopes=frozenset(Scope),
        request_id="req-cli-001",
        web_session_id=None,
    )
    with pytest.raises(NotAuthorized, match="Channel.WEB_UI"):
        svc.prepare_web_file_review(cli_ctx, req)
    with pytest.raises(NotAuthorized, match="Channel.WEB_UI"):
        svc.confirm_web_file_review(cli_ctx, req, token)

    # 3. WEB_UI channel with missing web_session_id cannot prepare or confirm
    no_session_ctx = CallContext(
        principal="web-user",
        channel=Channel.WEB_UI,
        scopes=frozenset(Scope),
        request_id="req-no-sess",
        web_session_id=None,
    )
    with pytest.raises(NotAuthorized, match="active web session"):
        svc.prepare_web_file_review(no_session_ctx, req)
    with pytest.raises(NotAuthorized, match="active web session"):
        svc.confirm_web_file_review(no_session_ctx, req, token)

    # 4. Zero exposure: verify absence from contracts.api.TOOLS and MCP tool list
    assert "prepare_web_file_review" not in api.TOOLS
    assert "confirm_web_file_review" not in api.TOOLS

    from refcollector.mcp.tools import list_tools

    mcp_tool_names = {t.name for t in list_tools()}
    assert "prepare_web_file_review" not in mcp_tool_names
    assert "confirm_web_file_review" not in mcp_tool_names


def test_confirm_web_file_review_revision_changed_after_prepare(
    file_review_setup: dict[str, Any],
) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    ctx = web_ctx()
    pid = file_review_setup["project_id"]
    oid = file_review_setup["occurrence_id"]
    wfid = file_review_setup["work_file_id"]
    rev = file_review_setup["revision"]

    req = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=oid,
        work_file_id=wfid,
        action="approve",
        expected_revision=rev,
        idempotency_key="idem-rev-change-001",
    )
    intent = svc.prepare_web_file_review(ctx, req)
    token = intent.confirmation_token

    # Bump occurrence revision in store after prepare
    occ = store.occurrences.get(pid, oid)
    store.occurrences.update(occ)
    assert store.occurrences.get(pid, oid).revision != rev

    with pytest.raises(RevisionConflict, match="occurrence revision has changed"):
        svc.confirm_web_file_review(ctx, req, token)

    # 0 mutations
    assert store.work_files.get(pid, wfid).state == RetrievalState.NEEDS_REVIEW


def test_web_file_review_rejects_unaccepted_identity_with_retained_work_id(
    file_review_setup: dict[str, Any],
) -> None:
    svc = file_review_setup["svc"]
    store: SqliteStore = file_review_setup["store"]
    ctx = web_ctx()
    pid = file_review_setup["project_id"]
    oid = file_review_setup["occurrence_id"]
    wfid = file_review_setup["work_file_id"]

    # Set occurrence identity to unaccepted state while retaining work_id
    occ = store.occurrences.get(pid, oid)
    assert occ.work_id is not None
    unaccepted_occ = occ.model_copy(update={"identity": IdentityState.NEEDS_REVIEW})
    store.occurrences._write(unaccepted_occ)

    req = ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=oid,
        work_file_id=wfid,
        action="approve",
        expected_revision=unaccepted_occ.revision,
        idempotency_key="idem-unacc-001",
    )

    # prepare must reject
    with pytest.raises(InvalidInput, match="occurrence has no accepted work"):
        svc.prepare_web_file_review(ctx, req)

    # confirm must also reject even if token were somehow registered
    fp = fingerprint(req)
    fake_intent = svc._intent_registry.register(
        purpose="file_review",
        principal=ctx.principal,
        web_session_id=ctx.web_session_id,
        project_id=pid,
        target_id=oid,
        candidate_id=wfid,
        action="approve",
        expected_revision=unaccepted_occ.revision,
        idempotency_key=req.idempotency_key,
        fingerprint=fp,
        summary={},
        now=file_review_setup["clock"](),
    )
    with pytest.raises(InvalidInput, match="occurrence has no accepted work"):
        svc.confirm_web_file_review(ctx, req, fake_intent.token)

    # 0 mutations
    assert store.work_files.get(pid, wfid).state == RetrievalState.NEEDS_REVIEW
