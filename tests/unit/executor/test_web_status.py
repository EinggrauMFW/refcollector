"""Unit tests for executor status inspection: get_executor_status."""

from __future__ import annotations

import os

import pytest

from refcollector import bootstrap
from refcollector.contracts.api import CallContext, GetExecutorStatusIn
from refcollector.domain.enums import Channel
from refcollector.domain.models import utcnow
from refcollector.errors import NotAuthorized


def test_executor_status_owned_and_unowned(settings, ctx_agent):
    handle = bootstrap.open_app(settings)
    try:
        # Initially unowned
        st = handle.service.get_executor_status(ctx_agent, GetExecutorStatusIn())
        assert st.state == "unowned"
        assert st.is_local_owner is False
        assert st.owner_id is None
        assert st.owner_pid is None
        assert st.heartbeat_at is None

        # Start executor -> owned
        acquired = handle.start_executor()
        assert acquired is True
        st_owned = handle.service.get_executor_status(ctx_agent, GetExecutorStatusIn())
        assert st_owned.state == "owned"
        assert st_owned.is_local_owner is True
        assert st_owned.owner_id == handle.executor.executor_id
        assert st_owned.owner_pid == os.getpid()
        assert st_owned.heartbeat_at is None
    finally:
        handle.close()

    # Closed handle -> unowned
    handle2 = bootstrap.open_app(settings)
    try:
        st_closed = handle2.service.get_executor_status(ctx_agent, GetExecutorStatusIn())
        assert st_closed.state == "unowned"
        assert st_closed.is_local_owner is False
    finally:
        handle2.close()


def test_executor_status_external_rows_never_signal_processes(settings, db, ctx_agent, monkeypatch):
    def forbid_signal(*args):
        raise AssertionError("status inspection must never signal a process")

    monkeypatch.setattr(os, "kill", forbid_signal)
    handle = bootstrap.open_app(settings)
    try:
        now = utcnow().isoformat()
        # Simulate active external process (our own PID, but not owned locally)
        db.execute(
            "INSERT INTO executor (id, pid, started_at, stopped_at) VALUES (?, ?, ?, NULL)",
            ("exe_external_active", os.getpid(), now),
        )
        st = handle.service.get_executor_status(ctx_agent, GetExecutorStatusIn())
        assert st.state == "unknown"
        assert st.is_local_owner is False
        assert st.owner_id == "exe_external_active"
        assert st.owner_pid == os.getpid()
        assert st.heartbeat_at is None

        # Simulate dead process (PID 999999)
        db.execute("DELETE FROM executor")
        db.execute(
            "INSERT INTO executor (id, pid, started_at, stopped_at) VALUES (?, ?, ?, NULL)",
            ("exe_external_stale", 999999, now),
        )
        st_stale = handle.service.get_executor_status(ctx_agent, GetExecutorStatusIn())
        assert st_stale.state == "unknown"
        assert st_stale.is_local_owner is False
        assert st_stale.owner_id == "exe_external_stale"
        assert st_stale.owner_pid == 999999
    finally:
        handle.close()


def test_executor_status_requires_projects_read(settings):
    handle = bootstrap.open_app(settings)
    try:
        ctx_no_scope = CallContext(
            principal="tester",
            channel=Channel.MCP,
            scopes=frozenset(),
            request_id="req_no_scope",
        )
        with pytest.raises(NotAuthorized):
            handle.service.get_executor_status(ctx_no_scope, GetExecutorStatusIn())
    finally:
        handle.close()
