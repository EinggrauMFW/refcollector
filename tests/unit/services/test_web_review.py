"""Tests for trusted web review (Wave 3).

Verifies prepare_web_review, confirm_web_review, ApprovalSurface.WEB_UI, interactive provenance,
intent binding, expiration, replay deduplication, and authorization guards.
"""

from __future__ import annotations

from typing import Any

import pytest

from refcollector.contracts import api
from refcollector.contracts.api import CallContext, ReviewDecisionIn
from refcollector.domain.enums import (
    ApprovalSurface,
    Channel,
    IdentityState,
    Scope,
)
from refcollector.errors import (
    InvalidState,
    NotAuthorized,
    RevisionConflict,
)
from tests.unit.services.conftest import Clock


@pytest.fixture
def review_setup(env: Any) -> dict[str, Any]:
    svc = env.svc
    pid = env.project("test-project")
    sid = env.text_source(pid, "Author. Title. 2020.")
    occ = env.occ(pid, sid, "Author. Title. 2020.")

    cand = env.candidate(pid, occ.occurrence_id, title="Test Paper", doi="10.1234/test", rank=1)
    cand_rej = env.candidate(pid, occ.occurrence_id, title="Rejected Paper", doi=None, rank=2)
    cand_rej = cand_rej.model_copy(update={"rejected": True})
    env.store.candidates.replace_for_occurrence(pid, occ.occurrence_id, [cand, cand_rej])

    occ = env.store.occurrences.get(pid, occ.occurrence_id)
    occ.identity = IdentityState.NEEDS_REVIEW
    env.store.occurrences.update(occ)
    occ = env.store.occurrences.get(pid, occ.occurrence_id)

    return {
        "svc": svc,
        "clock": env.clock,
        "project_id": pid,
        "occurrence_id": occ.occurrence_id,
        "candidate_id": cand.candidate_id,
        "rejected_candidate_id": cand_rej.candidate_id,
        "revision": occ.revision,
    }


def web_ctx(
    session_id: str = "sess-123",
    principal: str = "test-user",
    scopes: frozenset[Scope] | None = None,
    tty: bool = False,
) -> CallContext:
    return CallContext(
        principal=principal,
        channel=Channel.WEB_UI,
        scopes=scopes if scopes is not None else frozenset(Scope),
        request_id="req-web",
        tty=tty,
        web_session_id=session_id,
    )


def test_prepare_web_review_success(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    ctx = web_ctx()
    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="accept",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        note="Approved by user",
        idempotency_key="idem-review-001",
    )

    out = svc.prepare_web_review(ctx, req)
    assert isinstance(out, api.WebIntentOut)
    assert out.purpose == "review"
    assert out.confirmation_token
    assert out.expires_at > review_setup["clock"]()
    assert out.summary["project_id"] == req.project_id
    assert out.summary["occurrence_id"] == req.occurrence_id
    assert out.summary["action"] == "accept"
    assert out.summary["candidate"]["title"] == "Test Paper"
    assert out.summary["note"] == "Approved by user"


def test_prepare_web_review_requires_web_session(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    # Without session id
    ctx_no_sess = CallContext(
        principal="test-user",
        channel=Channel.WEB_UI,
        scopes=frozenset(Scope),
        request_id="req-1",
        web_session_id=None,
    )
    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="accept",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        idempotency_key="idem-review-002",
    )
    with pytest.raises(NotAuthorized, match="active web session"):
        svc.prepare_web_review(ctx_no_sess, req)

    # Wrong channel
    ctx_cli = CallContext(
        principal="test-user",
        channel=Channel.CLI,
        scopes=frozenset(Scope),
        request_id="req-2",
        web_session_id="sess-123",
    )
    with pytest.raises(NotAuthorized, match="Channel.WEB_UI"):
        svc.prepare_web_review(ctx_cli, req)


def test_prepare_web_review_scope_and_state_guards(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    ctx = web_ctx(scopes=frozenset({Scope.PROJECTS_READ}))
    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="accept",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        idempotency_key="idem-review-003",
    )
    with pytest.raises(NotAuthorized, match="references:write"):
        svc.prepare_web_review(ctx, req)

    ctx_ok = web_ctx()
    # Stale revision
    req_stale = req.model_copy(update={"expected_revision": review_setup["revision"] + 1})
    with pytest.raises(RevisionConflict):
        svc.prepare_web_review(ctx_ok, req_stale)

    # Rejected candidate cannot be accepted
    req_rej = req.model_copy(update={"candidate_id": review_setup["rejected_candidate_id"]})
    with pytest.raises(InvalidState, match="candidate was rejected"):
        svc.prepare_web_review(ctx_ok, req_rej)


def test_confirm_web_review_records_web_ui_approval(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    ctx = web_ctx()
    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="accept",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        note="Confirmed in web UI",
        idempotency_key="idem-review-004",
    )

    intent = svc.prepare_web_review(ctx, req)
    res = svc.confirm_web_review(ctx, req, intent.confirmation_token)

    assert isinstance(res, api.UpdateReferenceOut)
    assert res.identity_state == IdentityState.ACCEPTED_USER

    # Inspect occurrence in store
    occ = svc.store.occurrences.get(req.project_id, req.occurrence_id)
    assert occ.identity == IdentityState.ACCEPTED_USER
    assert occ.approval is not None
    assert occ.approval.surface == ApprovalSurface.WEB_UI
    assert occ.approval.occurrence_revision == occ.revision

    # Inspect provenance recorded in decision
    decisions = svc.store.db.query(
        "SELECT * FROM decision WHERE occurrence_id = ? ORDER BY created_at DESC LIMIT 1",
        (req.occurrence_id,),
    )
    assert len(decisions) == 1
    import json

    data = json.loads(decisions[0]["data"])
    assert data["action"] == "accept"
    assert data["approval"]["surface"] == "web_ui"
    assert data["provenance"]["channel"] == "web_ui"
    assert data["provenance"]["origin"] == "interactive"


def test_confirm_web_review_reject_action(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    ctx = web_ctx()
    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="reject",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        note="Rejected by reviewer",
        idempotency_key="idem-review-005",
    )

    intent = svc.prepare_web_review(ctx, req)
    res = svc.confirm_web_review(ctx, req, intent.confirmation_token)

    assert res.identity_state == IdentityState.REJECTED
    occ = svc.store.occurrences.get(req.project_id, req.occurrence_id)
    assert occ.approval is None  # Reject produces decision, not approval


def test_confirm_web_review_token_validation_failures(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    ctx = web_ctx(session_id="sess-A", principal="user-A")
    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="accept",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        idempotency_key="idem-review-006",
    )

    intent = svc.prepare_web_review(ctx, req)

    # Missing token
    with pytest.raises(NotAuthorized, match="confirmation token required"):
        svc.confirm_web_review(ctx, req, "")

    # Unknown token
    with pytest.raises(NotAuthorized, match="invalid or unknown confirmation token"):
        svc.confirm_web_review(ctx, req, "unknown-token-123")

    # Wrong session
    ctx_other_sess = web_ctx(session_id="sess-B", principal="user-A")
    with pytest.raises(NotAuthorized, match="another session"):
        svc.confirm_web_review(ctx_other_sess, req, intent.confirmation_token)

    # Wrong principal
    ctx_other_user = web_ctx(session_id="sess-A", principal="user-B")
    with pytest.raises(NotAuthorized, match="another principal"):
        svc.confirm_web_review(ctx_other_user, req, intent.confirmation_token)

    # Changed action
    req_wrong_action = req.model_copy(update={"action": "reject"})
    with pytest.raises(NotAuthorized, match="does not match confirmed intent"):
        svc.confirm_web_review(ctx, req_wrong_action, intent.confirmation_token)

    # Changed idempotency key
    req_wrong_key = req.model_copy(update={"idempotency_key": "different-key-999"})
    with pytest.raises(NotAuthorized, match="does not match confirmed intent"):
        svc.confirm_web_review(ctx, req_wrong_key, intent.confirmation_token)


def test_confirm_web_review_expiration(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    clock: Clock = review_setup["clock"]
    ctx = web_ctx()
    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="accept",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        idempotency_key="idem-review-007",
    )

    intent = svc.prepare_web_review(ctx, req)
    # Advance clock past 60s TTL
    clock.advance(seconds=61)

    with pytest.raises(NotAuthorized, match="token expired"):
        svc.confirm_web_review(ctx, req, intent.confirmation_token)


def test_confirm_web_review_replay_deduplication(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    ctx = web_ctx()
    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="accept",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        idempotency_key="idem-review-008",
    )

    intent = svc.prepare_web_review(ctx, req)
    first_res = svc.confirm_web_review(ctx, req, intent.confirmation_token)

    # Replay with same token and request
    second_res = svc.confirm_web_review(ctx, req, intent.confirmation_token)
    assert first_res.changed == second_res.changed

    # Verify no second approval was created
    import json

    rows = svc.store.db.query(
        "SELECT * FROM decision WHERE occurrence_id = ?",
        (req.occurrence_id,),
    )
    approvals = [json.loads(r["data"]) for r in rows if json.loads(r["data"]).get("approval") is not None]
    assert len(approvals) == 1


def test_direct_review_decision_from_web_denied_without_grant(review_setup: dict[str, Any]) -> None:
    svc = review_setup["svc"]
    # Even if tty=True is set on CallContext, ordinary Channel.WEB_UI cannot invoke review_decision directly
    ctx = CallContext(
        principal="test-user",
        channel=Channel.WEB_UI,
        scopes=frozenset(Scope),
        request_id="req-direct",
        tty=True,
        web_session_id="sess-123",
        review_grant=None,
    )
    assert ctx.origin == "agent"  # Web UI context without grant stays agent

    req = ReviewDecisionIn(
        project_id=review_setup["project_id"],
        occurrence_id=review_setup["occurrence_id"],
        action="accept",
        candidate_id=review_setup["candidate_id"],
        expected_revision=review_setup["revision"],
        idempotency_key="idem-review-009",
    )
    with pytest.raises(NotAuthorized, match="interactive review surface"):
        svc.review_decision(ctx, req)
