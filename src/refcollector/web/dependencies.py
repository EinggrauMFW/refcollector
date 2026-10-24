"""FastAPI request dependencies for service, context, session, and CSRF."""

from __future__ import annotations

import getpass
import inspect
from typing import Annotated, Any

from fastapi import Depends, Header, Request

from refcollector.contracts.api import CallContext, RefCollectorService
from refcollector.domain.enums import Channel, Scope
from refcollector.errors import NotAuthorized
from refcollector.ids import new_id
from refcollector.web.session import Session, SessionStore


def get_service(request: Request) -> RefCollectorService:
    """Return the shared RefCollectorService instance."""
    return request.app.state.service


def get_session_store(request: Request) -> SessionStore:
    """Return the shared session store instance."""
    return request.app.state.session_store


def get_call_context(request: Request) -> CallContext:
    """Construct an in-process CallContext for the web request."""
    session_id: str | None = None
    session: Session | None = getattr(request.state, "session", None)
    if session is not None:
        session_id = session.session_id
    else:
        cookie_val = request.cookies.get("rc_session")
        if cookie_val:
            store = getattr(request.app.state, "session_store", None)
            if store is not None:
                s = store.get_session(cookie_val)
                if s is not None:
                    session_id = s.session_id

    kwargs: dict[str, Any] = {
        "principal": getpass.getuser(),
        "channel": Channel.WEB_UI,
        "scopes": frozenset(Scope),
        "request_id": new_id("request"),
        "tty": False,
    }

    try:
        if "web_session_id" in inspect.signature(CallContext).parameters:
            kwargs["web_session_id"] = session_id
    except (ValueError, TypeError):
        pass

    return CallContext(**kwargs)


def require_session(
    request: Request,
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> Session:
    """Verify that the request has an active, valid session cookie."""
    session_id = request.cookies.get("rc_session")
    if not session_id:
        raise NotAuthorized(
            "Session cookie is missing",
            recovery_hint="Reload the page to start a new session.",
        )

    session = store.get_session(session_id)
    if not session:
        raise NotAuthorized(
            "Session is invalid or expired",
            recovery_hint="Reload the page to start a new session.",
        )

    request.state.session = session
    return session


def require_csrf(
    session: Annotated[Session, Depends(require_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """Verify that the mutating request sends a matching CSRF token."""
    if not x_csrf_token:
        raise NotAuthorized("X-CSRF-Token header is missing")

    if not store.validate_csrf(session.session_id, x_csrf_token):
        raise NotAuthorized("X-CSRF-Token is invalid")
