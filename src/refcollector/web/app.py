"""FastAPI application factory, lifespan management, and global error handlers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.middleware import Middleware
from starlette.staticfiles import StaticFiles

from refcollector import __version__, bootstrap
from refcollector.config import Settings, load_settings
from refcollector.contracts.api import AppHandle
from refcollector.errors import ErrorCode, NotFound, RcError
from refcollector.ids import new_id
from refcollector.web.library_routes import router as library_router
from refcollector.web.routes import router
from refcollector.web.security import (
    BodyLimitMiddleware,
    ExactHostMiddleware,
    OriginAndFetchMetadataMiddleware,
    SecurityHeadersMiddleware,
)
from refcollector.web.session import SessionStore

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    handle: AppHandle | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastAPI:
    """Create and configure the RefCollector local web application."""
    active_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal handle
        app.state.session_store = SessionStore()

        if handle is None:
            handle = bootstrap.open_app(active_settings)

        app.state.handle = handle
        app.state.service = handle.service
        app.state.holds_executor = handle.start_executor()

        try:
            yield
        finally:
            if handle is not None:
                handle.close()

    middleware = [
        Middleware(ExactHostMiddleware, host=host, port=port),
        Middleware(OriginAndFetchMetadataMiddleware, host=host, port=port),
        Middleware(SecurityHeadersMiddleware),
        Middleware(BodyLimitMiddleware, max_bytes=active_settings.limits.pdf_bytes + 64 * 1024),
    ]

    app = FastAPI(
        title="RefCollector Web Application",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
        middleware=middleware,
    )

    if handle is not None:
        app.state.handle = handle
        app.state.service = handle.service
        app.state.session_store = SessionStore()

    # Register error handlers
    @app.exception_handler(RcError)
    async def rc_error_handler(request: Request, exc: RcError) -> JSONResponse:
        status_code = 400
        if exc.code in (ErrorCode.NOT_AUTHORIZED, ErrorCode.PATH_NOT_ALLOWED):
            status_code = 403
        elif exc.code == ErrorCode.NOT_FOUND:
            status_code = 404
        elif exc.code in (
            ErrorCode.REVISION_CONFLICT,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            ErrorCode.REVISION_NOT_CURRENT,
        ):
            status_code = 409
        elif exc.code in (ErrorCode.LIMIT_EXCEEDED, ErrorCode.BUDGET_EXCEEDED):
            status_code = 413
        elif exc.code == ErrorCode.RATE_LIMITED:
            status_code = 429

        content = exc.to_payload()
        if exc.retry_after is not None:
            content["retry_after"] = exc.retry_after

        headers: dict[str, str] = {}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(int(exc.retry_after))

        return JSONResponse(status_code=status_code, content=content, headers=headers)

    def _format_validation_error(exc: RequestValidationError | ValidationError) -> JSONResponse:
        sanitized_details = []
        for err in exc.errors():
            loc = list(err.get("loc", ()))
            msg = str(err.get("msg", ""))
            err_type = str(err.get("type", "validation_error"))

            # Redact error message if loc points to a secret or key field
            if any(k in loc for k in ("value", "key", "secret", "password")):
                msg = "Invalid secret value"

            sanitized_details.append(
                {
                    "loc": loc,
                    "msg": msg,
                    "type": err_type,
                }
            )
        content = {
            "code": "INVALID_INPUT",
            "message": "Request validation failed",
            "details": sanitized_details,
        }
        return JSONResponse(status_code=400, content=content)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _format_validation_error(exc)

    @app.exception_handler(ValidationError)
    async def pydantic_validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        return _format_validation_error(exc)

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        req_id = new_id("request")
        log.exception("Unhandled server error [request_id=%s]: %s", req_id, exc)
        content = {
            "code": "INTERNAL",
            "message": "internal error",
            "request_id": req_id,
        }
        return JSONResponse(status_code=500, content=content)

    app.include_router(router)
    app.include_router(library_router)

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        library_index = static_dir / "library" / "index.html"
        advanced_index = static_dir / "index.html"

        @app.get("/advanced", include_in_schema=False)
        @app.get("/advanced/{full_path:path}", include_in_schema=False)
        async def advanced_page() -> FileResponse:
            target = advanced_index if advanced_index.is_file() else library_index
            return FileResponse(str(target))

        @app.get("/", include_in_schema=False)
        async def root() -> FileResponse:
            target = library_index if library_index.is_file() else advanced_index
            return FileResponse(str(target))

        @app.api_route(
            "/{full_path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            include_in_schema=False,
        )
        async def catch_all(request: Request, full_path: str) -> FileResponse:
            if full_path.startswith("api/") or full_path == "api" or request.method not in ("GET", "HEAD"):
                raise NotFound(f"Path '/{full_path}' not found")
            target = library_index if library_index.is_file() else advanced_index
            return FileResponse(str(target))

    return app
