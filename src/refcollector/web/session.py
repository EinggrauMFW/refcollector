"""Server-side session management and CSRF token store.

Enforces opaque session IDs and session-bound CSRF tokens.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

MAX_SESSIONS: int = 1000


@dataclass
class Session:
    session_id: str
    csrf_token: str
    created_at: float
    last_accessed_at: float = field(default_factory=time.time)


class SessionStore:
    """Thread-safe in-memory session store with absolute expiry and capacity bound."""

    def __init__(
        self,
        ttl_seconds: float = 86400.0,
        max_sessions: int = MAX_SESSIONS,
        max_lifetime_seconds: float | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        if max_sessions <= 0:
            raise ValueError(f"max_sessions must be positive, got {max_sessions}")
        if max_lifetime_seconds is not None and max_lifetime_seconds <= 0:
            raise ValueError(f"max_lifetime_seconds must be positive, got {max_lifetime_seconds}")

        self._ttl = ttl_seconds
        self._max_lifetime = max_lifetime_seconds if max_lifetime_seconds is not None else ttl_seconds
        self._max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create_session(self) -> Session:
        with self._lock:
            now = time.time()
            self._cleanup_locked(now)
            while len(self._sessions) >= self._max_sessions:
                oldest_sid = min(self._sessions, key=lambda sid: self._sessions[sid].last_accessed_at)
                self._sessions.pop(oldest_sid, None)
            session_id = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            session = Session(
                session_id=session_id,
                csrf_token=csrf_token,
                created_at=now,
                last_accessed_at=now,
            )
            self._sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            now = time.time()
            session = self._sessions.get(session_id)
            if not session:
                return None
            if now - session.created_at > self._max_lifetime:
                self._sessions.pop(session_id, None)
                return None
            if now - session.last_accessed_at > self._ttl:
                self._sessions.pop(session_id, None)
                return None
            session.last_accessed_at = now
            return session

    def validate_csrf(self, session_id: str, csrf_token: str) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        return secrets.compare_digest(session.csrf_token, csrf_token)

    def _cleanup_locked(self, now: float) -> None:
        expired = [
            sid
            for sid, s in self._sessions.items()
            if (now - s.created_at > self._max_lifetime) or (now - s.last_accessed_at > self._ttl)
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
