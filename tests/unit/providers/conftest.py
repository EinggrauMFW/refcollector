"""Fixtures for WP-04 tests: a real ProviderHttpClient over the test database, respx-mocked."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from refcollector.config import Limits
from refcollector.domain.enums import NetworkMode, PolicyProfile, VersionPolicy
from refcollector.domain.models import ConfigSnapshot
from refcollector.providers.http import ProviderHttpClient
from refcollector.storage.db import Database

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "http"
EMAIL = "tester@university.example.org"


def fixture_json(provider: str, name: str) -> Any:
    return json.loads((FIXTURES / provider / name).read_text(encoding="utf-8"))


def snapshot(mode: NetworkMode = NetworkMode.METADATA, email: str | None = EMAIL) -> ConfigSnapshot:
    return ConfigSnapshot(
        policy_profile=PolicyProfile.DEFAULT,
        network_mode=mode,
        provider_allowlist=[],
        identity_policy_version="identity-policy-v1",
        eligibility_policy_version="eligibility-policy-v1",
        adapter_versions={},
        version_policy=VersionPolicy.PREFER_PUBLISHED,
        contact_email=email,
    )


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def fake_sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
def respx_mock() -> Any:
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
async def make_client(
    db: Database, fake_sleep: FakeSleep, respx_mock: Any
) -> AsyncIterator[Callable[..., ProviderHttpClient]]:
    made: list[ProviderHttpClient] = []

    def make(
        mode: NetworkMode = NetworkMode.METADATA,
        *,
        secrets: dict[str, str] | None = None,
        limits: Limits | None = None,
    ) -> ProviderHttpClient:
        client = ProviderHttpClient(
            snapshot(mode),
            db,
            limits or Limits(),
            sleep=fake_sleep,
            secrets=secrets or {},
            download_transport=httpx.MockTransport(respx_mock.async_handler),
        )
        made.append(client)
        return client

    yield make
    for client in made:
        await client.aclose()


@pytest.fixture
def client(make_client: Callable[..., ProviderHttpClient]) -> ProviderHttpClient:
    return make_client()
