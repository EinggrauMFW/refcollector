"""Manual native-TTY W-18/W-19/W-30 proof; not collected by pytest.

Run: python -m tests.acceptance.tty_approval_smoke [mcp|cli], then press ONE `a` when
the real CLI review asks. Do not pipe input. The optional argument picks the surface
for the final edit that stales the approval (default mcp; cli runs `refcollector edit`).
Public service calls prepare and read data; only the production CLI records approval. The final edit uses the
actual MCP SDK Client. HTTP responses alone are faked; live sockets/DNS fail.
Public JSON export supplements get_reference, whose occurrence summary does
not expose the current approval. No store/SQL setup or assertions are used.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from mcp import Client

from refcollector.bootstrap import open_app
from refcollector.cli import main as cli_main
from refcollector.config import Settings, save_settings
from refcollector.contracts import api
from refcollector.mcp.server import build_server
from tests.acceptance.test_process_recovery import CHILD
from tests.unit.pipeline.conftest import TEXT, Offline, context


def drain(handle):
    asyncio.run(asyncio.wait_for(handle.executor.run_until_idle(), 20))


def reference(handle, pid, oid):
    return handle.service.get_reference(
        context(), api.GetReferenceIn(project_id=pid, occurrence_id=oid, sections=["history"])
    ).model_dump(mode="json")


def rows(handle, pid, sid):
    return handle.service.list_references(
        context(),
        api.ListReferencesIn(project_id=pid, filter=api.ReferenceFilter(source_id=sid), limit=100),
    ).occurrences


def import_text(handle, pid, text, key):
    return handle.service.import_source(
        context(),
        api.ImportSourceIn(project_id=pid, input={"type": "text", "text": text}, idempotency_key=key),
    ).source_id


def job(handle, pid, sid, stages, key, *, wait=True):
    started = handle.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid, stages=stages, scope=api.SourceScope(source_ids=[sid]), idempotency_key=key
        ),
    )
    if wait:
        drain(handle)
        result = handle.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=started.job_id))
        assert result.state == "completed", result
    return started.job_id


def update(handle, pid, change, key):
    return handle.service.update_reference(
        context(), api.UpdateReferenceIn(project_id=pid, change=change, idempotency_key=key)
    )


def exported_occurrence(handle, pid, oid, key):
    revision = handle.service.get_project(context(), api.GetProjectIn(project_id=pid)).revision
    started = handle.service.create_export(
        context(),
        api.CreateExportIn(
            project_id=pid,
            project_revision=revision,
            formats=["json"],
            selection="complete_audit",
            idempotency_key=key,
        ),
    )
    drain(handle)
    result = handle.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=started.job_id))
    assert result.state == "completed", result
    artifacts = handle.service.list_artifacts(context(), api.ListArtifactsIn(project_id=pid)).artifacts
    descriptor = next(
        a for a in artifacts if a.artifact_id in result.results.artifact_ids and a.name == "references.json"
    )
    chunks, offset = [], 0
    while True:
        part = handle.service.read_artifact(
            context(),
            api.ReadArtifactIn(
                project_id=pid, artifact_id=descriptor.artifact_id, offset=offset, length=65536
            ),
        )
        assert part.text is not None
        chunks.append(part.text)
        offset += len(part.text.encode("utf-8"))
        if part.eof:
            break
        assert part.text
    return next(
        row["occurrence"] for row in json.loads("".join(chunks)) if row["occurrence"]["occurrence_id"] == oid
    )


def kill_during_resolve(settings, ready):
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(settings.rc_home), str(ready)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not ready.exists():
            raise AssertionError(
                process.communicate(timeout=2) if process.poll() is not None else "child ready timeout"
            )
        process.kill()
        process.communicate(timeout=10)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


async def mcp_edit(handle, pid, oid, revision):
    async with Client(build_server(handle.service)) as client:
        result = await asyncio.wait_for(
            client.call_tool(
                "update_reference",
                {
                    "project_id": pid,
                    "change": {
                        "type": "edit_text",
                        "occurrence_id": oid,
                        "text": "After approval: " + TEXT,
                        "expected_revision": revision,
                    },
                    "idempotency_key": "mcp-edit-stales-approval",
                },
            ),
            20,
        )
        assert not result.is_error, result.structured_content


def cli_edit(pid, oid, revision):
    cli_main.set_app_factory(None)
    try:
        cli_main.app(
            ["--project", pid, "edit", oid, "--text", "After approval: " + TEXT]
            + ["--expected-revision", str(revision), "--idempotency-key", "cli-edit-stales-approval"],
            standalone_mode=False,
        )
    finally:
        cli_main._close_app()


def exercise(folder, edit_via="mcp"):
    assert sys.stdin.isatty() and sys.stdout.isatty(), "Native stdin/stdout TTY required"
    settings = Settings(
        rc_home=folder / "home",
        email="approval@example.org",
        allow_folders=[folder],
        enabled_providers=["crossref", "unpaywall"],
    )
    save_settings(settings)
    os.environ["RC_HOME"] = str(settings.rc_home)
    os.environ.pop("RC_PROJECT", None)
    handle = open_app(settings)
    try:
        pid = handle.service.create_project(
            context(), api.CreateProjectIn(name="Native TTY smoke", idempotency_key="tty-project")
        ).project_id
        sid = import_text(handle, pid, TEXT, "tty-source")
        job(handle, pid, sid, ["extract", "resolve"], "tty-resolve")
        row = rows(handle, pid, sid)[0]
        oid = row["occurrence_id"]
        update(
            handle,
            pid,
            api.EditText(
                type="edit_text",
                occurrence_id=oid,
                text="Corrected citation: " + TEXT,
                expected_revision=row["revision"],
            ),
            "tty-edit",
        )
        row = reference(handle, pid, oid)["occurrence"]
        update(
            handle,
            pid,
            api.SetFieldLocks(
                type="set_field_locks",
                occurrence_id=oid,
                locks={"title": True},
                expected_revision=row["revision"],
            ),
            "tty-lock",
        )
        prepared = reference(handle, pid, oid)
        assert prepared["locks"]["title"] and prepared["occurrence"]["edited_text"]
    finally:
        handle.close()

    print("NATIVE_TTY_APPROVAL_READY: press a once at the review prompt", flush=True)
    cli_main.set_app_factory(None)
    try:
        cli_main.app(["--project", pid, "review", "--all"], standalone_mode=False)
    finally:
        cli_main._close_app()

    handle = open_app(settings)
    try:
        approved_ref = reference(handle, pid, oid)
        assert approved_ref["occurrence"]["identity"]["state"] == "accepted_user"
        decisions = [d for d in approved_ref["history"] if d["kind"] == "decision" and d.get("approval")]
        assert len(decisions) == 1
        decision = decisions[0]
        approval = decision["approval"]
        assert approval["surface"] == "cli_tty"
        assert approval["occurrence_revision"] == approved_ref["occurrence"]["revision"]
        assert decision["provenance"]["channel"] == "cli_tty_review"
        assert decision["provenance"]["origin"] == "interactive"
        approved = exported_occurrence(handle, pid, oid, "approved-json")
        assert approved["approval"] == approval
        pending = import_text(
            handle,
            pid,
            TEXT.replace("10.1234/waves", "10.1234/first-recovery")
            + "\n\n"
            + TEXT.replace("10.1234/waves", "10.1234/second-recovery"),
            "recovery-source",
        )
        job(handle, pid, pending, ["extract"], "recovery-extract")
        assert len(rows(handle, pid, pending)) == 2
        recovery_job = job(handle, pid, pending, ["resolve"], "recovery-resolve", wait=False)
    finally:
        handle.close()
    kill_during_resolve(settings, folder / "child-ready")

    handle = open_app(settings)
    try:
        drain(handle)
        resumed = handle.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=recovery_job))
        assert resumed.state == "completed", resumed
        after = reference(handle, pid, oid)
        assert {k: v for k, v in after.items() if k != "meta"} == {
            k: v for k, v in approved_ref.items() if k != "meta"
        }
        assert after["locks"]["title"]
        assert exported_occurrence(handle, pid, oid, "resumed-json") == approved
        if edit_via == "cli":
            cli_edit(pid, oid, after["occurrence"]["revision"])
        else:
            asyncio.run(mcp_edit(handle, pid, oid, after["occurrence"]["revision"]))
        stale = reference(handle, pid, oid)
        assert stale["occurrence"]["identity"]["state"] == "needs_review"
        assert "APPROVAL_STALE" in stale["occurrence"]["identity"]["reasons"]
        assert decision in stale["history"]
        assert any(e.get("type") == "APPROVAL_STALE" for e in stale["history"])
        assert exported_occurrence(handle, pid, oid, "stale-json")["approval"] is None
        print(
            json.dumps(
                {
                    "W-18": "passed_native_cli_tty",
                    "W-19": "passed_cli_edit" if edit_via == "cli" else "passed_mcp_sdk_edit",
                    "W-30": "passed_killed_executor_restart",
                    "approval_surface": approval["surface"],
                    "approval_revision": approval["occurrence_revision"],
                }
            ),
            flush=True,
        )
    finally:
        handle.close()


def main():
    edit_via = sys.argv[1] if len(sys.argv) > 1 else "mcp"
    assert edit_via in ("mcp", "cli"), "argument must be mcp or cli"
    fixture = Offline()
    original_connect = socket.socket.connect

    def denied(*args, **kwargs):
        raise AssertionError("Live network and DNS forbidden in native TTY smoke")

    def connect(sock, address):
        if sys._getframe(1).f_code.co_name == "_fallback_socketpair":
            return original_connect(sock, address)
        return denied(sock, address)

    with tempfile.TemporaryDirectory(prefix="refcollector-tty-") as temp:
        with (
            patch("refcollector.pipeline.clients.ProviderHttpClient", fixture.http),
            patch.object(socket.socket, "connect", connect),
            patch.object(socket, "getaddrinfo", denied),
        ):
            exercise(Path(temp), edit_via)
        assert all(client._client.is_closed for client in fixture.clients)


if __name__ == "__main__":
    main()
