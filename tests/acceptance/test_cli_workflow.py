"""Independent offline CLI workflows through production composition.

Only HTTP responses and DNS are replaced. Every invocation opens/closes the real
app, service, executor and SQLite store. No direct store setup or assertions.
CliRunner does not prove real TTY approval or process-kill recovery. Source and
artifact readback (sections, lineage, audit contents, manifest hashes, bounded
reads) is proved in test_cli_gaps.py.
"""

import hashlib
import json
import re

import pytest
from typer.testing import CliRunner

from refcollector.cli import main
from refcollector.config import Settings, save_settings
from refcollector.files.netguard import DefaultNetGuard
from tests.fixtures.pdfs import paper_pdf, thesis_pdf, write
from tests.unit.pipeline.conftest import DOI, TEXT, TITLE, Offline


class Commands:
    def __init__(self):
        self.runner = CliRunner()
        self.pid = None

    def run(self, *args, ok=True):
        prefix = ["--project", self.pid] if self.pid else []
        result = self.runner.invoke(main.app, [*prefix, *map(str, args)])
        if ok:
            assert result.exit_code == 0, (result.output, result.exception)
        else:
            assert result.exit_code != 0, result.output
        return result.output

    def read(self, *args):
        return json.loads(self.run(*args, "--json"))

    def create(self):
        self.pid = re.search(r"prj_[A-Z0-9]+", self.run("project", "create", "cli-acceptance"))[0]

    def import_text(self, text=TEXT):
        return re.search(r"src_[A-Z0-9]+", self.run("import", "--text", text))[0]

    def job(self, *args, states=("completed",)):
        output = self.run(*args, "--wait")
        jid = re.search(r"job (job_[A-Z0-9]+)", output)[1]
        result = self.read("jobs", jid)
        assert result["state"] in states, result
        return jid, result

    def rows(self, *args):
        rows, cursor = [], None
        while True:
            page_args = ["--cursor", cursor] if cursor else []
            page = self.read("list", *args, "--limit", "1", *page_args)
            rows.extend(page["occurrences"])
            cursor = page["next_cursor"]
            if cursor is None:
                assert len(rows) == page["total"]
                assert len({r["occurrence_id"] for r in rows}) == len(rows)
                return rows

    def show(self, oid):
        return self.read("show", oid)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    offline = Offline()
    monkeypatch.setattr("refcollector.pipeline.clients.ProviderHttpClient", offline.http)
    original_init = DefaultNetGuard.__init__

    async def dns(host, port):
        assert host == "repo.example"
        return ["8.8.8.8"]

    def guard_init(self, **kwargs):
        original_init(self, resolve=dns)

    monkeypatch.setattr(DefaultNetGuard, "__init__", guard_init)

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    settings = Settings(
        rc_home=tmp_path / "home",
        email="acceptance@example.org",
        allow_folders=[allowed],
        enabled_providers=["crossref", "unpaywall"],
    )
    monkeypatch.setenv("RC_HOME", str(settings.rc_home))
    monkeypatch.delenv("RC_PROJECT", raising=False)
    save_settings(settings)
    main.set_app_factory(None)
    commands = Commands()
    yield commands, offline, allowed
    main._close_app()
    main.set_app_factory(None)
    assert all(client._client.is_closed for client in offline.clients)


def test_w01_w02_w03_w04_project_reopen_w05_w06_w07_pdf_cli(cli, tmp_path):
    c, _, allowed = cli
    c.create()
    pdf = write(allowed, "bibliography.pdf", thesis_pdf(refs=[TEXT, TEXT.replace(DOI, "10.1234/second")]))
    imported = c.run("import", pdf)
    sid = re.search(r"src_[A-Z0-9]+", imported)[0]
    assert "pages " in imported and "estimated entries " in imported
    repeated = c.run("import", pdf)
    assert sid in repeated and "already imported" in repeated
    outside = write(tmp_path, "outside.pdf", pdf.read_bytes())
    assert "PATH_NOT_ALLOWED" in c.run("import", outside, ok=False)
    assert any(p["project_id"] == c.pid for p in c.read("project", "list")["projects"])
    assert c.read("project", "show")["project_id"] == c.pid
    jid, job = c.job("run", "--source", sid, "--stages", "extract")
    rows = c.rows("--job", jid)
    assert len(rows) == job["results"]["occurrence_count"] == 2
    for row in rows:
        shown = c.show(row["occurrence_id"])["occurrence"]
        assert shown["raw_text"] and shown["source_id"] == sid
        assert shown["spans"] and all(span["page_label"] for span in shown["spans"])


def test_w09_w10_w11_resolve_conflict_without_tty(cli):
    c, _, _ = cli
    c.create()
    sid = c.import_text(
        TEXT
        + "\n\n"
        + TEXT.replace(TITLE, "Unrelated medical treatment in tropical forests").replace(
            "Smith, A.", "Roe, Z."
        )
    )
    _, _job = c.job("run", "--source", sid, "--stages", "extract,resolve")
    rows = c.rows("--source", sid)
    assert len(rows) == 2
    assert all(row["identity"]["state"] != "unprocessed" for row in rows)
    assert any(row["identity"]["state"] == "accepted_auto" for row in rows)
    conflicts = c.rows("--reason", "DOI_TITLE_CONFLICT")
    assert len(conflicts) == 1 and conflicts[0]["identity"]["state"] == "needs_review"
    assert c.rows("--identity", "accepted_user") == []
    assert "needs an interactive terminal" in c.run("review", ok=False)


def test_w12_w13_creation_w14_w15_w16_state_w30_edit_reopen(cli):
    c, _, _ = cli
    c.create()
    sid = c.import_text()
    c.job("run", "--source", sid, "--stages", "extract")
    oid = c.rows()[0]["occurrence_id"]
    before = c.show(oid)["occurrence"]
    c.run("edit", oid, "--text", "Changed citation", "--expected-revision", before["revision"])
    edited = c.show(oid)["occurrence"]
    assert edited["revision"] == before["revision"] + 1
    assert edited["edited_text"] == "Changed citation" and edited["raw_text"] == before["raw_text"]
    split = c.run(
        "split",
        oid,
        "--part",
        "First citation",
        "--part",
        "Second citation",
        "--expected-revision",
        edited["revision"],
    )
    created = re.findall(r"occ_[A-Z0-9]+", split.split("created:", 1)[1])
    assert len(created) == 2
    assert c.show(oid)["occurrence"]["extraction"] == "excluded"
    parts = [c.show(o)["occurrence"] for o in created]
    revision = c.read("project", "show")["revision"]
    stale = c.run(
        "merge",
        *created,
        "--expected-revision",
        f"{created[0]}={parts[0]['revision']}",
        "--expected-revision",
        f"{created[1]}={parts[1]['revision'] - 1}",
        ok=False,
    )
    assert "REVISION_CONFLICT" in stale
    assert c.read("project", "show")["revision"] == revision
    assert [c.show(o)["occurrence"] for o in created] == parts
    merged = c.run(
        "merge",
        *created,
        "--expected-revision",
        f"{created[0]}={parts[0]['revision']}",
        "--expected-revision",
        f"{created[1]}={parts[1]['revision']}",
    )
    merged_id = re.search(r"occ_[A-Z0-9]+", merged.split("created:", 1)[1])[0]
    assert c.show(merged_id)["occurrence"]["raw_text"] == "First citation Second citation"
    added = re.search(r"occ_[A-Z0-9]+", c.run("add", "--source", sid, "--text", "Added citation"))[0]
    assert c.show(added)["occurrence"]["source_id"] == sid
    c.run("exclude", added, "--expected-revision", c.show(added)["occurrence"]["revision"])
    assert c.show(added)["occurrence"]["extraction"] == "excluded"


def test_w20_w22_w23_w24_w25_job_w26_w31_file_reopen(cli):
    c, offline, allowed = cli
    offline.location = True
    c.create()
    sid = c.import_text()
    c.job("run", "--source", sid)
    oid = c.rows()[0]["occurrence_id"]
    access = c.show(oid)["access"][0]
    assert access["checked_at"] and "unpaywall" in access["sources_consulted"]
    assert access["sources_failed"] == [] and access["classification"] == "eligible_found"
    c.run("config", "network", "retrieval")
    c.run("config", "downloads", "on")
    wrong = write(allowed, "wrong.pdf", paper_pdf("Unrelated clinical paper", ["Roe"], "10.9999/other"))
    c.run("attach", oid, wrong, "--version", "published")
    shown = c.show(oid)
    assert shown["occurrence"]["local_copy"] == "unusable"
    assert any(copy["failed_check"] == "identity" for copy in shown["local_copies"])
    c.job("download", "--occurrence", oid)
    downloaded = c.show(oid)
    assert downloaded["occurrence"]["retrieval"] == "verified"
    assert len(downloaded["files"]) == 1
    assert downloaded["files"][0]["sha256"] == hashlib.sha256(offline.body).hexdigest()
    assert len(offline.downloads) == 1
    assert c.show(oid)["files"] == downloaded["files"]
    valid = write(allowed, "valid.pdf", offline.body)
    c.run("attach", oid, valid, "--version", "published")
    assert c.show(oid)["occurrence"]["retrieval"] == "satisfied_by_local_copy"
    c.job("download", "--occurrence", oid)
    assert len(offline.downloads) == 1
    revision = c.read("project", "show")["revision"]
    _, exported = c.job("export", "--revision", revision, "--format", "json,csv,bibtex,csl_json", "--audit")
    assert exported["results"]["artifact_ids"]
    artifacts = c.read("project", "show")["recent_artifacts"]
    by_id = {a["artifact_id"]: a for a in artifacts}
    assert set(exported["results"]["artifact_ids"]) <= by_id.keys()
    descriptors = [by_id[aid] for aid in exported["results"]["artifact_ids"]]
    assert all(a["project_revision"] == revision and a["sha256"] for a in descriptors)
    assert any(a["name"] == "manifest.json" for a in descriptors)
    c.run("add", "--source", sid, "--text", "Unresolved citation")
    assert "REVISION_NOT_CURRENT" in c.run("export", "--revision", revision, "--format", "json", ok=False)
