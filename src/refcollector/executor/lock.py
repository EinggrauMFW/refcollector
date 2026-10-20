"""WP-02: portalocker lock on RC_HOME/executor.lock and executor table row. Spec 6.1."""

from __future__ import annotations

import os
from typing import IO

import portalocker

from refcollector import ids
from refcollector.config import Settings
from refcollector.domain.models import utcnow
from refcollector.storage.db import Database


class ExecutorLock:
    """Exclusive, non-blocking OS file lock. The OS releases it when the process dies."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.executor_id = ids.new_id("executor")
        self._fh: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def try_acquire(self) -> bool:
        if self._fh is not None:
            return True
        path = self.settings.lock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+b")  # noqa: SIM115 - held open for the lock's lifetime
        try:
            portalocker.lock(fh, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.LockException:
            fh.close()
            return False
        self._fh = fh
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO executor (id, pid, started_at, stopped_at) VALUES (?, ?, ?, NULL)",
                (self.executor_id, os.getpid(), utcnow().isoformat()),
            )
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            with self.db.transaction():
                self.db.execute(
                    "UPDATE executor SET stopped_at = ? WHERE id = ?",
                    (utcnow().isoformat(), self.executor_id),
                )
        finally:
            fh, self._fh = self._fh, None
            portalocker.unlock(fh)
            fh.close()
