"""Mutable preconditions must be checked in the transaction that applies the change."""

from contextlib import contextmanager

import pytest

from refcollector.contracts import api
from refcollector.domain.enums import AccessClassification, IdentityState, NetworkMode, VersionPolicy
from refcollector.errors import NetworkModeDenied, NotAuthorized, PrerequisiteNotMet, RevisionConflict
from refcollector.storage.db import Database
from refcollector.storage.repos import SqliteStore
from tests.unit.services.conftest import key
from tests.unit.services.test_jobs import discovered, enable_retrieval


def before_transaction(env, monkeypatch, change):
    """Commit on a second connection after preparation but before the write lock."""
    original = env.store.transaction
    fired = False

    @contextmanager
    def transaction(immediate=False):
        nonlocal fired
        if not fired:
            fired = True
            other_db = Database(env.settings.db_path)
            try:
                other = SqliteStore(other_db, env.settings)
                with other.transaction(immediate=True):
                    change(other)
            finally:
                other_db.close()
        with original(immediate=immediate):
            yield

    monkeypatch.setattr(env.store, "transaction", transaction)


@pytest.mark.parametrize(
    "method,change,error",
    [
        ("start_job", "offline", NetworkModeDenied),
        ("start_job", "unaccepted", PrerequisiteNotMet),
        ("download_files", "offline", NetworkModeDenied),
        ("download_files", "revoke", NotAuthorized),
        ("download_files", "unaccepted", PrerequisiteNotMet),
        ("draft_requests", "open_copy", PrerequisiteNotMet),
    ],
)
def test_job_revalidates_mutable_preconditions(env, monkeypatch, method, change, error):
    pid = env.project()
    sid = env.text_source(pid)
    occ = discovered(env, pid, sid)
    enable_retrieval(env, pid)
    if method == "draft_requests":
        occ.classification = AccessClassification.NONE_FOUND
        env.store.occurrences.update(occ)

    def mutate(store):
        if change in {"offline", "revoke"}:
            project = store.projects.get(pid)
            if change == "offline":
                project.network_mode = NetworkMode.OFFLINE
            else:
                project.download_enabled = False
            store.projects.update(project)
        else:
            latest = store.occurrences.get(pid, occ.occurrence_id)
            if change == "unaccepted":
                latest.identity = IdentityState.UNRESOLVED
            else:
                latest.classification = AccessClassification.ELIGIBLE_FOUND
            store.occurrences.update(latest)

    args = dict(project_id=pid, scope={"occurrence_ids": [occ.occurrence_id]}, idempotency_key=key())
    if method == "start_job":
        req = api.StartJobIn(**args, stages=["discover_access"])
    elif method == "download_files":
        req = api.DownloadFilesIn(**args)
    else:
        req = api.DraftRequestsIn(**args, sender={"name": "Tester"}, language="en")
    before_transaction(env, monkeypatch, mutate)
    with pytest.raises(error):
        getattr(env.svc, method)(env.ctx, req)
    assert env.queue.list_jobs(pid) == []
    assert (
        env.store.idempotency.get(env.ctx.principal, pid, method, req.idempotency_key, now=env.clock())
        is None
    )


@pytest.mark.parametrize(
    "change,error",
    [
        ("unaccepted", PrerequisiteNotMet),
        ("different_work", RevisionConflict),
        ("policy", RevisionConflict),
        ("metadata", RevisionConflict),
    ],
)
def test_attach_rejects_stale_evaluation(env, monkeypatch, change, error):
    pid = env.project()
    sid = env.text_source(pid)
    occ = discovered(env, pid, sid)
    other = discovered(env, pid, sid)
    path = env.allowed / "copy.pdf"
    path.write_bytes(b"%PDF-1.7")

    def mutate(store):
        if change == "policy":
            project = store.projects.get(pid)
            project.version_policy = VersionPolicy.ANY_ELIGIBLE
            store.projects.update(project)
        elif change == "metadata":
            work = store.works.get(pid, occ.work_id)
            work.metadata.title = "Changed identity"
            store.works.update(work)
        else:
            latest = store.occurrences.get(pid, occ.occurrence_id)
            if change == "unaccepted":
                latest.identity = IdentityState.UNRESOLVED
            else:
                latest.work_id = other.work_id
            store.occurrences.update(latest)

    before_transaction(env, monkeypatch, mutate)
    req = api.AttachFileIn(
        project_id=pid, occurrence_id=occ.occurrence_id, path=str(path), idempotency_key=key()
    )
    with pytest.raises(error):
        env.svc.attach_file(env.ctx, req)
    assert env.store.local_copies.list_for_work(pid, occ.work_id) == []
    assert len(env.local.calls) == 1


def test_attach_allows_unrelated_project_revision_change(env, monkeypatch):
    pid = env.project()
    sid = env.text_source(pid)
    occ = discovered(env, pid, sid)
    path = env.allowed / "copy.pdf"
    path.write_bytes(b"%PDF-1.7")
    before_transaction(env, monkeypatch, lambda store: store.projects.bump_revision(pid))
    out = env.svc.attach_file(
        env.ctx,
        api.AttachFileIn(
            project_id=pid, occurrence_id=occ.occurrence_id, path=str(path), idempotency_key=key()
        ),
    )
    assert out.local_copy_id
    assert len(env.store.local_copies.list_for_work(pid, occ.work_id)) == 1


def test_download_replay_stays_available_after_permission_revoked(env):
    pid = env.project()
    sid = env.text_source(pid)
    occ = discovered(env, pid, sid)
    enable_retrieval(env, pid)
    req = api.DownloadFilesIn(
        project_id=pid, scope={"occurrence_ids": [occ.occurrence_id]}, idempotency_key=key()
    )
    first = env.svc.download_files(env.ctx, req)
    project = env.store.projects.get(pid)
    project.download_enabled = False
    project.network_mode = NetworkMode.OFFLINE
    env.store.projects.update(project)
    assert env.svc.download_files(env.ctx, req) == first
    assert len(env.queue.list_jobs(pid)) == 1


def test_read_artifact_rejects_length_that_cannot_fit_character(env):
    from refcollector.errors import InvalidInput
    from tests.unit.services.test_files_artifacts import add_artifact

    pid = env.project()
    text = "\u20ac and \U0001f600"
    art = add_artifact(env, pid, "unicode.md", text.encode())
    with pytest.raises(InvalidInput, match="character"):
        env.svc.read_artifact(
            env.ctx, api.ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id, length=1)
        )
    out = env.svc.read_artifact(
        env.ctx, api.ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id, length=3)
    )
    assert out.text == "\u20ac" and out.eof is False

    parts, offset = [], 0
    while True:
        out = env.svc.read_artifact(
            env.ctx, api.ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id, offset=offset, length=4)
        )
        assert out.text
        size = len(out.text.encode())
        assert 0 < size <= 4
        parts.append(out.text)
        offset += size
        assert out.eof is (offset == len(text.encode()))
        if out.eof:
            break
    assert "".join(parts) == text
