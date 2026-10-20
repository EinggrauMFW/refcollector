"""WP-12: Typer application and commands (spec 14.1). Entry point: `refcollector`.

The only place where `SystemExit` may be raised (spec 6.6).
"""

from __future__ import annotations

import typer

app = typer.Typer(name="refcollector", no_args_is_help=True, help="RefCollector P0 (in development).")


@app.callback()
def main() -> None:
    """RefCollector command line."""
