"""WP-01: AuditRepo over audit_event. Spec 7.2, 7.4."""

from __future__ import annotations

from refcollector.domain.models import AuditEvent
from refcollector.storage.db import Database
from refcollector.storage.idempotency import iso


class SqliteAuditRepo:
    """Append-only. `list` returns events in append order."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def append(self, event: AuditEvent) -> AuditEvent:
        self.db.execute(
            "INSERT INTO audit_event VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.project_id,
                event.type.value,
                event.subject_id,
                event.project_revision,
                iso(event.created_at),
                event.model_dump_json(),
            ),
        )
        return event.model_copy(deep=True)

    def list(self, project_id: str, *, subject_id: str | None = None) -> list[AuditEvent]:
        rows = self.db.query(
            "SELECT data FROM audit_event WHERE project_id = ? AND (? IS NULL OR subject_id = ?)"
            " ORDER BY rowid",
            (project_id, subject_id, subject_id),
        )
        return [AuditEvent.model_validate_json(r[0]) for r in rows]
