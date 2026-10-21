"""CLI-only readback evidence for W rows that lacked public CLI readback (WP-13).

Same rules as test_cli_workflow: production composition per invocation, HTTP/DNS fixtures only,
IDs read from public CLI output, paging to exhaustion. Artifact bytes are read with
`artifact read`, never from storage. `--json` is used where exact text matters because plain
output is cleaned for the terminal (CRLF and control characters are removed there).
W-19 needs a genuine approval, which pytest cannot create without a TTY: see the note at the end.
"""

import hashlib
import json
import re

import httpx

from refcollector.cli import main
from refcollector.config import load_settings, save_settings
from tests.acceptance.test_cli_workflow import Commands
from tests.fixtures.pdfs import paper_pdf, thesis_pdf, write
from tests.unit.pipeline.conftest import DOI, TEXT, TITLE

CONFLICT = f"Roe, Z. (1990). Unrelated deep space medicine. doi:{DOI}"
CORRECTED = "Référence corrigée: Smith, A. (2020). Édition révisée."


def ids(prefix: str, text: str) -> list[str]:
    return re.findall(rf"{prefix}_[A-Z0-9]+", text)


def all_pages(c: Commands, *args: str, key: str) -> list[dict]:
    """Page a CLI list command to exhaustion, one item per page."""
    items, cursor = [], None
    while True:
        page = c.read(*args, "--limit", "1", *(["--cursor", cursor] if cursor else []))
        items += page[key]
        cursor = page["next_cursor"]
        if cursor is None:
            return items


def chunks(c: Commands, aid: str, length: int) -> list[dict]:
    """Read an artifact in bounded chunks, advancing by the UTF-8 byte length of each text."""
    out, offset = [], 0
    while True:
        part = c.read("artifact", "read", aid, "--offset", offset, "--length", length)
        out.append(part)
        offset += len(part["text"].encode())
        if part["eof"]:
            return out
        assert part["text"]


def test_w01_import_and_source_show_detected_sections(cli):
    c, _, allowed = cli
    c.create()
    pdf = write(allowed, "bibliography.pdf", thesis_pdf(refs=[TEXT]))
    imported = c.run("import", pdf)
    sid = ids("src", imported)[0]
    assert re.search(r"section sec_\w+: pages \d+-\d+ \(0-based\)\s+heading References\s+detected", imported)
    shown = c.read("source", sid)
    sections = shown["preflight"]["sections"]
    assert sections and all(s["page_end"] >= s["page_start"] >= 0 for s in sections)
    assert shown["preflight"]["pages"] > 0 and shown["source"]["pages"] == shown["preflight"]["pages"]
    assert {s["section_id"] for s in sections} <= {s["section_id"] for s in shown["sections"]}
    plain = c.run("source", sid)
    assert sections[0]["section_id"] in plain and "heading References" in plain


def test_w04_reopen_sources_lists_source_and_project(cli):
    c, _, allowed = cli
    c.create()
    pdf = write(allowed, "bibliography.pdf", thesis_pdf(refs=[TEXT]))
    sid = ids("src", c.run("import", pdf))[0]
    text_sid = c.import_text()
    main._close_app()  # reopen: the next invocation builds a fresh app over the same RC_HOME
    main.set_app_factory(None)
    assert any(p["project_id"] == c.pid for p in c.read("project", "list")["projects"])
    listed = all_pages(c, "sources", key="sources")
    assert {s["source_id"] for s in listed} == {sid, text_sid}
    assert next(s for s in listed if s["source_id"] == sid)["type"] == "pdf"
    assert {s["source_id"] for s in c.read("sources")["sources"]} == {sid, text_sid}
    assert sid in c.run("sources")


def test_w08_missing_heading_yields_inferred_section_via_source(cli):
    c, _, allowed = cli
    c.create()
    pdf = write(allowed, "no-heading.pdf", thesis_pdf(refs=[TEXT], heading=None, toc=False))
    imported = c.run("import", pdf)
    sid = ids("src", imported)[0]
    assert "heading -  inferred" in imported
    preflight = c.read("source", sid)["preflight"]
    inferred = [s for s in preflight["sections"] if s["inferred"]]
    assert inferred and all(s["heading"] is None for s in inferred)
    assert "inferred" in c.run("source", sid)
    _, job = c.job("run", "--source", sid, "--stages", "extract", states=("completed_with_warnings",))
    assert "REFERENCE_SECTION_INFERRED" in job["warnings"]
    assert all(section["inferred"] and not section["manual"] for section in c.read("source", sid)["sections"])
    extraction = c.read("source", sid)["extraction"]
    assert extraction["state"] == "extracted" and extraction["occurrences"] == 1


def test_w13_split_and_merge_lineage_readback_via_show(cli):
    c, _, _ = cli
    c.create()
    sid = c.import_text()
    c.job("run", "--source", sid, "--stages", "extract")
    oid = c.rows()[0]["occurrence_id"]
    revision = c.show(oid)["occurrence"]["revision"]
    split = c.run(
        "split", oid, "--part", "First citation", "--part", "Second citation", "--expected-revision", revision
    )
    successors = ids("occ", split.split("created:", 1)[1])
    assert len(successors) == 2
    history = c.show(oid)["history"]
    splits = [e for e in history if e.get("type") == "OCCURRENCE_SPLIT"]
    assert len(splits) == 1 and splits[0]["payload"]["created"] == successors
    plain = c.run("show", oid)
    assert "lineage:" in plain and "OCCURRENCE_SPLIT: created " + ", ".join(successors) in plain
    merged = c.run("merge", *successors)
    merged_id = ids("occ", merged.split("created:", 1)[1])[0]
    merges = [e for e in c.show(merged_id)["history"] if e.get("type") == "OCCURRENCES_MERGED"]
    assert len(merges) == 1 and sorted(merges[0]["payload"]["merged"]) == sorted(successors)
    assert "merged from" in c.run("show", merged_id)


def export(c: Commands, *args: str) -> tuple[int, list[dict]]:
    revision = c.read("project", "show")["revision"]
    _, job = c.job("export", "--revision", revision, *args)
    wanted = set(job["results"]["artifact_ids"])
    listed = all_pages(c, "artifacts", key="artifacts")
    return revision, [a for a in listed if a["artifact_id"] in wanted]


def reference_rows(c: Commands, artifacts: list[dict]) -> list[dict]:
    data = next(a for a in artifacts if a["name"] == "references.json")
    return json.loads(c.read("artifact", "read", data["artifact_id"], "--all")["text"])


def test_w16_w25_w27_w28_artifacts_read_through_cli(cli):
    c, _, _ = cli
    c.create()
    sid = c.import_text(TEXT + "\n\n" + CONFLICT)
    c.job("run", "--source", sid, "--stages", "extract,resolve")
    rows = c.rows("--source", sid)
    good = next(r for r in rows if "space medicine" not in r["raw_text"])
    bad = next(r for r in rows if "space medicine" in r["raw_text"])
    assert good["identity"]["state"] == "accepted_auto" and bad["identity"]["state"] == "needs_review"
    c.run("exclude", bad["occurrence_id"], "--expected-revision", bad["revision"], "--reason", "noise")
    added = ids("occ", c.run("add", "--source", sid, "--text", CORRECTED))[0]
    everyone = {r["occurrence_id"] for r in c.rows()}
    assert {good["occurrence_id"], bad["occurrence_id"], added} <= everyone

    revision, accepted = export(c, "--format", "json,audit_report", "--selection", "accepted")
    accepted_ids = {r["occurrence"]["occurrence_id"] for r in reference_rows(c, accepted)}
    assert good["occurrence_id"] in accepted_ids
    assert bad["occurrence_id"] not in accepted_ids and added not in accepted_ids  # W-16

    revision, audit = export(c, "--format", "json", "--audit")
    assert {a["name"] for a in audit} >= {"manifest.json", "audit_report.md"}
    assert all(a["project_revision"] == revision for a in audit)
    complete = reference_rows(c, audit)
    by_id = {r["occurrence"]["occurrence_id"]: r["occurrence"] for r in complete}
    assert everyone <= by_id.keys()  # W-27: every occurrence is retained
    assert by_id[bad["occurrence_id"]]["extraction"] == "excluded"
    assert by_id[added]["identity"] == "unprocessed"
    assert by_id[added]["edited_text"] is None and CORRECTED in by_id[added]["raw_text"]

    # W-25: manifest read through the CLI lists the revision and hashes of the bytes read through the CLI
    manifest_art = next(a for a in c.read("artifacts", "--kind", "manifest")["artifacts"] if a in audit)
    manifest = json.loads(c.read("artifact", "read", manifest_art["artifact_id"], "--all")["text"])
    assert manifest["revision"] == revision and manifest["files"]
    for record in manifest["files"]:
        descriptor = next(a for a in audit if a["name"] == record["name"])
        body = c.read("artifact", "read", descriptor["artifact_id"], "--all")
        assert body["eof"] and body["descriptor"] == descriptor
        data = body["text"].encode()
        assert hashlib.sha256(data).hexdigest() == record["sha256"] == descriptor["sha256"]
        assert len(data) == descriptor["bytes"]

    # W-28: bounded UTF-8 chunks of the audit report concatenate to the full text
    report = next(a for a in audit if a["name"] == "audit_report.md")
    full = c.read("artifact", "read", report["artifact_id"], "--all")["text"]
    assert "Référence corrigée" in full and len(full.encode()) > len(full)
    parts = chunks(c, report["artifact_id"], 16)
    assert len(parts) > 3 and all(len(p["text"].encode()) <= 16 for p in parts)
    assert "".join(p["text"] for p in parts) == full
    assert parts[-1]["eof"] and not any(p["eof"] for p in parts[:-1])
    plain = c.run("artifact", "read", report["artifact_id"], "--all")
    assert "Référence corrigée" in plain
    # a window too small for one multibyte character fails with an actionable error
    cut = full.encode().index("é".encode())
    small = c.run("artifact", "read", report["artifact_id"], "--offset", cut, "--length", 1, ok=False)
    assert "INVALID_INPUT" in small and "length" in small


def test_w19_cli_surface_boundary_without_tty(cli):
    """Pytest cannot approve (no TTY), so W-19 stays native: `tty_approval_smoke cli`.

    This only pins the boundary: the non-interactive CLI refuses to approve, and its `edit`
    command is the one the native script runs after the genuine approval.
    """
    c, _, _ = cli
    c.create()
    sid = c.import_text()
    c.job("run", "--source", sid, "--stages", "extract,resolve")
    oid = c.rows()[0]["occurrence_id"]
    assert "needs an interactive terminal" in c.run("review", ok=False)
    c.run("edit", oid, "--text", "After approval: " + TEXT)
    shown = c.show(oid)
    assert shown["occurrence"]["identity"]["state"] != "accepted_user"
    assert not any(d.get("approval") for d in shown["history"] if d.get("kind") == "decision")


def test_w21_one_provider_answers_other_fails_eligible_found_partial(cli):
    c, offline, _ = cli
    original = offline.response

    def response(request):
        if request.url.host == "api.openalex.org":
            offline.requests.append(str(request.url))
            return httpx.Response(503)
        return original(request)

    offline.response = response
    offline.location = True
    settings = load_settings()
    settings.enabled_providers = ["crossref", "unpaywall", "openalex"]
    save_settings(settings)
    c.create()
    sid = c.import_text()
    c.job(
        "run",
        "--source",
        sid,
        "--stages",
        "extract,resolve,discover_access",
        states=("completed_with_warnings",),
    )
    row = c.rows("--source", sid)[0]
    assert row["access"]["classification"] == "eligible_found" and row["access"]["discovery"] == "partial"
    shown = c.show(row["occurrence_id"])
    check = shown["access"][0]
    assert check["checked_at"] and "unpaywall" in check["sources_consulted"]
    assert [f["source"] for f in check["sources_failed"]] == ["openalex"]
    assert shown["occurrence"]["access"]["discovery"] == "partial"
    text = c.run("show", row["occurrence_id"])
    assert "access: eligible_found  discovery: partial" in text
    assert any("api.openalex.org" in url for url in offline.requests)


def test_w24_wrong_paper_download_stays_needs_review_never_verified(cli):
    c, offline, _ = cli
    offline.location = True
    offline.body = paper_pdf("Different research paper", ["Roe"], "10.4321/other")
    c.create()
    sid = c.import_text()
    c.job("run", "--source", sid)
    oid = c.rows("--source", sid)[0]["occurrence_id"]
    c.run("config", "network", "retrieval")
    c.run("config", "downloads", "on")
    c.job("download", "--occurrence", oid)
    assert len(offline.downloads) == 1
    shown = c.show(oid)
    assert shown["occurrence"]["retrieval"] == "needs_review"
    assert [r["occurrence_id"] for r in c.rows("--retrieval", "needs_review")] == [oid]
    assert c.rows("--retrieval", "verified") == []
    assert all(f["sha256"] == hashlib.sha256(offline.body).hexdigest() for f in shown["files"])
    assert "retrieval: needs_review" in c.run("show", oid)
    assert TITLE not in json.dumps(shown["files"])
