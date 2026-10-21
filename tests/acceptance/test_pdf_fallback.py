"""Public CLI and SDK flow with only external HTTP replaced."""

import asyncio
import hashlib

import httpx
from mcp import Client

from refcollector.cli import main
from refcollector.config import Settings, save_settings
from refcollector.domain.enums import Scope
from refcollector.mcp.server import build_server
from tests.acceptance.test_cli_workflow import Commands
from tests.unit.pipeline.conftest import create, refs, run
from tests.unit.pipeline.test_acceptance import enable_download


def test_real_cli_configures_and_uses_fallback_through_default_root(tmp_path, monkeypatch, offline):
    original = offline.response

    def response(request):
        if request.url.host == "8.8.8.8":
            return httpx.Response(
                200,
                text='<embed id="pdf" src="https://8.8.4.4/paper.pdf">',
                headers={"Content-Type": "text/html"},
            )
        if request.url.host == "8.8.4.4":
            return httpx.Response(200, content=offline.body, headers={"Content-Type": "application/pdf"})
        return original(request)

    monkeypatch.setattr(offline, "response", response)
    monkeypatch.setattr("refcollector.pipeline.clients.ProviderHttpClient", offline.http)
    home = tmp_path / "home"
    save_settings(
        Settings(rc_home=home, email="test@example.org", enabled_providers=["crossref", "unpaywall"])
    )
    monkeypatch.setenv("RC_HOME", str(home))
    monkeypatch.delenv("RC_PROJECT", raising=False)
    main.set_app_factory(None)
    cli = Commands()
    try:
        cli.run("config", "pdf-fallback", "https://8.8.8.8/{doi}")
        assert cli.read("config", "show")["pdf_fallback_url"] == "https://8.8.8.8/{doi}"
        cli.create()
        sid = cli.import_text()
        cli.job("run", "--source", sid)
        cli.run("config", "network", "retrieval")
        cli.run("config", "downloads", "on")
        cli.job("download", "--source", sid, "--version-policy", "any_eligible")
        row = cli.rows()[0]
        detail = cli.show(row["occurrence_id"])
        assert detail["occurrence"]["retrieval"] == "verified"
        assert detail["occurrence"]["identity"]["state"] == "accepted_auto"
        file = detail["files"][0]
        assert file["source_url"] == "https://8.8.4.4/paper.pdf"
        assert file["sha256"] == hashlib.sha256(offline.body).hexdigest()
        cli.run("config", "pdf-fallback", "off")
        assert cli.read("config", "show")["pdf_fallback_url"] is None
    finally:
        main._close_app()
        main.set_app_factory(None)


def test_sdk_uses_trusted_snapshot_but_cannot_inject_resolver(app, fallback):
    app.settings.pdf_fallback_url = "https://fallback.example/{doi}"
    pid, sid = create(app)
    enable_download(app, pid)
    run(app, pid, sid)
    oid = refs(app, pid)[0].occurrence_id

    async def exercise():
        async with Client(build_server(app.service, scopes=frozenset(Scope))) as client:
            arguments = {
                "project_id": pid,
                "scope": {"occurrence_ids": [oid]},
                "version_policy": "any_eligible",
                "idempotency_key": "sdk-pdf-fallback",
            }
            denied = await client.call_tool(
                "download_files", {**arguments, "pdf_fallback_url": "https://attacker.example/{doi}"}
            )
            assert denied.is_error and denied.structured_content["error"]["code"] == "INVALID_INPUT"
            assert fallback["calls"] == []
            started = await client.call_tool("download_files", arguments)
            assert not started.is_error, started.structured_content
            await app.executor.run_until_idle()
            job = await client.call_tool(
                "get_job", {"project_id": pid, "job_id": started.structured_content["job_id"]}
            )
            assert job.structured_content["state"] == "completed"
            reference = await client.call_tool(
                "get_reference", {"project_id": pid, "occurrence_id": oid, "sections": ["files"]}
            )
            assert reference.structured_content["occurrence"]["retrieval"] == "verified"
            assert reference.structured_content["files"][0]["source_url"] == "https://pdf.example/paper.pdf"

    asyncio.run(exercise())
