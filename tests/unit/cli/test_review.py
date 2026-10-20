"""`refcollector review`: the TTY approval surface (spec 7.4; W-11, W-18)."""

from __future__ import annotations

import pytest

from refcollector.cli import review
from refcollector.domain.enums import Channel, IdentityState
from refcollector.errors import RevisionConflict
from tests.unit.cli import factories as f
from tests.unit.cli.conftest import Cli


class Script:
    """Scripted keys and lines; quits when keys run out."""

    def __init__(self, keys: str, lines: list[str] | None = None) -> None:
        self.keys = list(keys)
        self.lines = list(lines or [])
        self.prompts: list[str] = []

    def key(self) -> str:
        return self.keys.pop(0) if self.keys else "q"

    def line(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        return self.lines.pop(0) if self.lines else ""


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch, cli: Cli):  # noqa: ANN201
    cli.svc.responses["list_references"] = f.listing(f.occ_dict("occ_1", 4), f.occ_dict("occ_2", 6))
    cli.svc.responses["get_reference"] = lambda ctx, req: f.reference(
        req.occurrence_id, {"occ_1": 4, "occ_2": 6}[req.occurrence_id]
    )
    cli.svc.responses["review_decision"] = f.updated()
    cli.svc.responses["update_reference"] = f.updated()
    monkeypatch.setattr(review, "is_tty", lambda: True)

    def start(keys: str, lines: list[str] | None = None) -> Script:
        s = Script(keys, lines)
        monkeypatch.setattr(review, "read_key", s.key)
        monkeypatch.setattr(review, "read_line", s.line)
        return s

    cli.start = start  # type: ignore[attr-defined]
    return cli


def go(cli: Cli, keys: str, lines: list[str] | None = None, *extra: str):  # noqa: ANN201
    cli.start(keys, lines)  # type: ignore[attr-defined]
    return cli.run("--project", "prj_1", "review", *extra)


def test_refuses_without_tty(cli: Cli) -> None:
    r = cli.run("--project", "prj_1", "review")
    assert r.exit_code == 2
    assert "terminal" in r.stderr.lower()
    assert cli.svc.calls == [] and cli.app.started == 0


def test_is_tty_needs_both_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stream:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    for stdin, stdout, expected in ((True, True, True), (True, False, False), (False, True, False)):
        monkeypatch.setattr("sys.stdin", Stream(stdin))
        monkeypatch.setattr("sys.stdout", Stream(stdout))
        assert review.is_tty() is expected


def test_accept_top_candidate_is_tty_decision(tty: Cli) -> None:
    r = go(tty, "ak")
    assert r.exit_code == 0, r.output
    (_, ctx, req) = tty.calls("review_decision")[0]
    assert ctx.channel == Channel.CLI_TTY_REVIEW and ctx.tty is True and ctx.origin == "interactive"
    assert (req.action, req.candidate_id, req.occurrence_id) == ("accept", "cand_1", "occ_1")
    assert req.expected_revision == 4 and req.project_id == "prj_1" and len(req.idempotency_key) == 32
    assert tty.calls("list_references")[0][2].filter.identity == IdentityState.NEEDS_REVIEW
    assert "Title 1" in r.output and "Title matches candidate cand_1." in r.output
    assert "Neural wavefields" in r.output  # citation text


def test_choose_candidate_by_number(tty: Cli) -> None:
    go(tty, "2q")
    req = tty.calls("review_decision")[0][2]
    assert req.action == "accept" and req.candidate_id == "cand_2"


def test_number_out_of_range_makes_no_call(tty: Cli) -> None:
    r = go(tty, "5q")
    assert tty.calls("review_decision") == []
    assert "no candidate 5" in r.output.lower()


def test_unknown_key_ignored(tty: Cli) -> None:
    go(tty, "zzq")
    assert tty.svc.calls and tty.calls("review_decision") == []


def test_reject_stays_on_occurrence(tty: Cli) -> None:
    go(tty, "rk")
    req = tty.calls("review_decision")[0][2]
    assert req.action == "reject" and req.candidate_id == "cand_1"
    assert len(tty.calls("get_reference")) >= 3  # occ_1 twice (reload), occ_2


def test_set_doi(tty: Cli) -> None:
    go(tty, "dq", ["  10.1000/xyz123 "])
    (_, ctx, req) = tty.calls("update_reference")[0]
    assert ctx.channel == Channel.CLI_TTY_REVIEW and ctx.tty
    assert (
        req.change.type == "set_doi"
        and req.change.doi == "10.1000/xyz123"
        and req.change.expected_revision == 4
    )


def test_set_doi_too_short_makes_no_call(tty: Cli) -> None:
    go(tty, "dq", ["x"])
    assert tty.calls("update_reference") == []


def test_edit_text(tty: Cli) -> None:
    go(tty, "ek", ["Corrected (2020)."])
    ch = tty.calls("update_reference")[0][2].change
    assert ch.type == "edit_text" and ch.text == "Corrected (2020)." and ch.expected_revision == 4


def test_split(tty: Cli) -> None:
    go(tty, "sk", ["A (2020).", "B (2021).", ""])
    ch = tty.calls("update_reference")[0][2].change
    assert ch.type == "split" and [p.text for p in ch.parts] == ["A (2020).", "B (2021)."]


def test_split_with_one_part_cancelled(tty: Cli) -> None:
    go(tty, "sq", ["only", ""])
    assert tty.calls("update_reference") == []


def test_merge_with_next(tty: Cli) -> None:
    r = go(tty, "m")
    ch = tty.calls("update_reference")[0][2].change
    assert ch.type == "merge" and ch.occurrence_ids == ["occ_1", "occ_2"]
    assert ch.expected_revisions == {"occ_1": 4, "occ_2": 6}
    # occ_2 was merged away: the walk does not offer it again
    assert len([c for c in tty.calls("get_reference") if c[2].occurrence_id == "occ_2"]) == 1
    assert r.exit_code == 0


def test_merge_last_occurrence_has_no_next(tty: Cli) -> None:
    go(tty, "km")
    assert tty.calls("update_reference") == []


def test_exclude_and_mark_unresolved(tty: Cli) -> None:
    go(tty, "x", None)
    assert tty.calls("update_reference")[0][2].change.type == "exclude"
    go(tty, "uq")
    assert tty.calls("update_reference")[1][2].change.type == "mark_unresolved"


def test_add_after(tty: Cli) -> None:
    from refcollector.contracts.api import AddReferenceOut

    tty.svc.responses["add_reference"] = AddReferenceOut(occurrence_id="occ_9", revision=1)
    go(tty, "nq", ["Brand new (2019)."])
    (_, ctx, req) = tty.calls("add_reference")[0]
    assert ctx.channel == Channel.CLI_TTY_REVIEW and ctx.tty
    assert (req.source_id, req.text, req.after_occurrence_id) == ("src_1", "Brand new (2019).", "occ_1")


def test_skip_and_quit(tty: Cli) -> None:
    go(tty, "kq")
    assert tty.calls("review_decision") == [] and tty.calls("update_reference") == []
    assert [c[2].occurrence_id for c in tty.calls("get_reference")] == ["occ_1", "occ_2"]
    go(tty, "q")
    assert [c[2].occurrence_id for c in tty.calls("get_reference")][2:] == ["occ_1"]


def test_all_includes_accepted_auto_and_agent(tty: Cli) -> None:
    go(tty, "q", None, "--all")
    states = [c[2].filter.identity for c in tty.calls("list_references")]
    assert states == [IdentityState.NEEDS_REVIEW, IdentityState.ACCEPTED_AUTO, IdentityState.ACCEPTED_AGENT]


def test_revision_conflict_reloads(tty: Cli) -> None:
    tty.svc.responses["review_decision"] = RevisionConflict("stale")
    r = go(tty, "ak")
    assert r.exit_code == 0
    assert "REVISION_CONFLICT" in r.output
    assert len(tty.calls("get_reference")) >= 3


def test_nothing_to_review(tty: Cli) -> None:
    tty.svc.responses["list_references"] = f.listing()
    r = go(tty, "q")
    assert r.exit_code == 0 and "nothing to review" in r.output.lower()


def test_review_output_strips_control_chars(tty: Cli) -> None:
    evil = "Smith \x1b[2J\x1b[31mRED\x07 (2020)"
    tty.svc.responses["get_reference"] = lambda ctx, req: f.reference(req.occurrence_id, 4, raw_text=evil)
    out = go(tty, "q").output
    assert "Smith" in out and "RED" in out and "\x1b" not in out and "\x07" not in out


def test_windows_key_reader() -> None:
    chars = iter(["\xe0", "H", "a", "\x03", "\r"])
    assert review.read_key_with(lambda: next(chars)) == ""  # arrow key: ignored
    assert review.read_key_with(lambda: next(chars)) == "a"
    assert review.read_key_with(lambda: next(chars)) == "q"  # Ctrl-C quits
    assert review.read_key_with(lambda: next(chars)) == ""  # Enter: not a decision


def test_accept_uses_first_live_candidate_after_rejections(tty: Cli) -> None:
    ref = f.reference(n_candidates=6)
    for candidate in ref.candidates[:5]:
        candidate.rejected = True
    tty.svc.responses["get_reference"] = ref
    result = go(tty, "aq")
    assert result.exit_code == 0, result.output
    assert tty.calls("review_decision")[0][2].candidate_id == "cand_6"
    assert "Title 6" in result.output


def test_candidate_pages_use_displayed_numbers(tty: Cli) -> None:
    tty.svc.responses["get_reference"] = f.reference(n_candidates=7)
    result = go(tty, "p2q")
    assert result.exit_code == 0, result.output
    assert tty.calls("review_decision")[0][2].candidate_id == "cand_7"
    assert "Title 6" in result.output and "Title 7" in result.output


def test_candidate_pages_wrap_to_first_page(tty: Cli) -> None:
    tty.svc.responses["get_reference"] = f.reference(n_candidates=7)
    result = go(tty, "pp1q")
    assert result.exit_code == 0, result.output
    assert tty.calls("review_decision")[0][2].candidate_id == "cand_1"


def test_reject_on_last_page_returns_to_remaining_candidates(tty: Cli) -> None:
    ref = f.reference(n_candidates=6)
    tty.svc.responses["get_reference"] = lambda ctx, req: (
        ref if req.occurrence_id == "occ_1" else f.reference(req.occurrence_id)
    )

    def reject(ctx, req):  # noqa: ANN001, ANN202
        ref.candidates[5].rejected = True
        return f.updated()

    tty.svc.responses["review_decision"] = reject
    result = go(tty, "pr1q")
    assert result.exit_code == 0, result.output
    decisions = tty.calls("review_decision")
    assert [(req.action, req.candidate_id, req.occurrence_id) for _, _, req in decisions] == [
        ("reject", "cand_6", "occ_1"),
        ("accept", "cand_1", "occ_1"),
    ]
