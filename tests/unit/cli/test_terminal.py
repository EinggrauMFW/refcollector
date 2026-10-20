"""Console input regressions; native APIs are simulated on either host platform."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from refcollector.cli import review


@pytest.mark.parametrize("prefix", ["\x00", "\xe0"])
def test_windows_extended_key_does_not_consume_next_decision(
    monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    bursts = [[prefix, "H"], ["a"], ["q"]]
    pending: list[str] = []

    def getwch() -> str:
        if not pending:
            pending.extend(bursts.pop(0))
        return pending.pop(0)

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=getwch, kbhit=lambda: bool(pending)))
    monkeypatch.setattr(review, "sys", SimpleNamespace(platform="win32"))
    assert review.read_key() == ""
    assert review.read_key() == "a"
    assert review.read_key() == "q"


def test_windows_discards_whole_paste_after_one_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    bursts = [list("a" * 96), ["q"]]
    pending: list[str] = []

    def getwch() -> str:
        if not pending:
            pending.extend(bursts.pop(0))
        return pending.pop(0)

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=getwch, kbhit=lambda: bool(pending)))
    monkeypatch.setattr(review, "sys", SimpleNamespace(platform="win32"))
    assert review.read_key() == "a"
    assert review.read_key() == "q"


def test_posix_discards_large_paste_before_following_text_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = bytearray()
    bursts = [b"d" + b"a" * 95, b"q"]
    saved = ["original terminal attributes"]
    restored = []

    def read(fd: int, size: int) -> bytes:
        if not pending:
            pending.extend(bursts.pop(0))
        data = bytes(pending[:size])
        del pending[:size]
        return data

    def tcflush(fd: int, queue: int) -> None:
        pending.clear()

    def setcbreak(fd: int) -> None:
        # Python's real setcbreak defaults to TCSAFLUSH: discard old pending input.
        pending.clear()

    termios = SimpleNamespace(
        tcgetattr=lambda fd: saved,
        tcsetattr=lambda fd, when, attrs: restored.append(attrs),
        tcflush=tcflush,
        TCIFLUSH=0,
        TCSADRAIN=1,
    )
    monkeypatch.setitem(sys.modules, "termios", termios)
    monkeypatch.setitem(sys.modules, "tty", SimpleNamespace(setcbreak=setcbreak))
    monkeypatch.setattr(
        review, "sys", SimpleNamespace(platform="linux", stdin=SimpleNamespace(fileno=lambda: 0))
    )
    monkeypatch.setattr(review.os, "read", read)
    monkeypatch.setattr("builtins.input", lambda prompt: pending.decode())
    assert review.read_key() == "d"
    assert review.read_line("DOI: ") == ""
    assert review.read_key() == "q"
    assert restored == [saved, saved]
