"""D-48 numbered core requirements, with real service/storage and socket-free HTTP."""

import asyncio
import dataclasses
import hashlib

import pytest
from pydantic import ValidationError

from refcollector.contracts import api
from refcollector.domain.enums import (
    AuditEventType,
    Channel,
    ExtractionState,
    IdentityState,
    ReasonCode,
    Scope,
    StepType,
)
from refcollector.errors import InvalidInput, NotAuthorized, NotFound, PrerequisiteNotMet
from tests.fixtures.pdfs import paper_pdf
from tests.unit.pipeline.conftest import DOI, TEXT, context, create, refs, run
from tests.unit.pipeline.test_acceptance import enable_download
from tests.unit.pipeline.test_pdf_fallback import TEMPLATE
from tests.unit.pipeline.test_pdf_fallback import fallback as fallback


@pytest.fixture
def doi_case(app, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid, stages=("extract",))
    row = refs(app, pid)[0]
    row.identity = IdentityState.NEEDS_REVIEW
    row.identity_reasons = [ReasonCode.PROVIDER_DISAGREEMENT]
    row = app.store.occurrences.update(row)
    if hasattr(StepType, "RETRIEVE_DOI"):
        legacy = app.executor.handlers[StepType.RETRIEVE]
        handler = app.executor.handlers[StepType.RETRIEVE_DOI]
        handler.retriever_factory = legacy.retriever_factory
        handler.fallback_factory = legacy.fallback_factory
        handler.browser_factory = legacy.browser_factory
    return pid, row


def enqueue_doi(app, pid, row, key="doi-first-download", **kwargs):
    return app.service.download_files(
        context(),
        api.DownloadFilesIn(
            project_id=pid,
            scope=api.OccurrenceScope(occurrence_ids=[row.occurrence_id]),
            retrieval_basis="citation_doi",
            version_policy="any_eligible",
            idempotency_key=key,
            **kwargs,
        ),
    )


def finish(app, pid, queued):
    asyncio.run(app.executor.run_until_idle())
    return app.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=queued.job_id))


def current_files(app, pid, row):
    return app.service.get_reference(
        context(),
        api.GetReferenceIn(
            project_id=pid,
            occurrence_id=row.occurrence_id,
            sections=["citation_files"],
        ),
    ).citation_files


def web_context():
    return api.CallContext(
        principal="test",
        channel=Channel.WEB_UI,
        scopes=frozenset(Scope),
        request_id="doi-web",
        web_session_id="doi-session",
    )


def review_request(pid, row, file, action="approve"):
    return api.ReviewFileDecisionIn(
        project_id=pid,
        occurrence_id=row.occurrence_id,
        work_file_id=file["citation_file_id"],
        file_kind="citation_file",
        action=action,
        expected_revision=row.revision,
        idempotency_key=f"doi-review-{action}",
    )


def test_req1_req2_req3_req5_disagreement_downloads_without_work_or_discovery(app, fallback, doi_case):
    pid, row = doi_case
    before = row.model_dump()
    result = finish(app, pid, enqueue_doi(app, pid, row))
    assert result.state == "completed"
    assert fallback["calls"] == ["fallback.example", "pdf.example"]
    assert refs(app, pid)[0].model_dump() == before
    assert app.store.works._select("project_id = ?", (pid,)) == []
    file = current_files(app, pid, row)[0]
    assert file["state"] == "verified"
    assert file["target_doi"] == DOI
    assert file["verification_basis"] == "citation_doi"
    assert file["verification_evidence"]["primary_dois"] == [DOI]
    assert file["citation_revision"] == row.revision
    assert file["citation_text_sha256"] == hashlib.sha256(row.text.encode()).hexdigest()
    out = app.service.get_reference(
        context(), api.GetReferenceIn(project_id=pid, occurrence_id=row.occurrence_id)
    )
    assert out.work is None and out.occurrence.citation_doi == DOI
    assert out.occurrence.citation_file_id == file["citation_file_id"]
    read = app.service.read_project_file(
        context(),
        api.ReadProjectFileIn(
            project_id=pid,
            kind="citation_file",
            resource_id=file["citation_file_id"],
            length=65536,
        ),
    )
    assert read.data == fallback["pdf"]
    assert read.descriptor.kind == "citation_file"


@pytest.mark.parametrize("pdf_kind", ["missing", "wrong", "references", "supplement", "version_conflict"])
def test_req3_req4_reviewable_checks_preserve_pending_file(app, fallback, doi_case, pdf_kind):
    pid, row = doi_case
    texts = {
        "missing": "A different title without a DOI",
        "wrong": "Title\n10.4321/wrong",
        "references": "Title\nReferences\n" + DOI,
        "supplement": "Supplementary material\n" + DOI,
        "version_conflict": f"Title\n{DOI}\nPublished version\nPreprint",
    }
    fallback["pdf"] = paper_pdf(texts[pdf_kind], [])
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    assert file["state"] == "needs_review"
    assert file["reasons"]
    assert refs(app, pid)[0].identity == "needs_review"
    assert refs(app, pid)[0].work_id is None


@pytest.mark.parametrize("body", [b"not a PDF", b"%PDF-1.4 broken"])
def test_req4_corrupt_bytes_cannot_become_approvable_files(app, fallback, doi_case, body):
    pid, row = doi_case
    fallback["pdf"] = body
    result = finish(app, pid, enqueue_doi(app, pid, row))
    assert current_files(app, pid, row) == []
    assert "FILE_VALIDATION_FAILED" in result.warnings
    assert refs(app, pid)[0].retrieval == "not_requested"


@pytest.mark.parametrize("action,state", [("approve", "verified"), ("reject", "failed")])
def test_req4_citation_file_review_is_bound_replayed_and_metadata_independent(
    app,
    fallback,
    doi_case,
    action,
    state,
):
    pid, row = doi_case
    fallback["pdf"] = paper_pdf("Title without DOI", [])
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    request = review_request(pid, row, file, action)
    ctx = web_context()
    token = app.service.prepare_web_file_review(ctx, request).confirmation_token
    tampered = request.model_copy(update={"file_kind": "work_file"})
    with pytest.raises(NotAuthorized):
        app.service.confirm_web_file_review(ctx, tampered, token)
    result = app.service.confirm_web_file_review(ctx, request, token)
    assert result.retrieval == state
    assert app.service.confirm_web_file_review(ctx, request, token) == result
    changed = refs(app, pid)[0]
    assert changed.identity == row.identity and changed.identity_reasons == row.identity_reasons
    assert changed.retrieval == row.retrieval and changed.work_id is None and changed.approval is None
    events = [
        e
        for e in app.store.audit.list(pid)
        if e.type in (AuditEventType.FILE_APPROVED, AuditEventType.FILE_REJECTED)
    ]
    assert len(events) == 1
    assert current_files(app, pid, row)[0]["state"] == state


@pytest.mark.parametrize("invalid", ["no_doi", "multiple", "excluded"])
def test_req7_invalid_target_never_selects_an_arbitrary_doi(app, fallback, doi_case, invalid):
    pid, row = doi_case
    row.edited_text = "No DOI" if invalid == "no_doi" else TEXT + " doi:10.1234/other"
    if invalid == "excluded":
        row.edited_text = TEXT
        row.extraction = ExtractionState.EXCLUDED
    row = app.store.occurrences.update(row)
    with pytest.raises(PrerequisiteNotMet):
        enqueue_doi(app, pid, row)
    assert fallback["calls"] == []


def test_req7_replay_and_repeated_download_reuse_current_file(app, fallback, doi_case):
    pid, row = doi_case
    first = enqueue_doi(app, pid, row)
    assert enqueue_doi(app, pid, row).job_id == first.job_id
    finish(app, pid, first)
    first_id = current_files(app, pid, row)[0]["citation_file_id"]
    finish(app, pid, enqueue_doi(app, pid, row, key="doi-second-download"))
    assert len(current_files(app, pid, row)) == 1
    assert current_files(app, pid, row)[0]["citation_file_id"] == first_id
    assert fallback["calls"] == ["fallback.example", "pdf.example"]


def test_req2_metadata_processing_does_not_destroy_doi_file(app, fallback, doi_case):
    pid, row = doi_case
    finish(app, pid, enqueue_doi(app, pid, row))
    file_id = current_files(app, pid, row)[0]["citation_file_id"]
    run(app, pid, ids=[row.occurrence_id], stages=("resolve",), refresh=True)
    after = refs(app, pid)[0]
    assert current_files(app, pid, after)[0]["citation_file_id"] == file_id
    assert after.identity == "accepted_auto"


def test_req6_req7_edit_during_fetch_discards_staged_file(app, fallback, doi_case):
    pid, row = doi_case
    queued = enqueue_doi(app, pid, row)

    def edit():
        current = app.store.occurrences.get(pid, row.occurrence_id)
        current.edited_text = "Changed DOI 10.1234/new"
        app.store.occurrences.update(current)

    fallback["on_landing"] = edit
    finish(app, pid, queued)
    assert current_files(app, pid, row) == []
    assert app.store.citation_files.list_for_occurrence(pid, row.occurrence_id) == []
    assert (
        app.store.db.query(
            "SELECT * FROM file_grant WHERE project_id = ? AND sha256 = ?",
            (pid, hashlib.sha256(fallback["pdf"]).hexdigest()),
        )
        == []
    )


def test_req6_cancelled_lookup_publishes_no_file(app, fallback, doi_case):
    pid, row = doi_case
    queued = enqueue_doi(app, pid, row)
    fallback["on_landing"] = lambda: app.service.control_job(
        context(),
        api.ControlJobIn(
            project_id=pid,
            job_id=queued.job_id,
            action="cancel",
            idempotency_key="doi-cancel-key",
        ),
    )
    assert finish(app, pid, queued).state == "cancelled"
    assert current_files(app, pid, row) == []
    assert fallback["calls"] == ["fallback.example"]


@pytest.mark.parametrize("gate", ["scope", "downloads", "network", "config", "policy"])
def test_req6_authority_and_configuration_are_not_bypassed(app, fallback, doi_case, gate):
    pid, row = doi_case
    ctx = context()
    policy = "any_eligible"
    if gate == "scope":
        ctx = dataclasses.replace(ctx, scopes=frozenset())
    elif gate in ("downloads", "network"):
        project = app.store.projects.get(pid)
        project.download_enabled = gate != "downloads"
        if gate == "network":
            project.network_mode = "metadata"
        app.store.projects.update(project)
    elif gate == "config":
        app.settings.pdf_fallback_url = None
    else:
        policy = "prefer_published"
    from refcollector.errors import RcError

    with pytest.raises(RcError):
        app.service.download_files(
            ctx,
            api.DownloadFilesIn(
                project_id=pid,
                scope=api.OccurrenceScope(occurrence_ids=[row.occurrence_id]),
                retrieval_basis="citation_doi",
                version_policy=policy,
                idempotency_key="doi-denied-key",
            ),
        )
    assert fallback["calls"] == []


def test_req6_project_and_read_scope_guards(app, fallback, doi_case):
    pid, row = doi_case
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    other = app.service.create_project(
        context(), api.CreateProjectIn(name="other", idempotency_key="other-project")
    ).project_id
    with pytest.raises(NotFound):
        app.service.project_file_path(context(), other, "citation_file", file["citation_file_id"])
    ctx = dataclasses.replace(context(), scopes=frozenset())
    with pytest.raises(NotAuthorized):
        app.service.project_file_path(ctx, pid, "citation_file", file["citation_file_id"])


def test_req7_default_accepted_work_admission_stays_unchanged(app, fallback, doi_case):
    pid, row = doi_case
    with pytest.raises(PrerequisiteNotMet):
        app.service.download_files(
            context(),
            api.DownloadFilesIn(
                project_id=pid,
                scope=api.OccurrenceScope(occurrence_ids=[row.occurrence_id]),
                idempotency_key="doi-legacy-key",
            ),
        )
    with pytest.raises(ValidationError):
        api.StartJobIn(
            project_id=pid,
            stages=["retrieve_doi"],
            scope=api.OccurrenceScope(occurrence_ids=[row.occurrence_id]),
            idempotency_key="public-doi-stage",
        )


def test_req3_normalized_parenthesized_primary_doi_passes(app, fallback, doi_case):
    pid, row = doi_case
    row.edited_text = "Citation doi:10.1234/a(b)."
    row = app.store.occurrences.update(row)
    fallback["pdf"] = paper_pdf("Unknown title\nhttps://doi.org/10.1234/a(b)", [])
    finish(app, pid, enqueue_doi(app, pid, row))
    assert current_files(app, pid, row)[0]["state"] == "verified"


@pytest.mark.parametrize("change", ["edit", "exclude"])
def test_req7_old_link_hidden_and_unreadable_after_citation_changes(app, fallback, doi_case, change):
    pid, row = doi_case
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    if change == "edit":
        row.edited_text = "A new citation doi:10.1234/new"
    else:
        row.extraction = ExtractionState.EXCLUDED
    app.store.occurrences.update(row)
    assert current_files(app, pid, row) == []
    summary = app.service.list_references(context(), api.ListReferencesIn(project_id=pid)).occurrences[0]
    assert summary["citation_file_id"] is None
    with pytest.raises(NotFound):
        app.service.project_file_path(context(), pid, "citation_file", file["citation_file_id"])
    assert len(app.store.citation_files.list_for_occurrence(pid, row.occurrence_id)) == 1


def test_req6_snapshot_keeps_original_configured_endpoint(app, fallback, doi_case):
    pid, row = doi_case
    queued = enqueue_doi(app, pid, row)
    app.settings.pdf_fallback_url = "https://changed.example/{doi}"
    finish(app, pid, queued)
    assert current_files(app, pid, row)[0]["state"] == "verified"
    assert fallback["calls"] == ["fallback.example", "pdf.example"]


def test_req6_queue_time_text_is_bound_before_planning(app, fallback, doi_case):
    pid, row = doi_case
    queued = enqueue_doi(app, pid, row)
    row.edited_text = "Different doi:10.1234/changed"
    app.store.occurrences.update(row)
    result = finish(app, pid, queued)
    assert result.state == "failed"
    assert fallback["calls"] == []
    assert current_files(app, pid, row) == []


def test_req6_job_budget_counts_pending_and_verified_citation_files(app, fallback, doi_case):
    from tests.unit.pipeline.conftest import source

    pid, row = doi_case
    sid = source(app, pid, TEXT.replace(DOI, "10.1234/other"))
    run(app, pid, sid, stages=("extract",))
    queued = app.service.download_files(
        context(),
        api.DownloadFilesIn(
            project_id=pid,
            scope=api.FilterScope(filter=api.ReferenceFilter()),
            retrieval_basis="citation_doi",
            version_policy="any_eligible",
            budget=api.DownloadBudget(max_files=1),
            idempotency_key="doi-budget-key",
        ),
    )
    result = finish(app, pid, queued)
    files = [f for occurrence in refs(app, pid) for f in current_files(app, pid, occurrence)]
    assert len(files) == 1
    assert any(
        step.last_error and step.last_error["code"] == "BUDGET_EXCEEDED"
        for step in app.executor.queue.steps(result.job_id)
    )


def test_req6_remaining_byte_budget_denies_pdf(app, fallback, doi_case):
    pid, row = doi_case
    result = finish(app, pid, enqueue_doi(app, pid, row, budget=api.DownloadBudget(max_bytes=10)))
    assert current_files(app, pid, row) == []
    assert "LIMIT_EXCEEDED" in result.warnings


def test_req6_publication_failure_rolls_back_file_and_grant(app, fallback, doi_case, monkeypatch):
    pid, row = doi_case

    def fail(*args, **kwargs):
        raise InvalidInput("injected citation publication failure")

    monkeypatch.setattr(app.store.citation_files, "add", fail)
    finish(app, pid, enqueue_doi(app, pid, row))
    assert current_files(app, pid, row) == []
    sha = hashlib.sha256(fallback["pdf"]).hexdigest()
    assert not app.store.files.has_grant(pid, sha)
    assert not [e for e in app.store.audit.list(pid) if e.type == AuditEventType.FILE_RETRIEVED]


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", "other"),
        ("occurrence_id", "other"),
        ("work_file_id", "other"),
        ("action", "reject"),
        ("expected_revision", 100),
        ("note", "tampered"),
        ("idempotency_key", "changed-key"),
        ("file_kind", "work_file"),
    ],
)
def test_req4_confirmation_payload_cannot_change_after_prepare(app, fallback, doi_case, field, value):
    pid, row = doi_case
    fallback["pdf"] = paper_pdf("Missing DOI", [])
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    req = review_request(pid, row, file)
    token = app.service.prepare_web_file_review(web_context(), req).confirmation_token
    before = app.store.occurrences.get(pid, row.occurrence_id).model_dump()
    with pytest.raises(NotAuthorized):
        app.service.confirm_web_file_review(web_context(), req.model_copy(update={field: value}), token)
    assert app.store.occurrences.get(pid, row.occurrence_id).model_dump() == before
    assert current_files(app, pid, row)[0]["state"] == "needs_review"
    assert not [e for e in app.store.audit.list(pid) if e.type == AuditEventType.FILE_APPROVED]


@pytest.mark.parametrize("guard", ["mcp", "cli", "session", "principal", "scope", "expired", "purpose"])
def test_req4_confirmation_authority_token_checks_preserved(app, fallback, doi_case, guard):
    from datetime import timedelta

    pid, row = doi_case
    fallback["pdf"] = paper_pdf("Missing DOI", [])
    finish(app, pid, enqueue_doi(app, pid, row))
    req = review_request(pid, row, current_files(app, pid, row)[0])
    ctx = web_context()
    token = app.service.prepare_web_file_review(ctx, req).confirmation_token
    if guard in ("mcp", "cli"):
        ctx = dataclasses.replace(ctx, channel=Channel(guard))
    elif guard == "session":
        ctx = dataclasses.replace(ctx, web_session_id="other")
    elif guard == "principal":
        ctx = dataclasses.replace(ctx, principal="other")
    elif guard == "scope":
        ctx = dataclasses.replace(ctx, scopes=frozenset())
    elif guard == "expired":
        now = app.service.clock()
        app.service.clock = lambda: now + timedelta(seconds=61)
    else:
        registry = app.service._intent_registry
        record = registry.get(token)
        registry._pending[token] = dataclasses.replace(record, purpose="review")
    with pytest.raises(NotAuthorized):
        app.service.confirm_web_file_review(ctx, req, token)
    assert current_files(app, pid, row)[0]["state"] == "needs_review"


@pytest.mark.parametrize("unsafe", ["encrypted", "oversized", "private_url"])
def test_req4_req6_unsafe_input_never_becomes_pending_review(app, fallback, doi_case, unsafe):
    from tests.fixtures.pdfs import encrypted_pdf

    pid, row = doi_case
    if unsafe == "encrypted":
        fallback["pdf"] = encrypted_pdf()
    elif unsafe == "oversized":
        app.settings.limits.pdf_bytes = 100
    else:
        fallback["html"] = '<iframe id="pdf" src="https://127.0.0.1/private.pdf"></iframe>'
    finish(app, pid, enqueue_doi(app, pid, row))
    assert current_files(app, pid, row) == []
    assert refs(app, pid)[0].identity == "needs_review"


def test_req4_other_occurrence_cannot_approve_citation_file(app, fallback, doi_case):
    from tests.unit.pipeline.conftest import source

    pid, row = doi_case
    fallback["pdf"] = paper_pdf("Missing DOI", [])
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    sid = source(app, pid, TEXT + " ")
    run(app, pid, sid, stages=("extract",))
    other = next(o for o in refs(app, pid) if o.occurrence_id != row.occurrence_id)
    req = review_request(pid, other, file)
    with pytest.raises(InvalidInput):
        app.service.prepare_web_file_review(web_context(), req)


def test_req4_revision_change_after_prepare_prevents_confirmation(app, fallback, doi_case):
    from refcollector.errors import RevisionConflict

    pid, row = doi_case
    fallback["pdf"] = paper_pdf("Missing DOI", [])
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    req = review_request(pid, row, file)
    token = app.service.prepare_web_file_review(web_context(), req).confirmation_token
    row.edited_text = "Changed DOI 10.1234/other"
    app.store.occurrences.update(row)
    with pytest.raises(RevisionConflict):
        app.service.confirm_web_file_review(web_context(), req, token)
    assert app.store.citation_files.get(pid, file["citation_file_id"]).state == "needs_review"


def test_req4_confirmation_failure_rolls_back_file_occurrence_and_audit(app, fallback, doi_case, monkeypatch):
    pid, row = doi_case
    fallback["pdf"] = paper_pdf("Missing DOI", [])
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    req = review_request(pid, row, file)
    token = app.service.prepare_web_file_review(web_context(), req).confirmation_token

    def fail(*args, **kwargs):
        raise InvalidInput("injected occurrence failure")

    monkeypatch.setattr(app.store.occurrences, "update", fail)
    with pytest.raises(InvalidInput):
        app.service.confirm_web_file_review(web_context(), req, token)
    assert app.store.citation_files.get(pid, file["citation_file_id"]).state == "needs_review"
    assert not [e for e in app.store.audit.list(pid) if e.type == AuditEventType.FILE_APPROVED]


def test_req6_citation_file_reads_preserve_hash_and_rc_home_boundaries(
    app, fallback, doi_case, tmp_path, monkeypatch
):
    from refcollector.errors import FileValidationFailed, PathNotAllowed

    pid, row = doi_case
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    path = app.service.project_file_path(context(), pid, "citation_file", file["citation_file_id"])
    path.write_bytes(b"corrupt stored bytes")
    req = api.ReadProjectFileIn(project_id=pid, kind="citation_file", resource_id=file["citation_file_id"])
    with pytest.raises(FileValidationFailed):
        app.service.read_project_file(context(), req)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"data")
    monkeypatch.setattr(app.store.files, "path_for", lambda *args: outside)
    with pytest.raises(PathNotAllowed):
        app.service.project_file_path(context(), pid, "citation_file", file["citation_file_id"])


def test_req7_restart_keeps_citation_file_without_metadata(app, fallback, doi_case):
    from refcollector.storage.repos import SqliteStore

    pid, row = doi_case
    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    reloaded = SqliteStore(app.db, app.settings)
    restored = reloaded.citation_files.get(pid, file["citation_file_id"])
    assert restored.target_doi == DOI and restored.is_current(row)
    assert restored.state == "verified"


def test_req8_configured_browser_fallback_resolves_and_downloads_citation_file(app, fallback, doi_case):
    from refcollector.domain.enums import LocationBasis
    from refcollector.domain.models import AccessLocation

    pid, row = doi_case
    calls = []

    class Resolver:
        user_agent = "Mozilla/5.0 Browser"

        def __init__(self, **kwargs):
            self.python = kwargs["python"]

        async def resolve(self, template, doi, *, is_cancelled):
            calls.append(self.python)
            return AccessLocation(
                location_id="loc_browser",
                url="https://pdf.example/paper.pdf",
                provider="configured_browser_fallback",
                basis=LocationBasis.CONFIGURED_FALLBACK,
                eligible=True,
                is_pdf=True,
            )

    app.executor.handlers[StepType.RETRIEVE_DOI].browser_factory = Resolver
    app.settings.pdf_browser_python = "isolated-python"
    app.settings.pdf_browser_binary = "chromium"
    fallback["html"] = "<h1>Are you a robot?</h1>"

    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    assert file["state"] == "verified"
    assert file["target_doi"] == DOI
    assert calls == ["isolated-python"]
    assert fallback["calls"] == ["fallback.example", "pdf.example"]

    event = next(e for e in reversed(app.store.audit.list(pid)) if e.type == AuditEventType.FILE_RETRIEVED)
    assert event.payload["browser_attempted"] is True


def test_req8_static_success_does_not_launch_browser(app, fallback, doi_case):
    pid, row = doi_case
    calls = []

    class Resolver:
        def __init__(self, **kwargs):
            pass

        async def resolve(self, template, doi, *, is_cancelled):
            calls.append("browser")
            return None

    app.executor.handlers[StepType.RETRIEVE_DOI].browser_factory = Resolver
    app.settings.pdf_browser_python = "isolated-python"
    fallback["html"] = '<a href="https://pdf.example/storage/hash/paper.pdf">PDF</a>'

    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    assert file["state"] == "verified"
    assert calls == []

    event = next(e for e in reversed(app.store.audit.list(pid)) if e.type == AuditEventType.FILE_RETRIEVED)
    assert event.payload["browser_attempted"] is False


def test_req8_failed_static_pdf_retries_browser_fallback(app, fallback, doi_case):
    from refcollector.domain.enums import LocationBasis
    from refcollector.domain.models import AccessLocation

    pid, row = doi_case
    calls = []

    class Resolver:
        user_agent = "Mozilla/5.0 Browser"

        def __init__(self, **kwargs):
            self.python = kwargs["python"]

        async def resolve(self, template, doi, *, is_cancelled):
            calls.append(self.python)
            return AccessLocation(
                location_id="loc_browser",
                url="https://pdf.example/paper.pdf",
                provider="configured_browser_fallback",
                basis=LocationBasis.CONFIGURED_FALLBACK,
                eligible=True,
                is_pdf=True,
            )

    app.executor.handlers[StepType.RETRIEVE_DOI].browser_factory = Resolver
    app.settings.pdf_browser_python = "isolated-python"
    fallback["html"] = '<iframe id="pdf" src="https://repo.example/bad.pdf">'
    fallback["primary_html"] = True

    finish(app, pid, enqueue_doi(app, pid, row))
    file = current_files(app, pid, row)[0]
    assert file["state"] == "verified"
    assert calls == ["isolated-python"]
    assert fallback["calls"] == ["fallback.example", "repo.example", "pdf.example"]

    event = next(e for e in reversed(app.store.audit.list(pid)) if e.type == AuditEventType.FILE_RETRIEVED)
    assert event.payload["browser_attempted"] is True
