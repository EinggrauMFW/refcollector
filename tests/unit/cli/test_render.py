from __future__ import annotations

from refcollector.cli import render


def test_clean_strips_ansi_and_controls() -> None:
    raw = "a\x1b[31mb\x1b[0m\x1b]0;t\x07c\x00d\re‮f⁦g"
    assert render.clean(raw) == "abcdefg"


def test_clean_keeps_newlines_unless_single_line() -> None:
    assert render.clean("a\n\tb") == "a\n b"
    assert render.one_line("a\n\tb   c") == "a b c"


def test_one_line_truncates() -> None:
    out = render.one_line("x" * 100, width=10)
    assert len(out) == 10 and out.endswith("…")


def test_table_aligns_and_cleans() -> None:
    out = render.table(["ID", "TEXT"], [["a", "x\x1b[1my"], ["bbb", "z"]])
    lines = out.splitlines()
    assert lines[0].split() == ["ID", "TEXT"]
    assert "\x1b" not in out
    assert lines[1].index("xy") == lines[2].index("z")
