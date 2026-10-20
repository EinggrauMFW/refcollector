"""Shared helpers for executor tests: project row, jobs, a scripted planner and handlers."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Callable
from typing import Any

from refcollector import ids
from refcollector.config import Limits, Settings
from refcollector.contracts.ports import StepContext, StepResult, StepSpec, snapshot_hash
from refcollector.domain.enums import Channel, Origin, StepType
from refcollector.domain.models import Job, Project, Provenance
from refcollector.executor.loop import Executor
from refcollector.executor.queue import SqliteJobQueue
from refcollector.executor.snapshot import build_snapshot
from refcollector.storage.db import Database


def fast_settings(settings: Settings, **limits: Any) -> Settings:
    base = {"executor_poll_s": 0.01, "client_lock_poll_s": 0.05, "step_timeout_s": 5.0}
    base.update(limits)
    return settings.model_copy(update={"limits": Limits(**base)})


def add_project(db: Database, project_id: str | None = None) -> Project:
    project = Project(project_id=project_id or ids.new_id("project"), name="p")
    with db.transaction():
        db.execute(
            "INSERT INTO project (id, name, revision, created_at, updated_at, data) VALUES (?,?,?,?,?,?)",
            (
                project.project_id,
                project.name,
                0,
                project.created_at.isoformat(),
                project.updated_at.isoformat(),
                project.model_dump_json(),
            ),
        )
    return project


def new_job(
    settings: Settings,
    project: Project,
    items: dict[str, list[str]],
    stages: list[StepType] | None = None,
) -> Job:
    """`items` maps stage value -> subject ids the planner creates for that stage."""

    snap = build_snapshot(settings, project)
    return Job(
        job_id=ids.new_id("job"),
        project_id=project.project_id,
        stages=stages or [StepType(s) for s in items],
        scope={"items": items},
        snapshot=snap,
        snapshot_hash=snapshot_hash(snap),
        provenance=Provenance(principal="tester", channel=Channel.CLI, origin=Origin.INTERACTIVE),
    )


class ScopePlanner:
    """Plans one step per subject id in job.scope["items"][stage]; fingerprint = subject id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, StepType]] = []

    def plan(self, store: Any, job: Job, stage: StepType) -> list[StepSpec]:
        self.calls.append((job.job_id, stage))
        return [
            StepSpec(step_type=stage, subject_id=s, input_fingerprint=f"fp:{s}")
            for s in job.scope["items"].get(stage.value, [])
        ]


Behaviour = Callable[[StepContext], Any]


class ScriptedHandler:
    """Async handler. `script[subject_id]` is an async/sync callable run before the result;
    it may raise. Calls are counted per subject; ctx objects are recorded."""

    def __init__(self, step_type: StepType, script: dict[str, Behaviour] | None = None) -> None:
        self.step_type = step_type
        self.script = script or {}
        self.calls: Counter[str] = Counter()
        self.contexts: list[StepContext] = []
        self.committed: list[str] = []

    async def run(self, ctx: StepContext) -> StepResult:
        sid = ctx.step.subject_id
        self.calls[sid] += 1
        self.contexts.append(ctx)
        behaviour = self.script.get(sid)
        if behaviour is not None:
            out = behaviour(ctx)
            if asyncio.iscoroutine(out):
                await out

        def commit(store: Any) -> str:
            self.committed.append(sid)
            return f"out:{sid}"

        return StepResult(commit=commit)


def make_executor(
    settings: Settings,
    db: Database,
    handlers: list[ScriptedHandler],
    planner: ScopePlanner | None = None,
    store: Any = None,
    **knobs: Any,
) -> Executor:
    knobs.setdefault("backoff_base_s", 0.01)
    return Executor(
        settings,
        db,
        store if store is not None else object(),
        {h.step_type: h for h in handlers},
        planner or ScopePlanner(),
        **knobs,
    )


def step_states(queue: SqliteJobQueue, job_id: str) -> dict[str, str]:
    return {s.subject_id: s.state.value for s in queue.steps(job_id)}


def audit_types(db: Database) -> list[str]:
    return [r["type"] for r in db.query("SELECT type FROM audit_event ORDER BY created_at")]


def step_row(db: Database, step_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM step WHERE id = ?", (step_id,))
    assert row is not None
    return {**dict(row), "data": json.loads(row["data"])}
