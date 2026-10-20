"""WP-02: executor loop: recover, plan stages, claim (BEGIN IMMEDIATE), run handlers, commit. Spec 6.2-6.4.

One asyncio loop in a background thread (D-08). All SQLite work is synchronous and happens on
the loop thread in short transactions; no transaction spans an `await`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from refcollector import ids
from refcollector.config import Settings
from refcollector.contracts.ports import JobPlanner, StepContext, StepHandler, StepResult, Store
from refcollector.domain.enums import (
    TERMINAL_STEP_STATES,
    AuditEventType,
    Channel,
    JobState,
    Origin,
    SkipReason,
    StepState,
    StepType,
)
from refcollector.domain.models import AuditEvent, Job, Provenance, Step, utcnow
from refcollector.errors import Cancelled, InvalidState, RcError, TransientNetworkError
from refcollector.executor.lock import ExecutorLock
from refcollector.executor.queue import SqliteJobQueue
from refcollector.executor.retry import classify

log = logging.getLogger(__name__)

EXECUTOR_PROVENANCE = Provenance(principal="executor", channel=Channel.EXECUTOR, origin=Origin.AUTOMATION)
_FAILED = (StepState.FAILED_RETRYABLE, StepState.FAILED_PERMANENT)
_DONE_OK = (StepState.SUCCEEDED, StepState.SKIPPED)


def _error_payload(exc: BaseException, timeout_s: float) -> dict[str, Any]:
    if isinstance(exc, RcError):
        return exc.to_payload()
    if isinstance(exc, TimeoutError):
        return TransientNetworkError(f"step timed out after {timeout_s} s").to_payload()
    return {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}", "retryable": False}


class Executor:
    """Implements contracts.ports.Executor."""

    def __init__(
        self,
        settings: Settings,
        db: Any,
        store: Store | Any,
        handlers: Mapping[StepType, StepHandler],
        planner: JobPlanner,
        *,
        max_concurrency: int = 4,
        backoff_base_s: float = 1.0,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.limits = settings.limits
        self.db = db
        self.store = store
        self.handlers = dict(handlers)
        self.planner = planner
        self.queue = SqliteJobQueue(db)
        self.max_concurrency = max_concurrency
        self.backoff_base_s = backoff_base_s
        self.rng = rng
        self._lock = ExecutorLock(settings, db)
        self.executor_id = self._lock.executor_id
        self._tasks: set[asyncio.Task[None]] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- ownership (6.1) ----

    @property
    def is_owner(self) -> bool:
        return self._lock.held

    def try_acquire(self) -> bool:
        if self._lock.held:
            return True
        if not self._lock.try_acquire():
            return False
        self._recover()
        return True

    def _recover(self) -> None:
        """6.2 stale ownership + 6.3 start-up job transitions."""
        q = self.queue
        with self.db.transaction(immediate=True):
            for job in q.jobs_in([JobState.RUNNING, JobState.PAUSING, JobState.CANCELLING]):
                if job.state is JobState.CANCELLING:
                    for step in q.steps(job.job_id):
                        if step.state in (StepState.QUEUED, StepState.RETRY_WAIT, StepState.RUNNING):
                            step.state, step.not_before = StepState.CANCELLED, None
                            q.put_step(step)
                    job.state = JobState.CANCELLED
                else:
                    job.state = JobState.QUEUED if job.state is JobState.RUNNING else JobState.PAUSED
                q.put_job(job)
            rows = self.db.query(
                "SELECT data FROM step WHERE state = 'running' AND (executor_id IS NULL OR executor_id != ?)",
                (self.executor_id,),
            )
            for row in rows:
                step = Step.model_validate_json(row["data"])
                lost = step.executor_id
                step.state, step.executor_id = StepState.QUEUED, None
                q.put_step(step)
                self._audit(
                    step.project_id,
                    AuditEventType.RECOVERED_AFTER_EXECUTOR_LOSS,
                    step.step_id,
                    {"job_id": step.job_id, "lost_executor_id": lost, "executor_id": self.executor_id},
                )

    def _audit(
        self, project_id: str, type_: AuditEventType, subject_id: str, payload: dict[str, Any]
    ) -> None:
        row = self.db.query_one("SELECT revision FROM project WHERE id = ?", (project_id,))
        event = AuditEvent(
            event_id=ids.new_id("event"),
            project_id=project_id,
            type=type_,
            subject_id=subject_id,
            provenance=EXECUTOR_PROVENANCE,
            project_revision=row[0] if row else 0,
            payload=payload,
        )
        self.db.execute(
            "INSERT INTO audit_event (id, project_id, type, subject_id, project_revision, created_at, data)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                event.event_id,
                project_id,
                type_.value,
                subject_id,
                event.project_revision,
                event.created_at.isoformat(),
                event.model_dump_json(),
            ),
        )

    # ---- job advancement (6.3, D-22) ----

    def _advance_jobs(self) -> bool:
        changed = False
        active = [JobState.QUEUED, JobState.RUNNING, JobState.PAUSING, JobState.CANCELLING]
        for job in self.queue.jobs_in(active):
            with self.db.transaction(immediate=True):
                changed |= self._advance(self.queue.get_job(job.project_id, job.job_id))
        return changed

    def _advance(self, job: Job) -> bool:
        q = self.queue
        running = q.steps(job.job_id, state=StepState.RUNNING.value)
        if job.state is JobState.PAUSING:
            if running:
                return False
            job.state = JobState.PAUSED
            q.put_job(job)
            return True
        if job.state is JobState.CANCELLING:
            q.cancel_waiting_steps(job.job_id)
            if running:
                return False
            job.state = JobState.CANCELLED
            q.put_job(job)
            return True
        if job.state not in (JobState.QUEUED, JobState.RUNNING):
            return False
        changed = job.state is JobState.QUEUED
        job.state = JobState.RUNNING
        while True:
            steps = q.steps(job.job_id)
            if any(s.state not in TERMINAL_STEP_STATES for s in steps):
                break
            if not job.stages:
                self._finish(job, steps)
                break
            stage = job.stages[job.current_stage]
            if not any(s.step_type is stage for s in steps):
                try:
                    specs = self.planner.plan(self.store, job, stage)
                except Exception as exc:  # planner bug or bad scope: fail the job, keep the loop alive
                    log.exception("planning %s for %s failed", stage, job.job_id)
                    job.warnings.append(f"planning {stage.value} failed: {exc}")
                    job.state = JobState.FAILED
                    changed = True
                    break
                if specs:
                    q.add_steps(job, specs, max_attempts=self.limits.step_max_attempts)
                    changed = True
                    break
            if job.current_stage + 1 < len(job.stages):
                job.current_stage += 1
                changed = True
                continue
            self._finish(job, steps)
            changed = True
            break
        if changed:
            q.put_job(job)
        return changed

    @staticmethod
    def _finish(job: Job, steps: list[Step]) -> None:
        """Decision: any failed step -> completed_with_warnings, unless no step succeeded or was
        skipped -> failed. Handler warnings alone -> completed_with_warnings."""
        failed = sum(s.state in _FAILED for s in steps)
        ok = sum(s.state in _DONE_OK for s in steps)
        if failed and not ok:
            job.state = JobState.FAILED
        elif failed or job.warnings:
            job.state = JobState.COMPLETED_WITH_WARNINGS
        else:
            job.state = JobState.COMPLETED

    # ---- retry_wait -> queued ----

    def _wake_retry_waits(self) -> bool:
        now = utcnow()
        changed = False
        rows = self.db.query("SELECT data FROM step WHERE state = 'retry_wait'")
        for row in rows:
            step = Step.model_validate_json(row["data"])
            if step.not_before is None or step.not_before <= now:
                with self.db.transaction(immediate=True):
                    cur = self.db.execute(
                        "UPDATE step SET state = 'queued' WHERE id = ? AND state = 'retry_wait'",
                        (step.step_id,),
                    )
                    if cur.rowcount:
                        step.state, step.not_before = StepState.QUEUED, None
                        self.queue.put_step(step)
                        changed = True
        return changed

    def _next_wake(self) -> float | None:
        """Seconds until the earliest retry_wait step of an active job is due; None if none."""
        rows = self.db.query(
            "SELECT s.data FROM step s JOIN job j ON j.id = s.job_id"
            " WHERE s.state = 'retry_wait' AND j.state IN ('queued', 'running')"
        )
        if not rows:
            return None
        now = utcnow()
        due = [Step.model_validate_json(r[0]).not_before or now for r in rows]
        return max(0.0, (min(due) - now).total_seconds())

    # ---- claims (6.2) and output reuse (6.6) ----

    def _claim(self, n: int) -> list[Step]:
        claimed: list[Step] = []
        while len(claimed) < n:
            rows = self.db.query(
                "SELECT s.id FROM step s JOIN job j ON j.id = s.job_id"
                " WHERE s.state = 'queued' AND j.state = 'running'"
                " ORDER BY j.created_at, s.created_at, s.id LIMIT ?",
                (n - len(claimed),),
            )
            progress = False
            for row in rows:
                with self.db.transaction(immediate=True):
                    step = self.queue.step(row[0])
                    if self._reuse(step):
                        progress = True
                        continue
                    cur = self.db.execute(
                        "UPDATE step SET state = 'running', executor_id = ?, attempt = attempt + 1"
                        " WHERE id = ? AND state = 'queued'"
                        " AND job_id IN (SELECT id FROM job WHERE state = 'running')",
                        (self.executor_id, step.step_id),
                    )
                    if cur.rowcount == 1:
                        step.state, step.executor_id = StepState.RUNNING, self.executor_id
                        step.attempt += 1
                        self.queue.put_step(step)
                        claimed.append(step)
                        progress = True
            if not progress:
                break
        return claimed

    def _reuse(self, step: Step) -> bool:
        """6.6 output reuse, per project only: project-scoped outputs are never shared (7.1)."""
        row = self.db.query_one(
            "SELECT output_ref FROM step WHERE project_id = ? AND step_type = ? AND input_fingerprint = ?"
            " AND snapshot_hash = ? AND state = 'succeeded' AND id != ? LIMIT 1",
            (step.project_id, step.step_type.value, step.input_fingerprint, step.snapshot_hash, step.step_id),
        )
        if row is None:
            return False
        step.state, step.skip_reason, step.output_ref = StepState.SKIPPED, SkipReason.OUTPUT_REUSED, row[0]
        self.queue.put_step(step)
        return True

    # ---- running a step ----

    def _job_cancelled(self, job_id: str) -> bool:
        row = self.db.query_one("SELECT state FROM job WHERE id = ?", (job_id,))
        return row is not None and row[0] in (JobState.CANCELLING.value, JobState.CANCELLED.value)

    async def _run(self, step: Step) -> None:
        job = self.queue.get_job(step.project_id, step.job_id)
        ctx = StepContext(
            step=step, job=job, store=self.store, is_cancelled=lambda: self._job_cancelled(step.job_id)
        )
        handler = self.handlers.get(step.step_type)
        try:
            if handler is None:
                raise InvalidState(f"no handler for step type {step.step_type.value}")
            result = await asyncio.wait_for(handler.run(ctx), self.limits.step_timeout_s)
        except Exception as exc:
            self._fail(step, exc)
            return
        try:
            self._succeed(step, result)
        except Exception as exc:  # commit failed: its writes were rolled back with the step update
            self._fail(step, exc)

    def _succeed(self, step: Step, result: StepResult) -> None:
        with self.db.transaction(immediate=True):
            if self._job_cancelled(step.job_id):  # 6.4: uncommitted output is discarded
                step.state, step.last_error = StepState.CANCELLED, Cancelled("job cancelled").to_payload()
                self.queue.put_step(step)
                return
            step.output_ref = result.commit(self.store)
            step.state, step.last_error, step.not_before = StepState.SUCCEEDED, None, None
            self.queue.put_step(step)
            if result.warnings:
                job = self.queue.get_job(step.project_id, step.job_id)
                job.warnings.extend(result.warnings)
                self.queue.put_job(job)

    def _fail(self, step: Step, exc: BaseException) -> None:
        decision = classify(
            exc, step.attempt, step.max_attempts, self.limits, base_s=self.backoff_base_s, rng=self.rng
        )
        with self.db.transaction(immediate=True):
            state = StepState.CANCELLED if self._job_cancelled(step.job_id) else decision.state
            step.state = state
            step.last_error = _error_payload(exc, self.limits.step_timeout_s)
            step.not_before = (
                utcnow() + timedelta(seconds=decision.delay_s) if state is StepState.RETRY_WAIT else None
            )
            self.queue.put_step(step)

    # ---- loop ----

    def _cycle(self) -> bool:
        changed = self._wake_retry_waits()
        changed |= self._advance_jobs()
        for step in self._claim(self.max_concurrency - len(self._tasks)):
            task = asyncio.create_task(self._run(step))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            changed = True
        return changed

    async def run_until_idle(self) -> None:
        if not self.try_acquire():
            raise InvalidState("another process holds the executor lock")
        while True:
            changed = self._cycle()
            if self._tasks:
                await asyncio.wait(set(self._tasks), return_when=asyncio.FIRST_COMPLETED)
                continue
            if changed:
                continue
            wait = self._next_wake()
            if wait is None:
                return
            await asyncio.sleep(wait)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._main()), name="refcollector-executor", daemon=True
        )
        self._thread.start()

    async def _sleep(self, seconds: float) -> None:
        """Sleep, waking early when stop() is called."""
        await asyncio.to_thread(self._stop.wait, seconds)

    async def _main(self) -> None:
        poll = self.limits.executor_poll_s
        try:
            while not self._stop.is_set():
                try:
                    if not self.try_acquire():
                        await self._sleep(self.limits.client_lock_poll_s)
                        continue
                    self._cycle()
                except Exception:  # keep the executor alive (e.g. SQLITE_BUSY); retry next poll
                    log.exception("executor cycle failed")
                if self._tasks:
                    await asyncio.wait(set(self._tasks), timeout=poll, return_when=asyncio.FIRST_COMPLETED)
                else:
                    await self._sleep(poll)
        finally:
            # Interrupted steps stay `running` with this executor_id; the next owner recovers them.
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._lock.release()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                log.warning("executor thread did not stop within %s s", timeout)
                return
        self._lock.release()
