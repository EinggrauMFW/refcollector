"""WP-11: P0 prompts."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import Client

from refcollector.contracts.api import TOOLS
from refcollector.mcp.server import build_server
from tests.fakes.service import FakeService

NAMES = ["reconstruct-bibliography", "review-uncertain", "recover-copies"]


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    async with Client(build_server(FakeService())) as c:
        yield c


def _text(res: Any) -> str:
    return "\n".join(m.content.text for m in res.messages)


@pytest.mark.anyio
async def test_exactly_three_p0_prompts(client: Client) -> None:
    prompts = (await client.list_prompts()).prompts
    assert [p.name for p in prompts] == NAMES
    for p in prompts:
        assert p.description
        assert [a.name for a in p.arguments or []] == ["project_id"]


@pytest.mark.anyio
@pytest.mark.parametrize("name", NAMES)
async def test_prompt_uses_project_id_and_only_real_tools(client: Client, name: str) -> None:
    res = await client.get_prompt(name, {"project_id": "prj_42"})
    text = _text(res)
    assert "prj_42" in text
    assert all(m.role == "user" for m in res.messages)
    for tool in re.findall(r"`([a-z_]+)`", text):
        assert tool in TOOLS or tool in {"needs_review", "discover_access", "extract", "resolve"}, tool


@pytest.mark.anyio
async def test_prompt_content_rules(client: Client) -> None:
    rb = _text(await client.get_prompt("reconstruct-bibliography", {}))
    for tool in ("import_source", "start_job", "update_reference", "attach_file", "create_export"):
        assert tool in rb
    ru = _text(await client.get_prompt("review-uncertain", {}))
    assert "needs_review" in ru
    assert "confirm" in ru
    assert "agent decision" in ru
    assert "never claim" in ru.lower()
    rc = _text(await client.get_prompt("recover-copies", {}))
    assert "discover_access" in rc
    assert "download_files" in rc


@pytest.mark.anyio
async def test_unknown_prompt_errors(client: Client) -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011 - SDK raises MCPError
        await client.get_prompt("review-drafts", {})
