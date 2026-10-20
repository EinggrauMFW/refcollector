"""WP-12: TTY review, the P0 approval surface. Spec 7.4, 14.1.

One keypress per decision. `read_key` and `read_line` are module-level so tests replace them.
Accept/choose go through `review_decision` (-> `accepted_user`, W-18); everything else uses
`update_reference` / `add_reference` on the same TTY context.
"""

from __future__ import annotations

import getpass
import os
import sys
import uuid
from collections import Counter
from collections.abc import Callable

from pydantic import ValidationError

from refcollector.cli import render
from refcollector.cli.render import echo
from refcollector.contracts.api import (
    AddReferenceIn,
    CallContext,
    Change,
    EditText,
    Exclude,
    GetReferenceIn,
    GetReferenceOut,
    ListReferencesIn,
    MarkUnresolved,
    Merge,
    RefCollectorService,
    ReferenceFilter,
    ReviewDecisionIn,
    SetDoi,
    Split,
    SplitPart,
    UpdateReferenceIn,
)
from refcollector.domain.enums import Channel, ExtractionState, IdentityState, Scope
from refcollector.errors import RcError, RevisionConflict
from refcollector.ids import new_id

PROMPT = (
    "[a]ccept page top  [1-5] choose  [r]eject  [d]oi  [e]dit  [s]plit  [m]erge next  "
    "e[x]clude  [n]ew after  [u]nresolved  s[k]ip  [p]age candidates  [q]uit > "
)

# ---------- terminal input (injectable) ----------


def is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def read_key_with(getch: Callable[[], str]) -> str:
    """Normalise one raw character: '' = not a decision, 'q' = quit (Ctrl-C, Ctrl-D, EOF)."""
    ch = getch()
    if ch in ("\x00", "\xe0"):  # Windows function/arrow key: a second code follows
        getch()
        return ""
    if ch in ("", "\x03", "\x04"):
        return "q"
    return ch.lower() if ch.isprintable() else ""


def read_key() -> str:
    """Block for one keypress without Enter (msvcrt on Windows, termios on POSIX)."""
    if sys.platform == "win32":
        import msvcrt

        key = read_key_with(msvcrt.getwch)  # consume an extended-key suffix exactly once
        while msvcrt.kbhit():  # drop type-ahead and pasted text: one keypress, one decision
            msvcrt.getwch()
        return key

    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    def posix_getch() -> str:
        # Read the first burst, then flush the full input queue so escape sequences
        # and long pastes cannot leak into later decisions or line-input prompts.
        data = os.read(fd, 32).decode("utf-8", errors="ignore")
        termios.tcflush(fd, termios.TCIFLUSH)
        return "\x1b" if data.startswith("\x1b") else data[:1]

    try:
        tty.setcbreak(fd)
        return read_key_with(posix_getch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def read_line(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


# ---------- the review loop ----------


def _ctx() -> CallContext:
    return CallContext(
        principal=getpass.getuser(),
        channel=Channel.CLI_TTY_REVIEW,
        scopes=frozenset(Scope),
        request_id=new_id("request"),
        tty=True,
    )


def _idem() -> str:
    return uuid.uuid4().hex


def _queue(svc: RefCollectorService, project_id: str, states: list[IdentityState]) -> list[str]:
    ids: list[str] = []
    for state in states:
        cursor = None
        while True:
            out = svc.list_references(
                _ctx(),
                ListReferencesIn(
                    project_id=project_id,
                    filter=ReferenceFilter(identity=state),
                    fields=["occurrence_id"],
                    cursor=cursor,
                    limit=100,
                ),
            )
            ids += [o["occurrence_id"] for o in out.occurrences if o["occurrence_id"] not in ids]
            cursor = out.next_cursor
            if not cursor:
                break
    return ids


def _get(svc: RefCollectorService, project_id: str, occurrence_id: str) -> GetReferenceOut:
    return svc.get_reference(
        _ctx(), GetReferenceIn(project_id=project_id, occurrence_id=occurrence_id, sections=["candidates"])
    )


def run_review(svc: RefCollectorService, project_id: str, *, include_all: bool = False) -> None:
    states = [IdentityState.NEEDS_REVIEW]
    if include_all:
        states += [IdentityState.ACCEPTED_AUTO, IdentityState.ACCEPTED_AGENT]
    queue = _queue(svc, project_id, states)
    if not queue:
        echo("Nothing to review.")
        return
    counts: Counter[str] = Counter()
    i = 0
    try:
        while i < len(queue):
            outcome = _review_one(svc, project_id, queue, i, counts)
            if outcome == "quit":
                break
            i += 1
    except KeyboardInterrupt:
        echo("")
    echo("Review done: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "no decisions"))


def _review_one(
    svc: RefCollectorService, project_id: str, queue: list[str], i: int, counts: Counter[str]
) -> str:
    ref: GetReferenceOut | None = None
    candidate_offset = 0
    while True:
        if ref is None:
            ref = _get(svc, project_id, queue[i])
            if ref.occurrence.extraction == ExtractionState.EXCLUDED:
                counts["skipped"] += 1
                return "next"
            echo()
            echo(f"[{i + 1}/{len(queue)}] " + render.reference(ref, candidate_offset=candidate_offset))
        echo(PROMPT)
        key = read_key()
        if key == "p":
            live_count = sum(not c.rejected for c in ref.candidates or [])
            candidate_offset += render.MAX_CANDIDATES_SHOWN
            if candidate_offset >= live_count:
                candidate_offset = 0
            ref = None
            continue
        outcome = _handle(key, svc, project_id, ref, queue, i, counts, candidate_offset)
        if outcome in ("next", "quit"):
            return outcome
        if outcome == "stay":
            ref = None
            candidate_offset = 0


def _handle(
    key: str,
    svc: RefCollectorService,
    project_id: str,
    ref: GetReferenceOut,
    queue: list[str],
    i: int,
    counts: Counter[str],
    candidate_offset: int = 0,
) -> str:
    """Return 'next', 'quit', 'stay' (reload and show again) or 'again' (just ask again)."""
    occ = ref.occurrence
    oid, rev = occ.occurrence_id, occ.revision
    cands = render.candidate_window(ref.candidates or [], candidate_offset)
    live = cands

    def update(change: Change) -> None:
        svc.update_reference(
            _ctx(), UpdateReferenceIn(project_id=project_id, change=change, idempotency_key=_idem())
        )

    try:
        if not key:
            return "again"
        if key == "q":
            return "quit"
        if key == "k":
            counts["skipped"] += 1
            return "next"
        if key == "a" or key in "12345":
            if key == "a":
                chosen = live[0] if live else None
            else:
                idx = int(key) - 1
                chosen = cands[idx] if idx < len(cands) and not cands[idx].rejected else None
            if chosen is None:
                echo("No candidate " + ("to accept." if key == "a" else f"{key}."))
                return "again"
            _decide(svc, project_id, oid, "accept", chosen.candidate_id, rev)
            counts["accepted"] += 1
            return "next"
        if key == "r":
            if not live:
                echo("No candidate to reject.")
                return "again"
            has_other_candidates = sum(not c.rejected for c in ref.candidates or []) > 1
            _decide(svc, project_id, oid, "reject", live[0].candidate_id, rev)
            counts["rejected"] += 1
            return "stay" if has_other_candidates else "next"
        if key == "d":
            doi = read_line("DOI: ").strip()
            if not doi:
                return "again"
            update(SetDoi(type="set_doi", occurrence_id=oid, doi=doi, expected_revision=rev))
            counts["doi set"] += 1
            return "next"
        if key == "e":
            text = read_line("Corrected citation text: ").strip()
            if not text:
                return "again"
            update(EditText(type="edit_text", occurrence_id=oid, text=text, expected_revision=rev))
            counts["edited"] += 1
            return "next"
        if key == "s":
            parts: list[str] = []
            while text := read_line(f"Part {len(parts) + 1} (empty line to finish): ").strip():
                parts.append(text)
            if len(parts) < 2:
                echo("A split needs at least two parts. Cancelled.")
                return "again"
            update(
                Split(
                    type="split",
                    occurrence_id=oid,
                    parts=[SplitPart(text=t) for t in parts],
                    expected_revision=rev,
                )
            )
            counts["split"] += 1
            return "next"
        if key == "m":
            if i + 1 >= len(queue):
                echo("No next occurrence to merge with.")
                return "again"
            nxt = _get(svc, project_id, queue[i + 1]).occurrence
            update(
                Merge(
                    type="merge",
                    occurrence_ids=[oid, nxt.occurrence_id],
                    expected_revisions={oid: rev, nxt.occurrence_id: nxt.revision},
                )
            )
            del queue[i + 1]
            counts["merged"] += 1
            return "next"
        if key == "x":
            update(Exclude(type="exclude", occurrence_id=oid, expected_revision=rev))
            counts["excluded"] += 1
            return "next"
        if key == "n":
            text = read_line("New reference text (inserted after this one): ").strip()
            if not text:
                return "again"
            svc.add_reference(
                _ctx(),
                AddReferenceIn(
                    project_id=project_id,
                    source_id=occ.source_id,
                    text=text,
                    after_occurrence_id=oid,
                    idempotency_key=_idem(),
                ),
            )
            counts["added"] += 1
            return "again"
        if key == "u":
            update(MarkUnresolved(type="mark_unresolved", occurrence_id=oid, expected_revision=rev))
            counts["unresolved"] += 1
            return "next"
        return "again"
    except RevisionConflict as e:
        echo(f"{e.code}: {e.message}. Reloading the occurrence.")
        return "stay"
    except RcError as e:
        echo(f"{e.code}: {e.message}" + (f" ({e.recovery_hint})" if e.recovery_hint else ""))
        return "again"
    except ValidationError as e:
        echo("Invalid input: " + "; ".join(err["msg"] for err in e.errors()))
        return "again"


def _decide(
    svc: RefCollectorService,
    project_id: str,
    occurrence_id: str,
    action: str,
    candidate_id: str,
    revision: int,
) -> None:
    svc.review_decision(
        _ctx(),
        ReviewDecisionIn.model_validate(
            {
                "project_id": project_id,
                "occurrence_id": occurrence_id,
                "action": action,
                "candidate_id": candidate_id,
                "expected_revision": revision,
                "idempotency_key": _idem(),
            }
        ),
    )
