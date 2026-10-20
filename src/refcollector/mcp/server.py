"""WP-11: server start-up, stdio transport, logging to stderr. Spec 11.1, 13.

Low-level `mcp.server.Server` (SDK 2.x): explicit input/output JSON Schemas come from the
frozen DTOs, so FastMCP/MCPServer signature inference is not used. The server only translates.
"""

from __future__ import annotations

import getpass
import logging
import sys
from collections.abc import Collection
from typing import Any

import anyio
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from refcollector import __version__, bootstrap, ids
from refcollector.config import DEFAULT_MCP_SCOPES, Settings, load_settings
from refcollector.contracts.api import CallContext, RefCollectorService
from refcollector.domain.enums import Channel, Scope
from refcollector.mcp import prompts, tools

log = logging.getLogger(__name__)


def configure_logging() -> None:
    """stdout carries only protocol frames: send all logging to stderr."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def build_server(
    service: RefCollectorService,
    *,
    scopes: Collection[Scope] = DEFAULT_MCP_SCOPES,
    principal: str | None = None,
) -> Server[Any]:
    granted = frozenset(scopes)
    who = principal or getpass.getuser()

    def make_ctx() -> CallContext:
        # Origin, channel and scopes come from here only, never from tool arguments (7.4, AT-33).
        return CallContext(
            principal=who,
            channel=Channel.MCP,
            scopes=granted,
            request_id=ids.new_id("request"),
        )

    async def on_list_tools(
        ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=tools.list_tools())

    async def on_call_tool(ctx: ServerRequestContext[Any], params: CallToolRequestParams) -> CallToolResult:
        return await tools.call_tool(service, make_ctx, params.name, params.arguments)

    async def on_list_prompts(
        ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListPromptsResult:
        return ListPromptsResult(prompts=prompts.list_prompts())

    async def on_get_prompt(
        ctx: ServerRequestContext[Any], params: GetPromptRequestParams
    ) -> GetPromptResult:
        return prompts.get_prompt(params.name, params.arguments)

    return Server(
        "refcollector",
        version=__version__,
        instructions="RefCollector: extract, resolve and verify bibliographies.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_prompts=on_list_prompts,
        on_get_prompt=on_get_prompt,
    )


async def _serve(server: Server[Any]) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_stdio(settings: Settings) -> None:
    configure_logging()
    handle = bootstrap.open_app(settings)
    try:
        handle.start_executor()
        server = build_server(handle.service, scopes=settings.mcp_scopes)
        anyio.run(_serve, server)
    finally:
        handle.close()


def main() -> None:
    run_stdio(load_settings())


if __name__ == "__main__":
    main()
