"""Offline SDK acceptance through the real composition root.

TTY approval (W-18/19) and process-kill recovery (W-29) are not claimed.
W-30 below checks edits/locks only; W-31 uses a graceful reopen.
Provider transport and DNS fixtures are shared with WP-10, not the service.
"""

import asyncio
import hashlib
import itertools
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from mcp import Client

from refcollector.bootstrap import open_app
from refcollector.contracts import api
from refcollector.domain.enums import Scope
from refcollector.mcp.server import build_server
from tests.fixtures.pdfs import paper_pdf, thesis_pdf
from tests.unit.pipeline.conftest import DOI, TEXT, TITLE, context


class Workflow:
    def __init__(self, client, handle):
        self.client, self.app = client, handle
        self.keys = itertools.count()

    async def call(self, tool, **arguments):
        if tool in {
            "create_project",
            "import_source",
            "start_job",
            "update_reference",
            "add_reference",
            "attach_file",
            "download_files",
            "create_export",
        }:
            arguments.setdefault("idempotency_key", f"sdk-key-{next(self.keys):08}")
        result = await self.client.call_tool(tool, arguments)
        assert not result.is_error, result.structured_content
        assert len(result.content) == 1 and "\n" not in result.content[0].text
        return result.structured_content

    async def error(self, name, code, **arguments):
        arguments.setdefault("idempotency_key", f"sdk-error-{next(self.keys):08}")
        result = await self.client.call_tool(name, arguments)
        assert result.is_error, result.structured_content
        assert result.structured_content["error"]["code"] == code

    async def job(self, name, pid, **arguments):
        queued = await self.call(name, project_id=pid, **arguments)
        await asyncio.wait_for(self.app.executor.run_until_idle(), 15)
        result = await self.call("get_job", project_id=pid, job_id=queued["job_id"])
        assert result["state"] in {"completed", "completed_with_warnings"}, result
        return result

    async def rows(self, pid, **filters):
        rows, cursor = [], None
        while True:
            page = await self.call("list_references", project_id=pid, filter=filters, limit=1, cursor=cursor)
            rows.extend(page["occurrences"])
            cursor = page["next_cursor"]
            if cursor is None:
                assert len(rows) == page["total"]
                assert len({r["occurrence_id"] for r in rows}) == len(rows)
                return rows

    async def reference(self, pid, oid):
        return await self.call(
            "get_reference",
            project_id=pid,
            occurrence_id=oid,
            sections=["candidates", "evidence", "access", "files", "history"],
        )

    async def create(self, text=TEXT):
        pid = (await self.call("create_project", name="SDK acceptance"))["project_id"]
        sid = (await self.call("import_source", project_id=pid, input={"type": "text", "text": text}))[
            "source_id"
        ]
        return pid, sid

    async def text(self, pid, artifact_id, length=127):
        offset, chunks = 0, []
        while True:
            part = await self.call(
                "read_artifact", project_id=pid, artifact_id=artifact_id, offset=offset, length=length
            )
            chunk = part["text"]
            assert len(chunk.encode("utf-8")) <= length
            chunks.append(chunk)
            offset += len(chunk.encode("utf-8"))
            if part["eof"]:
                return "".join(chunks)
            assert chunk, "read_artifact must make progress"


def test_w01_w02_w03_w04_w05_w06_w07_w08_pdf_sdk_import_extract_reopen(app, tmp_path):
    async def exercise():
        async with Client(build_server(app.service)) as client:
            w = Workflow(client, app)
            pid = (await w.call("create_project", name="PDF acceptance"))["project_id"]
            path = tmp_path / "bibliography.pdf"
            path.write_bytes(thesis_pdf(refs=[TEXT, TEXT.replace("waves", "waves2")]))
            imported = await w.call(
                "import_source", project_id=pid, input={"type": "local_file", "path": str(path)}
            )
            sid = imported["source_id"]
            assert imported["preflight"]["pages"] > 0 and imported["preflight"]["sections"]
            repeated = await w.call(
                "import_source", project_id=pid, input={"type": "local_file", "path": str(path)}
            )
            assert repeated["source_id"] == sid and repeated["reused"]
            await w.error(
                "import_source",
                "PATH_NOT_ALLOWED",
                project_id=pid,
                input={"type": "local_file", "path": str(tmp_path.parent / "outside.pdf")},
            )
            source = await w.call("get_source", project_id=pid, source_id=sid)
            assert source["source"]["source_id"] == sid
            job = await w.job("start_job", pid, stages=["extract"], scope={"source_ids": [sid]})
            rows = await w.rows(pid, job_id=job["job_id"])
            assert len(rows) == job["results"]["occurrence_count"] == 2
            ref = await w.reference(pid, rows[0]["occurrence_id"])
            assert ref["occurrence"]["raw_text"]
            assert any(span["page_label"] for span in ref["occurrence"]["spans"])
            no_heading = tmp_path / "no-heading.pdf"
            no_heading.write_bytes(thesis_pdf(refs=[TEXT], heading=None, toc=False))
            result = await client.call_tool(
                "import_source",
                {
                    "project_id": pid,
                    "input": {"type": "local_file", "path": str(no_heading)},
                    "idempotency_key": "no-heading-import",
                },
            )
            if result.is_error:
                assert result.structured_content["error"]["code"] == "REFERENCE_SECTION_NOT_FOUND"
            else:
                preflight = result.structured_content["preflight"]
                assert (
                    any(s["inferred"] for s in preflight["sections"])
                    or "REFERENCE_SECTION_NOT_FOUND" in preflight["warnings"]
                )
        app.close()
        reopened = open_app(app.settings)
        try:
            async with Client(build_server(reopened.service)) as client:
                w = Workflow(client, reopened)
                assert pid in {p["project_id"] for p in (await w.call("list_projects"))["projects"]}
                assert (await w.call("get_project", project_id=pid))["project_id"] == pid
                assert sid in {
                    s["source_id"] for s in (await w.call("list_sources", project_id=pid))["sources"]
                }
        finally:
            reopened.close()

    asyncio.run(exercise())


def test_w09_w10_w11_w12_w13_w14_w15_w16_w17_w25_w26_w27_w28_sdk_repair_export(app):
    async def exercise():
        async with Client(build_server(app.service)) as client:
            w = Workflow(client, app)
            conflict = f"Roe, Z. (1990). Unrelated deep space medicine. doi:{DOI}"
            pid, sid = await w.create(TEXT + "\n\n" + conflict)
            await w.job("start_job", pid, stages=["extract", "resolve"], scope={"source_ids": [sid]})
            rows = await w.rows(pid)
            assert all(r["identity"]["state"] not in {"unprocessed", "accepted_user"} for r in rows)
            bad = next(r for r in rows if "space medicine" in r["raw_text"])
            assert bad["identity"]["state"] == "needs_review"
            assert "DOI_TITLE_CONFLICT" in bad["identity"]["reasons"]
            good = next(r for r in rows if r != bad)
            detail = await w.reference(pid, good["occurrence_id"])
            await w.call(
                "update_reference",
                project_id=pid,
                change={
                    "type": "select_candidate",
                    "occurrence_id": good["occurrence_id"],
                    "candidate_id": detail["candidates"][0]["candidate_id"],
                    "expected_revision": good["revision"],
                },
            )
            good = (await w.reference(pid, good["occurrence_id"]))["occurrence"]
            assert good["identity"]["state"] == "accepted_agent"
            edited = await w.call(
                "update_reference",
                project_id=pid,
                change={
                    "type": "edit_text",
                    "occurrence_id": bad["occurrence_id"],
                    "text": "Référence corrigée. " + TEXT,
                    "expected_revision": bad["revision"],
                },
            )
            assert edited["changed"][0]["revision"] > bad["revision"]
            current = (await w.reference(pid, bad["occurrence_id"]))["occurrence"]
            split = await w.call(
                "update_reference",
                project_id=pid,
                change={
                    "type": "split",
                    "occurrence_id": current["occurrence_id"],
                    "expected_revision": current["revision"],
                    "parts": [{"text": TEXT}, {"text": "Unresolved reference"}],
                },
            )
            assert len(split["created"]) == 2
            ids = [r["occurrence_id"] for r in split["created"]]
            # The lineage remains in the public history, not only in SQLite.
            history = await w.reference(pid, bad["occurrence_id"])
            assert all(oid in json.dumps(history["history"]) for oid in ids)
            before = [await w.reference(pid, oid) for oid in ids]
            revisions = {r["occurrence"]["occurrence_id"]: r["occurrence"]["revision"] for r in before}
            stale = dict(revisions)
            stale[ids[0]] += 100
            await w.error(
                "update_reference",
                "REVISION_CONFLICT",
                project_id=pid,
                change={"type": "merge", "occurrence_ids": ids, "expected_revisions": stale},
            )
            after = [await w.reference(pid, oid) for oid in ids]
            for old, new in zip(before, after, strict=True):
                assert old["meta"]["project_revision"] == new["meta"]["project_revision"]
                assert {k: v for k, v in old.items() if k != "meta"} == {
                    k: v for k, v in new.items() if k != "meta"
                }
            merged = await w.call(
                "update_reference",
                project_id=pid,
                change={"type": "merge", "occurrence_ids": ids, "expected_revisions": revisions},
            )
            assert merged["created"]
            added = await w.call(
                "add_reference", project_id=pid, source_id=sid, text="Unknown author. Missing work."
            )
            added_ref = (await w.reference(pid, added["occurrence_id"]))["occurrence"]
            assert added_ref["source_id"] == sid
            await w.call(
                "update_reference",
                project_id=pid,
                change={
                    "type": "exclude",
                    "occurrence_id": good["occurrence_id"],
                    "expected_revision": good["revision"],
                },
            )
            all_ids = {r["occurrence_id"] for r in await w.rows(pid)}

            async def export(selection):
                revision = (await w.call("get_project", project_id=pid))["revision"]
                result = await w.job(
                    "create_export",
                    pid,
                    project_revision=revision,
                    formats=["json", "audit_report"],
                    selection=selection,
                )
                artifacts = (await w.call("list_artifacts", project_id=pid))["artifacts"]
                selected = [a for a in artifacts if a["artifact_id"] in result["results"]["artifact_ids"]]
                assert len(selected) == 3
                manifest = next(a for a in selected if a["kind"] == "manifest")
                payload = json.loads(await w.text(pid, manifest["artifact_id"]))
                assert payload["revision"] == revision
                for record in payload["files"]:
                    descriptor = next(a for a in selected if a["name"] == record["name"])
                    raw = await asyncio.to_thread(
                        Path(unquote(urlsplit(descriptor["path"]).path).lstrip("/")).read_bytes
                    )
                    assert hashlib.sha256(raw).hexdigest() == record["sha256"] == descriptor["sha256"]
                data = next(a for a in selected if a["name"] == "references.json")
                audit = next(a for a in selected if a["kind"] != "manifest" and a != data)
                assert await w.text(pid, audit["artifact_id"], length=7)
                return json.loads(await w.text(pid, data["artifact_id"])), revision

            accepted, _ = await export("accepted")
            assert good["occurrence_id"] not in json.dumps(accepted)
            complete, revision = await export("complete_audit")
            assert all(oid in json.dumps(complete) for oid in all_ids)
            assert "excluded" in json.dumps(complete) and "unprocessed" in json.dumps(complete)
            assert "Référence corrigée" in json.dumps(complete, ensure_ascii=False)
            await w.error(
                "create_export",
                "REVISION_NOT_CURRENT",
                project_id=pid,
                project_revision=revision - 1,
                formats=["json"],
            )
            await w.error(
                "update_reference",
                "INVALID_INPUT",
                project_id=pid,
                change={
                    "type": "select_candidate",
                    "occurrence_id": good["occurrence_id"],
                    "candidate_id": detail["candidates"][0]["candidate_id"],
                    "expected_revision": good["revision"],
                },
                approval=True,
            )

    asyncio.run(exercise())


@pytest.mark.parametrize("wrong_download", [False, True])
def test_w20_w22_w23_w24_w30_edits_locks_only_w31_graceful_reopen_sdk_files(
    app, offline, tmp_path, wrong_download
):
    async def exercise():
        offline.location = True
        async with Client(build_server(app.service, scopes=frozenset(Scope))) as client:
            w = Workflow(client, app)
            pid, sid = await w.create()
            # Trusted setup only: MCP cannot raise network permissions.
            app.service.update_project_settings(
                context(),
                api.UpdateProjectSettingsIn(
                    project_id=pid,
                    network_mode="retrieval",
                    download_enabled=True,
                    idempotency_key="trusted-retrieval-setup",
                ),
            )
            await w.job(
                "start_job",
                pid,
                stages=["extract", "resolve", "discover_access"],
                scope={"source_ids": [sid]},
            )
            row = (await w.rows(pid))[0]
            oid = row["occurrence_id"]
            ref = await w.reference(pid, oid)
            check = ref["access"][0]
            assert check["checked_at"] and check["sources_consulted"]
            wrong = tmp_path / "wrong.pdf"
            wrong.write_bytes(paper_pdf("Different research paper", [], "10.4321/other"))
            attached = await w.call("attach_file", project_id=pid, occurrence_id=oid, path=str(wrong))
            assert (
                attached["local_copy"] == "unusable" and attached["checks"]["identity"]["outcome"] == "fail"
            )
            if wrong_download:
                offline.body = wrong.read_bytes()
            await w.job("download_files", pid, scope={"occurrence_ids": [oid]})
            ref = await w.reference(pid, oid)
            assert ref["occurrence"]["retrieval"] == ("needs_review" if wrong_download else "verified")
            assert ref["files"][0]["sha256"] == hashlib.sha256(offline.body).hexdigest()
            if wrong_download:
                return
            good = tmp_path / "good.pdf"
            good.write_bytes(paper_pdf(TITLE + "\nVersion of Record", ["Smith, A."], DOI))
            attached = await w.call("attach_file", project_id=pid, occurrence_id=oid, path=str(good))
            assert attached["local_copy"] == "usable"
            before = len(offline.downloads)
            await w.job("download_files", pid, scope={"occurrence_ids": [oid]})
            assert len(offline.downloads) == before
            ref = await w.reference(pid, oid)
            assert ref["occurrence"]["retrieval"] == "satisfied_by_local_copy"
            await w.call(
                "update_reference",
                project_id=pid,
                change={
                    "type": "set_fields",
                    "occurrence_id": oid,
                    "fields": {"title": "Corrected title"},
                    "expected_revision": ref["occurrence"]["revision"],
                },
            )
            ref = await w.reference(pid, oid)
            await w.call(
                "update_reference",
                project_id=pid,
                change={
                    "type": "set_field_locks",
                    "occurrence_id": oid,
                    "locks": {"title": True},
                    "expected_revision": ref["occurrence"]["revision"],
                },
            )
            before_reopen = await w.reference(pid, oid)
        app.close()
        reopened = open_app(app.settings)
        try:
            async with Client(build_server(reopened.service)) as client:
                after = await Workflow(client, reopened).reference(pid, oid)
                for field in ("occurrence", "work", "locks", "files"):
                    assert after[field] == before_reopen[field]
                assert after["locks"]["title"] and after["work"]["title"] == "Corrected title"
        finally:
            reopened.close()

    asyncio.run(exercise())


def test_w20_w21_sdk_discovery_retains_positive_location_after_provider_failure(app, offline):
    original = offline.response

    def response(request):
        if request.url.host == "api.openalex.org":
            offline.requests.append(str(request.url))
            return httpx.Response(503)
        return original(request)

    offline.response = response
    offline.location = True

    async def exercise():
        async with Client(build_server(app.service)) as client:
            w = Workflow(client, app)
            pid, sid = await w.create()
            await w.job("start_job", pid, stages=["extract", "resolve"], scope={"source_ids": [sid]})
            app.settings.enabled_providers = ["crossref", "unpaywall", "openalex"]
            await w.job("start_job", pid, stages=["discover_access"], scope={"source_ids": [sid]})
            row = (await w.rows(pid))[0]
            assert row["access"]["classification"] == "eligible_found"
            assert row["access"]["discovery"] == "partial"
            check = (await w.reference(pid, row["occurrence_id"]))["access"][0]
            assert check["checked_at"] and check["sources_consulted"] and check["sources_failed"]
            assert any(f["source"] == "openalex" for f in check["sources_failed"])

    asyncio.run(exercise())
