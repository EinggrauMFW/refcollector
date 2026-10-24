"""Independent HTTP checks against real persisted review/settings service behavior."""

import json

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient

from refcollector.config import load_settings
from refcollector.domain.enums import AuditEventType, IdentityState
from refcollector.web.app import create_app


class SharedServiceHandle:
    """Use the fixture's real service; its fixture owns storage cleanup."""

    def __init__(self, service):
        self.service = service

    def start_executor(self):
        return False

    def close(self):
        pass


@pytest.fixture
def authority_browser(env, monkeypatch):
    stored_keys = {}
    writes = []
    monkeypatch.setattr("keyring.get_password", lambda service, name: stored_keys.get((service, name)))

    def set_password(service, name, value):
        writes.append((service, name))
        stored_keys[service, name] = value

    monkeypatch.setattr("keyring.set_password", set_password)
    monkeypatch.setattr(
        "keyring.delete_password", lambda service, name: stored_keys.pop((service, name), None)
    )
    app = create_app(settings=env.settings, handle=SharedServiceHandle(env.svc))
    base = "http://127.0.0.1:8000"  # In-process ASGI requests, never a listener.
    with TestClient(app, base_url=base) as client:
        response = client.get("/api/session")
        assert response.status_code == 200
        client.headers.update({"Origin": base, "X-CSRF-Token": response.json()["csrf_token"]})
        yield client, stored_keys, writes


def test_web_review_confirms_once_and_replays_original_revision(env, authority_browser):
    client, _, _ = authority_browser
    pid = env.project("review HTTP")
    sid = env.text_source(pid)
    occ = env.occ(pid, sid)
    candidate = env.candidate(pid, occ.occurrence_id)
    path = f"/api/projects/{pid}/references/{occ.occurrence_id}"
    body = {
        "project_id": pid,
        "occurrence_id": occ.occurrence_id,
        "candidate_id": candidate.candidate_id,
        "action": "accept",
        "expected_revision": occ.revision,
        "idempotency_key": "independent-web-review",
    }
    assert client.post(f"{path}/review", json=body).status_code == 403
    intent = client.post(f"{path}/review-intent", json=body)
    assert intent.status_code == 200, intent.text
    assert env.store.occurrences.get(pid, occ.occurrence_id).approval is None
    headers = {"X-Confirmation-Token": intent.json()["confirmation_token"]}
    first = client.post(f"{path}/review", json=body, headers=headers)
    assert first.status_code == 200, first.text
    replay = client.post(f"{path}/review", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    current = env.store.occurrences.get(pid, occ.occurrence_id)
    assert current.identity == IdentityState.ACCEPTED_USER
    assert current.approval.surface.value == "web_ui"
    approvals = [
        row
        for row in env.store.audit.list(pid, subject_id=occ.occurrence_id)
        if row.type == AuditEventType.APPROVAL_RECORDED
    ]
    assert len(approvals) == 1
    assert approvals[0].provenance.channel.value == "web_ui"
    assert approvals[0].provenance.origin.value == "interactive"
    changed = client.post(f"{path}/review", json={**body, "note": "changed payload"}, headers=headers)
    assert changed.status_code in {403, 409}
    assert env.store.occurrences.get(pid, occ.occurrence_id).revision == current.revision


def test_web_settings_intent_has_no_effect_and_secret_replay_never_persists_value(env, authority_browser):
    client, stored_keys, writes = authority_browser
    before = client.get("/api/settings")
    assert before.status_code == 200, before.text
    secret = "independent-test-key-not-a-real-credential"
    body = {
        "expected_settings_revision": before.json()["revision"],
        "change": {"type": "set_provider_key", "provider": "openalex_key", "value": secret},
        "idempotency_key": "independent-web-key-write",
    }
    assert client.post("/api/settings", json=body).status_code == 403
    intent = client.post("/api/settings/intent", json=body)
    assert intent.status_code == 200, intent.text
    assert secret not in intent.text
    assert not writes
    headers = {"X-Confirmation-Token": intent.json()["confirmation_token"]}
    first = client.post("/api/settings", json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert secret not in first.text
    assert first.json()["keys"]["openalex_key"] is True
    assert secret in stored_keys.values()
    replay = client.post("/api/settings", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert len(writes) == 1
    altered = {**body, "change": {**body["change"], "value": "different-test-key"}}
    conflict = client.post("/api/settings", json=altered, headers=headers)
    assert conflict.status_code in {403, 409}
    assert len(writes) == 1
    config = load_settings(env.settings.rc_home)
    assert secret not in config.model_dump_json()
    for file in env.settings.rc_home.rglob("*"):
        if file.is_file():
            assert secret.encode() not in file.read_bytes(), f"secret persisted in {file.name}"
    assert secret not in json.dumps(first.json())


def test_keyring_failure_cannot_echo_secret_in_response_or_logs(authority_browser, monkeypatch, caplog):
    from keyring.errors import PasswordSetError

    client, _, _ = authority_browser
    current = client.get("/api/settings")
    assert current.status_code == 200, current.text
    secret = "backend-echo-test-key-not-a-real-credential"
    body = {
        "expected_settings_revision": current.json()["revision"],
        "change": {"type": "set_provider_key", "provider": "openalex_key", "value": secret},
        "idempotency_key": "independent-keyring-failure",
    }
    intent = client.post("/api/settings/intent", json=body)
    assert intent.status_code == 200, intent.text

    def reject_key(service, name, value):
        raise PasswordSetError(f"backend rejected {value}")

    monkeypatch.setattr("keyring.set_password", reject_key)
    response = client.post(
        "/api/settings",
        json=body,
        headers={"X-Confirmation-Token": intent.json()["confirmation_token"]},
    )
    assert response.status_code >= 400
    assert secret not in response.text
    assert secret not in caplog.text


@pytest.mark.parametrize("invalidated_by", ["new_session", "expiry"])
def test_settings_confirmation_rejects_wrong_session_or_expired_intent(
    env, authority_browser, invalidated_by
):
    client, _, writes = authority_browser
    current = client.get("/api/settings").json()
    body = {
        "expected_settings_revision": current["revision"],
        "change": {"type": "set_email", "value": "confirmed@example.org"},
        "idempotency_key": f"invalidated-intent-{invalidated_by}",
    }
    intent = client.post("/api/settings/intent", json=body)
    assert intent.status_code == 200, intent.text
    if invalidated_by == "new_session":
        client.cookies.clear()
        session = client.get("/api/session")
        assert session.status_code == 200
        client.headers["X-CSRF-Token"] = session.json()["csrf_token"]
    else:
        env.clock.advance(seconds=61)
    rejected = client.post(
        "/api/settings",
        json=body,
        headers={"X-Confirmation-Token": intent.json()["confirmation_token"]},
    )
    assert rejected.status_code == 403, rejected.text
    assert load_settings(env.settings.rc_home).email == current["email"]
    assert not writes


def test_key_write_recovers_after_completion_record_failure(env, authority_browser, monkeypatch):
    client, stored_keys, writes = authority_browser
    current = client.get("/api/settings").json()
    secret = "recovery-test-key-not-a-real-credential"
    body = {
        "expected_settings_revision": current["revision"],
        "change": {"type": "set_provider_key", "provider": "openalex_key", "value": secret},
        "idempotency_key": "independent-keyring-interrupted-completion",
    }
    intent = client.post("/api/settings/intent", json=body)
    assert intent.status_code == 200, intent.text
    headers = {"X-Confirmation-Token": intent.json()["confirmation_token"]}
    original_put = env.store.idempotency.put
    interrupted = False

    def interrupt_completed_record(record):
        nonlocal interrupted
        if record.result.get("status") == "completed" and not interrupted:
            interrupted = True
            raise RuntimeError("injected completion-record failure")
        return original_put(record)

    monkeypatch.setattr(env.store.idempotency, "put", interrupt_completed_record)
    with pytest.raises(RuntimeError, match="injected completion-record failure"):
        client.post("/api/settings", json=body, headers=headers)
    assert interrupted
    assert secret in stored_keys.values()
    assert len(writes) == 1
    recovered = client.post("/api/settings", json=body, headers=headers)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["keys"]["openalex_key"] is True
    replay = client.post("/api/settings", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == recovered.json()
    assert len(writes) == 1, "recovery must not repeat an already-persisted keyring write"
    for file in env.settings.rc_home.rglob("*"):
        if file.is_file():
            assert secret.encode() not in file.read_bytes(), f"secret persisted in {file.name}"


def test_keyring_read_failure_returns_safe_warning(authority_browser, monkeypatch, caplog):
    client, _, _ = authority_browser
    secret = "keyring-read-error-test-secret"

    def reject_read(service, name):
        raise RuntimeError(f"backend read failed near {secret}")

    monkeypatch.setattr("keyring.get_password", reject_read)
    response = client.get("/api/settings")
    assert response.status_code == 200, response.text
    assert response.json()["warnings"], "unavailable keyring must remain visible without backend details"
    assert secret not in response.text
    assert secret not in caplog.text
