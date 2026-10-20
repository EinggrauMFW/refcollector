from __future__ import annotations

import pytest

from refcollector import ids
from refcollector.contracts.ports import StepSpec
from refcollector.domain.enums import JobState, StepState, StepType
from refcollector.errors import InvalidState, NotFound
from refcollector.executor.queue import SqliteJobQueue
from tests.unit.executor.helpers import add_project, new_job


@pytest.fixture
def queue(db):  # type: ignore[no-untyped-def]
    return SqliteJobQueue(db)


@pytest.fixture
def project(db):  # type: ignore[no-untyped-def]
    return add_project(db)


def _job_with_steps(queue, settings, project, states):  # type: ignore[no-untyped-def]
    job = queue.create_job(new_job(settings, project, {"resolve": []}))
    specs = [StepSpec(StepType.RESOLVE, f"o{i}", f"fp{i}") for i in range(len(states))]
    steps = queue.add_steps(job, specs, max_attempts=5)
    for step, state in zip(steps, states, strict=True):
        queue.set_step_state(step.step_id, state)
    return job


def test_create_get_list(queue, settings, project) -> None:  # type: ignore[no-untyped-def]
    job = queue.create_job(new_job(settings, project, {"extract": ["s1"]}))
    got = queue.get_job(project.project_id, job.job_id)
    assert got.state is JobState.QUEUED
    assert got.snapshot == job.snapshot
    assert [j.job_id for j in queue.list_jobs(project.project_id)] == [job.job_id]
    with pytest.raises(NotFound):
        queue.get_job("prj_other", job.job_id)
    with pytest.raises(NotFound):
        queue.get_job(project.project_id, ids.new_id("job"))


def test_add_steps_and_stage_counts(queue, settings, project) -> None:  # type: ignore[no-untyped-def]
    job = _job_with_steps(queue, settings, project, [StepState.QUEUED, StepState.SUCCEEDED])
    steps = queue.steps(job.job_id)
    assert all(s.snapshot_hash == job.snapshot_hash for s in steps)
    assert queue.stage_counts(job.job_id) == {StepType.RESOLVE: {"queued": 1, "succeeded": 1}}
    assert len(queue.steps(job.job_id, state="queued")) == 1


def test_pause_queued_job_goes_paused_and_resume(queue, settings, project) -> None:  # type: ignore[no-untyped-def]
    job = _job_with_steps(queue, settings, project, [StepState.QUEUED])
    assert queue.request(project.project_id, job.job_id, "pause").state is JobState.PAUSED
    assert queue.request(project.project_id, job.job_id, "resume").state is JobState.QUEUED


def test_pause_with_running_step_is_pausing(queue, settings, project) -> None:  # type: ignore[no-untyped-def]
    job = _job_with_steps(queue, settings, project, [StepState.RUNNING])
    queue.set_job_state(job.job_id, JobState.RUNNING)
    assert queue.request(project.project_id, job.job_id, "pause").state is JobState.PAUSING


def test_cancel_cancels_waiting_steps(queue, settings, project) -> None:  # type: ignore[no-untyped-def]
    job = _job_with_steps(
        queue, settings, project, [StepState.QUEUED, StepState.RETRY_WAIT, StepState.SUCCEEDED]
    )
    assert queue.request(project.project_id, job.job_id, "cancel").state is JobState.CANCELLED
    assert [s.state for s in queue.steps(job.job_id)] == [
        StepState.CANCELLED,
        StepState.CANCELLED,
        StepState.SUCCEEDED,
    ]


def test_cancel_with_running_step_is_cancelling(queue, settings, project) -> None:  # type: ignore[no-untyped-def]
    job = _job_with_steps(queue, settings, project, [StepState.RUNNING, StepState.QUEUED])
    queue.set_job_state(job.job_id, JobState.RUNNING)
    assert queue.request(project.project_id, job.job_id, "cancel").state is JobState.CANCELLING


def test_retry_failed_requeues_only_failed_retryable(queue, settings, project) -> None:  # type: ignore[no-untyped-def]
    job = _job_with_steps(
        queue,
        settings,
        project,
        [StepState.FAILED_RETRYABLE, StepState.FAILED_PERMANENT, StepState.SUCCEEDED],
    )
    queue.set_job_state(job.job_id, JobState.COMPLETED_WITH_WARNINGS)
    assert queue.request(project.project_id, job.job_id, "retry_failed").state is JobState.QUEUED
    steps = queue.steps(job.job_id)
    assert [s.state for s in steps] == [StepState.QUEUED, StepState.FAILED_PERMANENT, StepState.SUCCEEDED]
    assert steps[0].attempt == 0


@pytest.mark.parametrize(
    ("job_state", "action"),
    [
        (JobState.QUEUED, "resume"),
        (JobState.COMPLETED, "pause"),
        (JobState.CANCELLED, "cancel"),
        (JobState.COMPLETED, "cancel"),
        (JobState.PAUSED, "pause"),
        (JobState.COMPLETED, "retry_failed"),  # no failed_retryable steps
        (JobState.CANCELLED, "retry_failed"),
    ],
)
def test_invalid_transitions(queue, settings, project, job_state, action) -> None:  # type: ignore[no-untyped-def]
    job = _job_with_steps(queue, settings, project, [StepState.FAILED_PERMANENT])
    queue.set_job_state(job.job_id, job_state)
    with pytest.raises(InvalidState):
        queue.request(project.project_id, job.job_id, action)
