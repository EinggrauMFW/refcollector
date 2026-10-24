"""WP-12: Typer application and commands (spec 14.1). Entry point: `refcollector`.

Every command calls `RefCollectorService`; no pipeline logic lives here (spec 5). The only place
where the process exits is this entry point (spec 6.6): commands raise `typer.Exit`, and Typer
turns that into the exit code.

Exit codes: 0 ok, 1 `RcError` (printed as `CODE: message` plus hint), 2 usage error.
"""

from __future__ import annotations

import asyncio
import functools
import getpass
import inspect
import io
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, ParamSpec, TypeVar

import typer
from keyring.errors import KeyringError
from pydantic import ValidationError

from refcollector.cli import render
from refcollector.cli import review as review_mod
from refcollector.cli.render import echo
from refcollector.config import (
    SECRET_NAMES,
    config_lock,
    load_settings,
    save_settings,
    secrets_present,
    set_secret,
)
from refcollector.contracts.api import (
    AddReferenceIn,
    AppHandle,
    AttachFileIn,
    CallContext,
    Change,
    ControlJobIn,
    CreateProjectIn,
    EditText,
    Exclude,
    GetJobIn,
    GetProjectIn,
    GetReferenceIn,
    GetSourceIn,
    ImportSourceIn,
    ListArtifactsIn,
    ListArtifactsOut,
    ListProjectsIn,
    ListReferencesIn,
    ListSourcesIn,
    ListSourcesOut,
    Merge,
    ReadArtifactIn,
    RefCollectorService,
    Restore,
    SetSourceSectionsIn,
    Split,
    SplitPart,
    UpdateProjectSettingsIn,
    UpdateReferenceIn,
)
from refcollector.domain.enums import (
    TERMINAL_JOB_STATES,
    AccessClassification,
    Channel,
    CopyVersion,
    DiscoveryState,
    DraftLanguage,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyState,
    NetworkMode,
    PolicyProfile,
    RetrievalState,
    Scope,
    Selection,
    VersionPolicy,
)
from refcollector.errors import (
    FeatureNotAvailable,
    IntegrationUnavailable,
    InvalidInput,
    RcError,
    RevisionConflict,
)
from refcollector.ids import new_id

app = typer.Typer(
    name="refcollector", no_args_is_help=True, help="RefCollector P0: bibliography to checked library."
)
project_app = typer.Typer(no_args_is_help=True, help="Create, list and show projects.")
config_app = typer.Typer(no_args_is_help=True, help="Settings, allowed folders, API keys, permissions.")
artifact_app = typer.Typer(no_args_is_help=True, help="Read export artifacts.")
app.add_typer(project_app, name="project")
app.add_typer(artifact_app, name="artifact")
app.add_typer(config_app, name="config")

_sleep = time.sleep  # tests replace this; `run --wait` and `serve` use it


# ---------- app lifecycle ----------


class _State:
    factory: Callable[[], AppHandle] | None = None
    app: AppHandle | None = None
    holds_executor: bool = False
    project: str | None = None
    resolved: dict[str, str]  # NAME_OR_ID -> project_id, per invocation

    def __init__(self) -> None:
        self.resolved = {}


_state = _State()


def _default_factory() -> AppHandle:
    from refcollector.bootstrap import open_app  # lazy: WP-10

    return open_app(load_settings())


def set_app_factory(factory: Callable[[], AppHandle] | None) -> None:
    """Replace how the app is built (tests). None restores `bootstrap.open_app`."""
    _state.factory = factory
    _state.app = None


def get_app() -> AppHandle:
    """Open the app once per invocation and try to become the executor (spec 6.1)."""
    if _state.app is None:
        app_handle = (_state.factory or _default_factory)()
        _state.holds_executor = app_handle.start_executor()
        _state.app = app_handle
    return _state.app


def _close_app() -> None:
    if _state.app is not None:
        _state.app.close()
        _state.app = None


def svc() -> RefCollectorService:
    return get_app().service


def _utf8_streams() -> None:
    """Write UTF-8 even when piped on Windows, where the default is the ANSI code page."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


@app.callback()
def _root(
    ctx: typer.Context,
    project: Annotated[
        str | None,
        typer.Option("--project", envvar="RC_PROJECT", help="Project name or ID (or set RC_PROJECT)."),
    ] = None,
) -> None:
    """RefCollector command line."""
    _utf8_streams()
    _state.project = project
    _state.resolved = {}
    ctx.call_on_close(_close_app)


# ---------- shared helpers ----------

Idem = Annotated[str | None, typer.Option("--idempotency-key", help="Reuse a key to retry safely.")]
AsJson = Annotated[bool, typer.Option("--json", help="Print the result as JSON.")]
Wait = Annotated[bool, typer.Option("--wait", help="Poll the job until it ends.")]
ExpectedRevision = Annotated[
    int | None,
    typer.Option("--expected-revision", help="Occurrence revision to edit. Default: the current one."),
]


def new_ctx() -> CallContext:
    """Provenance comes from here only (spec 7.4): non-interactive CLI is channel cli, origin agent."""
    return CallContext(
        principal=getpass.getuser(),
        channel=Channel.CLI,
        scopes=frozenset(Scope),
        request_id=new_id("request"),
    )


def idem(key: str | None) -> str:
    return key or uuid.uuid4().hex


P = ParamSpec("P")
R = TypeVar("R")


def guard(fn: Callable[P, R]) -> Callable[P, R]:
    """Command boundary: RcError -> `code: message` + hint on stderr, exit 1."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except RcError as e:
            echo(f"{e.code.value}: {e.message}", err=True)
            if e.recovery_hint:
                echo(f"hint: {e.recovery_hint}", err=True)
            if e.retry_after:
                echo(f"retry after {e.retry_after:g}s", err=True)
            raise typer.Exit(1) from None
        except ValidationError as e:
            echo(
                "INVALID_INPUT: "
                + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()),
                err=True,
            )
            raise typer.Exit(2) from None

    return wrapper


def project_id(override: str | None = None) -> str:
    """Resolve --project / RC_PROJECT (ID or exact name) through list_projects."""
    ident = override or _state.project
    if not ident:
        raise typer.BadParameter("pass --project NAME_OR_ID or set RC_PROJECT")
    if ident in _state.resolved:
        return _state.resolved[ident]
    matches: list[str] = []
    cursor = None
    while True:
        out = svc().list_projects(new_ctx(), ListProjectsIn(cursor=cursor, limit=100))
        for p in out.projects:
            if p.project_id == ident:
                _state.resolved[ident] = p.project_id
                return p.project_id
            if p.name == ident:
                matches.append(p.project_id)
        cursor = out.next_cursor
        if not cursor:
            break
    if len(matches) > 1:
        from refcollector.errors import InvalidInput

        raise InvalidInput(
            f"project name {ident!r} is ambiguous: {', '.join(matches)}", recovery_hint="Use the project ID."
        )
    if not matches:
        from refcollector.errors import NotFound

        raise NotFound(
            f"no project named or numbered {ident!r}", recovery_hint="List them: refcollector project list."
        )
    _state.resolved[ident] = matches[0]
    return matches[0]


def _csv(values: list[str] | None) -> list[str]:
    return [p.strip() for v in values or [] for p in v.split(",") if p.strip()]


def _revision(pid: str, occurrence_id: str, expected: int | None) -> int:
    if expected is not None:
        return expected
    ref = svc().get_reference(new_ctx(), GetReferenceIn(project_id=pid, occurrence_id=occurrence_id))
    return ref.occurrence.revision


def _update(pid: str, change: Change, key: str | None, occurrence_ids: list[str]) -> None:
    try:
        out = svc().update_reference(
            new_ctx(), UpdateReferenceIn(project_id=pid, change=change, idempotency_key=idem(key))
        )
    except RevisionConflict:
        echo("current revisions:", err=True)
        for oid in occurrence_ids:
            try:
                ref = svc().get_reference(new_ctx(), GetReferenceIn(project_id=pid, occurrence_id=oid))
                echo(f"  {oid}: revision {ref.occurrence.revision}", err=True)
            except RcError:
                echo(f"  {oid}: unknown", err=True)
        raise
    echo("changed: " + (", ".join(f"{r.occurrence_id}@{r.revision}" for r in out.changed) or "-"))
    if out.created:
        echo("created: " + ", ".join(f"{r.occurrence_id}@{r.revision}" for r in out.created))


def _job_started(pid: str, job_id: str, wait: bool) -> None:
    echo(f"job {job_id}" + ("" if wait else f"  (follow: refcollector jobs {job_id})"))
    if wait:
        _follow(pid, job_id)


def _follow(pid: str, job_id: str) -> None:
    """Poll get_job until the job ends (or pauses), printing stage counts when they change."""
    last = None
    while True:
        out = svc().get_job(new_ctx(), GetJobIn(project_id=pid, job_id=job_id))
        text = render.job(out)
        if text != last:
            echo(text)
            last = text
        if out.state in TERMINAL_JOB_STATES or out.state == JobState.PAUSED:
            break
        _sleep(min(max((out.next_poll_after_ms or 500) / 1000, 0.05), 5.0))
    if out.state in (JobState.FAILED, JobState.CANCELLED):
        raise typer.Exit(1)


def _abs(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))  # noqa: PTH100 - keep symlinks; the service checks them


# ---------- projects ----------


@project_app.command("create")
@guard
def project_create(
    name: str,
    profile: Annotated[PolicyProfile | None, typer.Option("--profile")] = None,
    version_policy: Annotated[VersionPolicy | None, typer.Option("--version-policy")] = None,
    idempotency_key: Idem = None,
) -> None:
    """Create a project."""
    out = svc().create_project(
        new_ctx(),
        CreateProjectIn(
            name=name,
            policy_profile=profile,
            version_policy=version_policy,
            idempotency_key=idem(idempotency_key),
        ),
    )
    echo(f"project {out.project_id}  revision {out.revision}")


@project_app.command("list")
@guard
def project_list(as_json: AsJson = False) -> None:
    """List projects."""
    out = svc().list_projects(new_ctx(), ListProjectsIn())
    render.dump_json(out) if as_json else echo(render.project_list(out))


@project_app.command("show")
@guard
def project_show(as_json: AsJson = False) -> None:
    """Show the project given by --project."""
    out = svc().get_project(new_ctx(), GetProjectIn(project_id=project_id()))
    render.dump_json(out) if as_json else echo(render.project(out))


# ---------- sources ----------


@app.command("import")
@guard
def import_source(
    path: Annotated[
        str | None, typer.Argument(metavar="[PATH]", help="PDF inside an allowed folder.")
    ] = None,
    text: Annotated[
        str | None, typer.Option("--text", help="Pasted bibliography text ('-' reads stdin).")
    ] = None,
    label: Annotated[str | None, typer.Option("--label")] = None,
    idempotency_key: Idem = None,
) -> None:
    """Import a PDF (PATH) or pasted text (--text)."""
    if (path is None) == (text is None):
        raise typer.BadParameter("give exactly one of PATH or --text")
    if text is not None:
        body = sys.stdin.read() if text == "-" else text
        source: dict[str, Any] = {"type": "text", "text": body, "label": label}
    else:
        assert path is not None
        source = {"type": "local_file", "path": _abs(path)}
    out = svc().import_source(
        new_ctx(),
        ImportSourceIn.model_validate(
            {"project_id": project_id(), "input": source, "idempotency_key": idem(idempotency_key)}
        ),
    )
    echo(f"source {out.source_id}" + ("  (already imported)" if out.reused else ""))
    echo(render.preflight(out.preflight))


Limit = Annotated[int | None, typer.Option("--limit", min=1, max=100, help="Page size; one page only.")]
Cursor = Annotated[str | None, typer.Option("--cursor", help="Continue after this cursor.")]


@app.command("sources")
@guard
def sources(limit: Limit = None, cursor: Cursor = None, as_json: AsJson = False) -> None:
    """List sources. Without --limit/--cursor, pages through all of them."""
    out = _list_sources(project_id(), limit, cursor, one_page=limit is not None or cursor is not None)
    render.dump_json(out) if as_json else echo(render.source_list(out))


def _list_sources(pid: str, limit: int | None, cursor: str | None, *, one_page: bool) -> ListSourcesOut:
    items = []
    while True:
        out = svc().list_sources(new_ctx(), ListSourcesIn(project_id=pid, cursor=cursor, limit=limit))
        items += out.sources
        cursor = out.next_cursor
        if one_page or not cursor:
            break
    return ListSourcesOut(sources=items, next_cursor=cursor)


@app.command("source")
@guard
def source_show(source_id: str, as_json: AsJson = False) -> None:
    """Show one source: preflight, sections, extraction state and warnings."""
    out = svc().get_source(new_ctx(), GetSourceIn(project_id=project_id(), source_id=source_id))
    render.dump_json(out) if as_json else echo(render.source(out))


_RANGE = re.compile(r"(\d+)(?:-(\d+))?")


def _parse_pages(spec: str) -> list[tuple[int, int]]:
    ranges = []
    for part in spec.split(","):
        m = _RANGE.fullmatch(part.strip())
        if not m:
            raise typer.BadParameter(f"bad page range {part!r}; use A-B[,C-D]", param_hint="--pages")
        a, b = int(m[1]), int(m[2] or m[1])
        if a > b:
            raise typer.BadParameter(f"page range {part!r} runs backwards", param_hint="--pages")
        ranges.append((a, b))
    return ranges


@app.command("sections")
@guard
def sections(
    source_id: str,
    pages: Annotated[str, typer.Option("--pages", help="Bibliography pages A-B[,C-D], 0-based, inclusive.")],
    expected_revision: Annotated[
        int | None, typer.Option("--expected-revision", help="Source revision.")
    ] = None,
    idempotency_key: Idem = None,
) -> None:
    """Set the bibliography page ranges of a source (re-extract afterwards)."""
    ranges = _parse_pages(pages)
    pid = project_id()
    rev = expected_revision
    if rev is None:
        rev = svc().get_source(new_ctx(), GetSourceIn(project_id=pid, source_id=source_id)).revision
    out = svc().set_source_sections(
        new_ctx(),
        SetSourceSectionsIn.model_validate(
            {
                "project_id": pid,
                "source_id": source_id,
                "sections": [{"page_start": a, "page_end": b} for a, b in ranges],
                "expected_source_revision": rev,
                "idempotency_key": idem(idempotency_key),
            }
        ),
    )
    echo(f"{len(out.sections)} section(s) set. Run: refcollector run --stages extract --source {source_id}")


# ---------- jobs ----------


@app.command("run")
@guard
def run(
    stages: Annotated[
        str, typer.Option("--stages", help="Comma list: extract,resolve,discover_access.")
    ] = "extract,resolve,discover_access",
    source: Annotated[list[str] | None, typer.Option("--source", help="Source ID (repeatable).")] = None,
    occurrence: Annotated[
        list[str] | None, typer.Option("--occurrence", help="Occurrence ID (repeatable).")
    ] = None,
    max_occurrences: Annotated[int | None, typer.Option("--max-occurrences", min=1)] = None,
    refresh: Annotated[bool, typer.Option("--refresh")] = False,
    wait: Wait = False,
    idempotency_key: Idem = None,
) -> None:
    """Start a job over sources or occurrences. Without either, use every source of the project."""
    if source and occurrence:
        raise typer.BadParameter("give --source or --occurrence, not both")
    from refcollector.contracts.api import StartJobIn

    pid = project_id()
    if not source and not occurrence:
        source = [s.source_id for s in _list_sources(pid, None, None, one_page=False).sources]
        if not source:
            raise typer.BadParameter("the project has no sources; run `refcollector import` first")
    req = StartJobIn.model_validate(
        {
            "project_id": pid,
            "stages": _csv([stages]),
            "scope": {"source_ids": source} if source else {"occurrence_ids": occurrence},
            "options": {"max_occurrences": max_occurrences, "refresh": refresh},
            "idempotency_key": idem(idempotency_key),
        }
    )
    out = svc().start_job(new_ctx(), req)
    echo(f"planned {out.steps_planned} step(s)")
    _job_started(pid, out.job_id, wait)


@app.command("jobs")
@guard
def jobs(
    job_id: Annotated[str | None, typer.Argument()] = None,
    pause: Annotated[bool, typer.Option("--pause")] = False,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    cancel: Annotated[bool, typer.Option("--cancel")] = False,
    retry_failed: Annotated[bool, typer.Option("--retry-failed")] = False,
    as_json: AsJson = False,
    idempotency_key: Idem = None,
) -> None:
    """Show recent jobs, one job, or control a job."""
    actions = [
        n
        for n, on in (
            ("pause", pause),
            ("resume", resume),
            ("cancel", cancel),
            ("retry_failed", retry_failed),
        )
        if on
    ]
    if len(actions) > 1 or (actions and not job_id):
        raise typer.BadParameter("give JOB_ID and at most one of --pause --resume --cancel --retry-failed")
    pid = project_id()
    if job_id is None:
        proj = svc().get_project(new_ctx(), GetProjectIn(project_id=pid))
        rows = [[j.job_id, j.state, ",".join(j.stages), j.created_at.isoformat()] for j in proj.recent_jobs]
        echo(render.table(["JOB", "STATE", "STAGES", "CREATED"], rows))
    elif actions:
        out = svc().control_job(
            new_ctx(),
            ControlJobIn.model_validate(
                {
                    "project_id": pid,
                    "job_id": job_id,
                    "action": actions[0],
                    "idempotency_key": idem(idempotency_key),
                }
            ),
        )
        echo(f"{out.job_id}: {out.state}")
    else:
        out = svc().get_job(new_ctx(), GetJobIn(project_id=pid, job_id=job_id))
        render.dump_json(out) if as_json else echo(render.job(out))


# ---------- references ----------


@app.command("list")
@guard
def list_(
    source: Annotated[str | None, typer.Option("--source")] = None,
    job: Annotated[str | None, typer.Option("--job")] = None,
    extraction: Annotated[ExtractionState | None, typer.Option("--extraction")] = None,
    identity: Annotated[IdentityState | None, typer.Option("--identity")] = None,
    discovery: Annotated[DiscoveryState | None, typer.Option("--discovery")] = None,
    classification: Annotated[AccessClassification | None, typer.Option("--classification")] = None,
    local_copy: Annotated[LocalCopyState | None, typer.Option("--local-copy")] = None,
    retrieval: Annotated[RetrievalState | None, typer.Option("--retrieval")] = None,
    reason: Annotated[
        str | None, typer.Option("--reason", help="Reason code, e.g. DOI_TITLE_CONFLICT.")
    ] = None,
    query: Annotated[str | None, typer.Option("--query")] = None,
    sort: Annotated[str, typer.Option("--sort", help="order, year or updated.")] = "order",
    limit: Annotated[int | None, typer.Option("--limit", min=1, max=100)] = None,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    as_json: AsJson = False,
) -> None:
    """List occurrences with filters."""
    from refcollector.contracts.api import ReferenceFilter

    flt = {
        k: v
        for k, v in dict(
            source_id=source, job_id=job, extraction=extraction, identity=identity, discovery=discovery,
            classification=classification, local_copy=local_copy, retrieval=retrieval, reason=reason,
        ).items()
        if v is not None
    }  # fmt: skip
    out = svc().list_references(
        new_ctx(),
        ListReferencesIn.model_validate(
            {
                "project_id": project_id(),
                "filter": ReferenceFilter(**flt) if flt else None,
                "query": query,
                "sort": sort,
                "cursor": cursor,
                "limit": limit,
            }
        ),
    )
    render.dump_json(out) if as_json else echo(render.occurrences(out))


@app.command("show")
@guard
def show(occurrence_id: str, as_json: AsJson = False) -> None:
    """Show one occurrence with candidates, access, copies and history."""
    out = svc().get_reference(
        new_ctx(),
        GetReferenceIn.model_validate(
            {
                "project_id": project_id(),
                "occurrence_id": occurrence_id,
                "sections": ["candidates", "access", "local_copies", "files", "history"],
            }
        ),
    )
    render.dump_json(out) if as_json else echo(render.reference(out))


@app.command("review")
@guard
def review(
    all_: Annotated[bool, typer.Option("--all", help="Also walk accepted_auto and accepted_agent.")] = False,
) -> None:
    """Review candidates on this terminal: the approval surface for accepted_user (spec 7.4)."""
    if not review_mod.is_tty():
        echo(
            "refcollector review needs an interactive terminal: stdin and stdout must both be a TTY. "
            "Run it in a terminal, not in a pipe or an agent shell.",
            err=True,
        )
        raise typer.Exit(2)
    review_mod.run_review(svc(), project_id(), include_all=all_)


@app.command("edit")
@guard
def edit(
    occurrence_id: str,
    text: Annotated[str, typer.Option("--text", help="Corrected citation text.")],
    expected_revision: ExpectedRevision = None,
    idempotency_key: Idem = None,
) -> None:
    """Correct the citation text of an occurrence."""
    pid = project_id()
    rev = _revision(pid, occurrence_id, expected_revision)
    _update(
        pid,
        EditText(type="edit_text", occurrence_id=occurrence_id, text=text, expected_revision=rev),
        idempotency_key,
        [occurrence_id],
    )


@app.command("split")
@guard
def split(
    occurrence_id: str,
    part: Annotated[
        list[str] | None, typer.Option("--part", help="Text of one part (repeat, 2 or more).")
    ] = None,
    expected_revision: ExpectedRevision = None,
    idempotency_key: Idem = None,
) -> None:
    """Split a merged occurrence into parts."""
    if not part or len(part) < 2:
        raise typer.BadParameter("give at least two --part", param_hint="--part")
    pid = project_id()
    rev = _revision(pid, occurrence_id, expected_revision)
    change = Split(
        type="split",
        occurrence_id=occurrence_id,
        parts=[SplitPart(text=t) for t in part],
        expected_revision=rev,
    )
    _update(pid, change, idempotency_key, [occurrence_id])


@app.command("merge")
@guard
def merge(
    occurrence_ids: Annotated[list[str], typer.Argument(metavar="OCCURRENCE_ID...")],
    text: Annotated[str | None, typer.Option("--text", help="Text of the merged occurrence.")] = None,
    expected_revision: Annotated[
        list[str] | None,
        typer.Option("--expected-revision", help="OCC=N (repeat). Default: current revisions."),
    ] = None,
    idempotency_key: Idem = None,
) -> None:
    """Merge two or more occurrences."""
    if len(occurrence_ids) < 2:
        raise typer.BadParameter("give at least two occurrence IDs")
    given: dict[str, int] = {}
    for item in expected_revision or []:
        occ, _, num = item.partition("=")
        if not num.isdigit():
            raise typer.BadParameter(f"{item!r}: use OCC=N", param_hint="--expected-revision")
        given[occ] = int(num)
    pid = project_id()
    revs = {o: _revision(pid, o, given.get(o)) for o in occurrence_ids}
    _update(
        pid,
        Merge(type="merge", occurrence_ids=occurrence_ids, text=text, expected_revisions=revs),
        idempotency_key,
        occurrence_ids,
    )


@app.command("add")
@guard
def add(
    source: Annotated[str, typer.Option("--source", help="Source ID.")],
    text: Annotated[str, typer.Option("--text", help="Citation text.")],
    after: Annotated[str | None, typer.Option("--after", help="Insert after this occurrence ID.")] = None,
    idempotency_key: Idem = None,
) -> None:
    """Add a missing reference."""
    out = svc().add_reference(
        new_ctx(),
        AddReferenceIn(
            project_id=project_id(),
            source_id=source,
            text=text,
            after_occurrence_id=after,
            idempotency_key=idem(idempotency_key),
        ),
    )
    echo(f"occurrence {out.occurrence_id}  revision {out.revision}")


@app.command("exclude")
@guard
def exclude(
    occurrence_id: str,
    restore: Annotated[bool, typer.Option("--restore", help="Bring an excluded occurrence back.")] = False,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    expected_revision: ExpectedRevision = None,
    idempotency_key: Idem = None,
) -> None:
    """Exclude an occurrence (noise), or restore it."""
    pid = project_id()
    rev = _revision(pid, occurrence_id, expected_revision)
    kind = Restore if restore else Exclude
    change = kind(
        type="restore" if restore else "exclude",
        occurrence_id=occurrence_id,
        reason=reason,
        expected_revision=rev,
    )  # type: ignore[arg-type]
    _update(pid, change, idempotency_key, [occurrence_id])


# ---------- files, exports, drafts ----------


@app.command("artifacts")
@guard
def artifacts(
    kind: Annotated[str | None, typer.Option("--kind", help="Artifact kind, e.g. manifest.")] = None,
    limit: Limit = None,
    cursor: Cursor = None,
    as_json: AsJson = False,
) -> None:
    """List export artifacts. Without --limit/--cursor, pages through all of them."""
    pid = project_id()
    one_page = limit is not None or cursor is not None
    items = []
    while True:
        out = svc().list_artifacts(
            new_ctx(), ListArtifactsIn(project_id=pid, kind=kind, cursor=cursor, limit=limit)
        )
        items += out.artifacts
        cursor = out.next_cursor
        if one_page or not cursor:
            break
    out = ListArtifactsOut(artifacts=items, next_cursor=cursor)
    render.dump_json(out) if as_json else echo(render.artifact_list(out.artifacts, out.next_cursor))


@artifact_app.command("read")
@guard
def artifact_read(
    artifact_id: str,
    offset: Annotated[int, typer.Option("--offset", min=0, help="Start byte.")] = 0,
    length: Annotated[int | None, typer.Option("--length", min=1, max=65536, help="Max bytes.")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Read every chunk until end of file.")] = False,
    as_json: AsJson = False,
) -> None:
    """Read artifact text. --json prints the exact text; plain output is cleaned for the terminal."""
    pid = project_id()
    ctx = new_ctx()

    def chunk(at: int) -> Any:
        req = ReadArtifactIn(project_id=pid, artifact_id=artifact_id, offset=at, length=length)
        return svc().read_artifact(ctx, req)

    out = chunk(offset)
    if all_ and out.text is not None:
        parts = [out.text]
        while not out.eof:
            offset += len(parts[-1].encode())  # service offsets are UTF-8 bytes
            out = chunk(offset)
            if not out.text:
                raise InvalidInput(f"read at byte {offset} returned no text before end of file")
            parts.append(out.text)
        out = out.model_copy(update={"text": "".join(parts)})
    if as_json:
        render.dump_json(out)
    elif out.text is None:
        echo("binary artifact; no text. Descriptor:")
        echo(render.artifact_list([out.descriptor]))
    else:
        echo(out.text)
        if not all_:
            echo("eof" if out.eof else f"next offset: {offset + len(out.text.encode())}", err=True)


@app.command("attach")
@guard
def attach(
    occurrence_id: str,
    path: str,
    version: Annotated[CopyVersion | None, typer.Option("--version", help="Version of the file.")] = None,
    idempotency_key: Idem = None,
) -> None:
    """Attach a local PDF to an occurrence."""
    out = svc().attach_file(
        new_ctx(),
        AttachFileIn(
            project_id=project_id(),
            occurrence_id=occurrence_id,
            path=_abs(path),
            version_hint=version,
            idempotency_key=idem(idempotency_key),
        ),
    )
    echo(f"local copy {out.local_copy_id}: {out.local_copy}")
    for name, check in out.checks.items():
        echo(f"  {name}: {check.outcome}")


@app.command("download")
@guard
def download(
    occurrence: Annotated[
        list[str] | None, typer.Option("--occurrence", help="Occurrence ID (repeatable).")
    ] = None,
    source: Annotated[str | None, typer.Option("--source", help="All occurrences of a source.")] = None,
    version_policy: Annotated[VersionPolicy | None, typer.Option("--version-policy")] = None,
    max_files: Annotated[int | None, typer.Option("--max-files", min=1)] = None,
    max_bytes: Annotated[int | None, typer.Option("--max-bytes", min=1)] = None,
    wait: Wait = False,
    idempotency_key: Idem = None,
) -> None:
    """Download eligible open-access copies (needs the project download grant)."""
    pid = project_id()
    out = svc().download_files(
        new_ctx(),
        _download_in(pid, occurrence, source, version_policy, max_files, max_bytes, idem(idempotency_key)),
    )
    _job_started(pid, out.job_id, wait)


def _download_in(pid, occurrence, source, version_policy, max_files, max_bytes, key):  # type: ignore[no-untyped-def]
    from refcollector.contracts.api import DownloadFilesIn

    return DownloadFilesIn.model_validate(
        {
            "project_id": pid,
            "scope": {"occurrence_ids": occurrence} if occurrence else {"filter": {"source_id": source}},
            "version_policy": version_policy,
            "budget": {"max_files": max_files, "max_bytes": max_bytes} if max_files or max_bytes else None,
            "idempotency_key": key,
        }
    )


@app.command("export")
@guard
def export(
    revision: Annotated[
        int | None, typer.Option("--revision", help="Project revision. Default: current.")
    ] = None,
    format_: Annotated[
        list[str] | None, typer.Option("--format", help="bibtex,csl_json,csv,json,unresolved,audit_report")
    ] = None,
    selection: Annotated[Selection, typer.Option("--selection")] = Selection.ACCEPTED,
    audit: Annotated[
        bool, typer.Option("--audit", help="complete_audit selection plus the audit report.")
    ] = False,
    wait: Wait = False,
    idempotency_key: Idem = None,
) -> None:
    """Export the project at a revision."""
    from refcollector.contracts.api import CreateExportIn

    formats = _csv(format_)
    if audit:
        selection = Selection.COMPLETE_AUDIT
        if "audit_report" not in formats:
            formats.append("audit_report")
    if not formats:
        raise typer.BadParameter("give --format (or --audit)", param_hint="--format")
    pid = project_id()
    rev = (
        revision
        if revision is not None
        else svc().get_project(new_ctx(), GetProjectIn(project_id=pid)).revision
    )
    req = CreateExportIn.model_validate(
        {
            "project_id": pid,
            "project_revision": rev,
            "formats": formats,
            "selection": selection,
            "idempotency_key": idem(idempotency_key),
        }
    )
    _job_started(pid, svc().create_export(new_ctx(), req).job_id, wait)


@app.command("requests")
@guard
def requests(
    occurrence: Annotated[list[str], typer.Option("--occurrence", help="Occurrence ID (repeatable).")],
    sender_name: Annotated[str, typer.Option("--sender-name")],
    language: Annotated[DraftLanguage, typer.Option("--language")] = DraftLanguage.EN,
    affiliation: Annotated[str | None, typer.Option("--affiliation")] = None,
    purpose: Annotated[str | None, typer.Option("--purpose")] = None,
    honorific: Annotated[str | None, typer.Option("--honorific")] = None,
    group_by_author: Annotated[bool, typer.Option("--group-by-author")] = False,
    wait: Wait = False,
    idempotency_key: Idem = None,
) -> None:
    """Draft copy-request messages for occurrences without an eligible copy."""
    from refcollector.contracts.api import DraftRequestsIn

    pid = project_id()
    req = DraftRequestsIn.model_validate(
        {
            "project_id": pid,
            "scope": {"occurrence_ids": occurrence},
            "sender": {
                "name": sender_name,
                "affiliation": affiliation,
                "purpose": purpose,
                "honorific_style": honorific,
            },
            "language": language,
            "group_by_author": group_by_author,
            "idempotency_key": idem(idempotency_key),
        }
    )
    _job_started(pid, svc().draft_requests(new_ctx(), req).job_id, wait)


# ---------- serve ----------


class ServeTarget(StrEnum):
    MCP = "mcp"
    WEB = "web"


@app.command("serve")
@guard
def serve(
    target: Annotated[ServeTarget | None, typer.Argument()] = None,
    host: Annotated[str, typer.Option("--host", help="Host address to bind to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = 8000,
) -> None:
    """Run the executor (no argument), the MCP stdio server (mcp), or the local web server (web)."""
    if target == ServeTarget.WEB:
        if not (1 <= port <= 65535):
            raise InvalidInput(f"Invalid port {port}. Port must be between 1 and 65535.")
        if host not in ("127.0.0.1", "localhost"):
            raise InvalidInput(
                "The web server only binds to 127.0.0.1 loopback (spec 7). Remote mode is prohibited."
            )
        try:
            from refcollector.web.server import run_web
        except ImportError as e:
            raise FeatureNotAvailable(
                "The web extra is not installed.",
                recovery_hint="Install with: pip install refcollector[web]",
            ) from e
        run_web(load_settings(), host=host, port=port)
        return
    if target == ServeTarget.MCP:
        try:
            from refcollector.mcp.server import run_stdio
        except ImportError as e:
            raise IntegrationUnavailable("The MCP server is not available in this build.") from e
        result = run_stdio(load_settings())
        if inspect.iscoroutine(result):
            asyncio.run(result)
        return
    get_app()
    echo(
        "executor running (this process holds the lock)"
        if _state.holds_executor
        else "another process is the executor; this one will take over if it exits",
        err=True,
    )
    try:
        while True:
            _sleep(1.0)
    except KeyboardInterrupt:
        echo("stopped", err=True)


# ---------- config ----------


@config_app.command("show")
@guard
def config_show(as_json: AsJson = False) -> None:
    """Show settings. API keys show presence only."""
    s = load_settings()
    try:
        keys = secrets_present()
    except KeyringError:
        keys = {}
    data = {
        "rc_home": str(s.rc_home),
        "email": s.email,
        "allow_folders": [str(p) for p in s.allow_folders],
        "download_enabled_global": s.download_enabled_global,
        "pdf_fallback_url": s.pdf_fallback_url,
        "pdf_browser_python": s.pdf_browser_python,
        "pdf_browser_binary": s.pdf_browser_binary,
        "enabled_providers": s.enabled_providers,
        "mcp_scopes": sorted(x.value for x in s.mcp_scopes),
        "keys": keys,
    }
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    echo(f"rc_home: {data['rc_home']}")
    echo(f"email: {s.email or '(not set)'}")
    echo(f"PDF fallback: {s.pdf_fallback_url or 'disabled'}")
    echo(f"PDF browser Python: {s.pdf_browser_python or 'disabled'}")
    echo(f"PDF browser binary: {s.pdf_browser_binary or 'disabled'}")
    echo("allowed folders: " + (", ".join(data["allow_folders"]) or "(none)"))
    echo("providers: " + ", ".join(s.enabled_providers))
    echo(
        "keys: " + (", ".join(f"{k}={'set' if v else 'missing'}" for k, v in keys.items()) or "(unavailable)")
    )


@config_app.command("pdf-browser")
@guard
def config_pdf_browser(
    python_path: str,
    binary: Annotated[Path | None, typer.Option("--binary")] = None,
) -> None:
    """Set external browser Python and Chromium paths, or select off."""
    if python_path == "off":
        python_value = binary_value = None
    else:
        if binary is None:
            raise typer.BadParameter("--binary is required when enabling the PDF browser")
        python_file = Path(python_path).expanduser().resolve()
        binary_file = binary.expanduser().resolve()
        if not python_file.is_file():
            raise typer.BadParameter("Python path must be an existing file")
        if not binary_file.is_file():
            raise typer.BadParameter("Chromium path must be an existing file")
        python_value, binary_value = str(python_file), str(binary_file)
    settings = load_settings()
    with config_lock(settings.rc_home):
        settings = load_settings(settings.rc_home)
        settings.pdf_browser_python = python_value
        settings.pdf_browser_binary = binary_value
        save_settings(settings)
    echo(f"PDF browser: {python_value or 'disabled'}")


@config_app.command("pdf-fallback")
@guard
def config_pdf_fallback(url_template: str) -> None:
    """Select box, su, st, ru, a custom HTTPS DOI template, or off."""
    from refcollector.files.pdf_fallback import validate_template

    presets = {name: f"https://sci-hub.{name}/{{doi}}" for name in ("box", "su", "st", "ru")}
    selected = presets.get(url_template, url_template)
    for template in presets.values():
        if selected.rstrip("/") == template.removesuffix("/{doi}"):
            selected = template
            break
    value = None if selected == "off" else validate_template(selected)
    s = load_settings()
    with config_lock(s.rc_home):
        s = load_settings(s.rc_home)
        s.pdf_fallback_url = value
        save_settings(s)
    echo(f"PDF fallback: {value or 'disabled'}")


@config_app.command("set")
@guard
def config_set(key: str, value: str) -> None:
    """Set a value. Supported: email (sent to providers' polite pools)."""
    if key != "email":
        raise typer.BadParameter(f"unknown setting {key!r}; supported: email", param_hint="KEY")
    s = load_settings()
    with config_lock(s.rc_home):
        s = load_settings(s.rc_home)
        s.email = value
        save_settings(s)
    echo(f"email set to {value}")


@config_app.command("allow-folder")
@guard
def config_allow_folder(path: str) -> None:
    """Allow imports and attachments from a folder (empty by default)."""
    folder = Path(path).expanduser().resolve()
    s = load_settings()
    with config_lock(s.rc_home):
        s = load_settings(s.rc_home)
        if folder not in s.allow_folders:
            s.allow_folders.append(folder)
            save_settings(s)
    if not folder.is_dir():
        echo(f"warning: {folder} is not an existing folder", err=True)
    echo(f"allowed: {folder}")


@config_app.command("set-key")
@guard
def config_set_key(name: str) -> None:
    """Store an API key in the OS keyring (hidden prompt)."""
    if name not in SECRET_NAMES:
        raise typer.BadParameter(
            f"unknown key {name!r}; choose from {', '.join(SECRET_NAMES)}", param_hint="NAME"
        )
    value = typer.prompt(f"Value for {name}", hide_input=True)
    s = load_settings()
    with config_lock(s.rc_home):
        try:
            set_secret(name, value)
        except KeyringError as e:
            raise IntegrationUnavailable(f"The OS keyring refused the key: {e}") from e
    echo(f"{name} stored in the OS keyring")


class OnOff(StrEnum):
    ON = "on"
    OFF = "off"


@config_app.command("network")
@guard
def config_network(
    mode: NetworkMode,
    project: Annotated[str | None, typer.Option("--project", help="Project name or ID.")] = None,
    idempotency_key: Idem = None,
) -> None:
    """Set the project network mode: offline, metadata or retrieval."""
    out = svc().update_project_settings(
        new_ctx(),
        UpdateProjectSettingsIn(
            project_id=project_id(project), network_mode=mode.value, idempotency_key=idem(idempotency_key)
        ),  # type: ignore[arg-type]
    )
    echo(f"{out.name}: network mode {out.network_mode}")


@config_app.command("downloads")
@guard
def config_downloads(
    state: OnOff,
    project: Annotated[str | None, typer.Option("--project", help="Project name or ID.")] = None,
    idempotency_key: Idem = None,
) -> None:
    """Allow or forbid file downloads for a project."""
    pid = project_id(project)
    svc().update_project_settings(
        new_ctx(),
        UpdateProjectSettingsIn(
            project_id=pid, download_enabled=state == OnOff.ON, idempotency_key=idem(idempotency_key)
        ),
    )
    echo(f"downloads {state.value} for {pid}")
