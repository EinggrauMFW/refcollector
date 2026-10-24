"""Settings, RC_HOME, scopes and limits (spec 6.1, 6.6, 8.1, 10.4, 11.2, 13).

Settings live in `RC_HOME/config.json`. Secrets (API keys) live in the OS keyring; only their
presence is reported (spec 13).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path

import keyring
import platformdirs
import portalocker
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
    pdf_browser_python: str | None = None
    pdf_browser_binary: str | None = None

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


_config_lock_state = threading.local()


@contextlib.contextmanager
def config_lock(rc_home: Path, timeout: float = 10.0):
    """Sidecar file lock (config.lock) that survives config.json replacement.

    Thread-local depth tracking by home path avoids nested reentrancy deadlocks
    on the same home, while correctly locking across different homes.
    """
    home = rc_home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    depths = getattr(_config_lock_state, "depths", None)
    if depths is None:
        depths = {}
        _config_lock_state.depths = depths

    current_depth = depths.get(home, 0)
    if current_depth > 0:
        depths[home] = current_depth + 1
        try:
            yield
        finally:
            depths[home] -= 1
            if depths[home] <= 0:
                depths.pop(home, None)
        return

    lock_path = home / "config.lock"
    with portalocker.Lock(str(lock_path), timeout=timeout):
        depths[home] = 1
        try:
            yield
        finally:
            depths[home] -= 1
            if depths[home] <= 0:
                depths.pop(home, None)


def load_settings(rc_home: Path | None = None) -> Settings:
    home = (rc_home or default_rc_home()).resolve()
    path = home / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["rc_home"] = home
    return Settings.model_validate(data)


def save_settings(settings: Settings) -> None:
    """Save settings atomically under the shared sidecar configuration lock.

    Saves through a unique same-directory temporary file, flush/close, and os.replace.
    """
    home = settings.rc_home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump(mode="json", exclude={"rc_home"})
    content = json.dumps(data, indent=2).encode("utf-8")
    with config_lock(home):
        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(home),
            prefix=".config.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_path_str)
        try:
            with open(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, settings.config_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise


def get_secret(name: str) -> str | None:
    return keyring.get_password(KEYRING_SERVICE, name)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(KEYRING_SERVICE, name, value)


def secrets_present() -> dict[str, bool]:
    """For get_capabilities: presence only, never values (spec 13)."""
    return {name: get_secret(name) is not None for name in SECRET_NAMES}


def _get_or_create_hmac_key(rc_home: Path) -> bytes:
    home = rc_home.resolve()
    key_file = home / ".secret_hmac_key"
    if key_file.exists():
        return key_file.read_bytes()
    key = secrets.token_bytes(32)
    home.mkdir(parents=True, exist_ok=True)
    with config_lock(home):
        if key_file.exists():
            return key_file.read_bytes()
        fd, tmp_path_str = tempfile.mkstemp(dir=str(home), prefix=".hmac.", suffix=".tmp")
        tmp_path = Path(tmp_path_str)
        try:
            with open(fd, "wb") as f:
                f.write(key)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, key_file)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
    return key


def keyed_secret_digest(rc_home: Path, secret_value: str) -> str:
    """HMAC-SHA256 of the secret value using a persistent service key. Never plain SHA."""
    key = _get_or_create_hmac_key(rc_home)
    return hmac.new(key, secret_value.encode("utf-8"), hashlib.sha256).hexdigest()


def get_key_fingerprint(rc_home: Path, name: str) -> str | None:
    val = get_secret(name)
    if val is None:
        return None
    return keyed_secret_digest(rc_home, val)


def keys_fingerprints(rc_home: Path) -> dict[str, str | None]:
    return {name: get_key_fingerprint(rc_home, name) for name in SECRET_NAMES}


def normalize_pdf_fallback(value: str | None) -> str | None:
    """Select box, su, st, ru, a custom HTTPS DOI template, off, or None."""
    if value is None or value == "off":
        return None
    presets = {name: f"https://sci-hub.{name}/{{doi}}" for name in ("box", "su", "st", "ru")}
    selected = presets.get(value, value)
    for template in presets.values():
        if selected.rstrip("/") == template.removesuffix("/{doi}"):
            selected = template
            break
    from refcollector.files.pdf_fallback import validate_template

    return validate_template(selected)
