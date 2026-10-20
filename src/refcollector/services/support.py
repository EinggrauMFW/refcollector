"""Shared service plumbing: request parsing, fingerprints, cursors, async bridge, mutation scope."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from refcollector.domain.enums import AuditEventType
from refcollector.domain.models import AuditEvent
from refcollector.errors import InvalidInput
from refcollector.ids import new_id

if TYPE_CHECKING:
    from refcollector.contracts.api import CallContext
    from refcollector.domain.models import Project
    from refcollector.services.service import RefCollectorServiceImpl

D = TypeVar("D", bound=BaseModel)
T = TypeVar("T")


def parse_request(model: type[D], payload: Any) -> D:
    """Validate a raw tool payload. Extra fields (`origin`, `channel`, `approval`) and bad values
    give INVALID_INPUT (7.4, AT-33). P1 values raise FeatureNotAvailable from the DTO itself (D-19)."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        errors = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        raise InvalidInput("invalid request", details={"errors": errors}) from exc


def fingerprint(req: BaseModel) -> str:
    """sha256 of the canonical JSON of the request without its idempotency key (11.3)."""
    data = req.model_dump(mode="json", exclude={"idempotency_key"})
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run provider coroutines from the synchronous service (D-08), also under a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------- cursors (D-13) ----------


def scope_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def encode_cursor(after: str | None, scope: str, now: datetime) -> str | None:
    if after is None:
        return None
    raw = json.dumps({"a": after, "s": scope, "t": now.timestamp()}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, scope: str, now: datetime, ttl_s: int) -> str | None:
    if cursor is None:
        return None
    hint = "Repeat the request without a cursor."
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        after, cursor_scope, issued = str(data["a"]), str(data["s"]), float(data["t"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidInput("invalid cursor", recovery_hint=hint) from exc
    if cursor_scope != scope:
        raise InvalidInput("cursor belongs to a different request", recovery_hint=hint)
    if now.timestamp() - issued > ttl_s:
        raise InvalidInput("cursor expired", recovery_hint=hint)
    return after


# ---------- mutation scope ----------


@dataclass
class Tx:
    """Inside one immediate transaction: audit events carry the revision this mutation commits."""

    svc: RefCollectorServiceImpl
    ctx: CallContext
    project: Project | None
    revision: int
    now: datetime
    events: int = 0

    def attach(self, project: Project) -> None:
        """create_project: the project exists only once the mutation starts; its first revision is 1."""
        self.project, self.revision = project, project.revision + 1

    def audit(self, type_: AuditEventType, subject_id: str | None = None, **payload: Any) -> None:
        assert self.project is not None
        self.svc.store.audit.append(
            AuditEvent(
                event_id=new_id("event"),
                project_id=self.project.project_id,
                type=type_,
                subject_id=subject_id,
                provenance=self.svc.provenance(self.ctx),
                project_revision=self.revision,
                payload=payload,
                created_at=self.now,
            )
        )
        self.events += 1


def expiry(now: datetime, ttl_s: int) -> datetime:
    return now + timedelta(seconds=ttl_s)
