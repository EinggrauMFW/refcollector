"""Local web server launch and uvicorn configuration."""

from __future__ import annotations

import logging
import sys

import uvicorn

from refcollector.config import Settings
from refcollector.errors import InvalidInput
from refcollector.web.app import create_app

log = logging.getLogger(__name__)


def configure_logging() -> None:
    """Send server logs to stderr."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def run_web(settings: Settings, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Launch the local web application server."""
    if host not in ("127.0.0.1", "localhost"):
        raise InvalidInput(
            "The web server only binds to 127.0.0.1 loopback (spec 7). Remote mode is prohibited."
        )
    if not (1 <= port <= 65535):
        raise InvalidInput(f"Invalid port {port}. Port must be between 1 and 65535.")

    configure_logging()
    app = create_app(settings=settings, host=host, port=port)
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        workers=1,
        log_level="info",
    )
