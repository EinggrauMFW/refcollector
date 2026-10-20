"""WP-01: IdempotencyRepo with 7-day expiry. Spec 11.3."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from refcollector.domain.models import IdempotencyRecord
from refcollector.storage.db import Database


def iso(dt: datetime) -> str:
    """Fixed-width UTC text, so SQL string comparison orders like time. Naive = UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


class SqliteIdempotencyRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(
        self, principal: str, project_id: str, tool: str, key: str, *, now: datetime
    ) -> IdempotencyRecord | None:
        row = self.db.query_one(
            "SELECT * FROM idempotency_record WHERE principal = ? AND project_id = ? AND tool = ?"
            " AND key = ? AND expires_at > ?",
            (principal, project_id, tool, key, iso(now)),
        )
        if row is None:
            return None
        return IdempotencyRecord(**{**dict(row), "result": json.loads(row["result"])})

    def put(self, record: IdempotencyRecord) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO idempotency_record VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.principal,
                record.project_id,
                record.tool,
                record.key,
                record.fingerprint,
                json.dumps(record.result),
                iso(record.created_at),
                iso(record.expires_at),
            ),
        )

    def purge_expired(self, now: datetime) -> int:
        return self.db.execute("DELETE FROM idempotency_record WHERE expires_at <= ?", (iso(now),)).rowcount
