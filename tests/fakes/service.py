"""Fake RefCollectorService for MCP (WP-11) and CLI (WP-12) tests.

Each call is recorded as (method, ctx, req). The return value comes from `responses[method]`:
an output DTO, an RcError to raise, or a callable (ctx, req) -> DTO. A method without a
response raises AssertionError, so tests state what they expect.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from refcollector.contracts.api import TOOLS, CallContext

EXTRA_METHODS = (
    "rename_project",
    "get_project_consequences",
    "delete_project",
    "update_project_settings",
    "review_decision",
    "list_jobs",
    "list_global_artifacts",
    "get_executor_status",
    "read_project_file",
    "upload_pdf",
    "render_source_page",
    "prepare_web_review",
    "confirm_web_review",
    "prepare_web_file_review",
    "confirm_web_file_review",
    "get_settings",
    "prepare_web_settings",
    "confirm_web_settings",
    "update_web_settings",
    "project_file_path",
)


class FakeService:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses: dict[str, Any] = dict(responses or {})
        self.calls: list[tuple[str, CallContext, Any]] = []

    def _call(self, method: str, ctx: CallContext, req: Any) -> Any:
        self.calls.append((method, ctx, req))
        if method not in self.responses:
            if method == "project_file_path" and hasattr(self, "store") and hasattr(self.store, "files"):
                pid, kind, rid = req
                sha = rid
                if kind == "source" and hasattr(self.store, "sources"):
                    try:
                        src = self.store.sources.get(pid, rid)
                        sha = getattr(src, "sha256", rid)
                    except Exception:
                        pass
                elif kind == "work_file" and hasattr(self.store, "work_files"):
                    try:
                        wf = self.store.work_files.get(pid, rid)
                        sha = getattr(wf, "sha256", rid)
                    except Exception:
                        pass
                return self.store.files.path_for(pid, sha)
            raise AssertionError(f"FakeService: no response set for {method}")
        resp = self.responses[method]
        if isinstance(resp, Exception):
            raise resp
        if callable(resp) and not hasattr(resp, "model_dump"):
            return resp(ctx, req)
        return resp

    def __getattr__(self, method: str) -> Callable[..., Any]:
        if method in TOOLS or method in EXTRA_METHODS:
            return lambda ctx, req, *args: self._call(method, ctx, (req, *args) if args else req)
        raise AttributeError(method)
