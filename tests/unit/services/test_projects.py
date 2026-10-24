"""Capabilities, projects, idempotency, cursors, scopes (spec 11.2, 11.3; AT-12a)."""

from __future__ import annotations

import pytest

from refcollector.config import IDENTITY_POLICY_VERSION
from refcollector.contracts.api import (
    CreateProjectIn,
    GetCapabilitiesIn,
    GetProjectIn,
    ListProjectsIn,
    ListReferencesIn,
    UpdateProjectSettingsIn,
)
from refcollector.domain.enums import (
    AuditEventType,
    IdentityState,
    NetworkMode,
    PolicyProfile,
    RetrievalState,
    Scope,
    VersionPolicy,
)
from refcollector.errors import (
    IdempotencyConflict,
    InvalidInput,
    NotAuthorized,
    NotFound,
)
from refcollector.services import parse_request
from tests.unit.services.conftest import key


def create(env, name="proj", k=None, **kw):
    return env.svc.create_project(env.ctx, CreateProjectIn(name=name, idempotency_key=k or key(), **kw))


def test_capabilities_p0_only(env):
    out = env.svc.get_capabilities(env.ctx, GetCapabilitiesIn())
    assert out.release == "P0"
    assert [s.value for s in out.source_types] == ["pdf", "text"]
    assert [s.value for s in out.stages] == ["extract", "resolve", "discover_access"]
    assert out.adapters == {"builtin": True, "grobid": False, "ocr": False}
    assert out.local_copies == ["managed", "attached"]
    assert out.approval_surfaces == ["cli_tty"]
    assert "ris" not in [f.value for f in out.export_formats]
    assert {p.name for p in out.providers} >= {"crossref", "unpaywall"}
    assert all(p.enabled and p.configured for p in out.providers)  # email is set
    assert out.config["email_set"] is True
    assert set(out.config["keys"]) == {"openalex_key", "s2_key"}
    assert set(out.scopes_granted) == set(Scope)
    assert out.limits["list_max"] == 100
    assert out.meta.request_id == "req_test"


def test_capabilities_tolerates_missing_keyring(env, monkeypatch):
    def boom():
        raise RuntimeError("no backend")

    monkeypatch.setattr("refcollector.services.service.secrets_present", boom)
    out = env.svc.get_capabilities(env.ctx, GetCapabilitiesIn())
    assert out.config["keys"] == {"openalex_key": False, "s2_key": False}


def test_create_project_defaults_and_audit(env):
    out = create(env, policy_profile=PolicyProfile.STRICT)
    project = env.store.projects.get(out.project_id)
    assert project.network_mode == NetworkMode.METADATA  # D-12
    assert project.policy_profile == PolicyProfile.STRICT
    assert project.download_enabled is False
    assert out.revision == project.revision == 1
    events = env.store.audit.list(out.project_id)
    assert [e.type for e in events] == [AuditEventType.PROJECT_CREATED]
    assert events[0].provenance.principal == "tester"
    assert events[0].provenance.origin.value == "agent"
    assert out.meta.project_revision == 1


def test_idempotent_repeat_returns_stored_result(env):  # AT-12a
    k = key()
    first = create(env, k=k)
    second = create(env, k=k)
    assert second == first
    assert len(env.store.projects.list(limit=10).items) == 1
    assert len(env.store.audit.list(first.project_id)) == 1


def test_idempotency_conflict_on_different_request(env):
    k = key()
    create(env, name="one", k=k)
    with pytest.raises(IdempotencyConflict):
        create(env, name="two", k=k)


def test_idempotency_expires(env):
    k = key()
    first = create(env, k=k)
    env.clock.advance(seconds=env.settings.limits.idempotency_ttl_s + 1)
    again = create(env, k=k)
    assert again.project_id != first.project_id  # treated as new after 7 days


def test_idempotency_scoped_by_principal_and_tool(env):
    k = key()
    first = create(env, k=k)
    other = env.ctx.__class__(
        principal="someone", channel=env.ctx.channel, scopes=env.ctx.scopes, request_id="r"
    )
    second = env.svc.create_project(other, CreateProjectIn(name="proj", idempotency_key=k))
    assert second.project_id != first.project_id


def test_scope_denied(env):
    ctx = env.ctx_for(Scope.PROJECTS_READ)
    with pytest.raises(NotAuthorized):
        env.svc.create_project(ctx, CreateProjectIn(name="x", idempotency_key=key()))
    ctx = env.ctx_for()
    with pytest.raises(NotAuthorized):
        env.svc.list_projects(ctx, ListProjectsIn())
    # get_capabilities needs no scope
    env.svc.get_capabilities(ctx, GetCapabilitiesIn())


def test_list_and_get_project_counts(env):
    pid = env.project("counted")
    sid = env.text_source(pid)
    env.occ(pid, sid)
    env.occ(pid, sid)
    listed = env.svc.list_projects(env.ctx, ListProjectsIn())
    item = next(p for p in listed.projects if p.project_id == pid)
    assert item.counts["occurrences"] == 2 and item.counts["sources"] == 1
    got = env.svc.get_project(env.ctx, GetProjectIn(project_id=pid))
    assert got.counts["occurrences"] == 2
    assert got.counts["by_identity"] == {
        state.value: 2 if state is IdentityState.UNPROCESSED else 0 for state in IdentityState
    }
    assert got.counts["by_access"] == {"not_checked": 2}
    assert got.counts["by_retrieval"] == {
        state.value: 2 if state is RetrievalState.NOT_REQUESTED else 0 for state in RetrievalState
    }
    assert got.sources_count == 1
    assert got.policy["identity_policy_version"] == IDENTITY_POLICY_VERSION
    assert got.network_mode == "metadata"
    assert got.meta.project_revision == got.revision


def test_get_project_not_found(env):
    with pytest.raises(NotFound):
        env.svc.get_project(env.ctx, GetProjectIn(project_id="prj_missing"))


def test_cursor_paging_and_expiry(env):
    ids = [create(env, name=f"p{i}").project_id for i in range(5)]
    seen = []
    cursor = None
    while True:
        page = env.svc.list_projects(env.ctx, ListProjectsIn(limit=2, cursor=cursor))
        seen += [p.project_id for p in page.projects]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == sorted(ids)

    first = env.svc.list_projects(env.ctx, ListProjectsIn(limit=2))
    env.clock.advance(hours=24, seconds=1)
    with pytest.raises(InvalidInput) as err:
        env.svc.list_projects(env.ctx, ListProjectsIn(limit=2, cursor=first.next_cursor))
    assert "expired" in err.value.message


def test_cursor_garbage_and_wrong_query_rejected(env):
    pid = env.project()
    with pytest.raises(InvalidInput):
        env.svc.list_projects(env.ctx, ListProjectsIn(cursor="not-a-cursor"))
    create(env, name="more")
    cursor = env.svc.list_projects(env.ctx, ListProjectsIn(limit=1)).next_cursor
    assert cursor
    with pytest.raises(InvalidInput):  # a project-list cursor is not valid for list_references
        env.svc.list_references(env.ctx, ListReferencesIn(project_id=pid, cursor=cursor))


def test_update_project_settings_is_cli_only(env):
    pid = env.project()
    req = UpdateProjectSettingsIn(
        project_id=pid,
        network_mode="retrieval",
        download_enabled=True,
        version_policy=VersionPolicy.ANY_ELIGIBLE,
        idempotency_key=key(),
    )
    with pytest.raises(NotAuthorized):  # MCP channel must not raise its own permissions (D-23)
        env.svc.update_project_settings(env.ctx, req)
    out = env.svc.update_project_settings(env.cli, req)
    assert out.network_mode == "retrieval"
    project = env.store.projects.get(pid)
    assert project.download_enabled and project.version_policy == VersionPolicy.ANY_ELIGIBLE
    assert env.store.audit.list(pid)[-1].type == AuditEventType.PROJECT_SETTINGS_CHANGED
    with pytest.raises(InvalidInput):  # nothing to change
        env.svc.update_project_settings(
            env.cli, UpdateProjectSettingsIn(project_id=pid, idempotency_key=key())
        )


def test_forged_provenance_fields_are_invalid_input():  # AT-33
    payload = {
        "project_id": "prj_x",
        "change": {"type": "exclude", "occurrence_id": "occ_x", "expected_revision": 1},
        "idempotency_key": "idem-key-xxxxxxxx",
    }
    from refcollector.contracts.api import UpdateReferenceIn

    assert parse_request(UpdateReferenceIn, payload)
    for forged in ({"origin": "interactive"}, {"channel": "cli_tty_review"}, {"approval": {"id": "x"}}):
        with pytest.raises(InvalidInput):
            parse_request(UpdateReferenceIn, {**payload, **forged})
    nested = {**payload, "change": {**payload["change"], "origin": "interactive"}}
    with pytest.raises(InvalidInput):
        parse_request(UpdateReferenceIn, nested)


def test_p1_value_is_feature_not_available_not_invalid_input():  # AT-41
    from refcollector.contracts.api import ImportSourceIn
    from refcollector.errors import FeatureNotAvailable

    with pytest.raises(FeatureNotAvailable):
        parse_request(
            ImportSourceIn,
            {
                "project_id": "p",
                "input": {"type": "doi", "value": "10.1/x"},
                "idempotency_key": "idem-key-xxxxxxxx",
            },
        )
