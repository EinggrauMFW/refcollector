"""Jobs, downloads, exports, drafts (spec 11.4 tools 9-11, 18-20; AT-40, W-26)."""

from __future__ import annotations

import pytest

from refcollector.contracts.api import (
    ControlJobIn,
    CreateExportIn,
    DownloadFilesIn,
    DraftRequestsIn,
    GetJobIn,
    ListReferencesIn,
    StartJobIn,
    UpdateProjectSettingsIn,
)
from refcollector.domain.enums import (
    AccessClassification,
    DiscoveryState,
    ExtractionState,
    IdentityState,
    JobState,
    NetworkMode,
    Scope,
    StepType,
    VersionPolicy,
)
from refcollector.domain.models import Job, ProjectWork, WorkMetadata
from refcollector.errors import (
    ConfigMissingEmail,
    InvalidState,
    NetworkModeDenied,
    NotAuthorized,
    NotFound,
    PrerequisiteNotMet,
    RevisionNotCurrent,
)
from refcollector.ids import new_id
from tests.unit.services.conftest import key


def start(env, pid, stages, scope, ctx=None, **kw):
    req = StartJobIn(project_id=pid, stages=stages, scope=scope, idempotency_key=key(), **kw)
    return env.svc.start_job(ctx or env.ctx, req)


def accepted(env, pid, sid, **kw):
    occ = env.occ(pid, sid, identity=IdentityState.ACCEPTED_AGENT, **kw)
    cand = env.candidate(pid, occ.occurrence_id)
    w = env.store.works.add(
        ProjectWork(
            work_id=new_id("work"), project_id=pid, metadata=WorkMetadata(doi=f"10.1/{occ.occurrence_id}")
        )
    )
    occ.work_id, occ.accepted_candidate_id = w.work_id, cand.candidate_id
    return env.store.occurrences.update(occ)


def test_start_extract_job(env):
    pid = env.project()
    sid = env.text_source(pid)
    out = start(env, pid, ["extract"], {"source_ids": [sid]})
    assert out.state == "queued" and out.steps_planned == 1 and out.next_poll_after_ms > 0
    job = env.queue.get_job(pid, out.job_id)
    assert job.stages == [StepType.EXTRACT]
    assert job.scope == {"source_ids": [sid]}
    assert job.snapshot.contact_email == env.settings.email
    assert job.snapshot.network_mode == NetworkMode.METADATA
    assert job.provenance.origin.value == "agent"
    assert job.snapshot_hash


def test_start_job_extract_needs_source_ids(env):
    pid = env.project()
    sid = env.text_source(pid)
    occ = env.occ(pid, sid)
    with pytest.raises(PrerequisiteNotMet):
        start(env, pid, ["extract"], {"occurrence_ids": [occ.occurrence_id]})


def test_start_resolve_needs_extracted_occurrences(env):
    pid = env.project()
    sid = env.text_source(pid)
    with pytest.raises(PrerequisiteNotMet):
        start(env, pid, ["resolve"], {"source_ids": [sid]})
    # extract + resolve in one job is fine: occurrences appear when the stage runs (D-22)
    out = start(env, pid, ["extract", "resolve"], {"source_ids": [sid]})
    assert out.steps_planned == 1  # first stage subjects
    env.occ(pid, sid)
    env.occ(pid, sid)
    env.occ(pid, sid, extraction=ExtractionState.EXCLUDED)
    out = start(env, pid, ["resolve"], {"source_ids": [sid]})
    assert out.steps_planned == 2


def test_start_discover_needs_accepted_identities(env):
    pid = env.project()
    sid = env.text_source(pid)
    occ = env.occ(pid, sid)
    with pytest.raises(PrerequisiteNotMet):
        start(env, pid, ["discover_access"], {"occurrence_ids": [occ.occurrence_id]})
    acc = accepted(env, pid, sid)
    out = start(env, pid, ["discover_access"], {"occurrence_ids": [acc.occurrence_id]})
    assert out.steps_planned == 1
    # occurrence-scope jobs are linked to their occurrences
    job = env.queue.get_job(pid, out.job_id)
    assert job.scope == {"occurrence_ids": [acc.occurrence_id]}
    got = env.svc.get_job(env.ctx, GetJobIn(project_id=pid, job_id=out.job_id))
    assert got.results.occurrence_count == 1


def test_start_job_validations(env):
    pid = env.project()
    sid = env.text_source(pid)
    env.occ(pid, sid)
    from refcollector.errors import InvalidInput

    with pytest.raises(InvalidInput):  # wrong order
        start(env, pid, ["resolve", "extract"], {"source_ids": [sid]})
    with pytest.raises(NotFound):
        start(env, pid, ["extract"], {"source_ids": ["src_missing"]})
    other = env.project("other")
    with pytest.raises(NotFound):  # another project's source
        start(env, other, ["extract"], {"source_ids": [sid]})
    out = start(env, pid, ["resolve"], {"source_ids": [sid]}, options={"max_occurrences": 1})
    assert out.steps_planned == 1


def test_capped_explicit_scope_reports_only_selected_occurrences(env):
    pid = env.project()
    sid = env.text_source(pid)
    rows = [env.occ(pid, sid), env.occ(pid, sid)]
    out = start(
        env,
        pid,
        ["resolve"],
        {"occurrence_ids": [row.occurrence_id for row in rows]},
        options={"max_occurrences": 1},
    )
    got = env.svc.get_job(env.ctx, GetJobIn(project_id=pid, job_id=out.job_id))
    assert out.steps_planned == 1
    assert got.results.occurrence_count == 1
    listed = env.svc.list_references(env.ctx, ListReferencesIn(project_id=pid, filter={"job_id": out.job_id}))
    assert [row["occurrence_id"] for row in listed.occurrences] == [rows[0].occurrence_id]


def test_start_job_scope_per_stage(env):
    pid = env.project()
    sid = env.text_source(pid)
    env.occ(pid, sid)
    only_extract = env.ctx_for(Scope.REFERENCES_WRITE)
    start(env, pid, ["extract"], {"source_ids": [sid]}, ctx=only_extract)
    with pytest.raises(NotAuthorized):
        start(env, pid, ["extract", "resolve"], {"source_ids": [sid]}, ctx=only_extract)
    with pytest.raises(NotAuthorized):
        start(env, pid, ["discover_access"], {"source_ids": [sid]}, ctx=only_extract)


def set_mode(env, pid, mode):
    req = UpdateProjectSettingsIn(project_id=pid, network_mode=mode, idempotency_key=key())
    env.svc.update_project_settings(env.cli, req)


def test_offline_denies_network_stages(env):
    pid = env.project()
    sid = env.text_source(pid)
    env.occ(pid, sid)
    set_mode(env, pid, "offline")
    with pytest.raises(NetworkModeDenied):
        start(env, pid, ["resolve"], {"source_ids": [sid]})
    with pytest.raises(NetworkModeDenied):
        start(env, pid, ["extract", "resolve"], {"source_ids": [sid]})
    start(env, pid, ["extract"], {"source_ids": [sid]})  # no network needed


def test_missing_email(env):
    pid = env.project()
    sid = env.text_source(pid)
    env.occ(pid, sid)
    env.svc.settings.email = None
    with pytest.raises(ConfigMissingEmail):
        start(env, pid, ["resolve"], {"source_ids": [sid]})
    start(env, pid, ["extract"], {"source_ids": [sid]})


def test_start_job_idempotent(env):
    pid = env.project()
    sid = env.text_source(pid)
    req = StartJobIn(project_id=pid, stages=["extract"], scope={"source_ids": [sid]}, idempotency_key=key())
    first = env.svc.start_job(env.ctx, req)
    assert env.svc.start_job(env.ctx, req) == first
    assert len(env.queue.list_jobs(pid)) == 1


def test_get_and_control_job(env):
    pid = env.project()
    sid = env.text_source(pid)
    out = start(env, pid, ["extract"], {"source_ids": [sid]})
    got = env.svc.get_job(env.ctx, GetJobIn(project_id=pid, job_id=out.job_id))
    assert got.state == JobState.QUEUED and got.next_poll_after_ms
    assert [s.name for s in got.stages] == ["extract"]
    assert got.results.source_ids == [sid]
    assert got.results.changed_filter == {"job_id": out.job_id}
    with pytest.raises(NotFound):
        env.svc.get_job(env.ctx, GetJobIn(project_id=env.project("o"), job_id=out.job_id))

    ctl = env.svc.control_job(
        env.ctx, ControlJobIn(project_id=pid, job_id=out.job_id, action="pause", idempotency_key=key())
    )
    assert ctl.state == JobState.PAUSED
    with pytest.raises(InvalidState):
        env.svc.control_job(
            env.ctx, ControlJobIn(project_id=pid, job_id=out.job_id, action="pause", idempotency_key=key())
        )
    env.svc.control_job(
        env.ctx, ControlJobIn(project_id=pid, job_id=out.job_id, action="cancel", idempotency_key=key())
    )
    done = env.svc.get_job(env.ctx, GetJobIn(project_id=pid, job_id=out.job_id))
    assert done.state == JobState.CANCELLED and done.next_poll_after_ms is None


def test_job_filter_in_list_references(env):  # W-06
    pid = env.project()
    sid = env.text_source(pid)
    a = accepted(env, pid, sid)
    env.occ(pid, sid)
    out = start(env, pid, ["discover_access"], {"occurrence_ids": [a.occurrence_id]})
    page = env.svc.list_references(env.ctx, ListReferencesIn(project_id=pid, filter={"job_id": out.job_id}))
    assert [o["occurrence_id"] for o in page.occurrences] == [a.occurrence_id]
    with pytest.raises(NotFound):
        env.svc.list_references(env.ctx, ListReferencesIn(project_id=pid, filter={"job_id": "job_missing"}))


# ---------- download_files ----------


def enable_retrieval(env, pid, download=True):
    req = UpdateProjectSettingsIn(
        project_id=pid, network_mode="retrieval", download_enabled=download, idempotency_key=key()
    )
    env.svc.update_project_settings(env.cli, req)


def discovered(env, pid, sid):
    occ = accepted(env, pid, sid)
    occ.discovery = DiscoveryState.COMPLETE
    occ.classification = AccessClassification.ELIGIBLE_FOUND
    return env.store.occurrences.update(occ)


def dl(env, pid, ids, ctx=None, **kw):
    req = DownloadFilesIn(project_id=pid, scope={"occurrence_ids": ids}, idempotency_key=key(), **kw)
    return env.svc.download_files(ctx or env.ctx, req)


def test_download_permission_rules(env):
    pid = env.project()
    sid = env.text_source(pid)
    occ = discovered(env, pid, sid)
    ids = [occ.occurrence_id]
    # the MCP default lacks files:download (11.2)
    mcp_default = env.ctx_for(*(set(Scope) - {Scope.FILES_DOWNLOAD}))
    with pytest.raises(NotAuthorized):
        dl(env, pid, ids, ctx=mcp_default)
    # scope granted but not enabled for the project or globally
    enable_retrieval(env, pid, download=False)
    with pytest.raises(NotAuthorized):
        dl(env, pid, ids)
    # global enable is enough
    env.settings.download_enabled_global = True
    assert dl(env, pid, ids).job_id


def test_download_network_mode_and_prereqs(env):
    pid = env.project()
    sid = env.text_source(pid)
    occ = discovered(env, pid, sid)
    ids = [occ.occurrence_id]
    set_mode(env, pid, "metadata")
    req = UpdateProjectSettingsIn(project_id=pid, download_enabled=True, idempotency_key=key())
    env.svc.update_project_settings(env.cli, req)
    with pytest.raises(NetworkModeDenied):
        dl(env, pid, ids)
    enable_retrieval(env, pid)
    raw = env.occ(pid, sid)  # identity not accepted
    with pytest.raises(PrerequisiteNotMet):
        dl(env, pid, [raw.occurrence_id])
    not_discovered = accepted(env, pid, sid)
    with pytest.raises(PrerequisiteNotMet):
        dl(env, pid, [not_discovered.occurrence_id])
    out = dl(env, pid, ids, budget={"max_files": 3}, version_policy=VersionPolicy.ANY_ELIGIBLE)
    job = env.queue.get_job(pid, out.job_id)
    assert job.stages == [StepType.RETRIEVE]
    assert job.options["budget"] == {"max_files": 3}
    assert job.snapshot.version_policy == VersionPolicy.ANY_ELIGIBLE
    assert job.snapshot.network_mode == NetworkMode.RETRIEVAL


def test_download_filter_scope(env):
    pid = env.project()
    sid = env.text_source(pid)
    occ = discovered(env, pid, sid)
    enable_retrieval(env, pid)
    env.occ(pid, sid)  # not accepted: skipped by the filter scope
    out = env.svc.download_files(
        env.ctx, DownloadFilesIn(project_id=pid, scope={"filter": {"source_id": sid}}, idempotency_key=key())
    )
    assert env.queue.get_job(pid, out.job_id).scope == {"occurrence_ids": [occ.occurrence_id]}


# ---------- create_export ----------


def test_create_export_current_revision_only(env):  # AT-40, W-26
    pid = env.project()
    rev = env.store.projects.get(pid).revision
    out = env.svc.create_export(
        env.ctx,
        CreateExportIn(
            project_id=pid, project_revision=rev, formats=["bibtex", "csv"], idempotency_key=key()
        ),
    )
    job = env.queue.get_job(pid, out.job_id)
    assert job.stages == [StepType.EXPORT]
    assert job.options == {"formats": ["bibtex", "csv"], "selection": "accepted", "project_revision": rev}
    assert env.store.projects.get(pid).revision == rev  # enqueueing is not a data change
    sid = env.text_source(pid)  # a mutation: the old revision is now stale
    with pytest.raises(RevisionNotCurrent) as err:
        env.svc.create_export(
            env.ctx,
            CreateExportIn(project_id=pid, project_revision=rev, formats=["json"], idempotency_key=key()),
        )
    assert err.value.details["current_revision"] == rev + 1
    assert sid


# ---------- draft_requests ----------


def test_draft_requests_classification_rules(env):
    pid = env.project()
    sid = env.text_source(pid)
    sender = {"name": "A. Student"}

    def draft(ids):
        return env.svc.draft_requests(
            env.ctx,
            DraftRequestsIn(
                project_id=pid,
                scope={"occurrence_ids": ids},
                sender=sender,
                language="en",
                idempotency_key=key(),
            ),
        )

    unchecked = env.occ(pid, sid)
    found = env.occ(pid, sid, classification=AccessClassification.ELIGIBLE_FOUND)
    none_found = env.occ(pid, sid, classification=AccessClassification.NONE_FOUND)
    unknown = env.occ(pid, sid, classification=AccessClassification.UNKNOWN)
    for bad in (unchecked, found):
        with pytest.raises(PrerequisiteNotMet):
            draft([bad.occurrence_id])
    out = draft([none_found.occurrence_id, unknown.occurrence_id])
    job = env.queue.get_job(pid, out.job_id)
    assert job.stages == [StepType.DRAFT]
    assert job.options["language"] == "en" and job.options["sender"] == {"name": "A. Student"}
    assert isinstance(job, Job)
