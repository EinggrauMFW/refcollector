"""RefCollector web package."""

from refcollector.web.app import create_app
from refcollector.web.server import run_web

__all__ = ["create_app", "run_web"]
