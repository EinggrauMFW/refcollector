"""Settings, RC_HOME, scopes and limits (spec 6.1, 6.6, 8.1, 10.4, 11.2, 13).

Settings live in `RC_HOME/config.json`. Secrets (API keys) live in the OS keyring; only their
presence is reported (spec 13).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import keyring
import platformdirs
from pydantic import BaseModel, ConfigDict, Field

from refcollector.domain.enums import Scope

APP_NAME = "refcollector"
KEYRING_SERVICE = "refcollector"
SECRET_NAMES = ("openalex_key", "s2_key")

IDENTITY_POLICY_VERSION = "identity-policy-v1.1"
ELIGIBILITY_POLICY_VERSION = "eligibility-policy-v1"

P0_PROVIDERS = ("crossref", "datacite", "doiorg", "openalex", "semanticscholar", "unpaywall", "openlibrary")

# MCP channel default: every scope except files:download (spec 11.2).
DEFAULT_MCP_SCOPES = frozenset(Scope) - {Scope.FILES_DOWNLOAD}


class Limits(BaseModel):
    """Spec 8.1, 10.4, 11.2."""

    model_config = ConfigDict(extra="forbid")

    pdf_bytes: int = 100 * 1024 * 1024
    pdf_pages: int = 1500
    occurrences_per_source: int = 5000
    pasted_text_bytes: int = 1024 * 1024
    citation_chars: int = 4000
    read_artifact_bytes: int = 64 * 1024
    list_default: int = 25
    list_max: int = 100
    cursor_ttl_s: int = 24 * 3600
    download_bytes: int = 100 * 1024 * 1024
    download_timeout_s: float = 120.0
    max_redirects: int = 5
    step_timeout_s: float = 120.0
    step_max_attempts: int = 5
    idempotency_ttl_s: int = 7 * 24 * 3600
    cache_ttl_identifier_s: int = 30 * 24 * 3600
    cache_ttl_search_s: int = 7 * 24 * 3600
    provider_exhausted_retry_after_s: float = 60.0
    client_lock_poll_s: float = 5.0
    executor_poll_s: float = 0.5


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rc_home: Path
    email: str | None = None
    allow_folders: list[Path] = Field(default_factory=list)  # D-12: empty by default
    mcp_scopes: set[Scope] = Field(default_factory=lambda: set(DEFAULT_MCP_SCOPES))
    download_enabled_global: bool = False
    enabled_providers: list[str] = Field(default_factory=lambda: list(P0_PROVIDERS))
    limits: Limits = Field(default_factory=Limits)
    pdf_fallback_url: str | None = None

    # ---- paths ----
    @property
    def db_path(self) -> Path:
        return self.rc_home / "refcollector.sqlite3"

    @property
    def lock_path(self) -> Path:
        return self.rc_home / "executor.lock"

    @property
    def tmp_dir(self) -> Path:
        return self.rc_home / "tmp"

    @property
    def store_dir(self) -> Path:
        return self.rc_home / "store"

    @property
    def artifacts_dir(self) -> Path:
        return self.rc_home / "artifacts"

    @property
    def config_path(self) -> Path:
        return self.rc_home / "config.json"


def default_rc_home() -> Path:
    env = os.environ.get("RC_HOME")
    return Path(env) if env else Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def load_settings(rc_home: Path | None = None) -> Settings:
    home = (rc_home or default_rc_home()).resolve()
    path = home / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["rc_home"] = home
    return Settings.model_validate(data)


def save_settings(settings: Settings) -> None:
    settings.rc_home.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump(mode="json", exclude={"rc_home"})
    settings.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_secret(name: str) -> str | None:
    return keyring.get_password(KEYRING_SERVICE, name)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(KEYRING_SERVICE, name, value)


def secrets_present() -> dict[str, bool]:
    """For get_capabilities: presence only, never values (spec 13)."""
    return {name: get_secret(name) is not None for name in SECRET_NAMES}
