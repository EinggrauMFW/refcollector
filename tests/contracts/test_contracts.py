"""Checks that the frozen contracts (v1) hold together. Orchestrator-owned."""

from __future__ import annotations

import inspect
from datetime import timedelta

import pymupdf
import pytest
from pydantic import ValidationError

from refcollector import errors
from refcollector.config import DEFAULT_MCP_SCOPES, Settings, load_settings, save_settings
from refcollector.contracts import api
from refcollector.contracts.ports import OccurrenceQuery, network_allows
from refcollector.domain import enums
from refcollector.domain.models import Approval, Occurrence, Project, utcnow
from refcollector.domain.rules import approval_is_stale
from refcollector.ids import kind_of, new_id, ulid
from tests.fakes.service import FakeService
from tests.fixtures import pdfs

SPEC_TOOLS = [
    "get_capabilities", "list_projects", "create_project", "get_project", "import_source",
    "list_sources", "get_source", "set_source_sections", "start_job", "get_job", "control_job",
    "list_references", "get_reference", "add_reference", "update_reference", "resolve_citation",
    "attach_file", "download_files", "create_export", "draft_requests", "list_artifacts", "read_artifact",
]  # fmt: skip


# ---------- errors ----------


def test_every_error_code_has_a_class_and_payload():
    assert set(errors.ERRORS) == set(errors.ErrorCode)
    for code, cls in errors.ERRORS.items():
        payload = cls("boom", details={"x": 1}).to_payload()
        assert payload["code"] == code.value
        assert set(payload) == {"code", "message", "retryable", "recovery_hint", "details"}
        assert payload["recovery_hint"]


def test_retryable_flags():
    assert errors.ProviderUnavailable().retryable and errors.RateLimited().retryable
    assert errors.TransientNetworkError().code == errors.ErrorCode.PROVIDER_UNAVAILABLE
    assert errors.BlockedAddress().code == errors.ErrorCode.NOT_AUTHORIZED
    assert not errors.FileValidationFailed().retryable


# ---------- ids ----------


def test_ids_are_prefixed_unique_and_sortable():
    a, b = ulid(1_000), ulid(2_000)
    assert len(a) == 26 and a < b
    ids = {new_id("occurrence") for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith("occ_") for i in ids)
    assert kind_of(next(iter(ids))) == "occurrence"


# ---------- db ----------


def test_migration_creates_schema_once(db):
    tables = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in (
        "project",
        "source",
        "occurrence",
        "candidate",
        "decision",
        "project_work",
        "access_check",
        "local_copy",
        "work_file",
        "file_blob",
        "file_grant",
        "artifact",
        "audit_event",
        "idempotency_record",
        "job",
        "step",
        "executor",
        "provider_record",
        "response_cache",
    ):
        assert t in tables
    assert db.migrate() == []  # already applied


def test_nested_transaction_rolls_back_as_one(db):
    now = utcnow().isoformat()
    with pytest.raises(RuntimeError), db.transaction(immediate=True):
        db.execute("INSERT INTO project VALUES ('p1','n',0,?,?,'{}')", (now, now))
        with db.transaction():
            db.execute("INSERT INTO project VALUES ('p2','n',0,?,?,'{}')", (now, now))
        raise RuntimeError("fail after inner block")
    assert db.query("SELECT id FROM project") == []


def test_cannot_upgrade_deferred_transaction(db):
    with pytest.raises(RuntimeError), db.transaction(), db.transaction(immediate=True):
        pass


# ---------- domain ----------


def test_p1_values_absent_from_p0_enums():
    assert "doi" not in {s.value for s in enums.SourceType}
    assert "contexts" not in {s.value for s in enums.Stage}
    assert "zotero_attachment" not in {s.value for s in enums.LocalCopyOrigin}
    assert "RETRACTED" not in {s.value for s in enums.ReasonCode}
    assert "web_ui" not in {s.value for s in enums.Channel}
    assert {"ris", "obsidian"}.isdisjoint({s.value for s in enums.ExportFormat})


def test_occurrence_text_prefers_edit_and_approval_staleness():
    occ = Occurrence(occurrence_id="occ_1", project_id="prj_1", source_id="src_1", order=1, raw_text="raw")
    assert occ.text == "raw"
    occ.edited_text = "edited"
    assert occ.text == "edited"
    occ.identity = enums.IdentityState.ACCEPTED_USER
    occ.revision = 3
    occ.approval = Approval(
        approval_id="apr_1", action=enums.DecisionAction.ACCEPT, candidate_id="cand_1",
        occurrence_revision=3, approved_at=utcnow(), surface=enums.ApprovalSurface.CLI_TTY,
    )  # fmt: skip
    assert not approval_is_stale(occ)
    occ.revision = 4
    assert approval_is_stale(occ)


def test_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        Project(project_id="prj_1", name="x", tenant_id="t")


def test_network_modes():
    assert not network_allows(enums.NetworkMode.OFFLINE, "metadata")
    assert network_allows(enums.NetworkMode.METADATA, "metadata")
    assert not network_allows(enums.NetworkMode.METADATA, "retrieval")
    assert network_allows(enums.NetworkMode.RETRIEVAL, "retrieval")


# ---------- config ----------


def test_settings_round_trip_and_default_scopes(rc_home):
    s = Settings(rc_home=rc_home, email="a@b.org")
    save_settings(s)
    loaded = load_settings(rc_home)
    assert loaded.email == "a@b.org"
    assert loaded.allow_folders == []  # D-12
    assert enums.Scope.FILES_DOWNLOAD not in DEFAULT_MCP_SCOPES
    assert loaded.limits.idempotency_ttl_s == 7 * 24 * 3600


# ---------- service API ----------


def test_tool_registry_matches_spec_and_service():
    assert list(api.TOOLS) == SPEC_TOOLS
    service_methods = {n for n, _ in inspect.getmembers(api.RefCollectorService, inspect.isfunction)}
    assert set(SPEC_TOOLS) <= service_methods
    for spec in api.TOOLS.values():
        spec.input.model_json_schema()
        spec.output.model_json_schema()
        has_key = "idempotency_key" in spec.input.model_fields
        assert has_key == spec.mutating, spec.name


def test_provenance_fields_in_arguments_are_rejected():  # AT-33 at schema level
    base = {"project_id": "prj_1", "idempotency_key": "key-12345678"}
    change = {
        "type": "select_candidate",
        "occurrence_id": "occ_1",
        "candidate_id": "cand_1",
        "expected_revision": 1,
    }
    api.UpdateReferenceIn.model_validate({**base, "change": change})
    for forged in ({"origin": "interactive"}, {"approval": {"x": 1}}, {"channel": "cli_tty_review"}):
        with pytest.raises(ValidationError):
            api.UpdateReferenceIn.model_validate({**base, "change": change, **forged})
        with pytest.raises(ValidationError):
            api.UpdateReferenceIn.model_validate({**base, "change": {**change, **forged}})


def test_p1_values_give_feature_not_available():  # AT-41 at schema level
    key = {"idempotency_key": "key-12345678", "project_id": "prj_1"}
    with pytest.raises(errors.FeatureNotAvailable):
        api.ImportSourceIn.model_validate({**key, "input": {"type": "doi", "doi": "10.1/x"}})
    with pytest.raises(errors.FeatureNotAvailable):
        api.StartJobIn.model_validate({**key, "stages": ["contexts"], "scope": {"source_ids": ["src_1"]}})
    with pytest.raises(errors.FeatureNotAvailable):
        api.CreateExportIn.model_validate({**key, "project_revision": 1, "formats": ["ris"]})
    with pytest.raises(ValidationError):  # unknown, non-P1 value stays INVALID_INPUT
        api.ImportSourceIn.model_validate({**key, "input": {"type": "floppy"}})
    ok = api.ImportSourceIn.model_validate({**key, "input": {"type": "text", "text": "Smith (2020). A."}})
    assert isinstance(ok.input, api.TextInput)


def test_idempotency_key_length():
    with pytest.raises(ValidationError):
        api.CreateProjectIn(name="x", idempotency_key="short")
    api.CreateProjectIn(name="x", idempotency_key="x" * 128)


def test_call_context_origin(ctx_agent, ctx_tty):
    assert ctx_agent.origin == "agent"
    assert ctx_tty.origin == "interactive"
    no_tty = api.CallContext("u", enums.Channel.CLI_TTY_REVIEW, frozenset(), "r", tty=False)
    assert no_tty.origin == "agent"


def test_fake_service_records_calls(ctx_agent):
    out = api.CreateProjectOut(project_id="prj_1", revision=1)
    fake = FakeService({"create_project": out, "get_project": errors.NotFound()})
    req = api.CreateProjectIn(name="phd", idempotency_key="key-12345678")
    assert fake.create_project(ctx_agent, req) is out
    with pytest.raises(errors.NotFound):
        fake.get_project(ctx_agent, api.GetProjectIn(project_id="prj_x"))
    assert [c[0] for c in fake.calls] == ["create_project", "get_project"]


# ---------- fake store ----------


def test_fake_store_revisions_scope_and_paging(fake_store):
    fake_store.projects.add(Project(project_id="prj_a", name="a"))
    occs = [
        Occurrence(
            occurrence_id=f"occ_{i}", project_id="prj_a", source_id="src_1", order=i, raw_text=f"ref {i}"
        )
        for i in range(5)
    ]
    fake_store.occurrences.add_many(occs)
    occ = fake_store.occurrences.get("prj_a", "occ_1")
    updated = fake_store.occurrences.update(occ, expected_revision=1)
    assert updated.revision == 2
    with pytest.raises(errors.RevisionConflict) as exc:
        fake_store.occurrences.update(occ, expected_revision=1)
    assert exc.value.details == {"current_revisions": {"occ_1": 2}}
    with pytest.raises(errors.NotFound):
        fake_store.occurrences.get("prj_b", "occ_1")
    page = fake_store.occurrences.list("prj_a", OccurrenceQuery(), limit=2)
    assert [o.occurrence_id for o in page.items] == ["occ_0", "occ_1"] and page.total == 5
    page2 = fake_store.occurrences.list("prj_a", OccurrenceQuery(), after=page.next_after, limit=10)
    assert [o.occurrence_id for o in page2.items] == ["occ_2", "occ_3", "occ_4"] and page2.next_after is None
    assert fake_store.projects.bump_revision("prj_a") == 1


def test_fake_idempotency_expiry(fake_store):
    from refcollector.domain.models import IdempotencyRecord

    now = utcnow()
    rec = IdempotencyRecord(principal="u", project_id="", tool="create_project", key="k" * 8, fingerprint="f",
                            result={}, created_at=now, expires_at=now + timedelta(days=7))  # fmt: skip
    fake_store.idempotency.put(rec)
    assert fake_store.idempotency.get("u", "", "create_project", "k" * 8, now=now) is not None
    assert fake_store.idempotency.get("u", "", "create_project", "k" * 8, now=now + timedelta(days=8)) is None


# ---------- PDF builder ----------


def _open(data: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=data, filetype="pdf")


def test_pdf_builders():
    with _open(pdfs.thesis_pdf()) as doc:
        text = "".join(p.get_text() for p in doc)
        assert "Contents" in text and "References" in text and "Babarit" in text
        assert doc[0].get_label() == "i" and doc[-1].get_label().isdigit()
    with _open(pdfs.per_chapter_pdf()) as doc:
        assert "".join(p.get_text() for p in doc).count("Daftar Pustaka") == 3
    with _open(pdfs.thesis_pdf(two_column=True)) as doc:
        assert "Vaswani" in doc[-1].get_text()
    with _open(pdfs.no_heading_pdf()) as doc:
        assert "References" not in "".join(p.get_text() for p in doc)
    with _open(pdfs.encrypted_pdf()) as doc:
        assert doc.needs_pass
    with _open(pdfs.image_only_pdf()) as doc:
        assert all(not p.get_text().strip() for p in doc)
    with _open(
        pdfs.paper_pdf("Neural wavefields", ["J. Smith"], "10.1190/geo2020-0001.1", supplement=True)
    ) as doc:
        assert "Supplementary Material" in doc[0].get_text() and "10.1190/geo2020-0001.1" in doc[0].get_text()
