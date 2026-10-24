"""W3 regressions for complete route scopes, local-copy navigation, and job recovery."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from refcollector.contracts import api
from refcollector.domain.enums import (
    DiscoveryState,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyState,
    RetrievalState,
    SourceType,
)
from refcollector.domain.models import WorkMetadata
from refcollector.errors import NotFound
from refcollector.services.library_folder import LibraryFolders
from refcollector.web.session import SessionStore
from tests.fakes.service import FakeService
from tests.unit.web.test_library_routes import _create_test_app

NOW = datetime(2026, 10, 23, tzinfo=UTC)


@pytest.fixture
def route_env(tmp_path: Path) -> Any:
    sources = [
        api.SourceListItem(
            source_id=f"src_{i:02}",
            label=f"Paper {i:02}.pdf",
            type=SourceType.PDF,
            pages=1,
            sections=1,
            extraction_state=ExtractionState.EXTRACTED,
            occurrence_count=1,
            imported_at=NOW,
        )
        for i in range(1, 27)
    ]
    refs = [
        api.OccurrenceSummary(
            occurrence_id=f"occ_{i:02}",
            source_id=f"src_{i:02}",
            revision=1,
            label=None,
            raw_text=f"Citation {i}",
            edited_text=None,
            spans=[],
            extraction=ExtractionState.EXTRACTED,
            identity=api.IdentityView(state=IdentityState.ACCEPTED_USER),
            access=api.AccessView(discovery=DiscoveryState.COMPLETE, classification=None),
            local_copy=LocalCopyState.NONE,
            retrieval=RetrievalState.NOT_REQUESTED,
            work_id="work_1",
            order=float(i),
        )
        for i in range(1, 27)
    ]
    projects = [
        api.ProjectListItem(
            project_id=f"prj_{i:02}", name=f"Collection {i}", revision=1, counts={}, updated_at=NOW
        )
        for i in range(1, 27)
    ]
    env = SimpleNamespace(sources=sources, refs=refs, projects=projects, jobs=[], files=[], copies=[])

    def page(rows: list[Any], req: Any) -> tuple[list[Any], str | None]:
        offset = int(req.cursor or 0)
        return rows[offset : offset + 25], str(offset + 25) if offset + 25 < len(rows) else None

    def list_sources(ctx: Any, req: Any) -> api.ListSourcesOut:
        rows, cursor = page(env.sources, req)
        return api.ListSourcesOut(sources=rows, next_cursor=cursor)

    def list_projects(ctx: Any, req: Any) -> api.ListProjectsOut:
        rows, cursor = page(env.projects, req)
        return api.ListProjectsOut(projects=rows, next_cursor=cursor)

    def list_refs(ctx: Any, req: Any) -> api.ListReferencesOut:
        rows = env.refs
        if req.filter and req.filter.source_id:
            rows = [r for r in rows if r.source_id == req.filter.source_id]
        if req.filter and req.filter.job_id:
            rows = [r for r in rows if r.occurrence_id in env.job_occurrences]
        selected, cursor = page(rows, req)
        return api.ListReferencesOut(
            occurrences=[r.model_dump(mode="json") for r in selected],
            next_cursor=cursor,
            total=len(rows),
        )

    def get_reference(ctx: Any, req: Any) -> api.GetReferenceOut:
        return api.GetReferenceOut(
            occurrence=next(r for r in env.refs if r.occurrence_id == req.occurrence_id),
            work=WorkMetadata(title="Shared cited work", year=2020),
            files=env.files if "files" in (req.sections or []) else [],
            local_copies=env.copies if "local_copies" in (req.sections or []) else [],
        )

    def get_source(ctx: Any, req: Any) -> Any:
        for source in env.sources:
            if source.source_id == req.source_id:
                return SimpleNamespace(source=source, preflight=None)
        raise NotFound("Source not found")

    def list_jobs(ctx: Any, req: Any) -> api.ListJobsOut:
        rows, cursor = page(env.jobs, req)
        return api.ListJobsOut(jobs=rows, next_cursor=cursor)

    env.job_occurrences = {r.occurrence_id for r in refs}
    env.job = api.GetJobOut(
        job_id="job_1",
        state=JobState.RUNNING,
        stages=[api.StageCounts(name="retrieve")],
        warnings=[],
        results=api.JobResults(
            source_ids=[],
            occurrence_count=26,
            changed_filter={"job_id": "job_1"},
            artifact_ids=[],
        ),
        next_poll_after_ms=2000,
    )
    settings = SimpleNamespace(
        rc_home=str(tmp_path / "home"),
        allow_folders=[str(tmp_path)],
        revision="settings_1",
        project=None,
        download_enabled_global=True,
    )
    svc = FakeService(
        {
            "list_projects": list_projects,
            "list_sources": list_sources,
            "list_references": list_refs,
            "get_reference": get_reference,
            "get_source": get_source,
            "get_settings": settings,
            "get_project": lambda ctx, req: SimpleNamespace(
                project_id=req.project_id,
                name="Collection",
                network_mode="retrieval",
                download_enabled=True,
                sources_count=len(env.sources),
                policy={},
            ),
            "list_jobs": list_jobs,
            "get_job": lambda ctx, req: env.job.model_copy(update={"job_id": req.job_id}),
            "start_job": api.StartJobOut(job_id="job_new", steps_planned=26, next_poll_after_ms=2000),
            "download_files": api.JobOut(job_id="job_new"),
        }
    )
    folders = LibraryFolders(tmp_path / "home", lambda: [tmp_path])
    folders.create("prj_01", tmp_path / "collection")
    sessions = SessionStore()
    session = sessions.create_session()
    app = _create_test_app(svc, sessions)
    app.state.library_folders = folders
    env.svc, env.app, env.folders = svc, app, folders
    env.cookies = {"rc_session": session.session_id}
    env.headers = {"X-CSRF-Token": session.csrf_token}
    return env


def client(env: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app),
        base_url="http://127.0.0.1:8000",
        cookies=env.cookies,
        headers=env.headers,
    )


@pytest.mark.anyio
async def test_w3_collections_include_26_projects_and_complete_check_counts(route_env: Any) -> None:
    env = route_env
    env.refs = [
        r.model_copy(update={"identity": api.IdentityView(state=IdentityState.NEEDS_REVIEW)})
        for r in env.refs
    ]
    async with client(env) as http:
        response = await http.get("/api/library/collections")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 26
    assert {r["check_count"] for r in rows} == {26}


@pytest.mark.anyio
async def test_w3_shelf_paginates_and_filters_after_all_26_sources(route_env: Any) -> None:
    async with client(route_env) as http:
        first = (await http.get("/api/library/collections/prj_01/shelf?limit=25")).json()
        assert first["total"] == 26
        second = (
            await http.get(
                "/api/library/collections/prj_01/shelf",
                params={"limit": 25, "cursor": first["next_cursor"]},
            )
        ).json()
        assert [p["paper_id"] for p in second["papers"]] == ["src_26"]
        filtered = (await http.get("/api/library/collections/prj_01/shelf?q=Paper%2026")).json()
        assert filtered["total"] == 1
        assert filtered["papers"][0]["citation_count"] == 1


@pytest.mark.anyio
async def test_w3_paper_detail_preserves_source_filter_across_reference_pages(route_env: Any) -> None:
    env = route_env
    env.refs = [r.model_copy(update={"source_id": "src_01"}) for r in env.refs]
    env.refs.append(env.refs[0].model_copy(update={"occurrence_id": "other", "source_id": "src_02"}))
    async with client(env) as http:
        response = await http.get("/api/library/collections/prj_01/papers/src_01")
    assert response.status_code == 200
    assert len(response.json()["citations"]) == 26
    assert response.json()["citations"][-1]["citation_id"] == "occ_26"


@pytest.mark.anyio
async def test_w3_find_metadata_includes_all_26_sources(route_env: Any) -> None:
    async with client(route_env) as http:
        response = await http.post(
            "/api/library/collections/prj_01/find-metadata", json={"idempotency_key": "metadata-w3-key"}
        )
    assert response.status_code == 200
    request = next(req for method, _, req in route_env.svc.calls if method == "start_job")
    assert request.scope.source_ids == [f"src_{i:02}" for i in range(1, 27)]


@pytest.mark.anyio
@pytest.mark.parametrize("paper_id", [None, "src_01"])
async def test_w3_download_includes_all_26_references(route_env: Any, paper_id: str | None) -> None:
    env = route_env
    env.refs = [r.model_copy(update={"source_id": "src_01"}) for r in env.refs]
    if paper_id:
        env.refs.append(env.refs[0].model_copy(update={"occurrence_id": "other", "source_id": "src_02"}))
    async with client(env) as http:
        response = await http.post(
            "/api/library/collections/prj_01/download",
            json={"paper_id": paper_id, "idempotency_key": "download-w3-key"},
        )
    assert response.status_code == 200
    request = next(req for method, _, req in env.svc.calls if method == "download_files")
    assert request.scope.occurrence_ids == [f"occ_{i:02}" for i in range(1, 27)]


@pytest.mark.anyio
async def test_w3_retry_sync_includes_26_sources_and_late_verified_reference(
    route_env: Any,
    tmp_path: Path,
) -> None:
    env = route_env
    paths = {}
    for i, source in enumerate(env.sources):
        path = tmp_path / f"input_{i}.pdf"
        path.write_bytes(f"source {i}".encode())
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        env.sources[i] = source.model_copy(update={"sha256": sha})
        paths[source.source_id] = path
    work = tmp_path / "work.pdf"
    work.write_bytes(b"verified work")
    sha = hashlib.sha256(work.read_bytes()).hexdigest()
    paths["wf_1"] = work
    env.refs[-1] = env.refs[-1].model_copy(update={"retrieval": RetrievalState.VERIFIED})
    env.files = [
        {"work_file_id": "wf_1", "state": "verified", "sha256": sha, "export_name": "downloaded.pdf"}
    ]
    env.svc.responses["project_file_path"] = lambda ctx, req: paths[req[2]]
    async with client(env) as http:
        response = await http.post("/api/library/collections/prj_01/sync", json={})
    assert response.status_code == 200
    destination = tmp_path / "collection"
    assert len(list(destination.glob("*.pdf"))) == 27
    assert (destination / "Paper 26.pdf").read_bytes() == b"source 25"
    assert (destination / "downloaded.pdf").read_bytes() == b"verified work"


@pytest.mark.anyio
async def test_w3_local_copy_citation_navigates_to_detail_shelf_and_stream(route_env: Any) -> None:
    env = route_env
    env.refs = [
        r.model_copy(update={"retrieval": RetrievalState.SATISFIED_BY_LOCAL_COPY}) for r in env.refs[:2]
    ]
    env.copies = [{"local_copy_id": "lc_1", "state": "usable", "sha256": "a" * 64}]
    env.svc.responses["read_project_file"] = api.ReadProjectFileOut(
        descriptor=api.ProjectFileDescriptor(
            resource_id="lc_1",
            kind="local_copy",
            name="local.pdf",
            media_type="application/pdf",
            bytes=4,
            sha256="a" * 64,
        ),
        data=b"%PDF",
        next_offset=4,
        eof=True,
    )
    async with client(env) as http:
        citation = (await http.get("/api/library/collections/prj_01/citations/occ_01")).json()["citation"]
        assert citation["status"] == "downloaded"
        detail = await http.get(f"/api/library/collections/prj_01/papers/{citation['downloaded_paper_id']}")
        assert detail.status_code == 200
        card = detail.json()["paper"]
        assert card["file_url"] == "/api/library/collections/prj_01/files/local_copy/lc_1"
        assert card["cited_by"] == ["src_01", "src_02"]
        assert (await http.get(card["file_url"])).content == b"%PDF"
        shelf = (await http.get("/api/library/collections/prj_01/shelf?kind=downloaded")).json()
        assert shelf["total"] == 1
        assert shelf["papers"][0]["cited_by"] == ["src_01", "src_02"]
        assert shelf["papers"][0]["file_url"] == card["file_url"]


@pytest.mark.anyio
@pytest.mark.parametrize("stage,kind", [("retrieve", "download"), ("resolve", "find_metadata")])
async def test_w3_occurrence_job_status_recovers_26_papers_from_real_result_shape(
    route_env: Any,
    stage: str,
    kind: str,
) -> None:
    env = route_env
    env.job = env.job.model_copy(update={"stages": [api.StageCounts(name=stage)]})
    env.refs.append(env.refs[0].model_copy(update={"occurrence_id": "other", "source_id": "outside_job"}))
    async with client(env) as http:
        response = await http.get("/api/library/collections/prj_01/jobs/job_1")
    assert response.status_code == 200
    assert response.json()["kind"] == kind
    assert response.json()["paper_ids"] == [f"src_{i:02}" for i in range(1, 27)]


@pytest.mark.anyio
async def test_w3_shelf_recovers_occurrence_job_after_reload_and_job_pagination(route_env: Any) -> None:
    env = route_env
    env.jobs = [
        api.JobListItem(
            project_id="prj_01",
            project_name="Collection",
            job_id=f"job_{i}",
            state=JobState.RUNNING,
            stages=["retrieve"],
            created_at=NOW,
            updated_at=NOW,
        )
        for i in range(1, 27)
    ]
    env.svc.responses["get_job"] = lambda ctx, req: env.job.model_copy(
        update={
            "job_id": req.job_id,
            "results": api.JobResults(
                source_ids=[] if req.job_id == "job_26" else ["src_01"], changed_filter={"job_id": req.job_id}
            ),
        }
    )
    async with client(env) as http:
        response = await http.get("/api/library/collections/prj_01/shelf")
    assert response.status_code == 200
    cards = response.json()["papers"]
    assert len(cards) == 26
    assert {p["active_job_id"] for p in cards} == {"job_26"}


@pytest.mark.anyio
async def test_w3_local_copy_does_not_duplicate_a_verified_work_file_on_shelf(
    route_env: Any,
    tmp_path: Path,
) -> None:
    env = route_env
    env.refs = [env.refs[0].model_copy(update={"retrieval": RetrievalState.VERIFIED})]
    source = tmp_path / "verified.pdf"
    source.write_bytes(b"verified PDF")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    env.svc.responses["project_file_path"] = lambda ctx, req: source
    env.files = [{"work_file_id": "wf_1", "state": "verified", "sha256": sha}]
    env.copies = [{"local_copy_id": "lc_1", "state": "usable", "sha256": sha}]
    async with client(env) as http:
        response = await http.get("/api/library/collections/prj_01/shelf?kind=downloaded")
        assert response.status_code == 200
        assert [p["paper_id"] for p in response.json()["papers"]] == ["wf_1"]
        # An existing local-copy citation link must still resolve after a work file appears.
        detail = await http.get("/api/library/collections/prj_01/papers/lc_1")
        assert detail.status_code == 200
        assert detail.json()["paper"]["file_url"].endswith("/files/local_copy/lc_1")


@pytest.mark.anyio
async def test_w3_unusable_local_copy_is_not_a_downloaded_paper(route_env: Any) -> None:
    env = route_env
    env.refs = [env.refs[0].model_copy(update={"retrieval": RetrievalState.SATISFIED_BY_LOCAL_COPY})]
    env.copies = [{"local_copy_id": "lc_bad", "state": "unusable", "sha256": "a" * 64}]
    async with client(env) as http:
        detail = await http.get("/api/library/collections/prj_01/papers/lc_bad")
        assert detail.status_code == 404
        shelf = await http.get("/api/library/collections/prj_01/shelf?kind=downloaded")
        assert shelf.json()["total"] == 0
