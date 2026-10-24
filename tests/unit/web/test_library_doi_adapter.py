"""D-48 adapter regressions against the frozen service interface."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

from refcollector.contracts import api
from refcollector.domain.enums import IdentityState, RetrievalState
from refcollector.web.library_api import FileReviewIn
from refcollector.web.library_routes import _build_citation
from tests.unit.web.test_library_route_fixes import client
from tests.unit.web.test_library_route_fixes import route_env as route_env


def detail(state="verified"):
    occurrence = SimpleNamespace(
        occurrence_id="occ_1",
        source_id="src_1",
        revision=1,
        order=1,
        raw_text="Citation 10.1234/example",
        edited_text=None,
        work_id=None,
        extraction="extracted",
        retrieval="not_requested",
        identity=api.IdentityView(state=IdentityState.NEEDS_REVIEW, reasons=["provider_disagreement"]),
        citation_doi="10.1234/example",
        citation_file_id="cf_1",
        citation_file_state=state,
    )
    return SimpleNamespace(
        occurrence=occurrence,
        work=None,
        files=[],
        local_copies=[],
        citation_files=[{"citation_file_id": "cf_1", "target_doi": "10.1234/example", "state": state}],
    )


def test_passing_doi_file_keeps_metadata_warning():
    citation = _build_citation(detail(), False, None)
    assert citation.status == "downloaded"
    assert citation.downloaded_paper_id == "cf_1"
    assert citation.metadata_review_pending is True
    assert citation.doi == "10.1234/example"
    assert citation.reasons == ["provider_disagreement"]


def test_pending_doi_file_uses_file_check():
    citation = _build_citation(detail("needs_review"), False, None)
    assert citation.status == "check"
    assert citation.check_kind == "file"
    assert citation.metadata_review_pending is True


def test_file_review_preserves_citation_kind():
    decision = FileReviewIn(
        work_file_id="cf_1",
        file_kind="citation_file",
        action="approve",
        expected_revision=1,
        idempotency_key="review",
    )
    assert decision.file_kind == "citation_file"


@pytest.mark.anyio
async def test_fallback_selects_unresolved_cited_doi_without_metadata(route_env):
    env = route_env
    env.svc.responses["get_settings"].pdf_fallback_url = "https://fallback.example/{doi}"
    env.refs = [
        env.refs[0].model_copy(
            update={
                "identity": api.IdentityView(state=IdentityState.UNRESOLVED),
                "citation_doi": "10.1234/example",
            }
        )
    ]
    row = env.refs[0].model_dump(mode="json")
    row["citation_doi"] = "10.1234/example"
    env.svc.responses["list_references"] = api.ListReferencesOut(occurrences=[row], next_cursor=None, total=1)
    async with client(env) as http:
        response = await http.post(
            "/api/library/collections/prj_01/download",
            json={"use_fallback": True, "idempotency_key": "doi-download"},
        )
    assert response.status_code == 200
    assert response.json()["job_id"] == "job_new"
    req = next(req for name, _, req in env.svc.calls if name == "download_files")
    assert req.retrieval_basis == "citation_doi"
    assert req.version_policy.value == "any_eligible"
    assert req.scope.occurrence_ids == ["occ_01"]


@pytest.fixture
def doi_env(route_env, tmp_path):
    env = route_env
    env.svc.responses["get_settings"].pdf_fallback_url = "https://fallback.example/{doi}"
    env.refs = [
        env.refs[0].model_copy(
            update={
                "identity": api.IdentityView(
                    state=IdentityState.NEEDS_REVIEW, reasons=["provider_disagreement"]
                ),
                "work_id": None,
                "citation_doi": "10.1234/example",
                "citation_file_id": "cf_1",
                "citation_file_state": RetrievalState.VERIFIED,
            }
        )
    ]
    env.citation_file = {
        "citation_file_id": "cf_1",
        "target_doi": "10.1234/example",
        "state": "verified",
        "sha256": "",
        "bytes": 14,
        "export_name": "Cited.pdf",
    }
    data = b"%PDF-1.4 test"
    env.citation_file["sha256"] = hashlib.sha256(data).hexdigest()
    path = tmp_path / "engine.pdf"
    path.write_bytes(data)
    env.data = data
    env.svc.responses["project_file_path"] = lambda ctx, req: path

    def reference(ctx, req):
        return api.GetReferenceOut(
            occurrence=env.refs[0],
            work=None,
            candidates=[],
            files=env.files,
            local_copies=[],
            citation_files=[env.citation_file] if "citation_files" in (req.sections or []) else [],
        )

    env.svc.responses["get_reference"] = reference
    env.svc.responses["read_project_file"] = api.ReadProjectFileOut(
        descriptor=api.ProjectFileDescriptor(
            resource_id="cf_1",
            kind="citation_file",
            name="Cited.pdf",
            media_type="application/pdf",
            bytes=len(data),
            sha256=env.citation_file["sha256"],
        ),
        data=data,
        next_offset=len(data),
        eof=True,
    )
    return env


@pytest.mark.anyio
async def test_verified_doi_shelf_folder_detail_and_viewer(doi_env):
    env = doi_env
    async with client(env) as http:
        shelf = await http.get("/api/library/collections/prj_01/shelf")
        assert shelf.status_code == 200
        downloaded = [p for p in shelf.json()["papers"] if p["kind"] == "downloaded"]
        assert len(downloaded) == 1
        card = downloaded[0]
        assert card["paper_id"] == "cf_1"
        assert card["doi"] == "10.1234/example"
        assert card["cited_by"] == ["src_01"]
        assert await anyio.to_thread.run_sync(Path(card["folder_path"]).read_bytes) == env.data
        source = (await http.get("/api/library/collections/prj_01/papers/src_01")).json()
        citation = source["citations"][0]
        assert citation["status"] == "downloaded"
        assert citation["metadata_review_pending"] is True
        assert citation["can_download_by_doi"] is True
        assert citation["can_download"] is False
        assert citation["downloaded_paper_id"] == "cf_1"
        detail_response = await http.get("/api/library/collections/prj_01/papers/cf_1")
        assert detail_response.status_code == 200
        assert detail_response.json()["paper"]["file_url"].endswith("/files/citation_file/cf_1")
        pdf = await http.get(card["file_url"])
        assert pdf.content == env.data
        assert pdf.headers["content-disposition"].startswith("inline;")
    assert not any("review" in name for name, _, _ in env.svc.calls)
    reads = [req for name, _, req in env.svc.calls if name == "read_project_file"]
    assert reads[0].kind == "citation_file"
    assert reads[0].resource_id == "cf_1"


@pytest.mark.anyio
async def test_pending_doi_requires_one_file_confirmation_and_never_metadata(doi_env):
    env = doi_env
    env.refs[0] = env.refs[0].model_copy(update={"citation_file_state": RetrievalState.NEEDS_REVIEW})
    env.citation_file.update(state="needs_review", reasons=["identity_missing"])
    env.svc.responses["prepare_web_file_review"] = api.WebIntentOut(
        purpose="file_review",
        confirmation_token="file-token",
        expires_at="2026-10-23T23:00:00Z",
        summary={"file": {}},
    )

    def confirm(ctx, args):
        req, token = args
        assert token == "file-token"
        env.citation_file["state"] = "verified"
        env.refs[0] = env.refs[0].model_copy(update={"citation_file_state": RetrievalState.VERIFIED})
        return SimpleNamespace()

    env.svc.responses["confirm_web_file_review"] = confirm
    async with client(env) as http:
        citation = (await http.get("/api/library/collections/prj_01/citations/occ_01")).json()
        assert citation["citation"]["check_kind"] == "file"
        assert citation["pending_file"]["file_kind"] == "citation_file"
        assert citation["pending_file"]["work_file_id"] == "cf_1"
        assert citation["pending_file"]["failed_checks"] == ["identity_missing"]
        await http.post("/api/library/collections/prj_01/sync")
        assert env.folders.get("prj_01").files == {}
        body = {
            "work_file_id": "cf_1",
            "file_kind": "citation_file",
            "action": "approve",
            "expected_revision": 1,
            "idempotency_key": "file-approve",
        }
        base = "/api/library/collections/prj_01/citations/occ_01/file-review"
        intent = await http.post(base + "/intent", json=body)
        assert intent.status_code == 200
        denied = await http.post(base + "/confirm", json=body)
        assert denied.status_code == 403
        approved = await http.post(
            base + "/confirm",
            json=body,
            headers={"X-Confirmation-Token": intent.json()["confirmation_token"]},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "downloaded"
        assert approved.json()["metadata_review_pending"] is True
    prepared = [req for name, _, req in env.svc.calls if name == "prepare_web_file_review"]
    confirmed = [args[0] for name, _, args in env.svc.calls if name == "confirm_web_file_review"]
    assert len(prepared) == len(confirmed) == 1
    assert prepared[0] == confirmed[0]
    assert prepared[0].file_kind == "citation_file"
    assert not any(name in ("prepare_web_review", "confirm_web_review") for name, _, _ in env.svc.calls)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text,extraction",
    [
        ("No cited DOI", "extracted"),
        ("10.1234/first and 10.1234/second", "extracted"),
        ("10.1234/example", "excluded"),
    ],
)
async def test_fallback_does_not_substitute_metadata_doi(route_env, text, extraction):
    env = route_env
    env.svc.responses["get_settings"].pdf_fallback_url = "https://fallback.example/{doi}"
    # Core unique_doi emits None for both no DOI and multiple distinct DOIs.
    row = env.refs[0].model_dump(mode="json")
    from refcollector.identity.identifiers import unique_doi

    row.update(citation_doi=unique_doi(text), extraction=extraction, raw_text=text)
    env.svc.responses["list_references"] = api.ListReferencesOut(occurrences=[row], next_cursor=None, total=1)
    async with client(env) as http:
        response = await http.post(
            "/api/library/collections/prj_01/download",
            json={"use_fallback": True, "idempotency_key": "no-substitute"},
        )
    assert response.status_code == 200
    assert response.json()["job_id"] is None
    assert not any(name == "download_files" for name, _, _ in env.svc.calls)


@pytest.mark.anyio
async def test_retrieve_doi_job_recovers_all_source_ids(route_env):
    env = route_env
    env.job.stages = [api.StageCounts(name="retrieve_doi")]
    async with client(env) as http:
        response = await http.get("/api/library/collections/prj_01/jobs/job_1")
    assert response.status_code == 200
    assert response.json()["kind"] == "download"
    assert response.json()["paper_ids"] == [f"src_{i:02}" for i in range(1, 27)]


@pytest.mark.anyio
async def test_retry_sync_exports_verified_citation_file(doi_env):
    env = doi_env
    async with client(env) as http:
        response = await http.post("/api/library/collections/prj_01/sync")
    assert response.status_code == 200
    assert env.citation_file["sha256"] in env.folders.get("prj_01").files
    paths = [req for name, _, req in env.svc.calls if name == "project_file_path"]
    assert ("prj_01", "citation_file", "cf_1") in paths


@pytest.mark.anyio
async def test_collection_counts_verified_doi_paper(doi_env):
    env = doi_env
    async with client(env) as http:
        collection = (await http.post("/api/library/collections/prj_01/sync")).json()
    assert collection["paper_count"] == len(env.sources) + 1
    assert collection["check_count"] == 0


@pytest.mark.anyio
async def test_shelf_deduplicates_same_hash_after_metadata_acceptance(doi_env):
    env = doi_env
    env.refs[0] = env.refs[0].model_copy(
        update={
            "identity": api.IdentityView(state=IdentityState.ACCEPTED_USER),
            "retrieval": RetrievalState.VERIFIED,
            "work_id": "work_1",
        }
    )
    env.files = [{**env.citation_file, "work_file_id": "wf_1"}]
    env.files[0].pop("citation_file_id")
    async with client(env) as http:
        shelf = (await http.get("/api/library/collections/prj_01/shelf")).json()
        citation = (await http.get("/api/library/collections/prj_01/citations/occ_01")).json()["citation"]
    assert len([p for p in shelf["papers"] if p["kind"] == "downloaded"]) == 1
    assert citation["metadata_review_pending"] is False
    assert citation["status"] == "downloaded"
    assert citation["downloaded_paper_id"] == "cf_1"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "identity", [IdentityState.UNRESOLVED, IdentityState.NEEDS_REVIEW, IdentityState.UNPROCESSED]
)
@pytest.mark.parametrize("scope", ["citation_id", "paper_id", "collection"])
async def test_doi_action_scope_without_metadata_gates(doi_env, identity, scope):
    env = doi_env
    env.refs[0] = env.refs[0].model_copy(update={"identity": api.IdentityView(state=identity)})
    body = {"use_fallback": True, "idempotency_key": "scope-doi-download"}
    if scope != "collection":
        body[scope] = "occ_01" if scope == "citation_id" else "src_01"
    async with client(env) as http:
        response = await http.post("/api/library/collections/prj_01/download", json=body)
    assert response.status_code == 200
    assert response.json()["job_id"] == "job_new"
    req = next(req for name, _, req in env.svc.calls if name == "download_files")
    assert req.retrieval_basis == "citation_doi"
    assert req.scope.occurrence_ids == ["occ_01"]
    assert req.version_policy.value == "any_eligible"
    assert not any("review" in name for name, _, _ in env.svc.calls)


@pytest.mark.anyio
async def test_doi_scope_keeps_all_reference_pages(route_env):
    env = route_env
    env.svc.responses["get_settings"].pdf_fallback_url = "https://fallback.example/{doi}"
    env.refs = [
        r.model_copy(
            update={
                "citation_doi": "10.1234/example",
                "identity": api.IdentityView(state=IdentityState.NEEDS_REVIEW),
            }
        )
        for r in env.refs
    ]
    async with client(env) as http:
        response = await http.post(
            "/api/library/collections/prj_01/download",
            json={"use_fallback": True, "idempotency_key": "paged-doi-download"},
        )
    assert response.json()["job_id"] == "job_new"
    req = next(req for name, _, req in env.svc.calls if name == "download_files")
    assert req.scope.occurrence_ids == [f"occ_{i:02}" for i in range(1, 27)]


@pytest.mark.anyio
async def test_failed_doi_file_has_no_file_review_or_export(doi_env):
    env = doi_env
    env.refs[0] = env.refs[0].model_copy(update={"citation_file_state": RetrievalState.FAILED})
    env.citation_file.update(state="failed", reasons=["invalid_pdf"])
    async with client(env) as http:
        citation = (await http.get("/api/library/collections/prj_01/citations/occ_01")).json()
        shelf = (await http.get("/api/library/collections/prj_01/shelf")).json()
        await http.post("/api/library/collections/prj_01/sync")
    assert citation["pending_file"] is None
    assert citation["citation"]["check_kind"] != "file"
    assert not any(p["kind"] == "downloaded" for p in shelf["papers"])
    assert env.folders.get("prj_01").files == {}


@pytest.mark.anyio
@pytest.mark.parametrize("problem", ["endpoint", "folder", "network", "downloads"])
async def test_doi_download_keeps_permission_gates(doi_env, problem):
    env = doi_env
    settings = env.svc.responses["get_settings"]
    if problem == "endpoint":
        settings.pdf_fallback_url = None
    elif problem == "folder":
        settings.allow_folders = []
    elif problem == "network":
        original = env.svc.responses["get_project"]

        def project(ctx, req):
            result = original(ctx, req)
            result.network_mode = "offline"
            return result

        env.svc.responses["get_project"] = project
    else:
        settings.download_enabled_global = False
        original = env.svc.responses["get_project"]

        def project(ctx, req):
            result = original(ctx, req)
            result.download_enabled = False
            return result

        env.svc.responses["get_project"] = project
    async with client(env) as http:
        response = await http.post(
            "/api/library/collections/prj_01/download",
            json={"use_fallback": True, "idempotency_key": "permission-doi"},
        )
        citation = (await http.get("/api/library/collections/prj_01/citations/occ_01")).json()["citation"]
    assert response.json()["job_id"] is None
    assert response.json()["disabled_reason"]
    assert citation["can_download_by_doi"] is (problem != "endpoint")
    assert not any(name == "download_files" for name, _, _ in env.svc.calls)
