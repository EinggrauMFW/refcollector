"""WP-02: JobQueue: job/step persistence and state machines. Spec 6.3.

The `data` column holds the full model JSON; the other columns are copies kept in sync by
`put_job` / `put_step`. Every write goes through those two helpers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from refcollector import ids
from refcollector.contracts.ports import StepSpec
from refcollector.domain.enums import TERMINAL_JOB_STATES, JobState, StepState, StepType
from refcollector.domain.models import Job, Step, utcnow
from refcollector.errors import InvalidState, NotFound
from refcollector.storage.db import Database

Action = Literal["pause", "resume", "cancel", "retry_failed"]


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


class SqliteJobQueue:
    """Implements contracts.ports.JobQueue over storage.db.Database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- writes ----

    def put_job(self, job: Job) -> Job:
        job.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO job (id, project_id, state, current_stage, snapshot_hash, created_at, updated_at,"
            " data) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,"
            " current_stage=excluded.current_stage, updated_at=excluded.updated_at, data=excluded.data",
            (
                job.job_id,
                job.project_id,
                job.state.value,
                job.current_stage,
                job.snapshot_hash,
                _iso(job.created_at),
                _iso(job.updated_at),
                job.model_dump_json(),
            ),
        )
        return job

    def put_step(self, step: Step) -> Step:
        step.updated_at = utcnow()
        self.db.execute(
            "INSERT OR REPLACE INTO step (id, job_id, project_id, step_type, subject_id, state, attempt,"
            " max_attempts, executor_id, not_before, input_fingerprint, snapshot_hash, output_ref,"
            " created_at, updated_at, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                step.step_id,
                step.job_id,
                step.project_id,
                step.step_type.value,
                step.subject_id,
                step.state.value,
                step.attempt,
                step.max_attempts,
                step.executor_id,
                _iso(step.not_before),
                step.input_fingerprint,
                step.snapshot_hash,
                step.output_ref,
                _iso(step.created_at),
                _iso(step.updated_at),
                step.model_dump_json(),
            ),
        )
        return step

    def create_job(self, job: Job) -> Job:
        job = job.model_copy(update={"state": JobState.QUEUED, "current_stage": 0})
        with self.db.transaction():
            return self.put_job(job)

    def add_steps(self, job: Job, specs: Sequence[StepSpec], *, max_attempts: int) -> list[Step]:
        steps = [
            Step(
                step_id=ids.new_id("step"),
                job_id=job.job_id,
                project_id=job.project_id,
                step_type=spec.step_type,
                subject_id=spec.subject_id,
                input_fingerprint=spec.input_fingerprint,
                snapshot_hash=job.snapshot_hash,
                max_attempts=max_attempts,
                params=dict(spec.params),
            )
            for spec in specs
        ]
        with self.db.transaction():
            for step in steps:
                self.put_step(step)
        return steps

    def set_job_state(self, job_id: str, state: JobState) -> Job:
        with self.db.transaction():
            job = self._job(job_id)
            job.state = state
            return self.put_job(job)

    def set_step_state(self, step_id: str, state: StepState, **fields: Any) -> Step:
        with self.db.transaction():
            step = self.step(step_id)
            for key, value in {"state": state, **fields}.items():
                setattr(step, key, value)
            return self.put_step(step)

    # ---- reads ----

    def _job(self, job_id: str) -> Job:
        row = self.db.query_one("SELECT data FROM job WHERE id = ?", (job_id,))
        if row is None:
            raise NotFound(f"job {job_id} not found")
        return Job.model_validate_json(row["data"])

    def get_job(self, project_id: str, job_id: str) -> Job:
        job = self._job(job_id)
        if job.project_id != project_id:
            raise NotFound(f"job {job_id} not found")
        return job

    def list_jobs(self, project_id: str, *, limit: int = 10) -> list[Job]:
        rows = self.db.query(
            "SELECT data FROM job WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (project_id, limit),
        )
        return [Job.model_validate_json(r["data"]) for r in rows]

    def jobs_in(self, states: Sequence[JobState]) -> list[Job]:
        marks = ",".join("?" * len(states))
        rows = self.db.query(
            f"SELECT data FROM job WHERE state IN ({marks}) ORDER BY created_at, id",  # noqa: S608
            [s.value for s in states],
        )
        return [Job.model_validate_json(r["data"]) for r in rows]

    def step(self, step_id: str) -> Step:
        row = self.db.query_one("SELECT data FROM step WHERE id = ?", (step_id,))
        if row is None:
            raise NotFound(f"step {step_id} not found")
        return Step.model_validate_json(row["data"])

    def steps(self, job_id: str, *, state: str | None = None) -> list[Step]:
        sql = "SELECT data FROM step WHERE job_id = ?"
        params: list[Any] = [job_id]
        if state is not None:
            sql += " AND state = ?"
            params.append(state)
        rows = self.db.query(sql + " ORDER BY created_at, id", params)
        return [Step.model_validate_json(r["data"]) for r in rows]

    def stage_counts(self, job_id: str) -> dict[StepType, dict[str, int]]:
        out: dict[StepType, dict[str, int]] = {}
        for r in self.db.query(
            "SELECT step_type, state, COUNT(*) FROM step WHERE job_id = ? GROUP BY step_type, state",
            (job_id,),
        ):
            out.setdefault(StepType(r[0]), {})[r[1]] = r[2]
        return out

    def _count(self, job_id: str, *states: StepState) -> int:
        marks = ",".join("?" * len(states))
        row = self.db.query_one(
            f"SELECT COUNT(*) FROM step WHERE job_id = ? AND state IN ({marks})",  # noqa: S608
            [job_id, *(s.value for s in states)],
        )
        return row[0] if row else 0

    # ---- control (6.3, 6.5) ----

    def request(self, project_id: str, job_id: str, action: Action) -> Job:
        with self.db.transaction(immediate=True):
            job = self.get_job(project_id, job_id)
            if action == "pause":
                self._pause(job)
            elif action == "resume":
                self._resume(job)
            elif action == "cancel":
                self._cancel(job)
            elif action == "retry_failed":
                self._retry_failed(job)
            else:
                raise InvalidState(f"unknown action {action}")
            return self.put_job(job)

    def _invalid(self, job: Job, action: str) -> InvalidState:
        return InvalidState(
            f"cannot {action} a job in state {job.state.value}",
            details={"job_id": job.job_id, "state": job.state.value},
        )

    def _pause(self, job: Job) -> None:
        if job.state not in (JobState.QUEUED, JobState.RUNNING):
            raise self._invalid(job, "pause")
        running = self._count(job.job_id, StepState.RUNNING)
        job.state = JobState.PAUSING if running else JobState.PAUSED

    def _resume(self, job: Job) -> None:
        if job.state is JobState.PAUSED:
            job.state = JobState.QUEUED
        elif job.state is JobState.PAUSING:  # pause not finished yet: just keep running
            job.state = JobState.RUNNING
        else:
            raise self._invalid(job, "resume")

    def _cancel(self, job: Job) -> None:
        if job.state not in (JobState.QUEUED, JobState.RUNNING, JobState.PAUSING, JobState.PAUSED):
            raise self._invalid(job, "cancel")
        self.cancel_waiting_steps(job.job_id)
        running = self._count(job.job_id, StepState.RUNNING)
        job.state = JobState.CANCELLING if running else JobState.CANCELLED

    def cancel_waiting_steps(self, job_id: str) -> None:
        for step in self.steps(job_id):
            if step.state in (StepState.QUEUED, StepState.RETRY_WAIT):
                step.state = StepState.CANCELLED
                step.not_before = None
                self.put_step(step)

    def _retry_failed(self, job: Job) -> None:
        if job.state in (JobState.CANCELLING, JobState.CANCELLED):
            raise self._invalid(job, "retry_failed")
        failed = self.steps(job.job_id, state=StepState.FAILED_RETRYABLE.value)
        if not failed:
            raise InvalidState("no failed_retryable steps to retry", details={"job_id": job.job_id})
        for step in failed:
            step.state = StepState.QUEUED
            step.attempt = 0
            step.not_before = None
            self.put_step(step)
        if job.state in TERMINAL_JOB_STATES:
            job.state = JobState.QUEUED
