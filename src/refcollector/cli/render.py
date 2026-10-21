"""WP-12: terminal rendering helpers. Spec 13, 14.1.

Document and provider text is untrusted: every line printed through `echo` loses ANSI escape
sequences, control characters and bidi overrides first (spec 13). JSON output is safe because
JSON escapes control characters.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any

import typer
from pydantic import BaseModel

from refcollector.contracts.api import (
    ArtifactDescriptor,
    CandidateView,
    GetJobOut,
    GetProjectOut,
    GetReferenceOut,
    GetSourceOut,
    ListProjectsOut,
    ListReferencesOut,
    ListSourcesOut,
)
from refcollector.domain.models import Person, Preflight, SectionInfo, WorkMetadata

_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC
    r"|\x1b[@-_]"  # two-character escapes
)
_BIDI = set("‎‏‪‫‬‭‮⁦⁧⁨⁩")
MAX_CANDIDATES_SHOWN = 5


def clean(text: str) -> str:
    """Strip ANSI sequences and control characters; keep newlines, tabs become spaces."""
    out = []
    for ch in _ANSI.sub("", text):
        if ch == "\n":
            out.append(ch)
        elif ch == "\t":
            out.append(" ")
        elif unicodedata.category(ch) != "Cc" and ch not in _BIDI:
            out.append(ch)
    return "".join(out)


def one_line(text: str, width: int | None = None) -> str:
    out = " ".join(clean(text).split())
    return out if width is None or len(out) <= width else out[: width - 1] + "…"


def echo(text: str = "", *, err: bool = False) -> None:
    typer.echo(clean(text), err=err)


def dump_json(model: BaseModel) -> None:
    typer.echo(model.model_dump_json(indent=2))


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]], width: int = 60) -> str:
    cells = [[one_line(str(c), width) for c in row] for row in rows]
    cols = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, cols, strict=True)).rstrip()]
    lines += ["  ".join(c.ljust(w) for c, w in zip(r, cols, strict=True)).rstrip() for r in cells]
    return "\n".join(lines)


# ---------- DTO text ----------


def person(p: Person) -> str:
    if p.literal:
        return p.literal
    return ", ".join(x for x in (p.family, p.given) if x)


def work_line(m: WorkMetadata) -> str:
    authors = "; ".join(person(a) for a in m.authors[:3]) + (" et al." if len(m.authors) > 3 else "")
    parts = [
        authors,
        f"({m.year})" if m.year else "",
        m.title or "(no title)",
        m.venue or "",
        f"doi:{m.doi}" if m.doi else "",
    ]
    return " ".join(p for p in parts if p)


def citation_text(raw: str, edited: str | None) -> str:
    return edited if edited is not None else raw


def project_list(out: ListProjectsOut) -> str:
    rows = [
        [p.project_id, p.name, p.revision, p.counts.get("occurrences", 0), p.updated_at.isoformat()]
        for p in out.projects
    ]
    return table(["ID", "NAME", "REV", "OCCURRENCES", "UPDATED"], rows)


def project(out: GetProjectOut) -> str:
    lines = [
        f"{out.name} ({out.project_id}) revision {out.revision}",
        f"network: {out.network_mode}  sources: {out.sources_count}",
        f"counts: {out.counts}",
    ]
    lines += [f"job {j.job_id} {j.state} {','.join(j.stages)}" for j in out.recent_jobs]
    return "\n".join(lines)


def job(out: GetJobOut) -> str:
    lines = [f"{out.job_id}: {out.state}"]
    for s in out.stages:
        lines.append(
            f"  {s.name}: queued={s.queued} running={s.running} succeeded={s.succeeded} "
            f"failed_retryable={s.failed_retryable} failed_permanent={s.failed_permanent} skipped={s.skipped}"
        )
    lines += [f"  warning: {w}" for w in out.warnings]
    return "\n".join(lines)


def section_line(s: SectionInfo) -> str:
    kind = "inferred" if s.inferred else "manual" if s.manual else "detected"
    pages = f"{s.page_start}-{s.page_end}"
    return f"section {s.section_id}: pages {pages} (0-based)  heading {s.heading or '-'}  {kind}"


def preflight(p: Preflight) -> str:
    lines = [f"pages {p.pages}  text coverage {p.text_coverage:.0%}  estimated entries {p.estimated_entries}"]
    lines += [section_line(s) for s in p.sections] or ["sections: none detected"]
    lines += [f"warning: {w}" for w in p.warnings]
    return "\n".join(lines)


def source_list(out: ListSourcesOut) -> str:
    rows = [
        [s.source_id, s.label, s.type, s.pages, s.sections, s.extraction_state, s.occurrence_count]
        for s in out.sources
    ]
    tail = f"\nnext cursor: {out.next_cursor}" if out.next_cursor else ""
    return table(["ID", "LABEL", "TYPE", "PAGES", "SECTIONS", "EXTRACTION", "OCCURRENCES"], rows) + tail


def source(out: GetSourceOut) -> str:
    s = out.source
    ex = out.extraction
    lines = [
        f"{s.label} ({s.source_id}) revision {out.revision}  type {s.type}",
        preflight(out.preflight),
        f"extraction: {ex.get('state')}  adapter: {ex.get('adapter') or '-'}"
        f"  occurrences: {ex.get('occurrences')}",
    ]
    lines += [f"extraction warning {code}: {n}" for code, n in (ex.get("warnings_by_code") or {}).items()]
    if out.sections != out.preflight.sections:
        lines += [section_line(x) for x in out.sections]
    lines += [f"job {j.job_id} {j.state} {','.join(j.stages)}" for j in out.jobs]
    return "\n".join(lines)


def artifact_list(artifacts: Sequence[ArtifactDescriptor], next_cursor: str | None = None) -> str:
    rows = [
        [a.artifact_id, a.kind, a.name, a.media_type, a.bytes, a.project_revision, a.sha256]
        for a in artifacts
    ]
    head = table(["ID", "KIND", "NAME", "TYPE", "BYTES", "REV", "SHA256"], rows, width=64)
    return head + (f"\nnext cursor: {next_cursor}" if next_cursor else "")


def lineage(history: Sequence[dict[str, Any]] | None) -> list[str]:
    """Successor/predecessor links the service records in audit payloads (split, merge)."""
    verbs = {"created": "created", "merged": "merged from", "predecessor_ids": "predecessors"}
    return [
        f"  {e.get('type')}: {verb} {', '.join(map(str, ids))}"
        for e in history or []
        for key, verb in verbs.items()
        if isinstance(ids := (e.get("payload") or {}).get(key), list) and ids
    ]


def occurrences(out: ListReferencesOut) -> str:
    rows = []
    for o in out.occurrences:
        ident = o.get("identity") or {}
        access = o.get("access") or {}
        rows.append(
            [
                o.get("occurrence_id", ""),
                o.get("revision", ""),
                o.get("label") or "",
                ident.get("state", ""),
                access.get("classification") or access.get("discovery") or "",
                o.get("local_copy", ""),
                citation_text(str(o.get("raw_text", "")), o.get("edited_text")),
            ]
        )
    head = table(["ID", "REV", "LABEL", "IDENTITY", "ACCESS", "COPY", "TEXT"], rows, width=70)
    tail = f"{len(out.occurrences)} of {out.total}"
    return f"{head}\n{tail}" + (f"  next cursor: {out.next_cursor}" if out.next_cursor else "")


def ranked(cands: Sequence[CandidateView]) -> list[CandidateView]:
    return sorted(cands, key=lambda c: c.rank)


def candidate_window(cands: Sequence[CandidateView], offset: int = 0) -> list[CandidateView]:
    """The same live-candidate page is used for display and decision keys."""
    return [c for c in ranked(cands) if not c.rejected][offset : offset + MAX_CANDIDATES_SHOWN]


def candidate_lines(cands: Sequence[CandidateView], *, offset: int | None = None) -> list[str]:
    lines: list[str] = []
    shown = ranked(cands)
    page = shown[:MAX_CANDIDATES_SHOWN] if offset is None else candidate_window(cands, offset)
    for i, c in enumerate(page, 1):
        flag = " [rejected]" if c.rejected else ""
        lines.append(f"  {i}. {work_line(c.metadata)}{flag}")
        lines.append(f"     providers: {', '.join(c.providers)}")
        lines += [f"     - {r}" for r in c.reasons]
    if offset is not None:
        live_count = sum(not c.rejected for c in shown)
        if live_count > MAX_CANDIDATES_SHOWN:
            lines.append(f"  Candidates {offset + 1}-{offset + len(page)} of {live_count}; [p] next page")
    elif len(shown) > MAX_CANDIDATES_SHOWN:
        lines.append(f"  (+{len(shown) - MAX_CANDIDATES_SHOWN} more)")
    return lines


def reference(out: GetReferenceOut, *, candidate_offset: int | None = None) -> str:
    o = out.occurrence
    reasons = ", ".join(o.identity.reasons)
    lines = [
        f"{o.occurrence_id}  revision {o.revision}  label {o.label or '-'}",
        f"extraction: {o.extraction}  identity: {o.identity.state}" + (f" ({reasons})" if reasons else ""),
        f"access: {o.access.classification or o.access.discovery}  discovery: {o.access.discovery}"
        f"  copy: {o.local_copy}  retrieval: {o.retrieval}",
        "",
        citation_text(o.raw_text, o.edited_text),
    ]
    lines += [f"warning: {w}" for w in o.warnings]
    if out.candidates:
        lines += ["", "Candidates:", *candidate_lines(out.candidates, offset=candidate_offset)]
    if links := lineage(out.history):
        lines += ["", "lineage:", *links]
    for name in ("access", "local_copies", "files", "history"):
        items = getattr(out, name)
        if items:
            lines += ["", f"{name}:", *(f"  {one_line(str(i), 100)}" for i in items)]
    return "\n".join(lines)
