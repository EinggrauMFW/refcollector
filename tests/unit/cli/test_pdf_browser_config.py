"""Trusted local browser settings do not start the service."""

import json
from pathlib import Path

import pytest

from refcollector.cli import main
from refcollector.config import load_settings
from tests.unit.cli.conftest import Cli


def test_enable_show_disable_roundtrip(cli: Cli, rc_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "secrets_present", lambda: {})
    python = rc_home / "python.exe"
    binary = rc_home / "chrome.exe"
    python.touch()
    binary.touch()
    initial = json.loads(cli.run("config", "show", "--json").output)
    assert initial["pdf_browser_python"] is None
    assert initial["pdf_browser_binary"] is None
    result = cli.run("config", "pdf-browser", str(python), "--binary", str(binary))
    assert result.exit_code == 0, result.output
    settings = load_settings(rc_home)
    assert settings.pdf_browser_python == str(python.resolve())
    assert settings.pdf_browser_binary == str(binary.resolve())
    shown = json.loads(cli.run("config", "show", "--json").output)
    assert shown["pdf_browser_python"] == settings.pdf_browser_python
    assert shown["pdf_browser_binary"] == settings.pdf_browser_binary
    text = cli.run("config", "show").output
    assert str(python.resolve()) in text
    assert str(binary.resolve()) in text
    assert cli.run("config", "pdf-browser", "off").exit_code == 0
    settings = load_settings(rc_home)
    assert settings.pdf_browser_python is None
    assert settings.pdf_browser_binary is None
    assert cli.app.started == 0
    assert cli.svc.calls == []


@pytest.mark.parametrize("invalid", ["missing_binary", "missing_python", "directory", "no_option"])
def test_invalid_paths_preserve_existing_config(cli: Cli, rc_home: Path, invalid: str) -> None:
    python = rc_home / "python.exe"
    binary = rc_home / "chrome.exe"
    python.touch()
    binary.touch()
    assert cli.run("config", "pdf-browser", str(python), "--binary", str(binary)).exit_code == 0
    before = (rc_home / "config.json").read_bytes()
    args = [str(python), "--binary", str(binary)]
    if invalid == "missing_binary":
        args[2] = str(rc_home / "missing.exe")
    elif invalid == "missing_python":
        args[0] = str(rc_home / "missing.exe")
    elif invalid == "directory":
        args[2] = str(rc_home)
    else:
        args = [str(python)]
    assert cli.run("config", "pdf-browser", *args).exit_code != 0
    assert (rc_home / "config.json").read_bytes() == before
    assert cli.app.started == 0
    assert cli.svc.calls == []
