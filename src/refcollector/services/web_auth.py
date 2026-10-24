"""Web authentication and intent registry for trusted review and web settings (Wave 3).

Service-owned locked registry for pending/completed confirmation tokens.
Tokens bind principal, web session, request fingerprint, target IDs, revisions, and keys.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal


@dataclass(frozen=True)
class IntentRecord:
    token: str
    purpose: Literal["review", "settings", "file_review"]
    principal: str
    web_session_id: str
    project_id: str | None
    target_id: str | None  # occurrence_id for review
    candidate_id: str | None
    action: str | None
    expected_revision: int | str  # int for occurrence revision, str for settings revision
    idempotency_key: str
    fingerprint: str
    expires_at: datetime
    summary: dict[str, Any]
    status: Literal["pending", "completed"] = "pending"


class IntentRegistry:
    """Service-owned in-memory intent registry with bounded capacity and TTL.

    Pending tokens expire in 60 seconds.
    Completed tokens are retained up to a bounded capacity to facilitate safe replay.
    """

    def __init__(self, max_pending: int = 1000, max_completed: int = 1000) -> None:
        self._lock = threading.Lock()
        self._pending: OrderedDict[str, IntentRecord] = OrderedDict()
        self._completed: OrderedDict[str, IntentRecord] = OrderedDict()
        self._max_pending = max_pending
        self._max_completed = max_completed

    def register(
        self,
        *,
        purpose: Literal["review", "settings", "file_review"],
        principal: str,
        web_session_id: str,
        project_id: str | None,
        target_id: str | None,
        candidate_id: str | None,
        action: str | None,
        expected_revision: int | str,
        idempotency_key: str,
        fingerprint: str,
        summary: dict[str, Any],
        now: datetime,
        ttl_seconds: float = 60.0,
    ) -> IntentRecord:
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=ttl_seconds)
        record = IntentRecord(
            token=token,
            purpose=purpose,
            principal=principal,
            web_session_id=web_session_id,
            project_id=project_id,
            target_id=target_id,
            candidate_id=candidate_id,
            action=action,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            expires_at=expires_at,
            summary=summary,
            status="pending",
        )
        with self._lock:
            self._prune_unlocked(now)
            while len(self._pending) >= self._max_pending:
                self._pending.popitem(last=False)
            self._pending[token] = record
        return record

    def get(self, token: str) -> IntentRecord | None:
        with self._lock:
            if token in self._pending:
                return self._pending[token]
            if token in self._completed:
                return self._completed[token]
            return None

    def mark_completed(self, token: str) -> None:
        with self._lock:
            rec = self._pending.pop(token, None)
            if rec is not None:
                completed_rec = IntentRecord(
                    token=rec.token,
                    purpose=rec.purpose,
                    principal=rec.principal,
                    web_session_id=rec.web_session_id,
                    project_id=rec.project_id,
                    target_id=rec.target_id,
                    candidate_id=rec.candidate_id,
                    action=rec.action,
                    expected_revision=rec.expected_revision,
                    idempotency_key=rec.idempotency_key,
                    fingerprint=rec.fingerprint,
                    expires_at=rec.expires_at,
                    summary=rec.summary,
                    status="completed",
                )
                while len(self._completed) >= self._max_completed:
                    self._completed.popitem(last=False)
                self._completed[token] = completed_rec

    def prune(self, now: datetime) -> None:
        with self._lock:
            self._prune_unlocked(now)

    def _prune_unlocked(self, now: datetime) -> None:
        expired = [t for t, r in self._pending.items() if now > r.expires_at]
        for t in expired:
            del self._pending[t]
