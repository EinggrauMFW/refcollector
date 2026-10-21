"""Real process-loss proof; provider I/O uses deterministic HTTP transports."""

import asyncio
import hashlib
import subprocess
import sys
import time

import pytest
from mcp import Client
from typer.testing import CliRunner

from refcollector.bootstrap import open_app
from refcollector.cli import main as cli_main
from refcollector.config import save_settings
from refcollector.contracts import api
from refcollector.mcp.server import build_server
from tests.unit.pipeline.conftest import TEXT, context, create, refs, run, source
from tests.unit.pipeline.test_acceptance import download, enable_download

CHILD = r"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import unquote
import httpx
from refcollector.bootstrap import open_app
from refcollector.config import load_settings
from refcollector.providers.http import ProviderHttpClient
from tests.unit.pipeline.conftest import Offline
import refcollector.pipeline.clients as clients

fixture = Offline()
async def respond(request):
    if '10.1234/second-recovery' in unquote(str(request.url)):
        Path(sys.argv[2]).write_text('blocked', encoding='utf-8')
        await asyncio.Event().wait()
    return fixture.response(request)

def http(snapshot, db, limits):
    return ProviderHttpClient(snapshot, db, limits,
        transport=httpx.MockTransport(respond), secrets={})

clients.ProviderHttpClient = http
handle = open_app(load_settings(Path(sys.argv[1])))
try:
    asyncio.run(handle.executor.run_until_idle())
finally:
    handle.close()
"""


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_w29_real_kill_restarts_running_resolution_without_repeating_success_w31_files(
    app, offline, tmp_path, monkeypatch, surface
):
    offline.location = True
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    verified = refs(app, pid)[0]
    download(app, pid, [verified.occurrence_id])
    verified = app.store.occurrences.get(pid, verified.occurrence_id)
    files_before = app.store.work_files.list_for_work(pid, verified.work_id)
    assert verified.retrieval == "verified" and files_before

    pending_source = source(
        app,
        pid,
        TEXT.replace("10.1234/waves", "10.1234/first-recovery")
        + "\n"
        + TEXT.replace("10.1234/waves", "10.1234/second-recovery"),
    )
    run(app, pid, pending_source, stages=("extract",))
    pending = [row for row in refs(app, pid) if row.source_id == pending_source]
    assert len(pending) == 2
    started = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["resolve"],
            scope=api.OccurrenceScope(occurrence_ids=[row.occurrence_id for row in pending]),
            idempotency_key="crash-recovery-job",
        ),
    )
    settings = app.settings
    save_settings(settings)
    app.close()
    ready = tmp_path / "child-ready"
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(settings.rc_home), str(ready)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), (
            process.communicate(timeout=2) if process.poll() is not None else "child timeout"
        )
        process.kill()
        process.communicate(timeout=10)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    reopened = open_app(settings)
    try:
        before = reopened.executor.queue.steps(started.job_id)
        succeeded = [step for step in before if step.state == "succeeded"]
        interrupted = [step for step in before if step.state == "running"]
        assert len(succeeded) == len(interrupted) == 1
        checkpoint = succeeded[0].model_dump()
        if surface == "cli":
            monkeypatch.setenv("RC_HOME", str(settings.rc_home))
            cli_main.set_app_factory(None)
            result = CliRunner().invoke(
                cli_main.app,
                ["--project", pid, "run", "--source", pending_source, "--stages", "resolve", "--wait"],
            )
            assert result.exit_code == 0, (result.output, result.exception)
        else:
            reopened.start_executor()

            async def observe():
                async with Client(build_server(reopened.service)) as client:
                    for _ in range(200):
                        result = await client.call_tool(
                            "get_job", {"project_id": pid, "job_id": started.job_id}
                        )
                        assert not result.is_error, result.structured_content
                        if result.structured_content["state"] == "completed":
                            return
                        await asyncio.sleep(0.05)
                    raise AssertionError("MCP restart did not complete the interrupted job")

            asyncio.run(observe())
        assert reopened.executor.queue.get_job(pid, started.job_id).state == "completed"
        after = reopened.executor.queue.steps(started.job_id)
        assert (
            next(step for step in after if step.step_id == checkpoint["step_id"]).model_dump() == checkpoint
        )
        assert all(step.state == "succeeded" for step in after)
        assert reopened.store.occurrences.get(pid, verified.occurrence_id) == verified
        assert reopened.store.work_files.list_for_work(pid, verified.work_id) == files_before
        for file in files_before:
            path = reopened.store.files.path_for(pid, file.sha256)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == file.sha256
    finally:
        reopened.close()
