"""Lock ownership (6.1) and AT-28: two OS processes, one executor, no step runs twice."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from refcollector.domain.enums import JobState
from refcollector.executor.lock import ExecutorLock
from refcollector.executor.queue import SqliteJobQueue
from tests.unit.executor.helpers import add_project, new_job

ROOT = Path(__file__).resolve().parents[3]


def test_lock_is_exclusive_and_recorded(settings, db) -> None:  # type: ignore[no-untyped-def]
    a, b = ExecutorLock(settings, db), ExecutorLock(settings, db)
    assert a.executor_id.startswith("exe_") and a.executor_id != b.executor_id
    assert a.try_acquire()
    assert a.try_acquire()  # idempotent for the holder
    assert not b.try_acquire()
    row = db.query_one("SELECT pid, stopped_at FROM executor WHERE id = ?", (a.executor_id,))
    assert row is not None and row["pid"] == os.getpid() and row["stopped_at"] is None
    a.release()
    assert db.query_one("SELECT stopped_at FROM executor WHERE id = ?", (a.executor_id,))["stopped_at"]
    assert b.try_acquire()
    b.release()


def test_at28_two_processes_one_executor(settings, db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    project = add_project(db)
    queue = SqliteJobQueue(db)
    subjects = [f"o{i}" for i in range(6)]
    job = queue.create_job(new_job(settings, project, {"resolve": subjects}))
    log = tmp_path / "runs.log"
    env = {**os.environ, "RC_HOME": str(settings.rc_home), "PYTHONPATH": str(ROOT)}
    cmd = [sys.executable, "-m", "tests.unit.executor.proc_worker", str(log)]
    procs = [
        subprocess.Popen(  # noqa: S603
            cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        for _ in range(2)
    ]
    try:
        for p in procs:
            assert p.stdout is not None and p.stdout.readline().strip() == "ready"
        for p in procs:  # release both at once
            assert p.stdin is not None
            p.stdin.write("go\n")
            p.stdin.flush()
        results = []
        for p in procs:
            assert p.stdout is not None
            results.append(p.stdout.readline().strip())
        assert sorted(results) == ["client", "owner"]
    finally:
        for p in procs:
            if p.stdin and not p.stdin.closed:
                p.stdin.write("exit\n")
                p.stdin.close()
            p.wait(timeout=30)
    runs = log.read_text(encoding="utf-8").split()
    assert sorted(runs) == sorted(subjects)  # every step once, none twice
    assert queue.get_job(project.project_id, job.job_id).state is JobState.COMPLETED
