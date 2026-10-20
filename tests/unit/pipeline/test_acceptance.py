import asyncio
import hashlib
import json
from email import policy
from email.parser import BytesParser

import pytest

from refcollector.bootstrap import open_app
from refcollector.contracts import api
from refcollector.domain.enums import StepType
from tests.unit.pipeline.conftest import TEXT, TITLE, change, context, create, refs, run, source


def test_r04_r07_at18_w09_stage_time_planning_and_two_source_dedup(app):
    pid, sid = create(app)
    first = run(app, pid, sid)
    second_sid = source(app, pid, TEXT + " ")
    second = run(app, pid, second_sid)
    rows = refs(app, pid)
    assert first.state == second.state == "completed"
    assert len(rows) == 2 and len({o.work_id for o in rows}) == 1
    assert {o.identity for o in rows} == {"accepted_auto"}
    work = app.store.works.get(pid, rows[0].work_id)
    assert work.metadata_source_ranks["title"] == 0
    assert app.store.access_checks.current(pid, work.work_id).classification == "none_found"


def test_r04_at03_split_resolves_only_successors(app, offline):
    pid, sid = create(app)
    run(app, pid, sid)
    old = refs(app, pid)[0]
    split = change(
        app,
        pid,
        api.Split(
            type="split",
            occurrence_id=old.occurrence_id,
            parts=[api.SplitPart(text=TEXT), api.SplitPart(text=TEXT)],
            expected_revision=old.revision,
        ),
    )
    result = run(app, pid, sid, stages=("resolve",))
    assert result.state == "completed"
    steps = app.executor.queue.steps(result.job_id)
    assert {s.subject_id for s in steps} == {r.occurrence_id for r in split.created}
    assert app.store.occurrences.get(pid, old.occurrence_id).extraction == "excluded"


def test_r04_r05_edited_citation_invalidates_completed_resolution(app):
    pid, sid = create(app)
    run(app, pid, sid)
    occ = refs(app, pid)[0]
    change(
        app,
        pid,
        api.EditText(
            type="edit_text",
            occurrence_id=occ.occurrence_id,
            text=TEXT + " Updated citation.",
            expected_revision=occ.revision,
        ),
    )
    result = run(app, pid, sid, stages=("resolve",))
    assert len(app.executor.queue.steps(result.job_id)) == 1
    assert result.results.occurrence_count == 1


def test_r07_at11_locks_and_ranked_metadata_survive_refresh(app):
    pid, sid = create(app)
    run(app, pid, sid)
    occ = refs(app, pid)[0]
    change(
        app,
        pid,
        api.SetFields(
            type="set_fields",
            occurrence_id=occ.occurrence_id,
            fields={"title": "Corrected locked title"},
            expected_revision=occ.revision,
        ),
    )
    occ = refs(app, pid)[0]
    change(
        app,
        pid,
        api.SetFieldLocks(
            type="set_field_locks",
            occurrence_id=occ.occurrence_id,
            locks={"title": True},
            expected_revision=occ.revision,
        ),
    )
    result = run(app, pid, sid, stages=("resolve",), refresh=True)
    assert result.state == "completed"
    work = app.store.works.get(pid, refs(app, pid)[0].work_id)
    assert work.metadata.title == "Corrected locked title"
    assert work.metadata_source_ranks["title"] == 0 and work.locks["title"]


def test_r03_clients_per_job_snapshot_and_closed(app, offline):
    pid, sid = create(app)
    assert offline.clients == []
    result = run(app, pid, sid)
    assert result.state == "completed"
    assert len(offline.clients) == 1
    assert all(s.contact_email == "test@example.org" for s in offline.snapshots)
    assert all(c._client.is_closed for c in offline.clients)


def test_r11_export_manifest_revision_hash_and_job_readback(app):
    pid, sid = create(app)
    run(app, pid, sid)
    revision = app.store.projects.get(pid).revision
    job = app.service.create_export(
        context(),
        api.CreateExportIn(
            project_id=pid,
            project_revision=revision,
            formats=["bibtex", "csl_json", "csv", "json", "unresolved", "audit_report"],
            selection="complete_audit",
            idempotency_key="export01",
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    result = app.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=job.job_id))
    assert result.state == "completed" and len(result.results.artifact_ids) == 7
    artifacts = [app.store.artifacts.get(pid, aid) for aid in result.results.artifact_ids]
    manifest = next(a for a in artifacts if a.kind == "manifest")
    content = app.service.read_artifact(
        context(), api.ReadArtifactIn(project_id=pid, artifact_id=manifest.artifact_id)
    )
    payload = json.loads(content.text)
    assert payload["revision"] == revision
    for file in payload["files"]:
        descriptor = next(a for a in artifacts if a.name == file["name"])
        raw = app.store.files.path_for(pid, descriptor.sha256).read_bytes()
        assert file["sha256"] == hashlib.sha256(raw).hexdigest() and file["bytes"] == len(raw)
    assert all(a.project_revision == revision and a.job_id == job.job_id for a in artifacts)


def test_r12_draft_artifacts_blank_recipients(app):
    pid, sid = create(app)
    run(app, pid, sid)
    job = app.service.draft_requests(
        context(),
        api.DraftRequestsIn(
            project_id=pid,
            scope=api.OccurrenceScope(occurrence_ids=[refs(app, pid)[0].occurrence_id]),
            sender=api.SenderIn(name="Researcher", affiliation="Institute", purpose="Study"),
            language="en",
            group_by_author=True,
            idempotency_key="draft001",
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    result = app.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=job.job_id))
    assert result.state == "completed"
    artifacts = [app.store.artifacts.get(pid, aid) for aid in result.results.artifact_ids]
    assert {a.kind for a in artifacts} == {"draft_bundle", "draft_eml"}
    for artifact in artifacts:
        raw = app.store.files.path_for(pid, artifact.sha256).read_bytes()
        if artifact.kind == "draft_eml":
            message = BytesParser(policy=policy.default).parsebytes(raw)
            assert not message["To"] and TITLE in message.get_body().get_content()


def test_r02_two_handles_never_delete_foreign_temp_and_close_safe(app):
    foreign = app.store.files.new_tmp_path(".foreign")
    foreign.write_bytes(b"active import")
    second = open_app(app.settings)
    try:
        assert app.executor.try_acquire()
        assert not second.executor.try_acquire()
        assert foreign.read_bytes() == b"active import"
        second.close()
        second.close()
        assert foreign.exists()
    finally:
        second.close()


def test_r06_reextract_preserves_split_and_edit_without_duplicates(app):
    pid, sid = create(app)
    run(app, pid, sid)
    old = refs(app, pid)[0]
    change(
        app,
        pid,
        api.Split(
            type="split",
            occurrence_id=old.occurrence_id,
            parts=[api.SplitPart(text=TEXT), api.SplitPart(text=TEXT)],
            expected_revision=old.revision,
        ),
    )
    run(app, pid, sid, stages=("extract",))
    active = [o for o in refs(app, pid) if o.extraction != "excluded"]
    assert len(active) == 2


def enable_download(app, pid):
    app.service.update_project_settings(
        context(),
        api.UpdateProjectSettingsIn(
            project_id=pid,
            network_mode="retrieval",
            download_enabled=True,
            idempotency_key="retrieve-permission",
        ),
    )


def download(app, pid, ids, budget=None):
    job = app.service.download_files(
        context(),
        api.DownloadFilesIn(
            project_id=pid,
            scope=api.OccurrenceScope(occurrence_ids=ids),
            budget=budget,
            idempotency_key=f"download-{len(app.executor.queue.list_jobs(pid, limit=1000)):04}",
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    return app.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=job.job_id))


def test_r09_secured_default_retrieval_real_http_client(app, offline):
    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    result = download(app, pid, [refs(app, pid)[0].occurrence_id])
    assert result.state == "completed"
    occ = refs(app, pid)[0]
    assert occ.retrieval == "verified" and occ.classification == "eligible_found"
    wf = app.store.work_files.list_for_work(pid, occ.work_id)[0]
    assert wf.sha256 == hashlib.sha256(offline.body).hexdigest()
    assert app.store.files.has_grant(pid, wf.sha256)


def test_r09_at19_managed_local_copy_no_second_download(app, offline):
    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    oid = refs(app, pid)[0].occurrence_id
    download(app, pid, [oid])
    # The PDF builder has no version statement; add positive published-version evidence.
    from tests.fixtures.pdfs import paper_pdf

    offline.body = paper_pdf(TITLE + "\nVersion of Record", ["Smith, A."], "10.1234/waves")
    download(app, pid, [oid])
    before = len(offline.downloads)
    result = download(app, pid, [oid])
    assert result.state == "completed"
    assert refs(app, pid)[0].retrieval == "satisfied_by_local_copy"
    assert len(offline.downloads) == before


@pytest.mark.parametrize("supplement", [False, True])
def test_r09_wrong_or_supplement_pdf_never_verified(app, offline, supplement):
    from tests.fixtures.pdfs import paper_pdf

    offline.location = True
    offline.body = paper_pdf(
        TITLE if supplement else "Different research paper",
        [],
        "10.1234/waves" if supplement else "10.4321/other",
        supplement=supplement,
    )
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    result = download(app, pid, [refs(app, pid)[0].occurrence_id])
    assert result.state == "completed"
    assert refs(app, pid)[0].retrieval == "needs_review"
    assert app.store.work_files.list_for_work(pid, refs(app, pid)[0].work_id)[0].state == "needs_review"


def test_r10_concurrent_edit_rejects_stale_resolution_without_partial_candidates(app, monkeypatch):
    from refcollector.contracts.ports import StepContext
    from refcollector.errors import RevisionConflict

    pid, sid = create(app)
    run(app, pid, sid, stages=("extract",))
    job = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["resolve"],
            scope=api.SourceScope(source_ids=[sid]),
            idempotency_key="race-job1",
        ),
    )
    stored = app.executor.queue.get_job(pid, job.job_id)
    with app.store.transaction(immediate=True):
        app.executor.queue.add_steps(
            stored, app.executor.planner.plan(app.store, stored, StepType.RESOLVE), max_attempts=1
        )
    step = app.executor.queue.steps(job.job_id)[0]
    ctx = StepContext(step, stored, app.store, lambda: False)
    result = asyncio.run(app.executor.handlers[StepType.RESOLVE].run(ctx))
    occ = refs(app, pid)[0]
    change(
        app,
        pid,
        api.EditText(
            type="edit_text",
            occurrence_id=occ.occurrence_id,
            text="New user citation",
            expected_revision=occ.revision,
        ),
    )
    with pytest.raises(RevisionConflict), app.store.transaction(immediate=True):
        result.commit(app.store)
    assert app.store.candidates.list_for_occurrence(pid, occ.occurrence_id) == []
    assert refs(app, pid)[0].text == "New user citation"
    asyncio.run(app.executor.providers.close())


def test_r10_commit_failure_rolls_back_entities_and_step(app, monkeypatch):
    pid, sid = create(app)
    run(app, pid, sid, stages=("extract",))
    revision = app.store.projects.get(pid).revision
    original = app.store.audit.append

    def broken(event):
        if event.type == "CANDIDATES_UPDATED":
            raise RuntimeError("injected audit failure")
        return original(event)

    monkeypatch.setattr(app.store.audit, "append", broken)
    result = run(app, pid, sid, stages=("resolve",))
    assert result.state == "failed"
    occ = refs(app, pid)[0]
    assert occ.identity == "unprocessed" and occ.work_id is None
    assert not app.store.candidates.list_for_occurrence(pid, occ.occurrence_id)
    assert app.store.projects.get(pid).revision == revision
    assert (
        app.executor.queue.steps(result.job_id)[0].last_error["message"]
        == "RuntimeError: injected audit failure"
    )


def test_r11_stale_revision_before_export_publication(app):
    pid, sid = create(app)
    run(app, pid, sid)
    revision = app.store.projects.get(pid).revision
    job = app.service.create_export(
        context(),
        api.CreateExportIn(
            project_id=pid, project_revision=revision, formats=["json"], idempotency_key="stale-export"
        ),
    )
    occ = refs(app, pid)[0]
    change(
        app,
        pid,
        api.EditText(
            type="edit_text",
            occurrence_id=occ.occurrence_id,
            text=TEXT + " Changed",
            expected_revision=occ.revision,
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    assert app.executor.queue.steps(job.job_id)[0].last_error["code"] == "REVISION_NOT_CURRENT"
    assert not app.store.artifacts.list(pid).items


def test_r04_explicit_scope_cap_and_project_isolation(app):
    pid, sid = create(app, TEXT + "\n\n" + TEXT.replace("waves", "waves2"))
    run(app, pid, sid, stages=("extract",))
    result = run(app, pid, sid, stages=("resolve",), cap=1)
    assert len(app.executor.queue.steps(result.job_id)) == 1
    other = app.service.create_project(
        context(), api.CreateProjectIn(name="other", idempotency_key="other-project")
    ).project_id
    from refcollector.errors import NotFound

    with pytest.raises(NotFound):
        app.service.start_job(
            context(),
            api.StartJobIn(
                project_id=other,
                stages=["extract"],
                scope=api.SourceScope(source_ids=[sid]),
                idempotency_key="cross-project",
            ),
        )


def test_r08_discovery_refresh_retains_history_and_partial_evidence(app, offline):
    pid, sid = create(app)
    run(app, pid, sid)
    first = refs(app, pid)[0]
    offline.failure = True
    app.db.execute("DELETE FROM response_cache")
    result = run(app, pid, sid, stages=("discover_access",), refresh=True)
    assert result.state == "completed_with_warnings"
    checks = app.store.access_checks.history(pid, first.work_id)
    assert len(checks) == 2 and sum(c.current for c in checks) == 1
    current = next(c for c in checks if c.current)
    assert current.sources_failed and current.classification == "unknown"
    assert refs(app, pid)[0].identity == first.identity


def test_r05_output_reuse_links_subjects_and_edit_changes_fingerprint(app):
    pid, sid = create(app)
    first = run(app, pid, sid, stages=("extract",))
    second = run(app, pid, sid, stages=("extract",))
    third = run(app, pid, sid, stages=("extract",))
    assert (
        first.results.occurrence_count
        == second.results.occurrence_count
        == third.results.occurrence_count
        == 1
    )
    step = app.executor.queue.steps(third.job_id)[0]
    assert step.state == "skipped" and step.skip_reason == "OUTPUT_REUSED"
    old_key = step.input_fingerprint
    occ = next(o for o in refs(app, pid) if o.extraction != "excluded")
    change(
        app,
        pid,
        api.EditText(
            type="edit_text",
            occurrence_id=occ.occurrence_id,
            text=TEXT + " Manual correction",
            expected_revision=occ.revision,
        ),
    )
    fourth = run(app, pid, sid, stages=("extract",))
    assert app.executor.queue.steps(fourth.job_id)[0].input_fingerprint != old_key
    assert app.store.occurrences.get(pid, occ.occurrence_id).text.endswith("Manual correction")


def test_r04_repeated_resolve_links_already_completed_subjects(app):
    pid, sid = create(app)
    run(app, pid, sid)
    result = run(app, pid, sid, stages=("resolve",))
    assert result.results.occurrence_count == 1
    assert app.executor.queue.steps(result.job_id) == []


def test_r06_extraction_cap_is_job_wide(app):
    pid, sid = create(app, TEXT + "\n\n" + TEXT.replace("waves", "waves2"))
    result = run(app, pid, sid, stages=("extract",), cap=1)
    assert result.results.occurrence_count == 1


def test_r07_approval_refresh_stales_approval_but_keeps_original_decision(app):
    from refcollector.contracts.api import CallContext
    from refcollector.domain.enums import Channel, Scope

    pid, sid = create(app)
    run(app, pid, sid)
    occ = refs(app, pid)[0]
    tty = CallContext(
        principal="user",
        channel=Channel.CLI_TTY_REVIEW,
        scopes=frozenset(Scope),
        request_id="approval",
        tty=True,
    )
    app.service.review_decision(
        tty,
        api.ReviewDecisionIn(
            project_id=pid,
            occurrence_id=occ.occurrence_id,
            action="accept",
            candidate_id=occ.accepted_candidate_id,
            expected_revision=occ.revision,
            idempotency_key="approval-user",
        ),
    )
    result = run(app, pid, sid, stages=("resolve",), refresh=True)
    assert result.state == "completed"
    occ = refs(app, pid)[0]
    assert occ.identity == "needs_review" and "APPROVAL_STALE" in occ.identity_reasons
    assert occ.approval is None
    assert any(d.approval for d in app.store.decisions.list_for_occurrence(pid, occ.occurrence_id))


def test_r07_rejected_candidate_survives_refresh(app):
    pid, sid = create(app)
    run(app, pid, sid)
    occ = refs(app, pid)[0]
    rejected_id = occ.accepted_candidate_id
    change(
        app,
        pid,
        api.RejectCandidate(
            type="reject_candidate",
            occurrence_id=occ.occurrence_id,
            candidate_id=rejected_id,
            expected_revision=occ.revision,
        ),
    )
    run(app, pid, sid, stages=("resolve",), refresh=True)
    assert refs(app, pid)[0].identity not in ("accepted_auto", "accepted_user")
    assert app.store.candidates.get(pid, rejected_id).rejected


def test_r09_attached_usable_copy_no_network(app, offline, tmp_path):
    from tests.fixtures.pdfs import paper_pdf

    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    path = tmp_path / "attached.pdf"
    path.write_bytes(paper_pdf(TITLE + "\nVersion of Record", [], "10.1234/waves"))
    oid = refs(app, pid)[0].occurrence_id
    app.service.attach_file(
        context(),
        api.AttachFileIn(
            project_id=pid,
            occurrence_id=oid,
            path=str(path),
            version_hint="published",
            idempotency_key="attach-file",
        ),
    )
    before = len(offline.clients)
    result = download(app, pid, [oid])
    assert result.state == "completed"
    assert refs(app, pid)[0].retrieval == "satisfied_by_local_copy"
    assert len(offline.clients) == before and not offline.downloads


def test_r09_failed_attachment_retained_then_download_proceeds(app, offline, tmp_path):
    from tests.fixtures.pdfs import paper_pdf

    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    path = tmp_path / "wrong.pdf"
    path.write_bytes(paper_pdf("Unrelated work", [], "10.4321/other"))
    oid = refs(app, pid)[0].occurrence_id
    attached = app.service.attach_file(
        context(),
        api.AttachFileIn(
            project_id=pid,
            occurrence_id=oid,
            path=str(path),
            version_hint="published",
            idempotency_key="wrong-attach",
        ),
    )
    assert attached.local_copy == "unusable"
    result = download(app, pid, [oid])
    assert result.state == "completed" and offline.downloads
    copies = app.store.local_copies.list_for_work(pid, refs(app, pid)[0].work_id)
    assert any(c.failed_check == "identity" for c in copies)


def test_r10_at30_cancel_before_commit_keeps_completed_files_and_no_new_grants(app, offline, monkeypatch):
    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    oid = refs(app, pid)[0].occurrence_id
    download(app, pid, [oid])
    work_id = refs(app, pid)[0].work_id
    existing = app.store.work_files.list_for_work(pid, work_id)
    grants = app.db.query("SELECT * FROM file_grant")
    original = app.executor._succeed

    def cancel(step, result):
        app.executor.queue.request(pid, step.job_id, "cancel")
        original(step, result)

    monkeypatch.setattr(app.executor, "_succeed", cancel)
    result = download(app, pid, [oid])
    assert result.state == "cancelled"
    assert app.store.work_files.list_for_work(pid, work_id) == existing
    assert len(app.db.query("SELECT * FROM file_grant")) == len(grants)
    assert list(app.store.files.tmp.iterdir()) == []


def test_r09_budget_max_files_enforced_across_work_steps(app, offline):
    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    offline.doi = "10.1234/other"
    other = source(app, pid, TEXT.replace("doi:10.1234/waves", "doi:10.1234/other"))
    run(app, pid, other)
    result = download(app, pid, [o.occurrence_id for o in refs(app, pid)], api.DownloadBudget(max_files=1))
    assert result.state == "completed_with_warnings"
    assert len(offline.downloads) == 1
    assert any(
        s.last_error and s.last_error["code"] == "BUDGET_EXCEEDED"
        for s in app.executor.queue.steps(result.job_id)
    )


def test_r03_resume_uses_snapshot_not_current_email_allowlist_or_network(app, offline):
    pid, sid = create(app)
    run(app, pid, sid, stages=("extract",))
    started = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["resolve", "discover_access"],
            scope=api.SourceScope(source_ids=[sid]),
            idempotency_key="snapshot-job",
        ),
    )
    app.settings.email = "changed@example.org"
    app.settings.enabled_providers = []
    project = app.store.projects.get(pid)
    project.network_mode = "offline"
    app.store.projects.update(project)
    asyncio.run(app.executor.run_until_idle())
    assert app.executor.queue.get_job(pid, started.job_id).state == "completed"
    assert len(offline.clients) == 1
    assert offline.snapshots[-1].contact_email == "test@example.org"
    assert offline.snapshots[-1].network_mode == "metadata"
    assert offline.snapshots[-1].provider_allowlist == ["crossref", "unpaywall"]


def test_r06_pdf_full_flow_preserves_spans(app, tmp_path):
    from tests.fixtures.pdfs import thesis_pdf

    pid, _ = create(app)
    path = tmp_path / "source.pdf"
    path.write_bytes(thesis_pdf([TEXT], body_pages=1))
    sid = app.service.import_source(
        context(),
        api.ImportSourceIn(
            project_id=pid,
            input=api.LocalFileInput(type="local_file", path=str(path)),
            idempotency_key="pdf-import",
        ),
    ).source_id
    result = run(app, pid, sid)
    assert result.state == "completed" and result.results.occurrence_count == 1
    row = next(o for o in refs(app, pid) if o.source_id == sid)
    assert row.spans and row.spans[0].page_label
    assert row.identity == "accepted_auto"


def test_r11_revision_change_after_render_rejects_all_artifacts(app):
    from refcollector.contracts.ports import StepContext
    from refcollector.errors import RevisionNotCurrent

    pid, sid = create(app)
    run(app, pid, sid)
    revision = app.store.projects.get(pid).revision
    out = app.service.create_export(
        context(),
        api.CreateExportIn(
            project_id=pid, project_revision=revision, formats=["json"], idempotency_key="render-race"
        ),
    )
    job = app.executor.queue.get_job(pid, out.job_id)
    with app.store.transaction(immediate=True):
        steps = app.executor.queue.add_steps(
            job, app.executor.planner.plan(app.store, job, StepType.EXPORT), max_attempts=1
        )
    result = asyncio.run(
        app.executor.handlers[StepType.EXPORT].run(StepContext(steps[0], job, app.store, lambda: False))
    )
    row = refs(app, pid)[0]
    change(
        app,
        pid,
        api.EditText(
            type="edit_text",
            occurrence_id=row.occurrence_id,
            text=TEXT + " Edit",
            expected_revision=row.revision,
        ),
    )
    with pytest.raises(RevisionNotCurrent), app.store.transaction(immediate=True):
        result.commit(app.store)
    assert app.store.artifacts.list(pid).items == []


def test_r02_background_executor_ownership_and_shutdown_before_database_close(app):
    from refcollector.config import Limits

    app.executor.limits = Limits(executor_poll_s=0.01, client_lock_poll_s=0.01)
    second = open_app(app.settings)
    second.executor.limits = app.executor.limits
    try:
        assert app.start_executor()
        assert app.start_executor()
        assert not second.start_executor()
        assert app.executor.is_owner and not second.executor.is_owner
        thread = app.executor._thread
        app.close()
        assert not thread.is_alive()
        second.close()
        assert not app.start_executor()
    finally:
        second.close()


def test_r01_initialization_failure_closes_database(tmp_path, monkeypatch):
    import refcollector.bootstrap as root
    from refcollector.config import Settings
    from refcollector.storage.db import Database

    databases = []

    class Tracked(Database):
        def __init__(self, path):
            super().__init__(path)
            databases.append(self)

    def fail(*args, **kwargs):
        raise RuntimeError("service initialization failed")

    monkeypatch.setattr(root, "Database", Tracked)
    monkeypatch.setattr(root, "RefCollectorServiceImpl", fail)
    with pytest.raises(RuntimeError, match="service initialization failed"):
        root.open_app(Settings(rc_home=tmp_path))
    assert databases[0]._all == []


def test_r02_stale_temp_namespace_removed_only_after_lease_is_free(app):
    from refcollector.pipeline.files import SessionFileStore

    orphan = SessionFileStore(app.db, app.settings)
    stale = orphan.new_tmp_path(".pdf")
    stale.write_bytes(b"interrupted")
    app.executor.try_acquire()
    assert stale.exists()
    orphan.close_lease()
    app.store.files.cleanup_abandoned()
    assert not stale.exists()


def test_r07_shared_work_refresh_invalidates_other_user_approval(app, offline):
    from refcollector.contracts.api import CallContext
    from refcollector.domain.enums import Channel, Scope

    pid, sid = create(app)
    run(app, pid, sid)
    second = source(app, pid, TEXT + " ")
    run(app, pid, second)
    first, other = refs(app, pid)
    tty = CallContext(
        principal="user",
        channel=Channel.CLI_TTY_REVIEW,
        scopes=frozenset(Scope),
        request_id="approval",
        tty=True,
    )
    app.service.review_decision(
        tty,
        api.ReviewDecisionIn(
            project_id=pid,
            occurrence_id=other.occurrence_id,
            action="accept",
            candidate_id=other.accepted_candidate_id,
            expected_revision=other.revision,
            idempotency_key="approval-shared",
        ),
    )
    offline.publisher = "New publisher"
    app.db.execute("DELETE FROM response_cache")
    run(app, pid, ids=[first.occurrence_id], stages=("resolve",), refresh=True)
    changed = app.store.occurrences.get(pid, other.occurrence_id)
    assert changed.identity == "needs_review" and changed.approval is None
    assert "APPROVAL_STALE" in changed.identity_reasons


def test_r09_attached_recheck_does_not_publish_before_executor_commit(app, tmp_path):
    from refcollector.contracts.ports import StepContext
    from tests.fixtures.pdfs import paper_pdf

    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    row = refs(app, pid)[0]
    path = tmp_path / "attached-for-recheck.pdf"
    path.write_bytes(paper_pdf(TITLE + "\nVersion of Record", [], "10.1234/waves"))
    app.service.attach_file(
        context(),
        api.AttachFileIn(
            project_id=pid,
            occurrence_id=row.occurrence_id,
            path=str(path),
            version_hint="published",
            idempotency_key="recheck-attach",
        ),
    )
    out = app.service.download_files(
        context(),
        api.DownloadFilesIn(
            project_id=pid,
            scope=api.OccurrenceScope(occurrence_ids=[row.occurrence_id]),
            idempotency_key="recheck-download",
        ),
    )
    job = app.executor.queue.get_job(pid, out.job_id)
    with app.store.transaction(immediate=True):
        steps = app.executor.queue.add_steps(
            job, app.executor.planner.plan(app.store, job, StepType.RETRIEVE), max_attempts=1
        )
    before = app.db.conn.total_changes
    handler = app.executor.handlers[StepType.RETRIEVE]
    asyncio.run(handler.run(StepContext(steps[0], job, app.store, lambda: False)))
    assert app.db.conn.total_changes == before
    handler.cleanup(steps[0].step_id)


def test_r07_provider_evidence_records_persist_with_candidates(app):
    pid, sid = create(app)
    run(app, pid, sid)
    candidate = app.store.candidates.list_for_occurrence(pid, refs(app, pid)[0].occurrence_id)[0]
    assert candidate.features and candidate.evidence
    evidence = candidate.evidence[0]
    record = app.store.provider_records.get(evidence.provider, evidence.record_key)
    assert record is not None and record.metadata.doi == candidate.metadata.doi


def test_r03_synchronous_identity_factory_closes_each_loop_client(app, offline):
    for _ in range(2):
        result = app.service.resolve_citation(context(), api.ResolveCitationIn(citation=TEXT))
        assert result.decision_hint == "strong"
    assert len(offline.clients) == 2
    assert all(c._client.is_closed for c in offline.clients)


def test_r04_cap_does_not_expand_to_other_eligible_occurrences(app):
    pid, sid = create(app)
    run(app, pid, sid)
    app.service.add_reference(
        context(),
        api.AddReferenceIn(project_id=pid, source_id=sid, text=TEXT, idempotency_key="cap-new-reference"),
    )
    result = run(app, pid, sid, stages=("resolve",), cap=1)
    assert result.results.occurrence_count == 1
    assert app.executor.queue.steps(result.job_id) == []


def test_r13_job_outputs_audit_revision_and_provenance(app):
    pid, sid = create(app)
    result = run(app, pid, sid)
    assert result.results.occurrence_count == 1
    events = [e for e in app.store.audit.list(pid) if e.payload.get("job_id") == result.job_id]
    assert {e.type for e in events} >= {"OCCURRENCES_EXTRACTED", "CANDIDATES_UPDATED", "ACCESS_CHECKED"}
    assert all(e.provenance.principal == "test" for e in events)
    assert app.store.projects.get(pid).revision == max(e.project_revision for e in events)
    assert refs(app, pid)[0].revision >= 3


def test_r11_export_includes_granted_verified_pdf_and_no_review_pdf(app, offline):
    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    download(app, pid, [refs(app, pid)[0].occurrence_id])
    revision = app.store.projects.get(pid).revision
    out = app.service.create_export(
        context(),
        api.CreateExportIn(
            project_id=pid, project_revision=revision, formats=["json"], idempotency_key="pdf-export"
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    artifacts = [a for a in app.store.artifacts.list(pid).items if a.job_id == out.job_id]
    pdfs = [a for a in artifacts if a.media_type == "application/pdf"]
    assert len(pdfs) == 1
    assert app.store.files.path_for(pid, pdfs[0].sha256).read_bytes() == offline.body


def test_r10_retrieval_rollback_retains_only_orphan_bytes(app, offline, monkeypatch):
    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    row = refs(app, pid)[0]
    original = app.store.audit.append

    def fail(event):
        if event.type == "FILE_RETRIEVED":
            raise RuntimeError("retrieval audit failed")
        return original(event)

    monkeypatch.setattr(app.store.audit, "append", fail)
    result = download(app, pid, [row.occurrence_id])
    assert result.state == "failed"
    sha = hashlib.sha256(offline.body).hexdigest()
    assert not app.store.files.has_grant(pid, sha)
    assert app.store.work_files.list_for_work(pid, row.work_id) == []
    assert app.store.local_copies.list_for_work(pid, row.work_id) == []
    assert refs(app, pid)[0].retrieval == row.retrieval
    assert app.db.query_one("SELECT 1 FROM file_blob WHERE sha256 = ?", (sha,)) is None
    assert (app.settings.store_dir / sha[:2] / sha).is_file()
    assert app.store.files.index_orphans() >= 1
    assert not app.store.files.has_grant(pid, sha)


def test_r06_w06_pagination_returns_every_job_occurrence(app):
    pid, sid = create(app, "\n\n".join(TEXT.replace("waves", f"waves{i}") for i in range(32)))
    result = run(app, pid, sid, stages=("extract",))
    cursor, ids = None, []
    while True:
        page = app.service.list_references(
            context(),
            api.ListReferencesIn(
                project_id=pid, filter=api.ReferenceFilter(job_id=result.job_id), limit=7, cursor=cursor
            ),
        )
        ids.extend(o["occurrence_id"] for o in page.occurrences)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert result.results.occurrence_count == len(ids) == len(set(ids)) == 32


def test_r03_unsupported_snapshot_policy_fails_closed_without_client(app, offline):
    pid, sid = create(app)
    run(app, pid, sid, stages=("extract",))
    started = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["resolve"],
            scope=api.SourceScope(source_ids=[sid]),
            idempotency_key="bad-policy-snapshot",
        ),
    )
    job = app.executor.queue.get_job(pid, started.job_id)
    job.snapshot.identity_policy_version = "unavailable-policy"
    app.executor.queue.put_job(job)
    asyncio.run(app.executor.run_until_idle())
    assert app.executor.queue.steps(job.job_id)[0].last_error["code"] == "INTEGRATION_UNAVAILABLE"
    assert offline.clients == []


def test_r10_cancellation_at_download_io_boundary_discards_partial_output(app, offline):
    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    oid = refs(app, pid)[0].occurrence_id

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait():
            entered.set()
            await release.wait()

        offline.download_wait = wait
        out = app.service.download_files(
            context(),
            api.DownloadFilesIn(
                project_id=pid,
                scope=api.OccurrenceScope(occurrence_ids=[oid]),
                idempotency_key="cancel-io-boundary",
            ),
        )
        task = asyncio.create_task(app.executor.run_until_idle())
        await asyncio.wait_for(entered.wait(), 5)
        app.executor.queue.request(pid, out.job_id, "cancel")
        release.set()
        await task
        assert app.executor.queue.get_job(pid, out.job_id).state == "cancelled"

    asyncio.run(exercise())
    assert not app.store.work_files.list_for_work(pid, refs(app, pid)[0].work_id)
    assert list(app.store.files.tmp.iterdir()) == []


def test_r07_automated_unresolved_does_not_block_later_refresh(app):
    pid, sid = create(app)
    app.settings.enabled_providers = []
    run(app, pid, sid, stages=("extract", "resolve"))
    assert refs(app, pid)[0].identity == "unresolved"
    app.settings.enabled_providers = ["crossref", "unpaywall"]
    run(app, pid, sid, stages=("resolve",), refresh=True)
    assert refs(app, pid)[0].identity == "accepted_auto"


def test_r03_provider_pool_rejects_cross_loop_client_reuse(app):
    from refcollector.errors import IntegrationUnavailable

    pid, sid = create(app)
    out = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["extract"],
            scope=api.SourceScope(source_ids=[sid]),
            idempotency_key="loop-bound-job",
        ),
    )
    job = app.executor.queue.get_job(pid, out.job_id)
    factory = app.executor.providers

    async def use():
        async with factory.job(job):
            pass

    with asyncio.Runner() as runner:
        runner.run(use())
        try:
            with pytest.raises(IntegrationUnavailable, match="event loop"):
                asyncio.run(use())
        finally:
            runner.run(factory.close())


def test_r03_job_clients_close_when_job_finishes_without_executor_shutdown(app, offline):
    pid, sid = create(app)
    out = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["extract", "resolve", "discover_access"],
            scope=api.SourceScope(source_ids=[sid]),
            idempotency_key="close-finished-job",
        ),
    )

    async def exercise():
        app.executor.try_acquire()
        for _ in range(500):
            app.executor._cycle()
            if app.executor.queue.get_job(pid, out.job_id).state == "completed":
                break
            await asyncio.sleep(0.001)
        assert app.executor.queue.get_job(pid, out.job_id).state == "completed"
        try:
            assert all(c._client.is_closed for c in offline.clients)
        finally:
            await app.executor.providers.close()

    asyncio.run(exercise())


def test_r02_start_failure_releases_lock_and_closes_resources(app, monkeypatch):
    def fail():
        raise OSError("orphan index unavailable")

    monkeypatch.setattr(app.store.files, "index_orphans", fail)
    with pytest.raises(OSError, match="orphan index unavailable"):
        app.start_executor()
    assert not app.executor.is_owner
    assert app.db._all == []
    assert not app.start_executor()


def test_r06_multisource_cap_does_not_mark_unprocessed_source_reusable(app):
    pid, first = create(app)
    second = source(app, pid, TEXT + " ")
    out = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["extract"],
            scope=api.SourceScope(source_ids=[first, second]),
            options=api.JobOptions(max_occurrences=1),
            idempotency_key="multi-source-cap",
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    assert (
        app.service.get_job(
            context(), api.GetJobIn(project_id=pid, job_id=out.job_id)
        ).results.occurrence_count
        == 1
    )
    result = run(app, pid, second, stages=("extract",), cap=1)
    assert result.results.occurrence_count == 1


def test_r11_export_complete_audit_includes_excluded_and_unresolved(app):
    pid, sid = create(app, TEXT + "\n\nUnknown author (1900). Unresolved research.")
    run(app, pid, sid, stages=("extract",))
    row = refs(app, pid)[0]
    change(
        app, pid, api.Exclude(type="exclude", occurrence_id=row.occurrence_id, expected_revision=row.revision)
    )
    revision = app.store.projects.get(pid).revision
    out = app.service.create_export(
        context(),
        api.CreateExportIn(
            project_id=pid,
            project_revision=revision,
            formats=["json"],
            selection="complete_audit",
            idempotency_key="complete-audit-rows",
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    artifact = next(
        a
        for a in app.store.artifacts.list(pid).items
        if a.job_id == out.job_id and a.name == "references.json"
    )
    raw = app.store.files.path_for(pid, artifact.sha256).read_bytes()
    payload = json.loads(raw)
    assert len(payload) == 2
    assert b"excluded" in raw and b"unprocessed" in raw


def test_r12_excluded_reference_cannot_create_empty_draft_artifact(app):
    pid, sid = create(app)
    run(app, pid, sid)
    row = refs(app, pid)[0]
    change(
        app, pid, api.Exclude(type="exclude", occurrence_id=row.occurrence_id, expected_revision=row.revision)
    )
    out = app.service.draft_requests(
        context(),
        api.DraftRequestsIn(
            project_id=pid,
            scope=api.OccurrenceScope(occurrence_ids=[row.occurrence_id]),
            sender=api.SenderIn(name="Researcher"),
            language="en",
            idempotency_key="excluded-draft",
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    assert app.executor.queue.get_job(pid, out.job_id).state == "failed"
    assert app.store.artifacts.list(pid).items == []


def test_r07_explicit_acceptance_refresh_preserves_decision_and_updates_metadata(app, offline):
    pid, sid = create(app)
    run(app, pid, sid)
    row = refs(app, pid)[0]
    change(
        app,
        pid,
        api.SelectCandidate(
            type="select_candidate",
            occurrence_id=row.occurrence_id,
            candidate_id=row.accepted_candidate_id,
            expected_revision=row.revision,
        ),
    )
    before = app.store.decisions.list_for_occurrence(pid, row.occurrence_id)
    offline.publisher = "Updated publisher"
    app.db.execute("DELETE FROM response_cache")
    run(app, pid, sid, stages=("resolve",), refresh=True)
    after = app.store.occurrences.get(pid, row.occurrence_id)
    assert after.identity == "accepted_agent" and after.accepted_candidate_id == row.accepted_candidate_id
    assert app.store.decisions.list_for_occurrence(pid, row.occurrence_id) == before
    assert app.store.works.get(pid, after.work_id).metadata.publisher == "Updated publisher"


def test_r07_refresh_to_different_work_never_rewrites_shared_original(app, offline):
    pid, sid = create(app)
    run(app, pid, sid)
    other_source = source(app, pid, TEXT + " ")
    run(app, pid, other_source)
    first, other = refs(app, pid)
    original = app.store.works.get(pid, first.work_id)
    change(
        app,
        pid,
        api.EditText(
            type="edit_text",
            occurrence_id=first.occurrence_id,
            text=TEXT.replace("10.1234/waves", "10.1234/different"),
            expected_revision=first.revision,
        ),
    )
    offline.doi = "10.1234/different"
    app.db.execute("DELETE FROM response_cache")
    run(app, pid, ids=[first.occurrence_id], stages=("resolve",), refresh=True)
    updated = app.store.occurrences.get(pid, first.occurrence_id)
    assert updated.work_id != other.work_id
    assert app.store.works.get(pid, original.work_id) == original
    assert app.store.occurrences.get(pid, other.occurrence_id).work_id == original.work_id
    assert app.store.works.get(pid, updated.work_id).metadata.doi == "10.1234/different"


def test_r03_empty_final_stage_closes_background_job_clients(app, offline):
    pid, sid = create(app, "Unrelated author (1900). Missing research title.")
    out = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["extract", "resolve", "discover_access"],
            scope=api.SourceScope(source_ids=[sid]),
            idempotency_key="empty-final-stage",
        ),
    )

    async def exercise():
        app.executor.try_acquire()
        for _ in range(500):
            app.executor._cycle()
            if app.executor.queue.get_job(pid, out.job_id).state == "completed":
                break
            await asyncio.sleep(0.001)
        if app.executor._tasks:
            await asyncio.gather(*app.executor._tasks)
        try:
            assert app.executor.queue.get_job(pid, out.job_id).state == "completed"
            assert offline.clients and all(c._client.is_closed for c in offline.clients)
        finally:
            await app.executor.providers.close()

    asyncio.run(exercise())


def test_r07_changed_or_cleared_work_resets_downstream_occurrence_states(app, offline):
    pid, sid = create(app)
    run(app, pid, sid)
    row = refs(app, pid)[0]
    assert row.discovery == "complete" and row.classification == "none_found"
    change(
        app,
        pid,
        api.EditText(
            type="edit_text",
            occurrence_id=row.occurrence_id,
            text=TEXT.replace("10.1234/waves", "10.1234/new"),
            expected_revision=row.revision,
        ),
    )
    offline.doi = "10.1234/new"
    app.db.execute("DELETE FROM response_cache")
    run(app, pid, sid, stages=("resolve",), refresh=True)
    changed = refs(app, pid)[0]
    assert changed.work_id != row.work_id
    assert changed.discovery == "not_checked" and changed.classification is None
    assert changed.access_checked_at is None and changed.local_copy == "none"
    assert changed.retrieval == "not_requested"
    app.settings.enabled_providers = []
    run(app, pid, sid, stages=("resolve",), refresh=True)
    cleared = refs(app, pid)[0]
    assert cleared.work_id is None and cleared.discovery == "not_checked" and cleared.classification is None


def test_r03_cancelled_client_cleanup_drains_before_executor_can_close_database(app):
    pid, sid = create(app)
    out = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["extract"],
            scope=api.SourceScope(source_ids=[sid]),
            idempotency_key="cancel-client-close",
        ),
    )
    job = app.executor.queue.get_job(pid, out.job_id)

    async def exercise():
        factory = app.executor.providers
        async with factory.job(job) as (http, _identity, _access):
            entered, release = asyncio.Event(), asyncio.Event()
            original = http.aclose

            async def close():
                entered.set()
                await release.wait()
                await original()

            http.aclose = close
        task = asyncio.create_task(factory.close_job(job.job_id))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        try:
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await original()
        assert http._client.is_closed

    asyncio.run(exercise())
