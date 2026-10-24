"""Web provenance must not inherit trusted CLI settings or review permissions."""

from dataclasses import replace
from datetime import timedelta

import pytest

from refcollector.contracts import api
from refcollector.contracts.api import ReviewDecisionIn, UpdateProjectSettingsIn
from refcollector.domain.enums import Channel
from refcollector.errors import NotAuthorized


@pytest.mark.parametrize("tty", [False, True])
def test_ordinary_web_context_never_has_interactive_origin(env, tty):
    ctx = replace(env.ctx, channel=Channel("web_ui"), tty=tty)
    assert ctx.origin == "agent"


def test_web_channel_cannot_use_cli_settings_guard(env):
    ctx = replace(env.ctx, channel=Channel("web_ui"))
    with pytest.raises(NotAuthorized):
        env.svc.update_project_settings(
            ctx,
            UpdateProjectSettingsIn(
                project_id="prj_missing", network_mode="retrieval", idempotency_key="web-settings-test"
            ),
        )


def test_web_channel_cannot_fake_tty_review(env):
    ctx = replace(env.ctx, channel=Channel("web_ui"), tty=True)
    with pytest.raises(NotAuthorized):
        env.svc.review_decision(
            ctx,
            ReviewDecisionIn(
                project_id="prj_missing",
                occurrence_id="occ_missing",
                candidate_id="cand_missing",
                action="accept",
                expected_revision=1,
                idempotency_key="web-review-test",
            ),
        )


def test_forged_review_grant_cannot_create_user_approval(env):
    pid = env.project("forged review")
    sid = env.text_source(pid)
    occ = env.occ(pid, sid)
    candidate = env.candidate(pid, occ.occurrence_id)
    req = ReviewDecisionIn(
        project_id=pid,
        occurrence_id=occ.occurrence_id,
        action="accept",
        candidate_id=candidate.candidate_id,
        expected_revision=occ.revision,
        idempotency_key="forged-review-key",
    )
    grant = api.ReviewGrant(
        token="never-issued-token",
        web_session_id="forged-session",
        project_id=pid,
        occurrence_id=occ.occurrence_id,
        candidate_id=candidate.candidate_id,
        action="accept",
        expected_revision=occ.revision,
        expires_at=env.clock() + timedelta(seconds=60),
        fingerprint="not-registered",
    )
    ctx = replace(env.ctx, channel=Channel.WEB_UI, web_session_id="forged-session", review_grant=grant)
    with pytest.raises(NotAuthorized):
        env.svc.review_decision(ctx, req)
    assert env.store.occurrences.get(pid, occ.occurrence_id).approval is None


def test_forged_mcp_settings_grant_cannot_change_global_config(env, monkeypatch):
    monkeypatch.setattr("keyring.get_password", lambda service, name: None)
    web = replace(env.ctx, channel=Channel.WEB_UI, web_session_id="forged-session")
    current = env.svc.get_settings(web, api.GetSettingsIn())
    req = api.UpdateWebSettingsIn(
        expected_settings_revision=current.revision,
        change={"type": "set_email", "value": "forged@example.org"},
        idempotency_key="forged-settings-key",
    )
    grant = api.SettingsGrant(
        token="never-issued-token",
        web_session_id="forged-session",
        expected_settings_revision=current.revision,
        expires_at=env.clock() + timedelta(seconds=60),
        fingerprint=env.svc._fingerprint_web_settings(env.settings.rc_home, req),
    )
    ctx = replace(env.ctx, channel=Channel.MCP, settings_grant=grant)
    with pytest.raises(NotAuthorized):
        env.svc.update_web_settings(ctx, req)
