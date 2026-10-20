"""Real CLI entry and MCP handler smoke tests through the shared root."""

import asyncio
import json
import re

from typer.testing import CliRunner

from refcollector.cli.main import app as cli
from refcollector.config import save_settings
from refcollector.mcp.tools import call_tool
from tests.unit.pipeline.conftest import TEXT


def test_r01_r14_real_cli_full_pasted_flow_without_root_patch(app, monkeypatch):
    save_settings(app.settings)
    monkeypatch.setenv("RC_HOME", str(app.settings.rc_home))
    runner = CliRunner()
    created = runner.invoke(cli, ["project", "create", "cli-smoke"])
    assert created.exit_code == 0, created.output
    pid = re.search(r"prj_[A-Z0-9]+", created.output)[0]
    imported = runner.invoke(cli, ["--project", pid, "import", "--text", TEXT])
    assert imported.exit_code == 0, imported.output
    sid = re.search(r"src_[A-Z0-9]+", imported.output)[0]
    result = runner.invoke(cli, ["--project", pid, "run", "--source", sid, "--wait"])
    assert result.exit_code == 0 and "completed" in result.output, result.output
    listed = runner.invoke(cli, ["--project", pid, "list", "--json"])
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.output)
    assert len(payload["occurrences"]) == 1
    assert payload["occurrences"][0]["identity"]["state"] == "accepted_auto"


def test_r14_real_mcp_handler_full_pasted_flow(app):
    from refcollector.contracts.api import CallContext
    from refcollector.domain.enums import Channel, Scope

    def mcp_context():
        return CallContext(principal="mcp", channel=Channel.MCP, scopes=frozenset(Scope), request_id="mcp")

    async def exercise():
        async def call(tool, **arguments):
            result = await call_tool(app.service, mcp_context, tool, arguments)
            assert not result.is_error, result.content
            return result.structured_content

        project = await call("create_project", name="mcp-smoke", idempotency_key="mcp-project")
        pid = project["project_id"]
        source = await call(
            "import_source",
            project_id=pid,
            input={"type": "text", "text": TEXT},
            idempotency_key="mcp-source",
        )
        started = await call(
            "start_job",
            project_id=pid,
            stages=["extract", "resolve", "discover_access"],
            scope={"source_ids": [source["source_id"]]},
            idempotency_key="mcp-start",
        )
        await app.executor.run_until_idle()
        job = await call("get_job", project_id=pid, job_id=started["job_id"])
        assert job["state"] == "completed" and job["results"]["occurrence_count"] == 1
        rows = await call("list_references", project_id=pid, filter={"job_id": started["job_id"]})
        assert rows["occurrences"][0]["identity"]["state"] == "accepted_auto"

    asyncio.run(exercise())
