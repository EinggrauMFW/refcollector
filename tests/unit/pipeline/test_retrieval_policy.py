"""Offline regressions for retrieval with no policy-selected location (spec 10.2-10.4)."""

import asyncio

import httpx

from refcollector.contracts import api
from refcollector.contracts.ports import StepContext
from refcollector.domain.enums import AuditEventType, RetrievalState, StepType, VersionPolicy
from tests.fixtures.pdfs import paper_pdf
from tests.unit.pipeline.conftest import TITLE, context, create, refs, run
from tests.unit.pipeline.test_acceptance import download, enable_download


def prepare(app, offline, monkeypatch, *, submitted=False):
    if submitted:
        offline.location = True
        original = offline.response

        def response(request):
            result = original(request)
            if request.url.host == "api.unpaywall.org":
                data = result.json()
                data["oa_locations"][0]["version"] = "submittedVersion"
                return httpx.Response(200, json=data)
            return result

        monkeypatch.setattr(offline, "response", response)
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    return pid, refs(app, pid)[0]


def test_no_locations_keeps_not_requested(app, offline, monkeypatch):
    pid, occ = prepare(app, offline, monkeypatch)
    result = download(app, pid, [occ.occurrence_id])
    assert result.state == "completed_with_warnings"
    assert refs(app, pid)[0].retrieval == "not_requested"
    assert refs(app, pid)[0].classification == "none_found"


def test_only_excluded_versions_keeps_not_requested_and_classification(app, offline, monkeypatch):
    pid, occ = prepare(app, offline, monkeypatch, submitted=True)
    download(app, pid, [occ.occurrence_id])
    assert refs(app, pid)[0].retrieval == "not_requested"
    assert refs(app, pid)[0].classification == "eligible_found"
    assert not offline.downloads


def test_empty_selection_does_not_open_network_session(app, offline, monkeypatch):
    pid, occ = prepare(app, offline, monkeypatch)
    before = len(offline.clients)
    download(app, pid, [occ.occurrence_id])
    assert len(offline.clients) == before
    assert not offline.downloads


def test_empty_selection_has_useful_warning_without_failed_download(app, offline, monkeypatch):
    pid, occ = prepare(app, offline, monkeypatch, submitted=True)
    result = download(app, pid, [occ.occurrence_id])
    assert any("prefer_published" in warning and "skipped" in warning for warning in result.warnings)
    events = [
        event
        for event in app.store.audit.list(pid)
        if event.type == AuditEventType.FILE_RETRIEVED and event.payload.get("job_id") == result.job_id
    ]
    assert len(events) == 1
    assert events[0].payload["state"] == "not_requested"
    assert events[0].payload["error"] is None
    assert events[0].payload["bytes"] == 0
    assert events[0].payload["reasons"]
    assert not app.store.work_files.list_for_work(pid, occ.work_id)


def test_usable_local_copy_wins_with_empty_selection(app, offline, monkeypatch, tmp_path):
    pid, occ = prepare(app, offline, monkeypatch, submitted=True)
    path = tmp_path / "published.pdf"
    path.write_bytes(paper_pdf(TITLE + "\nVersion of Record", [], "10.1234/waves"))
    app.service.attach_file(
        context(),
        api.AttachFileIn(
            project_id=pid,
            occurrence_id=occ.occurrence_id,
            path=str(path),
            version_hint="published",
            idempotency_key="local-published",
        ),
    )
    before = len(offline.clients)
    result = download(app, pid, [occ.occurrence_id])
    assert result.state == "completed"
    assert refs(app, pid)[0].retrieval == "satisfied_by_local_copy"
    assert len(offline.clients) == before
    assert refs(app, pid)[0].classification == "eligible_found"


def test_empty_selection_preserves_previously_verified_file(app, offline, monkeypatch):
    offline.location = True
    pid, occ = prepare(app, offline, monkeypatch)
    first = download(app, pid, [occ.occurrence_id])
    assert first.state == "completed"
    stored = app.store.work_files.list_for_work(pid, occ.work_id)[0]
    assert refs(app, pid)[0].retrieval == "verified"
    offline.location = False
    run(app, pid, ids=[occ.occurrence_id], stages=("discover_access",), refresh=True)
    previous = app.store.occurrences.get(pid, occ.occurrence_id)
    previous.retrieval = RetrievalState.FAILED
    app.store.occurrences.update(previous, expected_revision=previous.revision)
    before = len(offline.clients)
    job = app.executor.queue.get_job(pid, first.job_id)
    # A cited-only request cannot reuse this file without cited-version evidence.
    job.snapshot.version_policy = VersionPolicy.CITED_VERSION_ONLY
    step = app.executor.queue.steps(first.job_id)[0]
    handler = app.executor.handlers[StepType.RETRIEVE]
    result = asyncio.run(handler.run(StepContext(step, job, app.store, lambda: False)))
    with app.store.transaction(immediate=True):
        result.commit(app.store)
    handler.cleanup(step.step_id)
    assert result.warnings
    assert refs(app, pid)[0].retrieval == "failed"
    assert app.store.work_files.list_for_work(pid, occ.work_id) == [stored]
    assert app.store.files.path_for(pid, stored.sha256).read_bytes() == offline.body
    assert len(offline.clients) == before
