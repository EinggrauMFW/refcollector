"""Shared pytest fixtures (contract C8).

- rc_home / settings: a fresh RC_HOME per test (tmp_path); no keyring or network use
- db: migrated SQLite Database in that RC_HOME
- fake_store: in-memory Store (tests/fakes/store.py)
- ctx_agent / ctx_tty: CallContext for an MCP agent and for the TTY review surface
- anyio_backend: async tests run on asyncio (`@pytest.mark.anyio`)

Rules for every WP: every test folder has an __init__.py (tests is a package, so
`from tests.fakes...` works); tests make no live network calls; use respx with recorded fixtures or
tests/fakes/http.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from refcollector.config import Settings, save_settings
from refcollector.contracts.api import CallContext
from refcollector.domain.enums import Channel, Scope
from refcollector.storage.db import Database
from tests.fakes.store import FakeStore


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def rc_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "rc_home"
    home.mkdir()
    monkeypatch.setenv("RC_HOME", str(home))
    return home


@pytest.fixture
def settings(rc_home: Path, tmp_path: Path) -> Settings:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    s = Settings(rc_home=rc_home, email="tester@university.example.org", allow_folders=[allowed])
    save_settings(s)
    return s


@pytest.fixture
def allowed_dir(settings: Settings) -> Path:
    return settings.allow_folders[0]


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    database = Database(settings.db_path)
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def fake_store(tmp_path: Path) -> FakeStore:
    return FakeStore(tmp_path / "fake_store")


@pytest.fixture
def ctx_agent() -> CallContext:
    return CallContext(
        principal="tester", channel=Channel.MCP, scopes=frozenset(Scope), request_id="req_test"
    )


@pytest.fixture
def ctx_tty() -> CallContext:
    return CallContext(
        principal="tester",
        channel=Channel.CLI_TTY_REVIEW,
        scopes=frozenset(Scope),
        request_id="req_tty",
        tty=True,
    )
