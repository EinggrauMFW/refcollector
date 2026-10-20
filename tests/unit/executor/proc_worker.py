"""Child process for AT-28. Prints `ready`, waits for `go`, tries the lock, runs all steps when
owner, prints `owner` or `client`, then holds the lock until `exit` on stdin."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from refcollector.config import load_settings
from refcollector.contracts.ports import StepContext
from refcollector.domain.enums import StepType
from refcollector.storage.db import Database
from tests.unit.executor.helpers import ScriptedHandler, fast_settings, make_executor


def main(log: Path) -> None:
    settings = fast_settings(load_settings())
    db = Database(settings.db_path)

    def record(ctx: StepContext) -> None:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(ctx.step.subject_id + "\n")

    handler = ScriptedHandler(StepType.RESOLVE)
    handler.script = {f"o{i}": record for i in range(100)}
    ex = make_executor(settings, db, [handler])
    print("ready", flush=True)
    sys.stdin.readline()
    if ex.try_acquire():
        asyncio.run(ex.run_until_idle())
        print("owner", flush=True)
    else:
        print("client", flush=True)
    sys.stdin.readline()
    ex.stop()
    db.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
