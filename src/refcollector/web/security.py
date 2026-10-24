"""Security middleware and checks for local HTTP loopback operations.

Enforces:
- Exact Host and port matching
- Exact Origin on mutating requests and rejection of foreign Origin
- Fetch Metadata (Sec-Fetch-Site: cross-site rejection)
- Anti-framing and restrictive CSP headers
- Request body size limits
"""

from __future__ import annotations

from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


class ExactHostMiddleware:
    """Validate exact Host authority including port."""

    def __init__(self, app: ASGIApp, host: str = "127.0.0.1", port: int = 8000) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.expected_authorities = {f"{host}:{port}"}
        if port == 80:
            self.expected_authorities.add(host)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        host_header = headers.get("host")
        if not host_header or host_header not in self.expected_authorities:
            response = PlainTextResponse("Invalid host header", status_code=400)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class OriginAndFetchMetadataMiddleware:
    """Enforce exact Origin and reject cross-site Fetch Metadata."""

    def __init__(self, app: ASGIApp, host: str = "127.0.0.1", port: int = 8000) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.expected_origins = {f"http://{host}:{port}"}
        if port == 80:
            self.expected_origins.add(f"http://{host}")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        method = scope.get("method", "GET").upper()

        # Reject cross-site Fetch Metadata
        fetch_site = headers.get("sec-fetch-site")
        if fetch_site and fetch_site.lower() == "cross-site":
            response = JSONResponse(
                {"code": "NOT_AUTHORIZED", "message": "Cross-site requests forbidden"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        origin = headers.get("origin")
        if origin:
            if origin not in self.expected_origins:
                response = JSONResponse(
                    {"code": "NOT_AUTHORIZED", "message": "Foreign origin forbidden"},
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        elif method in MUTATING_METHODS:
            response = JSONResponse(
                {"code": "NOT_AUTHORIZED", "message": "Origin header required for mutating requests"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Add secure headers to all HTTP responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_library_file = path.startswith("/api/library/collections/") and "/files/" in path
        is_library_page = (
            not path.startswith("/api")
            and not path.startswith("/advanced")
            and (not path.startswith("/static") or path.startswith("/static/library"))
        )

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for (k, v) in message.get("headers", [])
                    if k.lower()
                    not in (
                        b"x-content-type-options",
                        b"x-frame-options",
                        b"content-security-policy",
                        b"referrer-policy",
                    )
                ]

                if is_library_file:
                    x_frame_options = b"SAMEORIGIN"
                    csp = (
                        b"default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; "
                        b"object-src 'none'; frame-ancestors 'self';"
                    )
                elif is_library_page:
                    x_frame_options = b"DENY"
                    csp = (
                        b"default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; "
                        b"object-src 'none'; frame-ancestors 'none'; frame-src 'self';"
                    )
                else:
                    x_frame_options = b"DENY"
                    csp = (
                        b"default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; "
                        b"object-src 'none'; frame-ancestors 'none';"
                    )

                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", x_frame_options),
                        (b"content-security-policy", csp),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodyLimitMiddleware:
    """Reject requests with bodies larger than the maximum allowed size."""

    def __init__(self, app: ASGIApp, max_bytes: int = 100 * 1024 * 1024) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > self.max_bytes:
                    response = JSONResponse(
                        {"code": "LIMIT_EXCEEDED", "message": "Request body too large"},
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(
                    {"code": "INVALID_INPUT", "message": "Invalid content-length"},
                    status_code=400,
                )
                await response(scope, receive, send)
                return

        total_received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal total_received
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                total_received += len(body)
                if total_received > self.max_bytes:
                    raise BodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLargeError:
            response = JSONResponse(
                {"code": "LIMIT_EXCEEDED", "message": "Request body stream too large"},
                status_code=413,
            )
            await response(scope, receive, send)


class BodyTooLargeError(Exception):
    pass
