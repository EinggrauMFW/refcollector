"""Trusted local PDF fallback configuration."""

import json
from pathlib import Path

import pytest

from refcollector.cli import main
from refcollector.config import load_settings
from tests.unit.cli.conftest import Cli

URL = "https://resolver.example/{doi}"


def test_default_is_disabled_without_service_calls(cli: Cli, rc_home: Path) -> None:
    assert load_settings(rc_home).pdf_fallback_url is None
    assert cli.app.started == 0
    assert cli.svc.calls == []


def test_set_and_clear_roundtrip(cli: Cli, rc_home: Path) -> None:
    result = cli.run("config", "pdf-fallback", URL)
    assert result.exit_code == 0, result.output
    assert load_settings(rc_home).pdf_fallback_url == URL
    assert cli.run("config", "pdf-fallback", "off").exit_code == 0
    assert load_settings(rc_home).pdf_fallback_url is None
    assert cli.app.started == 0
    assert cli.svc.calls == []


@pytest.mark.parametrize(
    "url",
    ["http://resolver.example/{doi}", "https://resolver.example/", "https://resolver.example/{doi}?x=1"],
)
def test_invalid_template_does_not_write(cli: Cli, rc_home: Path, url: str) -> None:
    assert cli.run("config", "pdf-fallback", URL).exit_code == 0
    path = rc_home / "config.json"
    before = path.read_bytes()
    assert cli.run("config", "pdf-fallback", url).exit_code != 0
    assert path.read_bytes() == before


def test_show_includes_enabled_url_and_disabled_state(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "secrets_present", lambda: {})
    assert json.loads(cli.run("config", "show", "--json").output)["pdf_fallback_url"] is None
    assert "disabled" in cli.run("config", "show").output
    assert cli.run("config", "pdf-fallback", URL).exit_code == 0
    assert json.loads(cli.run("config", "show", "--json").output)["pdf_fallback_url"] == URL
    assert URL in cli.run("config", "show").output


@pytest.mark.parametrize("domain", ["box", "su", "st", "ru"])
def test_preset_and_base_url_store_doi_template(cli: Cli, rc_home: Path, domain: str) -> None:
    expected = f"https://sci-hub.{domain}/{{doi}}"
    for value in (domain, f"https://sci-hub.{domain}/"):
        result = cli.run("config", "pdf-fallback", value)
        assert result.exit_code == 0, result.output
        assert load_settings(rc_home).pdf_fallback_url == expected
    assert cli.app.started == 0
    assert cli.svc.calls == []
