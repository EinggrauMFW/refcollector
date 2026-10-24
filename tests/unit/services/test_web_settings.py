"""Tests for web settings: get_settings, prepare_web_settings, confirm_web_settings,
and update_web_settings (Wave 3).

Verifies enumerated writes, optimistic revision checks, SecretStr safety, keyed secret fingerprints,
cross-store pending/completed recovery, idempotency replay, and keyring error handling.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from refcollector.config import load_settings, save_settings
from refcollector.contracts import api
from refcollector.contracts.api import (
    AddAllowedFolderChange,
    CallContext,
    GetSettingsIn,
    SetEmailChange,
    SetPdfFallbackChange,
    SetProjectDownloadsChange,
    SetProjectNetworkChange,
    SetProviderKeyChange,
    UpdateWebSettingsIn,
)
from refcollector.domain.enums import Channel, NetworkMode, Scope, VersionPolicy
from refcollector.domain.models import IdempotencyRecord
from refcollector.errors import (
    IdempotencyConflict,
    IntegrationUnavailable,
    NotAuthorized,
    RevisionConflict,
)
from tests.unit.services.conftest import key


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}

    def fake_get_password(service: str, name: str) -> str | None:
        return store.get(name)

    def fake_set_password(service: str, name: str, value: str) -> None:
        store[name] = value

    monkeypatch.setattr("keyring.get_password", fake_get_password)
    monkeypatch.setattr("keyring.set_password", fake_set_password)
    return store


def web_ctx(
    session_id: str = "sess-settings-1",
    principal: str = "test-user",
    scopes: frozenset[Scope] | None = None,
) -> CallContext:
    return CallContext(
        principal=principal,
        channel=Channel.WEB_UI,
        scopes=scopes if scopes is not None else frozenset(Scope),
        request_id="req-settings-web",
        web_session_id=session_id,
    )


def test_get_settings_basic_and_project(env: Any, fake_keyring: dict[str, str]) -> None:
    svc = env.svc
    ctx = web_ctx()

    # Without project_id
    out = svc.get_settings(ctx, GetSettingsIn())
    assert isinstance(out, api.GetSettingsOut)
    assert len(out.revision) == 64  # SHA-256 digest
    assert out.project is None
    assert out.rc_home == str(env.settings.rc_home.resolve())
    assert out.keys["openalex_key"] is False
    assert out.keys["s2_key"] is False

    # With project_id
    pid = env.project("proj-settings-test")
    out_proj = svc.get_settings(ctx, GetSettingsIn(project_id=pid))
    assert out_proj.project is not None
    assert out_proj.project.project_id == pid
    assert out_proj.project.network_mode == NetworkMode.METADATA
    assert out_proj.project.download_enabled is False
    assert out_proj.project.version_policy == VersionPolicy.PREFER_PUBLISHED


def test_get_settings_requires_web_session_and_scope(env: Any) -> None:
    svc = env.svc
    # Missing session
    ctx_no_sess = CallContext(
        principal="test-user",
        channel=Channel.WEB_UI,
        scopes=frozenset(Scope),
        request_id="req-1",
        web_session_id=None,
    )
    with pytest.raises(NotAuthorized, match="active web session"):
        svc.get_settings(ctx_no_sess, GetSettingsIn())

    # Missing projects:read scope
    ctx_no_scope = web_ctx(scopes=frozenset({Scope.REFERENCES_READ}))
    with pytest.raises(NotAuthorized, match="projects:read"):
        svc.get_settings(ctx_no_scope, GetSettingsIn())


def test_prepare_and_confirm_set_email(env: Any) -> None:
    svc = env.svc
    ctx = web_ctx()

    current = svc.get_settings(ctx, GetSettingsIn())
    req = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=SetEmailChange(value="researcher@example.org"),
        idempotency_key=key(),
    )

    intent = svc.prepare_web_settings(ctx, req)
    assert intent.purpose == "settings"
    assert intent.summary["change"] == "set_email"
    assert intent.summary["after"] == "researcher@example.org"

    confirmed = svc.confirm_web_settings(ctx, req, intent.confirmation_token)
    assert confirmed.email == "researcher@example.org"
    assert confirmed.revision != current.revision

    # Verify on-disk file and service instance
    fresh = load_settings(env.settings.rc_home)
    assert fresh.email == "researcher@example.org"
    assert svc.settings.email == "researcher@example.org"


def test_prepare_and_confirm_add_allowed_folder(env: Any, tmp_path: Path) -> None:
    svc = env.svc
    ctx = web_ctx()

    current = svc.get_settings(ctx, GetSettingsIn())
    new_folder = tmp_path / "extra_docs"
    new_folder.mkdir()

    req = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=AddAllowedFolderChange(path=str(new_folder)),
        idempotency_key=key(),
    )

    intent = svc.prepare_web_settings(ctx, req)
    assert intent.summary["change"] == "add_allowed_folder"
    assert intent.summary["folder"] == str(new_folder.resolve())

    confirmed = svc.confirm_web_settings(ctx, req, intent.confirmation_token)
    assert str(new_folder.resolve()) in confirmed.allow_folders


def test_prepare_and_confirm_pdf_fallback(env: Any) -> None:
    svc = env.svc
    ctx = web_ctx()

    current = svc.get_settings(ctx, GetSettingsIn())
    req = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=SetPdfFallbackChange(value="box"),
        idempotency_key=key(),
    )

    intent = svc.prepare_web_settings(ctx, req)
    assert intent.summary["after"] == "https://sci-hub.box/{doi}"

    confirmed = svc.confirm_web_settings(ctx, req, intent.confirmation_token)
    assert confirmed.pdf_fallback_url == "https://sci-hub.box/{doi}"

    # Now turn off
    req_off = UpdateWebSettingsIn(
        expected_settings_revision=confirmed.revision,
        change=SetPdfFallbackChange(value="off"),
        idempotency_key=key(),
    )
    intent_off = svc.prepare_web_settings(ctx, req_off)
    assert intent_off.summary["after"] is None
    confirmed_off = svc.confirm_web_settings(ctx, req_off, intent_off.confirmation_token)
    assert confirmed_off.pdf_fallback_url is None


def test_prepare_and_confirm_set_provider_key(env: Any, fake_keyring: dict[str, str]) -> None:
    svc = env.svc
    ctx = web_ctx()

    current = svc.get_settings(ctx, GetSettingsIn())
    secret_value = "super-secret-token-12345"
    req = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=SetProviderKeyChange(provider="openalex_key", value=SecretStr(secret_value)),
        idempotency_key=key(),
    )

    intent = svc.prepare_web_settings(ctx, req)
    # Crucial security assertion: secret value and secret digest must NOT appear in summary
    assert intent.summary["provider"] == "openalex_key"
    assert intent.summary["after_configured"] is True
    summary_str = json.dumps(intent.summary)
    assert secret_value not in summary_str

    confirmed = svc.confirm_web_settings(ctx, req, intent.confirmation_token)
    assert confirmed.keys["openalex_key"] is True

    # Stored in keyring
    assert fake_keyring["openalex_key"] == secret_value

    # Crucial security assertion: check SQLite idempotency record does NOT contain raw secret
    rows = svc.store.db.query("SELECT * FROM idempotency_record WHERE key = ?", (req.idempotency_key,))
    assert len(rows) == 1
    db_data = rows[0]["result"]
    assert secret_value not in db_data


def test_secret_str_different_values_conflict(env: Any, fake_keyring: dict[str, str]) -> None:
    svc = env.svc
    ctx = web_ctx()

    current = svc.get_settings(ctx, GetSettingsIn())
    shared_key = key()

    req1 = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=SetProviderKeyChange(provider="s2_key", value=SecretStr("secret-A")),
        idempotency_key=shared_key,
    )
    intent1 = svc.prepare_web_settings(ctx, req1)
    svc.confirm_web_settings(ctx, req1, intent1.confirmation_token)

    # Calling with same key but DIFFERENT secret value must conflict!
    req2 = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=SetProviderKeyChange(provider="s2_key", value=SecretStr("secret-B")),
        idempotency_key=shared_key,
    )
    with pytest.raises((IdempotencyConflict, RevisionConflict, NotAuthorized)):
        # Prepare or confirm must detect conflict
        intent2 = svc.prepare_web_settings(ctx, req2)
        svc.confirm_web_settings(ctx, req2, intent2.confirmation_token)


def test_project_settings_changes(env: Any) -> None:
    svc = env.svc
    ctx = web_ctx()
    pid = env.project("proj-network-test")

    current = svc.get_settings(ctx, GetSettingsIn(project_id=pid))
    assert current.project is not None
    assert current.project.network_mode == NetworkMode.METADATA

    # Change network mode
    req_net = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=SetProjectNetworkChange(project_id=pid, mode=NetworkMode.RETRIEVAL),
        idempotency_key=key(),
    )
    intent_net = svc.prepare_web_settings(ctx, req_net)
    out_net = svc.confirm_web_settings(ctx, req_net, intent_net.confirmation_token)
    assert out_net.project is not None
    assert out_net.project.network_mode == NetworkMode.RETRIEVAL

    # Change downloads
    req_dl = UpdateWebSettingsIn(
        expected_settings_revision=out_net.revision,
        change=SetProjectDownloadsChange(project_id=pid, enabled=True),
        idempotency_key=key(),
    )
    intent_dl = svc.prepare_web_settings(ctx, req_dl)
    out_dl = svc.confirm_web_settings(ctx, req_dl, intent_dl.confirmation_token)
    assert out_dl.project is not None
    assert out_dl.project.download_enabled is True


def test_stale_settings_revision_raises_conflict(env: Any) -> None:
    svc = env.svc
    ctx = web_ctx()
    current = svc.get_settings(ctx, GetSettingsIn())

    # Simulate an external CLI edit modifying config.json
    fresh = load_settings(env.settings.rc_home)
    fresh.email = "cli_user@example.com"
    save_settings(fresh)

    # Attempting to use the stale revision fails
    req = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=SetPdfFallbackChange(value="su"),
        idempotency_key=key(),
    )
    with pytest.raises(RevisionConflict):
        svc.prepare_web_settings(ctx, req)


def test_cross_store_recovery_after_external_write_interruption(env: Any) -> None:
    svc = env.svc
    ctx = web_ctx()

    current = svc.get_settings(ctx, GetSettingsIn())
    idem_key = key()
    req = UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change=SetEmailChange(value="recovered@example.org"),
        idempotency_key=idem_key,
    )
    intent = svc.prepare_web_settings(ctx, req)

    # Prepare safe target output
    home = env.settings.rc_home
    fresh = load_settings(home)
    fresh.email = "recovered@example.org"
    save_settings(fresh)
    target_out = svc.get_settings(ctx, GetSettingsIn())

    # Manually simulate the crash state: PENDING record in SQLite idempotency table
    # where the external write has occurred, but SQLite was not marked completed
    pending_payload = {
        "status": "pending",
        "before_revision": current.revision,
        "target_revision": target_out.revision,
        "response": target_out.model_dump(mode="json"),
    }
    svc.store.idempotency.put(
        IdempotencyRecord(
            principal=ctx.principal,
            project_id="",
            tool="update_web_settings",
            key=idem_key,
            fingerprint=svc._fingerprint_web_settings(home, req),
            result=pending_payload,
            created_at=env.clock(),
            expires_at=env.clock() + timedelta(days=7),
        )
    )

    # Client retries confirm_web_settings with the same token and request
    recovered_out = svc.confirm_web_settings(ctx, req, intent.confirmation_token)
    assert recovered_out.email == "recovered@example.org"

    # Verify idempotency record in SQLite is now completed
    rec = svc.store.idempotency.get(ctx.principal, "", "update_web_settings", idem_key, now=env.clock())
    assert rec is not None
    assert rec.result["status"] == "completed"


def test_keyring_failure_handling(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = env.svc
    ctx = web_ctx()

    # When keyring throws error on get_settings, safe warning reported
    def broken_get_password(service: str, name: str) -> None:
        raise RuntimeError("Keyring locked or unavailable")

    monkeypatch.setattr("keyring.get_password", broken_get_password)

    out = svc.get_settings(ctx, GetSettingsIn())
    assert any("keyring unavailable" in w for w in out.warnings)
    assert out.keys["openalex_key"] is False

    # Key writes fail closed with IntegrationUnavailable
    req = UpdateWebSettingsIn(
        expected_settings_revision=out.revision,
        change=SetProviderKeyChange(provider="openalex_key", value=SecretStr("some-key")),
        idempotency_key=key(),
    )
    with pytest.raises(IntegrationUnavailable):
        svc.prepare_web_settings(ctx, req)


def test_update_web_settings_denied_without_grant(env: Any) -> None:
    svc = env.svc
    ctx = web_ctx()  # settings_grant is None
    req = UpdateWebSettingsIn(
        expected_settings_revision="dummy-rev",
        change=SetEmailChange(value="bad@example.com"),
        idempotency_key=key(),
    )
    with pytest.raises(NotAuthorized, match="service-validated settings grant"):
        svc.update_web_settings(ctx, req)
