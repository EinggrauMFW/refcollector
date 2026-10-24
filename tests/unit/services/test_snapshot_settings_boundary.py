"""Independent public-service regressions for supplied settings and later CLI writes."""

import pytest

from refcollector.bootstrap import open_app
from refcollector.config import Settings, save_settings
from refcollector.contracts.api import CreateProjectIn, ImportSourceIn, StartJobIn


def seed_source(handle, ctx):
    project = handle.service.create_project(
        ctx, CreateProjectIn(name="snapshot boundary", idempotency_key="boundary-project")
    )
    source = handle.service.import_source(
        ctx,
        ImportSourceIn(
            project_id=project.project_id,
            input={"type": "text", "text": "Doe (2020). A reference. Journal."},
            idempotency_key="boundary-source",
        ),
    )
    return project.project_id, source.source_id


def queue_job(handle, ctx, project_id, source_id, key):
    return handle.service.start_job(
        ctx,
        StartJobIn(
            project_id=project_id,
            scope={"source_ids": [source_id]},
            stages=["extract", "resolve"],
            idempotency_key=key,
        ),
    )


@pytest.mark.parametrize("initial_file", [False, True])
def test_unchanged_disk_never_replaces_explicit_caller_settings(tmp_path, ctx_agent, initial_file):
    supplied = Settings(
        rc_home=tmp_path / "home",
        email="supplied@example.org",
        allow_folders=[tmp_path],
        enabled_providers=["crossref"],
    )
    if initial_file:
        save_settings(supplied.model_copy(update={"email": "disk@example.org", "enabled_providers": []}))
    handle = open_app(supplied)
    try:
        pid, sid = seed_source(handle, ctx_agent)
        first = queue_job(handle, ctx_agent, pid, sid, "boundary-first-job")
        second = queue_job(handle, ctx_agent, pid, sid, "boundary-second-job")
        for result in (first, second):
            snapshot = handle.executor.queue.get_job(pid, result.job_id).snapshot
            assert snapshot.contact_email == "supplied@example.org"
            assert snapshot.provider_allowlist == ["crossref"]
        assert handle.service.settings.allow_folders == [tmp_path]
    finally:
        handle.close()


def test_later_cli_email_is_available_before_job_validation_and_old_snapshot_stays_frozen(
    tmp_path, ctx_agent
):
    supplied = Settings(rc_home=tmp_path / "home", allow_folders=[tmp_path])
    handle = open_app(supplied)
    try:
        pid, sid = seed_source(handle, ctx_agent)
        queued = handle.service.start_job(
            ctx_agent,
            StartJobIn(
                project_id=pid,
                scope={"source_ids": [sid]},
                stages=["extract"],
                idempotency_key="boundary-old-job",
            ),
        )
        old = handle.executor.queue.get_job(pid, queued.job_id)
        old_snapshot = old.snapshot.model_dump(mode="json")
        old_hash = old.snapshot_hash
        save_settings(
            supplied.model_copy(update={"email": "later@example.org", "enabled_providers": ["crossref"]})
        )
        new = queue_job(handle, ctx_agent, pid, sid, "boundary-later-job")
        assert handle.executor.queue.get_job(pid, new.job_id).snapshot.contact_email == "later@example.org"
        unchanged = handle.executor.queue.get_job(pid, queued.job_id)
        assert unchanged.snapshot.model_dump(mode="json") == old_snapshot
        assert unchanged.snapshot_hash == old_hash
    finally:
        handle.close()
