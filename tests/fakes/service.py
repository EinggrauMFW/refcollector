"""Fake RefCollectorService for MCP (WP-11) and CLI (WP-12) tests.

Each call is recorded as (method, ctx, req). The return value comes from `responses[method]`:
an output DTO, an RcError to raise, or a callable (ctx, req) -> DTO. A method without a
response raises AssertionError, so tests state what they expect.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from refcollector.contracts.api import TOOLS, CallContext

EXTRA_METHODS = ("update_project_settings", "review_decision")


class FakeService:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses: dict[str, Any] = dict(responses or {})
        self.calls: list[tuple[str, CallContext, Any]] = []

    def _call(self, method: str, ctx: CallContext, req: Any) -> Any:
        self.calls.append((method, ctx, req))
        if method not in self.responses:
            raise AssertionError(f"FakeService: no response set for {method}")
        resp = self.responses[method]
        if isinstance(resp, Exception):
            raise resp
        if callable(resp) and not hasattr(resp, "model_dump"):
            return resp(ctx, req)
        return resp

    def __getattr__(self, method: str) -> Callable[[CallContext, Any], Any]:
        if method in TOOLS or method in EXTRA_METHODS:
            return lambda ctx, req: self._call(method, ctx, req)
        raise AttributeError(method)
