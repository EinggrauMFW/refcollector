"""Executor loop: AT-10b/W-29, AT-29, AT-30, AT-31, AT-32, retries, timeout, reuse, commit atomicity."""

from __future__ import annotations

import asyncio
import time

import pytest

from refcollector.config import save_settings
from refcollector.contracts.ports import StepContext, StepSpec
from refcollector.domain.enums import (
    AuditEventType,
    JobState,
    NetworkMode,
    SkipReason,
    StepState,
    StepType,
)
from refcollector.errors import Cancelled, InvalidInput, RateLimited
from refcollector.executor.queue import SqliteJobQueue
from tests.unit.executor.helpers import (
    ScopePlanner,
    ScriptedHandler,
    add_project,
    audit_types,
    fast_settings,
    make_executor,
    new_job,
    step_states,
)

pytestmark = pytest.mark.anyio

R, E, D = StepType.RESOLVE, StepType.EXTRACT, StepType.DISCOVER_ACCESS


@pytest.fixture
def queue(db):  # type: ignore[no-untyped-def]
    return SqliteJobQueue(db)


@pytest.fixture
def project(db):  # type: ignore[no-untyped-def]
    return add_project(db)


@pytest.fixture
def fast(settings):  # type: ignore[no-untyped-def]
    return fast_settings(settings)


def _fast(settings, **kw):  # type: ignore[no-untyped-def]
    return fast_settings(settings, **kw)


async def test_stages_run_in_order_and_complete(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    order: list[str] = []
    he = ScriptedHandler(E, {"s1": lambda c: order.append("e")})
    hr = ScriptedHandler(R, {"o1": lambda c: order.append("r"), "o2": lambda c: order.append("r")})
    planner = ScopePlanner()
    job = queue.create_job(new_job(fast, project, {"extract": ["s1"], "resolve": ["o1", "o2"]}))
    ex = make_executor(fast, db, [he, hr], planner)
    await ex.run_until_idle()
    assert order == ["e", "r", "r"]
    # resolve was planned only after extract finished (D-22)
    assert planner.calls == [(job.job_id, E), (job.job_id, R)]
    got = queue.get_job(project.project_id, job.job_id)
    assert got.state is JobState.COMPLETED
    assert {s.output_ref for s in queue.steps(job.job_id)} == {"out:s1", "out:o1", "out:o2"}
    ex.stop()


async def test_empty_stage_is_skipped(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    job = queue.create_job(new_job(fast, project, {"extract": [], "resolve": ["o1"]}))
    hr = ScriptedHandler(R)
    ex = make_executor(fast, db, [hr])
    await ex.run_until_idle()
    assert queue.get_job(project.project_id, job.job_id).state is JobState.COMPLETED
    assert hr.calls["o1"] == 1
    ex.stop()


# ---------- AT-10b / W-29: executor loss ----------


async def test_recovery_after_executor_loss(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    job = queue.create_job(new_job(fast, project, {"resolve": ["o1", "o2", "o3"]}))
    steps = queue.add_steps(job, [StepSpec(R, s, f"fp:{s}") for s in ("o1", "o2", "o3")], max_attempts=5)
    queue.set_job_state(job.job_id, JobState.RUNNING)
    queue.set_step_state(steps[0].step_id, StepState.SUCCEEDED, output_ref="out:o1", attempt=1)
    queue.set_step_state(steps[1].step_id, StepState.RUNNING, executor_id="exe_dead", attempt=1)

    hr = ScriptedHandler(R)
    ex = make_executor(fast, db, [hr])
    assert ex.try_acquire()
    recovered = queue.steps(job.job_id)[1]
    assert recovered.state is StepState.QUEUED
    assert queue.get_job(project.project_id, job.job_id).state is JobState.QUEUED
    assert audit_types(db) == [AuditEventType.RECOVERED_AFTER_EXECUTOR_LOSS.value]

    await ex.run_until_idle()
    assert dict(hr.calls) == {"o2": 1, "o3": 1}  # succeeded step not re-run
    assert queue.get_job(project.project_id, job.job_id).state is JobState.COMPLETED
    ex.stop()


async def test_recovery_pausing_and_cancelling(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    j1 = queue.create_job(new_job(fast, project, {"resolve": ["a"]}))
    j2 = queue.create_job(new_job(fast, project, {"resolve": ["b"]}))
    s1 = queue.add_steps(j1, [StepSpec(R, "a", "fp:a")], max_attempts=5)[0]
    s2 = queue.add_steps(j2, [StepSpec(R, "b", "fp:b")], max_attempts=5)[0]
    queue.set_job_state(j1.job_id, JobState.PAUSING)
    queue.set_job_state(j2.job_id, JobState.CANCELLING)
    queue.set_step_state(s1.step_id, StepState.RUNNING, executor_id="exe_dead")
    queue.set_step_state(s2.step_id, StepState.RUNNING, executor_id="exe_dead")
    hr = ScriptedHandler(R)
    ex = make_executor(fast, db, [hr])
    assert ex.try_acquire()
    assert queue.get_job(project.project_id, j1.job_id).state is JobState.PAUSED
    assert queue.get_job(project.project_id, j2.job_id).state is JobState.CANCELLED
    assert step_states(queue, j1.job_id) == {"a": "queued"}
    assert step_states(queue, j2.job_id) == {"b": "cancelled"}
    await ex.run_until_idle()
    assert hr.calls == {}
    ex.stop()


# ---------- AT-29 pause ----------


async def test_pause_during_running(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    started, release = asyncio.Event(), asyncio.Event()

    async def block(ctx: StepContext) -> None:
        started.set()
        await release.wait()

    hr = ScriptedHandler(R, {"o1": block})
    job = queue.create_job(new_job(fast, project, {"resolve": ["o1", "o2", "o3"]}))
    ex = make_executor(fast, db, [hr], max_concurrency=1)
    task = asyncio.create_task(ex.run_until_idle())
    await asyncio.wait_for(started.wait(), 5)
    assert queue.request(project.project_id, job.job_id, "pause").state is JobState.PAUSING
    release.set()
    await asyncio.wait_for(task, 5)
    assert queue.get_job(project.project_id, job.job_id).state is JobState.PAUSED
    assert step_states(queue, job.job_id) == {"o1": "succeeded", "o2": "queued", "o3": "queued"}
    assert dict(hr.calls) == {"o1": 1}

    queue.request(project.project_id, job.job_id, "resume")
    await ex.run_until_idle()
    assert queue.get_job(project.project_id, job.job_id).state is JobState.COMPLETED
    assert dict(hr.calls) == {"o1": 1, "o2": 1, "o3": 1}
    ex.stop()


# ---------- AT-30 cancel ----------


async def test_cancel_during_running(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    started = asyncio.Event()

    async def cooperative(ctx: StepContext) -> None:
        started.set()
        while not ctx.is_cancelled():  # noqa: ASYNC110 - handlers poll is_cancelled by contract
            await asyncio.sleep(0.01)
        raise Cancelled("stop")

    hr = ScriptedHandler(R, {"o2": cooperative})
    job = queue.create_job(new_job(fast, project, {"resolve": ["o1", "o2", "o3"]}))
    ex = make_executor(fast, db, [hr], max_concurrency=1)
    task = asyncio.create_task(ex.run_until_idle())
    await asyncio.wait_for(started.wait(), 5)
    assert queue.request(project.project_id, job.job_id, "cancel").state is JobState.CANCELLING
    await asyncio.wait_for(task, 5)
    assert queue.get_job(project.project_id, job.job_id).state is JobState.CANCELLED
    assert step_states(queue, job.job_id) == {"o1": "succeeded", "o2": "cancelled", "o3": "cancelled"}
    assert queue.steps(job.job_id)[0].output_ref == "out:o1"  # completed output kept
    ex.stop()


async def test_cancel_discards_uncommitted_output(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    started, release = asyncio.Event(), asyncio.Event()

    async def ignores_cancel(ctx: StepContext) -> None:
        started.set()
        await release.wait()

    hr = ScriptedHandler(R, {"o1": ignores_cancel})
    job = queue.create_job(new_job(fast, project, {"resolve": ["o1"]}))
    ex = make_executor(fast, db, [hr])
    task = asyncio.create_task(ex.run_until_idle())
    await asyncio.wait_for(started.wait(), 5)
    queue.request(project.project_id, job.job_id, "cancel")
    release.set()
    await asyncio.wait_for(task, 5)
    assert hr.committed == []
    assert step_states(queue, job.job_id) == {"o1": "cancelled"}
    assert queue.get_job(project.project_id, job.job_id).state is JobState.CANCELLED
    ex.stop()


# ---------- AT-31 retry_failed ----------


async def test_retry_failed_reruns_only_failed_retryable(settings, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    s = _fast(settings, step_max_attempts=2)
    outage = {"on": True}

    def flaky(ctx: StepContext) -> None:
        if outage["on"]:
            raise RateLimited("provider down")

    def broken(ctx: StepContext) -> None:
        raise InvalidInput("bad citation")

    hr = ScriptedHandler(R, {"b": flaky, "c": broken})
    job = queue.create_job(new_job(s, project, {"resolve": ["a", "b", "c"]}))
    ex = make_executor(s, db, [hr])
    await ex.run_until_idle()
    assert step_states(queue, job.job_id) == {
        "a": "succeeded",
        "b": "failed_retryable",
        "c": "failed_permanent",
    }
    assert dict(hr.calls) == {"a": 1, "b": 2, "c": 1}  # permanent: no retry
    assert queue.get_job(project.project_id, job.job_id).state is JobState.COMPLETED_WITH_WARNINGS

    outage["on"] = False
    assert queue.request(project.project_id, job.job_id, "retry_failed").state is JobState.QUEUED
    await ex.run_until_idle()
    assert dict(hr.calls) == {"a": 1, "b": 3, "c": 1}
    assert step_states(queue, job.job_id) == {"a": "succeeded", "b": "succeeded", "c": "failed_permanent"}
    assert queue.get_job(project.project_id, job.job_id).state is JobState.COMPLETED_WITH_WARNINGS
    ex.stop()


async def test_all_failed_job_is_failed(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    def broken(ctx: StepContext) -> None:
        raise InvalidInput("bad")

    hr = ScriptedHandler(R, {"a": broken})
    job = queue.create_job(new_job(fast, project, {"resolve": ["a"]}))
    ex = make_executor(fast, db, [hr])
    await ex.run_until_idle()
    job = queue.get_job(project.project_id, job.job_id)
    assert job.state is JobState.FAILED
    assert queue.steps(job.job_id)[0].last_error["code"] == "INVALID_INPUT"  # type: ignore[index]
    ex.stop()


# ---------- AT-32 snapshot ----------


async def test_resumed_job_uses_its_snapshot(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    job = queue.create_job(new_job(fast, project, {"resolve": ["o1"]}))
    queue.request(project.project_id, job.job_id, "pause")
    changed = fast.model_copy(update={"enabled_providers": ["crossref"], "email": "new@example.org"})
    save_settings(changed)
    project.network_mode = NetworkMode.OFFLINE
    queue.request(project.project_id, job.job_id, "resume")
    hr = ScriptedHandler(R)
    ex = make_executor(changed, db, [hr])
    await ex.run_until_idle()
    seen = hr.contexts[0].job
    assert seen.snapshot == job.snapshot
    assert seen.snapshot.provider_allowlist == fast.enabled_providers
    assert seen.snapshot.network_mode is NetworkMode.METADATA
    assert hr.contexts[0].step.snapshot_hash == job.snapshot_hash
    ex.stop()


# ---------- retries, timeout, reuse ----------


async def test_retry_honours_retry_after_then_succeeds(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    seen: list[float] = []

    def once(ctx: StepContext) -> None:
        seen.append(time.monotonic())
        if len(seen) == 1:
            raise RateLimited("slow down", retry_after=0.3)

    hr = ScriptedHandler(R, {"o1": once})
    job = queue.create_job(new_job(fast, project, {"resolve": ["o1"]}))
    ex = make_executor(fast, db, [hr])
    await ex.run_until_idle()
    step = queue.steps(job.job_id)[0]
    assert step.state is StepState.SUCCEEDED
    assert step.attempt == 2
    assert seen[1] - seen[0] >= 0.29
    ex.stop()


async def test_provider_exhausted_not_retried(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    def exhausted(ctx: StepContext) -> None:
        raise RateLimited("quota", retry_after=3600)

    hr = ScriptedHandler(R, {"o1": exhausted})
    job = queue.create_job(new_job(fast, project, {"resolve": ["o1"]}))
    ex = make_executor(fast, db, [hr])
    await ex.run_until_idle()
    assert step_states(queue, job.job_id) == {"o1": "failed_retryable"}
    assert hr.calls["o1"] == 1
    ex.stop()


async def test_timeout_retries_then_fails_retryable(settings, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    s = _fast(settings, step_timeout_s=0.05, step_max_attempts=2)

    async def slow(ctx: StepContext) -> None:
        await asyncio.sleep(5)

    hr = ScriptedHandler(R, {"o1": slow})
    job = queue.create_job(new_job(s, project, {"resolve": ["o1"]}))
    ex = make_executor(s, db, [hr])
    await ex.run_until_idle()
    step = queue.steps(job.job_id)[0]
    assert step.state is StepState.FAILED_RETRYABLE
    assert step.attempt == 2
    assert hr.calls["o1"] == 2
    ex.stop()


async def test_output_reused(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    hr = ScriptedHandler(R)
    j1 = queue.create_job(new_job(fast, project, {"resolve": ["o1"]}))
    ex = make_executor(fast, db, [hr])
    await ex.run_until_idle()
    j2 = queue.create_job(new_job(fast, project, {"resolve": ["o1", "o2"]}))
    await ex.run_until_idle()
    assert dict(hr.calls) == {"o1": 1, "o2": 1}
    reused = queue.steps(j2.job_id)[0]
    assert reused.state is StepState.SKIPPED
    assert reused.skip_reason is SkipReason.OUTPUT_REUSED
    assert reused.output_ref == "out:o1"
    assert queue.get_job(project.project_id, j1.job_id).state is JobState.COMPLETED
    assert queue.get_job(project.project_id, j2.job_id).state is JobState.COMPLETED
    ex.stop()


async def test_output_not_reused_across_projects(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    """Spec 7.1: project-scoped outputs are never shared between projects."""
    other = add_project(db)
    hr = ScriptedHandler(R)
    j1 = queue.create_job(new_job(fast, project, {"resolve": ["o1"]}))
    ex = make_executor(fast, db, [hr])
    await ex.run_until_idle()  # project A's step succeeds first
    j2 = queue.create_job(new_job(fast, other, {"resolve": ["o1"]}))
    assert j1.snapshot_hash == j2.snapshot_hash
    await ex.run_until_idle()
    assert hr.calls["o1"] == 2
    step = queue.steps(j2.job_id)[0]
    assert step.state is StepState.SUCCEEDED
    assert step.skip_reason is None
    ex.stop()


async def test_commit_runs_in_step_transaction(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    """A failing commit rolls back its own writes and the `succeeded` update together (6.4)."""

    class Committing(ScriptedHandler):
        async def run(self, ctx: StepContext):  # type: ignore[no-untyped-def]
            from refcollector.contracts.ports import StepResult

            self.calls[ctx.step.subject_id] += 1

            def commit(store) -> str:  # type: ignore[no-untyped-def]
                assert db.in_transaction
                db.execute(
                    "INSERT INTO job_occurrence (job_id, occurrence_id, project_id) VALUES (?,?,?)",
                    (ctx.job.job_id, ctx.step.subject_id, ctx.job.project_id),
                )
                if ctx.step.subject_id == "bad":
                    raise InvalidInput("commit failed")
                return "ok"

            return StepResult(commit=commit)

    h = Committing(R)
    job = queue.create_job(new_job(fast, project, {"resolve": ["good", "bad"]}))
    ex = make_executor(fast, db, [h])
    await ex.run_until_idle()
    assert step_states(queue, job.job_id) == {"good": "succeeded", "bad": "failed_permanent"}
    rows = db.query("SELECT occurrence_id FROM job_occurrence")
    assert [r[0] for r in rows] == ["good"]
    ex.stop()


async def test_missing_handler_fails_permanent(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    job = queue.create_job(new_job(fast, project, {"discover_access": ["w1"]}))
    ex = make_executor(fast, db, [ScriptedHandler(R)])
    await ex.run_until_idle()
    assert step_states(queue, job.job_id) == {"w1": "failed_permanent"}
    assert queue.get_job(project.project_id, job.job_id).state is JobState.FAILED
    ex.stop()


# ---------- background thread ----------


def _wait_for(pred, timeout: float = 10.0) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_background_thread_and_client_takeover(fast, db, queue, project) -> None:  # type: ignore[no-untyped-def]
    a = make_executor(fast, db, [ScriptedHandler(R)])
    hb = ScriptedHandler(R)
    b = make_executor(fast, db, [hb])
    assert a.try_acquire()
    b.start()  # client: polls for the lock
    time.sleep(0.15)
    assert not b.is_owner
    a.stop()
    job = queue.create_job(new_job(fast, project, {"resolve": ["o1"]}))
    assert _wait_for(lambda: queue.get_job(project.project_id, job.job_id).state is JobState.COMPLETED)
    assert b.is_owner
    assert hb.calls["o1"] == 1
    b.stop()
    assert not b.is_owner
