"""Composition root: build Store, executor, adapters and service for one RC_HOME.

WP-10 implements `open_app`. MCP (WP-11) and CLI (WP-12) call it and use only the
returned `AppHandle` (contracts.api).
"""

from __future__ import annotations

from refcollector.config import Settings
from refcollector.contracts.api import AppHandle


def open_app(settings: Settings) -> AppHandle:
    raise NotImplementedError("WP-10 implements open_app")
