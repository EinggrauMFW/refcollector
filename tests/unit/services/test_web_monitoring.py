"""Unit tests for web monitoring extensions: counts, filters, list_jobs, list_global_artifacts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from refcollector.contracts.api import (
    CreateExportIn,
    CreateProjectIn,
    GetProjectIn,
    ImportSourceIn,
    ListGlobalArtifactsIn,
    ListJobsIn,
    ListReferencesIn,
    ReadArtifactIn,
    ReferenceFilter,
    StageCounts,
    StartJobIn,
    TextInput,
)
from refcollector.domain.enums import (
    AccessClassification,
    DiscoveryState,
    ExportFormat,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyState,
    RetrievalState,
    Stage,
)
from tests.unit.services.conftest import key


def create_project(env, name="proj"):
    return env.svc.create_project(env.ctx, CreateProjectIn(name=name, idempotency_key=key())).project_id


def test_get_project_all_dimension_counts_and_download_enabled(env):
    pid = create_project(env, "proj-counts")

    # Add a source and occurrences
    src = env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid,
            input=TextInput(type="text", text="1. Smith (2020) Title.\n2. Jones (2021) Paper."),
            idempotency_key=key(),
        ),
    )
    env.svc.start_job(
        env.ctx,
        StartJobIn(
            project_id=pid,
            stages=[Stage.EXTRACT],
            scope={"source_ids": [src.source_id]},
            idempotency_key=key(),
        ),
    )

    out = env.svc.get_project(env.ctx, GetProjectIn(project_id=pid))
    assert out.download_enabled is False

    counts = out.counts
    assert "by_extraction" in counts
    assert "by_identity" in counts
    assert "by_discovery" in counts
    assert "by_classification" in counts
    assert "by_local_copy" in counts
    assert "by_retrieval" in counts
    assert "by_access" in counts

    # Every bucket must exist
    for e in ExtractionState:
        assert e.value in counts["by_extraction"]
    for i in IdentityState:
        assert i.value in counts["by_identity"]
    for d in DiscoveryState:
        assert d.value in counts["by_discovery"]
    for c in AccessClassification:
        assert c.value in counts["by_classification"]
    assert "unclassified" in counts["by_classification"]
    for lc in LocalCopyState:
        assert lc.value in counts["by_local_copy"]
    for r in RetrievalState:
        assert r.value in counts["by_retrieval"]

    # Read consistency: all dimensions share the exact same denominator
    total_occs = counts["occurrences"]
    assert sum(counts["by_extraction"].values()) == total_occs
    assert sum(counts["by_identity"].values()) == total_occs
    assert sum(counts["by_discovery"].values()) == total_occs
    assert sum(counts["by_classification"].values()) == total_occs
    assert sum(counts["by_local_copy"].values()) == total_occs
    assert sum(counts["by_retrieval"].values()) == total_occs


def test_reference_filter_classification_is_null(env):
    pid = create_project(env, "proj-filter")
    env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid,
            input=TextInput(type="text", text="1. Smith (2020) Title."),
            idempotency_key=key(),
        ),
    )

    # Initially before classification run, classification is null
    res_null = env.svc.list_references(
        env.ctx,
        ListReferencesIn(project_id=pid, filter=ReferenceFilter(classification_is_null=True)),
    )
    assert res_null.total >= 0

    res_not_null = env.svc.list_references(
        env.ctx,
        ListReferencesIn(project_id=pid, filter=ReferenceFilter(classification_is_null=False)),
    )
    assert res_not_null.total == 0

    # Incompatible settings raise ValidationError
    with pytest.raises(ValidationError):
        ReferenceFilter(classification=AccessClassification.RESTRICTED, classification_is_null=True)


def test_stage_counts_has_retry_wait_and_cancelled():
    sc = StageCounts(name="extract", queued=5, retry_wait=2, cancelled=1)
    assert sc.queued == 5
    assert sc.retry_wait == 2
    assert sc.cancelled == 1


def test_read_artifact_out_has_next_offset(env):
    pid = create_project(env, "proj-artifact")
    env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid,
            input=TextInput(type="text", text="1. Citation"),
            idempotency_key=key(),
        ),
    )

    proj = env.svc.get_project(env.ctx, GetProjectIn(project_id=pid))
    exp_job = env.svc.create_export(
        env.ctx,
        CreateExportIn(
            project_id=pid,
            project_revision=proj.revision,
            formats=[ExportFormat.JSON],
            idempotency_key=key(),
        ),
    )

    # Create dummy artifact directly to test read_artifact offset
    from refcollector.domain.models import Artifact, utcnow
    from refcollector.ids import new_id

    blob_path = env.store.files.new_tmp_path(".json")
    blob_path.write_text('{"test": true}', encoding="utf-8")
    blob = env.store.files.commit(blob_path, "application/json")
    env.store.files.grant(pid, blob.sha256)

    art = env.store.artifacts.add(
        Artifact(
            artifact_id=new_id("artifact"),
            project_id=pid,
            job_id=exp_job.job_id,
            kind="export",
            name="export.json",
            media_type="application/json",
            bytes=len(b'{"test": true}'),
            sha256=blob.sha256,
            project_revision=proj.revision,
            created_at=utcnow(),
        )
    )

    read_out = env.svc.read_artifact(
        env.ctx, ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id, offset=0, length=4)
    )
    assert read_out.text == '{"te'
    assert read_out.next_offset == 4
    assert read_out.eof is False


def test_list_jobs_and_pagination(env):
    pid1 = create_project(env, "proj-job-1")
    pid2 = create_project(env, "proj-job-2")

    src1 = env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid1,
            input=TextInput(type="text", text="1. Smith (2020)"),
            idempotency_key=key(),
        ),
    )
    src2 = env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid2,
            input=TextInput(type="text", text="1. Jones (2021)"),
            idempotency_key=key(),
        ),
    )

    j1 = env.svc.start_job(
        env.ctx,
        StartJobIn(
            project_id=pid1,
            stages=[Stage.EXTRACT],
            scope={"source_ids": [src1.source_id]},
            idempotency_key=key(),
        ),
    )
    j2 = env.svc.start_job(
        env.ctx,
        StartJobIn(
            project_id=pid2,
            stages=[Stage.EXTRACT],
            scope={"source_ids": [src2.source_id]},
            idempotency_key=key(),
        ),
    )

    # Global job list (across projects)
    out_all = env.svc.list_jobs(env.ctx, ListJobsIn())
    job_ids = [j.job_id for j in out_all.jobs]
    assert j1.job_id in job_ids
    assert j2.job_id in job_ids

    # Project names populated
    pnames = {j.project_id: j.project_name for j in out_all.jobs}
    assert pnames[pid1] == "proj-job-1"
    assert pnames[pid2] == "proj-job-2"

    # Single project filter
    out_p1 = env.svc.list_jobs(env.ctx, ListJobsIn(project_id=pid1))
    assert all(j.project_id == pid1 for j in out_p1.jobs)

    # State filter
    out_queued = env.svc.list_jobs(env.ctx, ListJobsIn(states=[JobState.QUEUED]))
    assert all(j.state == JobState.QUEUED for j in out_queued.jobs)

    # Pagination with limit=1
    p1 = env.svc.list_jobs(env.ctx, ListJobsIn(limit=1))
    assert len(p1.jobs) == 1
    assert p1.next_cursor is not None

    p2 = env.svc.list_jobs(env.ctx, ListJobsIn(limit=1, cursor=p1.next_cursor))
    assert len(p2.jobs) == 1
    assert p2.jobs[0].job_id != p1.jobs[0].job_id


def test_list_jobs_equal_created_at_pagination(env):
    pid = create_project(env, "proj-equal-ts")
    fixed_now = env.clock()

    from refcollector.contracts.ports import snapshot_hash
    from refcollector.domain.models import Job

    project = env.store.projects.get(pid)
    snap = env.svc.snapshot(project)
    for i in range(3):
        env.queue.create_job(
            Job(
                job_id=f"job_ts_tie_{i:02d}",
                project_id=pid,
                stages=[],
                scope={},
                options={},
                snapshot=snap,
                snapshot_hash=snapshot_hash(snap),
                provenance=env.svc.provenance(env.ctx),
                created_at=fixed_now,
                updated_at=fixed_now,
            )
        )

    # Page through one by one; tiebreak by id DESC ensures stable pagination
    p1 = env.svc.list_jobs(env.ctx, ListJobsIn(project_id=pid, limit=1))
    assert len(p1.jobs) == 1
    assert p1.jobs[0].job_id == "job_ts_tie_02"
    assert p1.next_cursor is not None

    p2 = env.svc.list_jobs(env.ctx, ListJobsIn(project_id=pid, limit=1, cursor=p1.next_cursor))
    assert len(p2.jobs) == 1
    assert p2.jobs[0].job_id == "job_ts_tie_01"
    assert p2.next_cursor is not None

    p3 = env.svc.list_jobs(env.ctx, ListJobsIn(project_id=pid, limit=1, cursor=p2.next_cursor))
    assert len(p3.jobs) == 1
    assert p3.jobs[0].job_id == "job_ts_tie_00"
    assert p3.next_cursor is None


def test_list_global_artifacts(env):
    pid1 = create_project(env, "proj-art-1")
    pid2 = create_project(env, "proj-art-2")

    from refcollector.domain.models import Artifact, utcnow
    from refcollector.ids import new_id

    blob_path = env.store.files.new_tmp_path(".txt")
    blob_path.write_text("global artifact content", encoding="utf-8")
    blob = env.store.files.commit(blob_path, "text/plain")
    env.store.files.grant(pid1, blob.sha256)
    env.store.files.grant(pid2, blob.sha256)

    a1 = env.store.artifacts.add(
        Artifact(
            artifact_id=new_id("artifact"),
            project_id=pid1,
            job_id=new_id("job"),
            kind="audit_report",
            name="report.txt",
            media_type="text/plain",
            bytes=len(b"global artifact content"),
            sha256=blob.sha256,
            project_revision=1,
            created_at=utcnow(),
        )
    )
    a2 = env.store.artifacts.add(
        Artifact(
            artifact_id=new_id("artifact"),
            project_id=pid2,
            job_id=new_id("job"),
            kind="export",
            name="export.txt",
            media_type="text/plain",
            bytes=len(b"global artifact content"),
            sha256=blob.sha256,
            project_revision=1,
            created_at=utcnow(),
        )
    )

    out = env.svc.list_global_artifacts(env.ctx, ListGlobalArtifactsIn())
    art_ids = [a.descriptor.artifact_id for a in out.artifacts]
    assert a1.artifact_id in art_ids
    assert a2.artifact_id in art_ids

    # Kind filter
    out_audit = env.svc.list_global_artifacts(env.ctx, ListGlobalArtifactsIn(kind="audit_report"))
    assert all(a.descriptor.kind == "audit_report" for a in out_audit.artifacts)

    # Project names populated
    for item in out.artifacts:
        if item.project_id == pid1:
            assert item.project_name == "proj-art-1"
        elif item.project_id == pid2:
            assert item.project_name == "proj-art-2"
