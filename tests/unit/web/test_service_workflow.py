"""Exercise real bootstrap/service persistence through the in-process HTTP adapter."""

import hashlib
import time

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient

from refcollector import bootstrap
from refcollector.web.app import create_app


def test_text_extract_export_and_download_share_real_service(settings):
    app = create_app(settings=settings)
    base = "http://127.0.0.1:8000"  # ASGI transport only; no socket or server starts.
    with TestClient(app, base_url=base) as client:
        session = client.get("/api/session")
        assert session.status_code == 200
        client.headers.update({"Origin": base, "X-CSRF-Token": session.json()["csrf_token"]})

        def post(path, body):
            response = client.post(path, json=body)
            assert response.status_code == 200, response.text
            return response.json()

        def get(path):
            response = client.get(path)
            assert response.status_code == 200, response.text
            return response.json()

        def wait_job(project_id, job_id):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = get(f"/api/projects/{project_id}/jobs/{job_id}")
                if result["state"] in {"completed", "completed_with_warnings", "failed", "cancelled"}:
                    assert result["state"] in {"completed", "completed_with_warnings"}, result
                    return result
                time.sleep(0.02)
            raise AssertionError("local extraction/export job did not finish")

        project = post("/api/projects", {"name": "HTTP workflow", "idempotency_key": "workflow-project"})
        pid = project["project_id"]
        source = post(
            f"/api/projects/{pid}/sources/import",
            {
                "project_id": pid,
                "input": {"type": "text", "text": "[1] Smith, J. (2020). A test paper. Journal 10, 1-5."},
                "idempotency_key": "workflow-source",
            },
        )
        job = post(
            f"/api/projects/{pid}/jobs",
            {
                "project_id": pid,
                "stages": ["extract"],
                "scope": {"source_ids": [source["source_id"]]},
                "idempotency_key": "workflow-extract",
            },
        )
        wait_job(pid, job["job_id"])
        references = get(f"/api/projects/{pid}/references")
        assert references["total"] == 1
        detail = get(f"/api/projects/{pid}")
        for dimension in ("extraction", "identity", "discovery", "classification", "local_copy", "retrieval"):
            assert sum(detail["counts"][f"by_{dimension}"].values()) == 1

        export = post(
            f"/api/projects/{pid}/exports",
            {
                "project_id": pid,
                "project_revision": detail["revision"],
                "formats": ["json"],
                "selection": "complete_audit",
                "idempotency_key": "workflow-export",
            },
        )
        wait_job(pid, export["job_id"])
        artifacts = get(f"/api/projects/{pid}/artifacts")["artifacts"]
        assert artifacts
        for artifact in artifacts:
            response = client.get(f"/api/projects/{pid}/files/artifact/{artifact['artifact_id']}")
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
            assert response.headers["content-disposition"].startswith("attachment;")

    # A second client sees persisted data and can acquire the released executor lock.
    handle = bootstrap.open_app(settings)
    try:
        assert handle.start_executor()
    finally:
        handle.close()
