"""Each CLI command maps to the right service call (spec 14.1, 7.4, W-11)."""

from __future__ import annotations

import getpass
import json
import sys
import types
from pathlib import Path

import pytest

from refcollector.cli import main
from refcollector.config import load_settings
from refcollector.contracts.api import (
    AddReferenceOut,
    AttachFileOut,
    ControlJobOut,
    CreateProjectOut,
    GetProjectOut,
    GetSourceOut,
    ImportSourceOut,
    JobOut,
    SetSourceSectionsOut,
    SourceListItem,
    StartJobOut,
)
from refcollector.domain.enums import (
    Channel,
    ExtractionState,
    JobState,
    LocalCopyState,
    Selection,
    SourceType,
)
from refcollector.domain.models import Preflight
from refcollector.errors import NotAuthorized, RevisionConflict
from tests.unit.cli import factories as f
from tests.unit.cli.conftest import Cli


def project_out(**kw: object) -> GetProjectOut:
    base = dict(
        project_id="prj_1",
        name="Thesis",
        revision=7,
        policy={"profile": "default"},
        network_mode="metadata",
        counts={"occurrences": 2},
        sources_count=1,
        recent_jobs=[],
        recent_artifacts=[],
    )
    return GetProjectOut(**{**base, **kw})


def source_out(revision: int = 3) -> GetSourceOut:
    item = SourceListItem(
        source_id="src_1",
        label="thesis.pdf",
        type=SourceType.PDF,
        pages=100,
        sections=1,
        extraction_state=ExtractionState.PENDING,
        occurrence_count=0,
        imported_at=f.NOW,
    )
    return GetSourceOut(
        source=item,
        revision=revision,
        preflight=Preflight(pages=100, text_coverage=1.0),
        sections=[],
        extraction={},
        jobs=[],
    )


def assert_agent_ctx(ctx: object) -> None:
    assert ctx.channel == Channel.CLI
    assert ctx.origin == "agent"
    assert ctx.tty is False
    assert ctx.principal == getpass.getuser()
    assert ctx.request_id.startswith("req_")


# ---------- projects ----------


def test_project_create_maps_call_and_idempotency(cli: Cli) -> None:
    cli.svc.responses["create_project"] = CreateProjectOut(project_id="prj_9", revision=1)
    r = cli.run("project", "create", "My Thesis", "--profile", "strict")
    assert r.exit_code == 0, r.output
    assert "prj_9" in r.output
    (_, ctx, req) = cli.calls("create_project")[0]
    assert_agent_ctx(ctx)
    assert req.name == "My Thesis"
    assert req.policy_profile == "strict"
    assert len(req.idempotency_key) == 32  # uuid4 hex
    int(req.idempotency_key, 16)


def test_idempotency_key_option_wins(cli: Cli) -> None:
    cli.svc.responses["create_project"] = CreateProjectOut(project_id="prj_9", revision=1)
    cli.run("project", "create", "X", "--idempotency-key", "my-key-12345")
    assert cli.calls("create_project")[0][2].idempotency_key == "my-key-12345"


def test_keys_differ_between_runs(cli: Cli) -> None:
    cli.svc.responses["create_project"] = CreateProjectOut(project_id="prj_9", revision=1)
    cli.run("project", "create", "X")
    cli.run("project", "create", "X")
    keys = {c[2].idempotency_key for c in cli.calls("create_project")}
    assert len(keys) == 2


def test_project_list_json_parses(cli: Cli) -> None:
    r = cli.run("project", "list", "--json")
    assert r.exit_code == 0
    assert json.loads(r.output)["projects"][0]["name"] == "Thesis"


def test_project_list_table(cli: Cli) -> None:
    r = cli.run("project", "list")
    assert "prj_1" in r.output and "Thesis" in r.output


def test_project_show_resolves_name_and_id(cli: Cli) -> None:
    cli.svc.responses["get_project"] = project_out()
    for ident in ("Thesis", "prj_1"):
        r = cli.run("--project", ident, "project", "show", "--json")
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["project_id"] == "prj_1"
    assert [c[2].project_id for c in cli.calls("get_project")] == ["prj_1", "prj_1"]


def test_project_from_env(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    cli.svc.responses["get_project"] = project_out()
    monkeypatch.setenv("RC_PROJECT", "Thesis")
    assert cli.run("project", "show").exit_code == 0
    assert cli.calls("get_project")[0][2].project_id == "prj_1"


def test_missing_project_is_usage_error(cli: Cli) -> None:
    r = cli.run("list")
    assert r.exit_code == 2
    assert "--project" in r.stderr


def test_unknown_project_not_found(cli: Cli) -> None:
    r = cli.run("--project", "Nope", "list")
    assert r.exit_code == 1
    assert r.stderr.startswith("NOT_FOUND:")


# ---------- sources ----------


def test_import_path_and_text(cli: Cli, tmp_path: Path) -> None:
    cli.svc.responses["import_source"] = ImportSourceOut(
        source_id="src_1", reused=False, preflight=Preflight(pages=10, text_coverage=1.0)
    )
    pdf = tmp_path / "a.pdf"
    r = cli.run("--project", "prj_1", "import", str(pdf))
    assert r.exit_code == 0, r.output
    req = cli.calls("import_source")[0][2]
    assert req.input.type == "local_file" and Path(req.input.path) == pdf.absolute()
    r = cli.run("--project", "prj_1", "import", "--text", "Smith (2020). A paper.", "--label", "pasted")
    assert r.exit_code == 0
    req = cli.calls("import_source")[1][2]
    assert req.input.type == "text" and req.input.label == "pasted"
    assert_agent_ctx(cli.calls("import_source")[1][1])


def test_import_needs_exactly_one_input(cli: Cli) -> None:
    assert cli.run("--project", "prj_1", "import").exit_code == 2
    assert cli.run("--project", "prj_1", "import", "a.pdf", "--text", "x").exit_code == 2
    assert cli.calls("import_source") == []


def test_sections_uses_source_revision(cli: Cli) -> None:
    cli.svc.responses["get_source"] = source_out(revision=3)
    cli.svc.responses["set_source_sections"] = SetSourceSectionsOut(sections=[])
    r = cli.run("--project", "prj_1", "sections", "src_1", "--pages", "10-20,30-31")
    assert r.exit_code == 0, r.output
    req = cli.calls("set_source_sections")[0][2]
    assert [(s.page_start, s.page_end) for s in req.sections] == [(10, 20), (30, 31)]
    assert req.expected_source_revision == 3
    r = cli.run("--project", "prj_1", "sections", "src_1", "--pages", "10-20", "--expected-revision", "9")
    assert cli.calls("set_source_sections")[1][2].expected_source_revision == 9


def test_sections_bad_range(cli: Cli) -> None:
    assert cli.run("--project", "prj_1", "sections", "src_1", "--pages", "abc").exit_code == 2


# ---------- jobs ----------


def test_run_maps_stages_and_scope(cli: Cli) -> None:
    cli.svc.responses["start_job"] = StartJobOut(job_id="job_1", steps_planned=3, next_poll_after_ms=10)
    r = cli.run(
        "--project", "prj_1", "run", "--stages", "extract,resolve", "--source", "src_1", "--source", "src_2"
    )
    assert r.exit_code == 0, r.output
    (_, ctx, req) = cli.calls("start_job")[0]
    assert_agent_ctx(ctx)
    assert [s.value for s in req.stages] == ["extract", "resolve"]
    assert req.scope.source_ids == ["src_1", "src_2"]
    assert "job_1" in r.output
    r = cli.run("--project", "prj_1", "run", "--occurrence", "occ_1", "--max-occurrences", "5")
    assert cli.calls("start_job")[1][2].scope.occurrence_ids == ["occ_1"]
    assert cli.calls("start_job")[1][2].options.max_occurrences == 5


def test_run_needs_scope_and_rejects_both(cli: Cli) -> None:
    assert cli.run("--project", "prj_1", "run").exit_code == 2
    assert cli.run("--project", "prj_1", "run", "--source", "s", "--occurrence", "o").exit_code == 2


def test_run_p1_stage_is_feature_not_available(cli: Cli) -> None:
    r = cli.run("--project", "prj_1", "run", "--stages", "contexts", "--source", "src_1")
    assert r.exit_code == 1
    assert r.stderr.startswith("FEATURE_NOT_AVAILABLE:")
    assert cli.calls("start_job") == []


def test_run_unknown_stage_is_usage_error(cli: Cli) -> None:
    assert cli.run("--project", "prj_1", "run", "--stages", "bogus", "--source", "s").exit_code == 2


def test_run_wait_polls_to_terminal(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    cli.svc.responses["start_job"] = StartJobOut(job_id="job_1", steps_planned=3, next_poll_after_ms=10)
    states = iter(
        [f.job(JobState.QUEUED, queued=2), f.job(JobState.RUNNING, 1, 1), f.job(JobState.COMPLETED, 2)]
    )
    cli.svc.responses["get_job"] = lambda ctx, req: next(states)
    sleeps: list[float] = []
    monkeypatch.setattr(main, "_sleep", sleeps.append)
    r = cli.run("--project", "prj_1", "run", "--source", "src_1", "--wait")
    assert r.exit_code == 0, r.output
    assert len(cli.calls("get_job")) == 3 and len(sleeps) == 2
    assert "extract" in r.output and "succeeded=2" in r.output
    assert "completed" in r.output


def test_run_wait_failed_job_exits_1(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    cli.svc.responses["start_job"] = StartJobOut(job_id="job_1", steps_planned=3, next_poll_after_ms=10)
    cli.svc.responses["get_job"] = f.job(JobState.FAILED)
    monkeypatch.setattr(main, "_sleep", lambda s: None)
    assert cli.run("--project", "prj_1", "run", "--source", "src_1", "--wait").exit_code == 1


def test_jobs_show_control_and_recent(cli: Cli) -> None:
    cli.svc.responses["get_job"] = f.job(JobState.RUNNING, 1, 1)
    r = cli.run("--project", "prj_1", "jobs", "job_1", "--json")
    assert json.loads(r.output)["job_id"] == "job_1"
    cli.svc.responses["control_job"] = ControlJobOut(job_id="job_1", state=JobState.PAUSING)
    for flag, action in (
        ("--pause", "pause"),
        ("--resume", "resume"),
        ("--cancel", "cancel"),
        ("--retry-failed", "retry_failed"),
    ):
        assert cli.run("--project", "prj_1", "jobs", "job_1", flag).exit_code == 0
        (_, ctx, req) = cli.calls("control_job")[-1]
        assert req.action == action and req.job_id == "job_1" and len(req.idempotency_key) == 32
        assert_agent_ctx(ctx)
    cli.svc.responses["get_project"] = project_out()
    assert cli.run("--project", "prj_1", "jobs").exit_code == 0
    assert cli.run("--project", "prj_1", "jobs", "job_1", "--pause", "--cancel").exit_code == 2
    assert cli.run("--project", "prj_1", "jobs", "--pause").exit_code == 2


# ---------- references ----------


def test_list_filters_and_json(cli: Cli) -> None:
    cli.svc.responses["list_references"] = f.listing(f.occ_dict("occ_1"), f.occ_dict("occ_2"))
    r = cli.run(
        "--project", "prj_1", "list", "--source", "src_1", "--identity", "needs_review",
        "--classification", "eligible_found", "--reason", "DOI_TITLE_CONFLICT", "--query", "wave",
        "--sort", "year", "--limit", "10", "--json",
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["total"] == 2
    req = cli.calls("list_references")[0][2]
    assert req.filter.source_id == "src_1" and req.filter.identity == "needs_review"
    assert req.filter.classification == "eligible_found" and req.filter.reason == "DOI_TITLE_CONFLICT"
    assert req.query == "wave" and req.sort == "year" and req.limit == 10
    assert_agent_ctx(cli.calls("list_references")[0][1])
    r = cli.run("--project", "prj_1", "list")
    assert "occ_1" in r.output and "occ_2" in r.output
    assert cli.calls("list_references")[1][2].filter is None


def test_show_sections_and_candidates(cli: Cli) -> None:
    cli.svc.responses["get_reference"] = f.reference()
    r = cli.run("--project", "prj_1", "show", "occ_1")
    assert r.exit_code == 0, r.output
    assert "Title 1" in r.output and "Title matches candidate cand_1." in r.output
    assert "candidates" in cli.calls("get_reference")[0][2].sections
    assert json.loads(cli.run("--project", "prj_1", "show", "occ_1", "--json").output)["occurrence"]


def test_control_characters_stripped(cli: Cli) -> None:
    evil = "Smith \x1b[31mRED\x1b[0m \x07bell \x1b]0;title\x07x\r\n‮evil (2020)"
    cli.svc.responses["get_reference"] = f.reference(raw_text=evil)
    cli.svc.responses["list_references"] = f.listing(f.occ_dict(raw_text=evil))
    for args in (("show", "occ_1"), ("list",)):
        out = cli.run("--project", "prj_1", *args).output
        assert "Smith" in out and "evil (2020)" in out
        assert "\x1b" not in out and "\x07" not in out and "\r" not in out and "‮" not in out


def test_edit_takes_current_revision(cli: Cli) -> None:
    cli.svc.responses["get_reference"] = f.reference(revision=4)
    cli.svc.responses["update_reference"] = f.updated(revision=5)
    r = cli.run("--project", "prj_1", "edit", "occ_1", "--text", "Fixed text (2020).")
    assert r.exit_code == 0, r.output
    (_, ctx, req) = cli.calls("update_reference")[0]
    assert_agent_ctx(ctx)
    assert req.change.type == "edit_text" and req.change.expected_revision == 4
    assert req.change.text == "Fixed text (2020)."
    assert len(req.idempotency_key) == 32
    cli.run("--project", "prj_1", "edit", "occ_1", "--text", "T", "--expected-revision", "2")
    assert cli.calls("update_reference")[1][2].change.expected_revision == 2
    assert len(cli.calls("get_reference")) == 1


def test_edit_revision_conflict_prints_current_revisions(cli: Cli) -> None:
    cli.svc.responses["get_reference"] = f.reference(revision=9)
    cli.svc.responses["update_reference"] = RevisionConflict("stale", details={"current": 9})
    r = cli.run("--project", "prj_1", "edit", "occ_1", "--text", "T", "--expected-revision", "2")
    assert r.exit_code == 1
    assert "REVISION_CONFLICT: stale" in r.stderr
    assert "occ_1" in r.stderr and "9" in r.stderr


def test_split_merge_add_exclude(cli: Cli) -> None:
    cli.svc.responses["get_reference"] = lambda ctx, req: f.reference(
        req.occurrence_id, 4 if req.occurrence_id == "occ_1" else 6
    )
    cli.svc.responses["update_reference"] = f.updated()
    cli.svc.responses["add_reference"] = AddReferenceOut(occurrence_id="occ_9", revision=1)

    assert (
        cli.run(
            "--project", "prj_1", "split", "occ_1", "--part", "A (2020).", "--part", "B (2021)."
        ).exit_code
        == 0
    )
    ch = cli.calls("update_reference")[-1][2].change
    assert (
        ch.type == "split"
        and [p.text for p in ch.parts] == ["A (2020).", "B (2021)."]
        and ch.expected_revision == 4
    )
    assert cli.run("--project", "prj_1", "split", "occ_1", "--part", "only one").exit_code == 2

    assert cli.run("--project", "prj_1", "merge", "occ_1", "occ_2", "--text", "AB").exit_code == 0
    ch = cli.calls("update_reference")[-1][2].change
    assert ch.type == "merge" and ch.occurrence_ids == ["occ_1", "occ_2"] and ch.text == "AB"
    assert ch.expected_revisions == {"occ_1": 4, "occ_2": 6}
    cli.run(
        "--project",
        "prj_1",
        "merge",
        "occ_1",
        "occ_2",
        "--expected-revision",
        "occ_1=1",
        "--expected-revision",
        "occ_2=2",
    )
    assert cli.calls("update_reference")[-1][2].change.expected_revisions == {"occ_1": 1, "occ_2": 2}

    r = cli.run(
        "--project", "prj_1", "add", "--source", "src_1", "--text", "New ref (2019).", "--after", "occ_1"
    )
    assert r.exit_code == 0 and "occ_9" in r.output
    (_, ctx, req) = cli.calls("add_reference")[0]
    assert_agent_ctx(ctx)
    assert (req.source_id, req.text, req.after_occurrence_id) == ("src_1", "New ref (2019).", "occ_1")

    assert cli.run("--project", "prj_1", "exclude", "occ_1", "--reason", "noise").exit_code == 0
    ch = cli.calls("update_reference")[-1][2].change
    assert ch.type == "exclude" and ch.reason == "noise" and ch.expected_revision == 4
    assert cli.run("--project", "prj_1", "exclude", "occ_1", "--restore").exit_code == 0
    assert cli.calls("update_reference")[-1][2].change.type == "restore"


def test_attach_download_export_requests(cli: Cli) -> None:
    cli.svc.responses["attach_file"] = AttachFileOut(
        local_copy_id="lcp_1", checks={}, local_copy=LocalCopyState.USABLE
    )
    r = cli.run("--project", "prj_1", "attach", "occ_1", "paper.pdf", "--version", "accepted")
    assert r.exit_code == 0 and "lcp_1" in r.output
    req = cli.calls("attach_file")[0][2]
    assert req.version_hint == "accepted" and Path(req.path) == Path("paper.pdf").absolute()
    assert_agent_ctx(cli.calls("attach_file")[0][1])

    cli.svc.responses["download_files"] = JobOut(job_id="job_2")
    assert (
        cli.run("--project", "prj_1", "download", "--occurrence", "occ_1", "--max-files", "3").exit_code == 0
    )
    req = cli.calls("download_files")[0][2]
    assert req.scope.occurrence_ids == ["occ_1"] and req.budget.max_files == 3
    assert cli.run("--project", "prj_1", "download", "--source", "src_1").exit_code == 0
    assert cli.calls("download_files")[1][2].scope.filter.source_id == "src_1"

    cli.svc.responses["get_project"] = project_out(revision=7)
    cli.svc.responses["create_export"] = JobOut(job_id="job_3")
    r = cli.run(
        "--project", "prj_1", "export", "--revision", "7", "--format", "bibtex,csv", "--selection", "reviewed"
    )
    assert r.exit_code == 0 and "job_3" in r.output
    req = cli.calls("create_export")[0][2]
    assert req.project_revision == 7 and [x.value for x in req.formats] == ["bibtex", "csv"]
    assert req.selection == Selection.REVIEWED
    assert cli.run("--project", "prj_1", "export", "--format", "json", "--audit").exit_code == 0
    req = cli.calls("create_export")[1][2]
    assert req.selection == Selection.COMPLETE_AUDIT and "audit_report" in [x.value for x in req.formats]
    assert req.project_revision == 7  # default: current project revision
    r = cli.run("--project", "prj_1", "export", "--format", "ris")
    assert r.exit_code == 1 and r.stderr.startswith("FEATURE_NOT_AVAILABLE:")

    cli.svc.responses["draft_requests"] = JobOut(job_id="job_4")
    r = cli.run(
        "--project", "prj_1", "requests", "--occurrence", "occ_1", "--sender-name", "A. Student",
        "--language", "id", "--group-by-author",
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    req = cli.calls("draft_requests")[0][2]
    assert req.sender.name == "A. Student" and req.language == "id" and req.group_by_author


# ---------- provenance, errors, app lifecycle ----------


def test_provenance_cannot_be_set_from_options(cli: Cli) -> None:
    for opt in ("--origin", "--channel", "--approval", "--principal", "--tty"):
        r = cli.run("--project", "prj_1", "list", opt, "x")
        assert r.exit_code == 2, opt
    assert cli.calls("list_references") == []


def test_rc_error_mapping(cli: Cli) -> None:
    cli.svc.responses["list_references"] = NotAuthorized("scope references:read missing")
    r = cli.run("--project", "prj_1", "list")
    assert r.exit_code == 1
    assert "NOT_AUTHORIZED: scope references:read missing" in r.stderr
    assert "Grant the scope" in r.stderr  # recovery hint
    assert r.stdout == ""


def test_app_started_once_and_closed(cli: Cli) -> None:
    cli.svc.responses["list_references"] = f.listing()
    cli.run("--project", "prj_1", "list")
    assert cli.app.started == 1 and cli.app.closed == 1


def test_help_and_no_args(cli: Cli) -> None:
    assert cli.run("--help").exit_code == 0
    assert cli.app.started == 0  # help never opens the app


# ---------- serve ----------


def test_serve_web_not_available(cli: Cli) -> None:
    r = cli.run("serve", "web")
    assert r.exit_code == 1
    assert r.stderr.startswith("FEATURE_NOT_AVAILABLE:")
    assert cli.app.started == 0


def test_serve_mcp_calls_run_stdio(cli: Cli, monkeypatch: pytest.MonkeyPatch, rc_home: Path) -> None:
    seen: list[object] = []
    fake = types.ModuleType("refcollector.mcp.server")
    fake.run_stdio = seen.append  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "refcollector.mcp.server", fake)
    assert cli.run("serve", "mcp").exit_code == 0
    assert seen and seen[0].rc_home == rc_home.resolve()  # type: ignore[attr-defined]
    assert cli.app.started == 0  # run_stdio owns its app


def test_serve_executor_until_ctrl_c(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    def stop(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "_sleep", stop)
    r = cli.run("serve")
    assert r.exit_code == 0
    assert cli.app.started == 1 and cli.app.closed == 1


# ---------- config ----------


def test_config_set_email_and_allow_folder_persist(cli: Cli, rc_home: Path, tmp_path: Path) -> None:
    assert cli.run("config", "set", "email", "me@uni.example.org").exit_code == 0
    folder = tmp_path / "pdfs"
    folder.mkdir()
    assert cli.run("config", "allow-folder", str(folder)).exit_code == 0
    assert cli.run("config", "allow-folder", str(folder)).exit_code == 0  # idempotent
    s = load_settings(rc_home)
    assert s.email == "me@uni.example.org"
    assert s.allow_folders == [folder.resolve()]
    assert cli.run("config", "set", "colour", "red").exit_code == 2
    assert cli.app.started == 0  # config never opens the app


def test_config_show(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "secrets_present", lambda: {"openalex_key": True, "s2_key": False})
    cli.run("config", "set", "email", "me@uni.example.org")
    data = json.loads(cli.run("config", "show", "--json").output)
    assert data["email"] == "me@uni.example.org" and data["keys"] == {"openalex_key": True, "s2_key": False}
    assert "me@uni.example.org" in cli.run("config", "show").output


def test_config_set_key_hidden_prompt(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(main, "set_secret", stored.__setitem__)
    r = cli.run("config", "set-key", "openalex_key", input="s3cret-value\n")
    assert r.exit_code == 0, r.output
    assert stored == {"openalex_key": "s3cret-value"}
    assert "s3cret-value" not in r.output
    assert cli.run("config", "set-key", "other", input="x\n").exit_code == 2


def test_config_network_and_downloads(cli: Cli) -> None:
    cli.svc.responses["update_project_settings"] = project_out()
    assert cli.run("config", "network", "retrieval", "--project", "Thesis").exit_code == 0
    (_, ctx, req) = cli.calls("update_project_settings")[0]
    assert_agent_ctx(ctx)
    assert req.project_id == "prj_1" and req.network_mode == "retrieval" and req.download_enabled is None
    assert cli.run("config", "downloads", "on", "--project", "prj_1").exit_code == 0
    assert cli.calls("update_project_settings")[1][2].download_enabled is True
    assert cli.run("config", "downloads", "off", "--project", "prj_1").exit_code == 0
    assert cli.calls("update_project_settings")[2][2].download_enabled is False
    assert cli.run("config", "network", "bogus", "--project", "prj_1").exit_code == 2
    assert cli.run("config", "downloads", "maybe", "--project", "prj_1").exit_code == 2
    # global --project works too
    assert cli.run("--project", "prj_1", "config", "network", "offline").exit_code == 0
