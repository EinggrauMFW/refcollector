"""SQLite access: one connection per thread, WAL, migrations, reentrant transactions (spec 6.2, 6.4).

Usage:
    db = Database(settings.db_path)
    db.migrate()
    with db.transaction(immediate=True):   # BEGIN IMMEDIATE for step claims
        db.execute("UPDATE step SET ...", (...))

Nested `transaction()` calls join the outermost one, so a step handler's commit and the
executor's `succeeded` update land in one transaction (spec 6.4). Only the outermost call
decides BEGIN mode; asking for `immediate=True` inside a deferred transaction raises.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    # ---- connections ----

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 30000")
            if self.path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
            self._local.depth = 0
            self._local.immediate = False
            with self._lock:
                self._all.append(conn)
        return conn

    def close(self) -> None:
        with self._lock:
            for conn in self._all:
                conn.close()
            self._all.clear()
        self._local = threading.local()

    # ---- transactions ----

    @property
    def in_transaction(self) -> bool:
        return getattr(self._local, "depth", 0) > 0

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        if self._local.depth:
            if immediate and not self._local.immediate:
                raise RuntimeError("cannot upgrade a deferred transaction to IMMEDIATE")
            self._local.depth += 1
            try:
                yield conn
            finally:
                self._local.depth -= 1
            return
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        self._local.depth = 1
        self._local.immediate = immediate
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            self._local.depth = 0
            self._local.immediate = False

    # ---- helpers ----

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    # ---- migrations ----

    def migrate(self) -> list[str]:
        """Apply pending `migrations/NNNN_*.sql` files in order. Returns the applied names."""
        conn = self.conn
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        applied = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with self.transaction(immediate=True):
                for statement in _split_sql(path.read_text(encoding="utf-8")):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?)", (path.name, datetime.now(UTC).isoformat())
                )
            applied.append(path.name)
        return applied


def _split_sql(script: str) -> list[str]:
    """Split a migration into statements (no semicolons inside literals in our migrations)."""
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
