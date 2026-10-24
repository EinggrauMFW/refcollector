"""Tests for service configuration refresh boundaries (Wave 3 regression fix).

Verifies:
1. Supplied caller settings without disk config.json are preserved across snapshot().
2. Subsequent CLI/persisted config updates are detected at the refresh boundary
   and affect future job snapshots.
3. Already queued/running/resumed jobs retain their saved ConfigSnapshot.
"""

from __future__ import annotations

from pathlib import Path

from refcollector.bootstrap import open_app
from refcollector.config import Settings, save_settings
from refcollector.contracts import api
from refcollector.domain.enums import Channel, Scope, StepType


def test_supplied_settings_without_disk_config_are_preserved(tmp_path: Path) -> None:
    rc_home = tmp_path / "home"
    settings = Settings(rc_home=rc_home, email="caller@example.org")
    app = open_app(settings)
    try:
        assert not settings.config_path.exists()
        assert app.service.settings.email == "caller@example.org"

        ctx = api.CallContext(
            principal="test", channel=Channel.CLI, scopes=frozenset(Scope), request_id="req1"
        )
        proj = app.service.create_project(
            ctx, api.CreateProjectIn(name="test_proj", idempotency_key="key-proj-001")
        )
        p = app.service.store.projects.get(proj.project_id)

        snap = app.service.snapshot(p)
        assert snap.contact_email == "caller@example.org"
        assert app.service.settings.email == "caller@example.org"
        assert not settings.config_path.exists()
    finally:
        app.close()


def test_subsequent_cli_config_update_affects_future_snapshots(tmp_path: Path) -> None:
    rc_home = tmp_path / "home"
    settings = Settings(rc_home=rc_home, email="caller@example.org")
    app = open_app(settings)
    try:
        ctx = api.CallContext(
            principal="test", channel=Channel.CLI, scopes=frozenset(Scope), request_id="req1"
        )
        proj = app.service.create_project(
            ctx, api.CreateProjectIn(name="test_proj", idempotency_key="key-proj-001")
        )
        p = app.service.store.projects.get(proj.project_id)

        first_snap = app.service.snapshot(p)
        assert first_snap.contact_email == "caller@example.org"

        # Simulate CLI writing an updated config to disk
        updated_settings = Settings(rc_home=rc_home, email="updated_cli@example.org")
        save_settings(updated_settings)
        assert settings.config_path.exists()

        second_snap = app.service.snapshot(p)
        assert second_snap.contact_email == "updated_cli@example.org"
        assert app.service.settings.email == "updated_cli@example.org"
    finally:
        app.close()


def test_already_queued_job_retains_saved_snapshot(tmp_path: Path) -> None:
    rc_home = tmp_path / "home"
    settings = Settings(rc_home=rc_home, email="original@example.org")
    app = open_app(settings)
    try:
        ctx = api.CallContext(
            principal="test", channel=Channel.CLI, scopes=frozenset(Scope), request_id="req1"
        )
        proj = app.service.create_project(
            ctx, api.CreateProjectIn(name="test_proj", idempotency_key="key-proj-001")
        )
        p = app.service.store.projects.get(proj.project_id)
        src = app.service.import_source(
            ctx,
            api.ImportSourceIn(
                project_id=p.project_id,
                input=api.TextInput(type="text", text="Smith (2020). Title. Venue."),
                idempotency_key="key-source-001",
            ),
        )

        # Enqueue a job using start_job
        job_out = app.service.start_job(
            ctx,
            api.StartJobIn(
                project_id=p.project_id,
                stages=[StepType.EXTRACT],
                scope=api.SourceScope(source_ids=[src.source_id]),
                idempotency_key="queue-test-key-1",
            ),
        )
        job = app.service.queue.get_job(p.project_id, job_out.job_id)
        assert job is not None
        assert job.snapshot.contact_email == "original@example.org"

        # Update config.json on disk (simulating CLI edit)
        updated_settings = Settings(rc_home=rc_home, email="new_after_queue@example.org")
        save_settings(updated_settings)

        # Verify the already queued job's snapshot is completely unchanged
        persisted_job = app.service.queue.get_job(p.project_id, job_out.job_id)
        assert persisted_job is not None
        assert persisted_job.snapshot.contact_email == "original@example.org"

        # Verify a new snapshot reflects the updated config
        new_snap = app.service.snapshot(p)
        assert new_snap.contact_email == "new_after_queue@example.org"
    finally:
        app.close()
