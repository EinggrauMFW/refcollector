"""Opt-in fallback keeps normal retrieval, scope and verification boundaries."""

import asyncio

import httpx
import pytest

from refcollector.contracts import api
from refcollector.domain.enums import AuditEventType, StepType
from refcollector.files.netguard import DefaultNetGuard
from tests.fixtures.pdfs import paper_pdf
from tests.unit.pipeline.conftest import DOI, TEXT, TITLE, context, create, refs, run, source
from tests.unit.pipeline.test_acceptance import enable_download

TEMPLATE = "https://fallback.example/{doi}"


@pytest.fixture
def fallback(app, offline, monkeypatch):
    from refcollector.files.pdf_fallback import DoiPdfFallback

    original = offline.response
    state = {
        "calls": [],
        "primary_html": False,
        "html": '<iframe id="pdf" src="https://pdf.example/paper.pdf"></iframe>',
        "pdf": offline.body,
    }

    def response(request):
        if request.url.host in {"repo.example", "fallback.example", "pdf.example"}:
            state["calls"].append(request.url.host)
        if request.url.host == "fallback.example":
            if callback := state.get("on_landing"):
                callback()
            return httpx.Response(200, text=state["html"], headers={"Content-Type": "text/html"})
        if request.url.host == "pdf.example":
            return httpx.Response(200, content=state["pdf"], headers={"Content-Type": "application/pdf"})
        if request.url.host == "repo.example" and state["primary_html"]:
            return httpx.Response(200, text="Not a PDF", headers={"Content-Type": "text/html"})
        return original(request)

    monkeypatch.setattr(offline, "response", response)

    async def resolve(host, port):
        return ["8.8.8.8"]

    app.executor.handlers[StepType.RETRIEVE].fallback_factory = lambda files, limits: DoiPdfFallback(
        files, limits=limits, guard=DefaultNetGuard(resolve=resolve)
    )
    return state


def prepare(app):
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    return pid, refs(app, pid)[0]


def enqueue(app, pid, ids, *, policy="any_eligible", budget=None):
    return app.service.download_files(
        context(),
        api.DownloadFilesIn(
            project_id=pid,
            scope=api.OccurrenceScope(occurrence_ids=ids),
            version_policy=policy,
            budget=budget,
            idempotency_key=f"fallback-{len(app.executor.queue.list_jobs(pid, limit=1000)):04}",
        ),
    )


def finish(app, pid, queued):
    asyncio.run(app.executor.run_until_idle())
    return app.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=queued.job_id))


def test_disabled_fallback_does_not_contact_resolver(app, fallback):
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert fallback["calls"] == []
    assert refs(app, pid)[0].retrieval == "not_requested"


def test_fallback_verifies_pdf_without_changing_oa_or_approval(app, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    pid, row = prepare(app)
    result = finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert result.state == "completed"
    updated = refs(app, pid)[0]
    assert updated.retrieval == "verified" and updated.classification == "none_found"
    assert updated.identity == "accepted_auto" and updated.approval is None
    assert fallback["calls"] == ["fallback.example", "pdf.example"]
    file = app.store.work_files.list_for_work(pid, updated.work_id)[0]
    assert file.source_url == "https://pdf.example/paper.pdf"
    events = [e for e in app.store.audit.list(pid) if e.type == AuditEventType.FILE_RETRIEVED]
    assert events[-1].payload["fallback_attempted"] is True


def test_normal_failure_precedes_fallback(app, offline, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    offline.location = True
    fallback["primary_html"] = True
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "verified"
    assert fallback["calls"] == ["repo.example", "fallback.example", "pdf.example"]
    event = next(e for e in reversed(app.store.audit.list(pid)) if e.type == AuditEventType.FILE_RETRIEVED)
    assert event.payload["primary_error"]["code"] == "FILE_VALIDATION_FAILED"


@pytest.mark.parametrize("review_pdf", [False, True])
def test_existing_normal_pdf_prevents_fallback_even_when_review_needed(app, offline, fallback, review_pdf):
    app.settings.pdf_fallback_url = TEMPLATE
    offline.location = True
    if review_pdf:
        offline.body = paper_pdf("Unrelated medical study", ["Other"], "10.9999/wrong")
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == ("needs_review" if review_pdf else "verified")
    assert fallback["calls"] == ["repo.example"]


def test_default_version_policy_does_not_assume_fallback_is_published(app, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    pid, row = prepare(app)
    result = finish(app, pid, enqueue(app, pid, [row.occurrence_id], policy="prefer_published"))
    assert fallback["calls"] == []
    assert refs(app, pid)[0].retrieval == "not_requested"
    assert any("any_eligible" in warning for warning in result.warnings)


@pytest.mark.parametrize("enabled_when_queued", [False, True])
def test_fallback_uses_queued_snapshot_not_current_settings(app, fallback, enabled_when_queued):
    app.settings.pdf_fallback_url = TEMPLATE if enabled_when_queued else None
    pid, row = prepare(app)
    queued = enqueue(app, pid, [row.occurrence_id])
    app.settings.pdf_fallback_url = None if enabled_when_queued else TEMPLATE
    finish(app, pid, queued)
    assert ("fallback.example" in fallback["calls"]) is enabled_when_queued


def test_missing_pdf_link_stays_not_requested_with_warning(app, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    fallback["html"] = "<html>CAPTCHA or unavailable article</html>"
    pid, row = prepare(app)
    result = finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "not_requested"
    assert not app.store.work_files.list_for_work(pid, row.work_id)
    assert any("no PDF link" in warning for warning in result.warnings)
    assert fallback["calls"] == ["fallback.example"]


def test_fallback_still_rejects_wrong_identity(app, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    fallback["pdf"] = paper_pdf("Unrelated medical study", ["Other"], "10.9999/wrong")
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "needs_review"
    assert app.store.work_files.list_for_work(pid, row.work_id)[0].state == "needs_review"


def test_usable_local_copy_prevents_fallback(app, fallback, tmp_path):
    app.settings.pdf_fallback_url = TEMPLATE
    pid, row = prepare(app)
    path = tmp_path / "attached.pdf"
    path.write_bytes(paper_pdf(TITLE + "\nVersion of Record", ["Smith, A."], DOI))
    app.service.attach_file(
        context(),
        api.AttachFileIn(
            project_id=pid,
            occurrence_id=row.occurrence_id,
            path=str(path),
            version_hint="published",
            idempotency_key="fallback-local-copy",
        ),
    )
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "satisfied_by_local_copy"
    assert fallback["calls"] == []


def test_job_file_budget_stops_second_fallback(app, offline, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    pid, row = prepare(app)
    offline.doi = "10.1234/other"
    sid = source(app, pid, TEXT.replace("doi:10.1234/waves", "doi:10.1234/other"))
    run(app, pid, sid)
    result = finish(
        app,
        pid,
        enqueue(app, pid, [r.occurrence_id for r in refs(app, pid)], budget=api.DownloadBudget(max_files=1)),
    )
    assert result.state == "completed_with_warnings"
    assert fallback["calls"] == ["fallback.example", "pdf.example"]
    assert any(
        s.last_error and s.last_error["code"] == "BUDGET_EXCEEDED"
        for s in app.executor.queue.steps(result.job_id)
    )


def test_fallback_pdf_obeys_remaining_byte_budget(app, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    pid, row = prepare(app)
    result = finish(
        app,
        pid,
        enqueue(app, pid, [row.occurrence_id], budget=api.DownloadBudget(max_bytes=len(fallback["pdf"]) - 1)),
    )
    assert result.state == "completed_with_warnings"
    assert refs(app, pid)[0].retrieval == "failed"
    assert not app.store.work_files.list_for_work(pid, row.work_id)
    assert "LIMIT_EXCEEDED" in result.warnings


def test_cancelled_landing_lookup_does_not_download_or_publish_pdf(app, fallback):
    app.settings.pdf_fallback_url = TEMPLATE
    pid, row = prepare(app)
    queued = enqueue(app, pid, [row.occurrence_id])
    fallback["on_landing"] = lambda: app.service.control_job(
        context(),
        api.ControlJobIn(
            project_id=pid, job_id=queued.job_id, action="cancel", idempotency_key="cancel-fallback"
        ),
    )
    result = finish(app, pid, queued)
    assert result.state == "cancelled"
    assert fallback["calls"] == ["fallback.example"]
    assert not app.store.work_files.list_for_work(pid, row.work_id)
    assert not list(app.store.files.tmp.glob("*.html"))


def test_fallback_timeout_has_typed_error_and_no_file(app, fallback):
    class TimedOut:
        async def resolve(self, *args, **kwargs):
            raise TimeoutError

    app.settings.pdf_fallback_url = TEMPLATE
    app.executor.handlers[StepType.RETRIEVE].fallback_factory = lambda files, limits: TimedOut()
    pid, row = prepare(app)
    result = finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "failed"
    assert "PROVIDER_UNAVAILABLE" in result.warnings
    assert not app.store.work_files.list_for_work(pid, row.work_id)


def test_saved_review_pdf_prevents_fallback_in_later_job(app, offline, fallback):
    offline.location = True
    offline.body = paper_pdf("Unrelated medical study", ["Other"], "10.9999/wrong")
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    saved = app.store.work_files.list_for_work(pid, row.work_id)
    assert len(saved) == 1 and saved[0].state == "needs_review"
    offline.location = False
    app.db.execute("DELETE FROM response_cache")
    run(app, pid, ids=[row.occurrence_id], stages=("discover_access",), refresh=True)
    app.settings.pdf_fallback_url = TEMPLATE
    fallback["calls"].clear()
    result = finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert fallback["calls"] == []
    assert refs(app, pid)[0].retrieval == "needs_review"
    assert app.store.work_files.list_for_work(pid, row.work_id) == saved
    assert any("stored PDF" in warning for warning in result.warnings)
