from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from typer.testing import CliRunner

from refcollector.cli import main
from tests.fakes.service import FakeService
from tests.unit.cli.factories import FakeApp, projects


@dataclass
class Cli:
    runner: CliRunner
    svc: FakeService
    app: FakeApp

    def run(self, *args: str, input: str | None = None) -> Any:
        return self.runner.invoke(main.app, list(args), input=input)

    def calls(self, method: str) -> list[Any]:
        return [c for c in self.svc.calls if c[0] == method]


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, rc_home: Any) -> Iterator[Cli]:
    monkeypatch.delenv("RC_PROJECT", raising=False)
    svc = FakeService({"list_projects": projects(("prj_1", "Thesis"))})
    app = FakeApp(svc)
    main.set_app_factory(lambda: app)
    yield Cli(CliRunner(), svc, app)
    main.set_app_factory(None)
