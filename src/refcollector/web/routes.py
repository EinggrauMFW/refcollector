"""Typed HTTP API routes mapping directly to RefCollectorService.

Zero direct storage access; all operations call AppHandle.service through
anyio.to_thread.run_sync.
"""

from __future__ import annotations

import getpass
import math
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import quote, unquote

import anyio
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile

from refcollector.contracts import api
from refcollector.contracts.api import (
    AddReferenceIn,
    AttachFileIn,
    CallContext,
    ControlJobIn,
    CreateExportIn,
    CreateProjectIn,
    DownloadFilesIn,
    DraftRequestsIn,
    GetCapabilitiesIn,
    GetJobIn,
    GetProjectIn,
    GetReferenceIn,
    GetSourceIn,
    ImportSourceIn,
    ListArtifactsIn,
    ListProjectsIn,
    ListReferencesIn,
    ListSourcesIn,
    ReadArtifactIn,
    RefCollectorService,
    ReferenceFilter,
    RenderSourcePageIn,
    ResolveCitationIn,
    ReviewDecisionIn,
    SetSourceSectionsIn,
    StartJobIn,
    UpdateReferenceIn,
    UploadPdfIn,
)
from refcollector.domain.enums import (
    AccessClassification,
    DiscoveryState,
    ExtractionState,
    IdentityState,
    LocalCopyState,
    RetrievalState,
)
from refcollector.errors import FeatureNotAvailable, InvalidInput, NotAuthorized
from refcollector.web.dependencies import (
    get_call_context,
    get_service,
    get_session_store,
    require_csrf,
    require_session,
)
from refcollector.web.security import BodyTooLargeError
from refcollector.web.session import SessionStore

router = APIRouter(prefix="/api")


def _check_match(path_val: str, body_val: str, name: str) -> None:
    if path_val != body_val:
        raise InvalidInput(f"Path {name} '{path_val}' does not match body {name} '{body_val}'")


ALLOWED_UPLOAD_FIELDS = {
    "file",
    "purpose",
    "name",
    "declared_bytes",
    "idempotency_key",
    "occurrence_id",
    "version_hint",
}


def _sanitize_filename(name: str) -> tuple[str, str]:
    """Sanitize filename to prevent header injection and return (safe_ascii, rfc5987_encoded)."""
    # Strip directory components (Windows and POSIX)
    clean = name.replace("\\", "/").split("/")[-1].strip()
    # Strip control characters (including CR, LF, nulls)
    clean = "".join(c if (ord(c) >= 32 and ord(c) != 127) else "_" for c in clean)
    # Strip quotes
    clean = clean.replace('"', "_").replace("'", "_").strip()
    if not clean or clean in (".", ".."):
        clean = "download"

    # ASCII fallback
    ascii_chars = []
    for c in clean:
        if c.isascii() and (c.isalnum() or c in ".-_"):
            ascii_chars.append(c)
        else:
            ascii_chars.append("_")
    safe_ascii = "".join(ascii_chars).strip()
    if not safe_ascii or safe_ascii in (".", ".."):
        safe_ascii = "download"

    encoded = quote(clean, encoding="utf-8")
    return safe_ascii, encoded


# ---------- 1. Session and System ----------


@router.get("/session")
async def get_session(
    request: Request,
    response: Response,
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> dict[str, Any]:
    """Establish or reuse an opaque session and return the CSRF token."""
    session_id = request.cookies.get("rc_session")
    session = store.get_session(session_id) if session_id else None
    if session is None:
        session = store.create_session()
        response.set_cookie(
            key="rc_session",
            value=session.session_id,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=86400,
        )
    return {
        "csrf_token": session.csrf_token,
        "principal": getpass.getuser(),
    }


@router.get("/capabilities", dependencies=[Depends(require_session)])
async def get_capabilities(
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Return engine capabilities and configuration flags."""
    out = await anyio.to_thread.run_sync(svc.get_capabilities, ctx, GetCapabilitiesIn())
    return out.model_dump()


@router.get("/executor/status", dependencies=[Depends(require_session)])
async def get_executor_status(
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Authoritative lease/heartbeat status of the engine executor."""
    if not hasattr(svc, "get_executor_status"):
        raise FeatureNotAvailable("get_executor_status is not implemented on the service")

    in_cls = getattr(api, "GetExecutorStatusIn", None)
    req = in_cls() if in_cls is not None else None
    out = await anyio.to_thread.run_sync(svc.get_executor_status, ctx, req)
    return out.model_dump() if hasattr(out, "model_dump") else out


# ---------- 2. Projects ----------


@router.get("/projects", dependencies=[Depends(require_session)])
async def list_projects(
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List all projects with pagination."""
    req = ListProjectsIn(cursor=cursor, limit=limit)
    out = await anyio.to_thread.run_sync(svc.list_projects, ctx, req)
    return out.model_dump()


@router.post("/projects", dependencies=[Depends(require_csrf)])
async def create_project(
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    body: CreateProjectIn,
) -> dict[str, Any]:
    """Create a new project."""
    out = await anyio.to_thread.run_sync(svc.create_project, ctx, body)
    return out.model_dump()


@router.get("/projects/{project_id}", dependencies=[Depends(require_session)])
async def get_project(
    project_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Get project summary, policies, and counts."""
    req = GetProjectIn(project_id=project_id)
    out = await anyio.to_thread.run_sync(svc.get_project, ctx, req)
    return out.model_dump()


# ---------- 3. Sources ----------


@router.get("/projects/{project_id}/sources", dependencies=[Depends(require_session)])
async def list_sources(
    project_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List sources in a project."""
    req = ListSourcesIn(project_id=project_id, cursor=cursor, limit=limit)
    out = await anyio.to_thread.run_sync(svc.list_sources, ctx, req)
    return out.model_dump()


@router.post("/projects/{project_id}/sources/import", dependencies=[Depends(require_csrf)])
async def import_source(
    project_id: str,
    body: ImportSourceIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Import a source (text or allowed local file)."""
    _check_match(project_id, body.project_id, "project_id")
    out = await anyio.to_thread.run_sync(svc.import_source, ctx, body)
    return out.model_dump()


@router.get("/projects/{project_id}/sources/{source_id}", dependencies=[Depends(require_session)])
async def get_source(
    project_id: str,
    source_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Get source detail and sections."""
    req = GetSourceIn(project_id=project_id, source_id=source_id)
    out = await anyio.to_thread.run_sync(svc.get_source, ctx, req)
    return out.model_dump()


@router.post("/projects/{project_id}/sources/{source_id}/sections", dependencies=[Depends(require_csrf)])
async def set_source_sections(
    project_id: str,
    source_id: str,
    body: SetSourceSectionsIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Configure source section ranges."""
    _check_match(project_id, body.project_id, "project_id")
    _check_match(source_id, body.source_id, "source_id")
    out = await anyio.to_thread.run_sync(svc.set_source_sections, ctx, body)
    return out.model_dump()


@router.get(
    "/projects/{project_id}/sources/{source_id}/pages/{page_index}",
    dependencies=[Depends(require_session)],
)
async def render_source_page(
    project_id: str,
    source_id: str,
    page_index: int,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    scale: float = 1.0,
) -> Response:
    """Render a raster PNG preview of a source PDF page."""
    if page_index < 0:
        raise InvalidInput(f"page_index must be >= 0, got {page_index}")
    if not math.isfinite(scale) or not (0.5 <= scale <= 2.0):
        raise InvalidInput(f"scale must be a finite float between 0.5 and 2.0, got {scale}")

    if not hasattr(svc, "render_source_page"):
        raise FeatureNotAvailable("render_source_page is not implemented on the service")

    req = RenderSourcePageIn(
        project_id=project_id,
        source_id=source_id,
        page_index=page_index,
        scale=scale,
    )
    out = await anyio.to_thread.run_sync(svc.render_source_page, ctx, req)
    span_overlay = "1" if getattr(out, "span_overlay_supported", True) else "0"
    headers = {
        "Content-Type": "image/png",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "X-Page-Index": str(out.page_index),
        "X-Page-Width": str(out.width),
        "X-Page-Height": str(out.height),
        "X-Span-Overlay-Supported": span_overlay,
    }
    return Response(content=out.data, media_type="image/png", headers=headers)


# ---------- 4. Jobs ----------


@router.get("/jobs", dependencies=[Depends(require_session)])
async def list_jobs_global(
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    project_id: str | None = None,
    states: Annotated[list[str] | None, Query()] = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List jobs globally or for an optional project."""
    if not hasattr(svc, "list_jobs"):
        raise FeatureNotAvailable("list_jobs is not implemented on the service")

    in_cls = getattr(api, "ListJobsIn", None)
    if in_cls is not None:
        req = in_cls(project_id=project_id, states=states, cursor=cursor, limit=limit)
    else:
        req = {"project_id": project_id, "states": states, "cursor": cursor, "limit": limit}
    out = await anyio.to_thread.run_sync(svc.list_jobs, ctx, req)
    return out.model_dump() if hasattr(out, "model_dump") else out


@router.get("/projects/{project_id}/jobs", dependencies=[Depends(require_session)])
async def list_jobs_project(
    project_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    states: Annotated[list[str] | None, Query()] = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List jobs for a specific project."""
    if not hasattr(svc, "list_jobs"):
        raise FeatureNotAvailable("list_jobs is not implemented on the service")

    in_cls = getattr(api, "ListJobsIn", None)
    if in_cls is not None:
        req = in_cls(project_id=project_id, states=states, cursor=cursor, limit=limit)
    else:
        req = {"project_id": project_id, "states": states, "cursor": cursor, "limit": limit}
    out = await anyio.to_thread.run_sync(svc.list_jobs, ctx, req)
    return out.model_dump() if hasattr(out, "model_dump") else out


@router.post("/projects/{project_id}/jobs", dependencies=[Depends(require_csrf)])
async def start_job(
    project_id: str,
    body: StartJobIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Enqueue a new processing job."""
    _check_match(project_id, body.project_id, "project_id")
    out = await anyio.to_thread.run_sync(svc.start_job, ctx, body)
    return out.model_dump()


@router.get("/projects/{project_id}/jobs/{job_id}", dependencies=[Depends(require_session)])
async def get_job(
    project_id: str,
    job_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Poll job state, warnings, and results."""
    req = GetJobIn(project_id=project_id, job_id=job_id)
    out = await anyio.to_thread.run_sync(svc.get_job, ctx, req)
    return out.model_dump()


@router.post("/projects/{project_id}/jobs/{job_id}/control", dependencies=[Depends(require_csrf)])
async def control_job(
    project_id: str,
    job_id: str,
    body: ControlJobIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Pause, resume, cancel, or retry a job."""
    _check_match(project_id, body.project_id, "project_id")
    _check_match(job_id, body.job_id, "job_id")
    out = await anyio.to_thread.run_sync(svc.control_job, ctx, body)
    return out.model_dump()


# ---------- 5. References ----------


@router.get("/projects/{project_id}/references", dependencies=[Depends(require_session)])
async def list_references(
    project_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    source_id: str | None = None,
    job_id: str | None = None,
    extraction: ExtractionState | None = None,
    identity: IdentityState | None = None,
    discovery: DiscoveryState | None = None,
    classification: AccessClassification | None = None,
    classification_is_null: bool | None = None,
    local_copy: LocalCopyState | None = None,
    retrieval: RetrievalState | None = None,
    reason: str | None = None,
    query: str | None = None,
    sort: str = "order",
    fields: Annotated[list[str] | None, Query()] = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List reference occurrences with filters."""
    has_filter = any(
        v is not None
        for v in (
            source_id,
            job_id,
            extraction,
            identity,
            discovery,
            classification,
            classification_is_null,
            local_copy,
            retrieval,
            reason,
        )
    )
    ref_filter = (
        ReferenceFilter(
            source_id=source_id,
            job_id=job_id,
            extraction=extraction,
            identity=identity,
            discovery=discovery,
            classification=classification,
            classification_is_null=classification_is_null,
            local_copy=local_copy,
            retrieval=retrieval,
            reason=reason,
        )
        if has_filter
        else None
    )
    req = ListReferencesIn(
        project_id=project_id,
        filter=ref_filter,
        query=query,
        sort=sort,  # type: ignore[arg-type]
        fields=fields,
        cursor=cursor,
        limit=limit,
    )
    out = await anyio.to_thread.run_sync(svc.list_references, ctx, req)
    return out.model_dump()


@router.post("/projects/{project_id}/references", dependencies=[Depends(require_csrf)])
async def add_reference(
    project_id: str,
    body: AddReferenceIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Manually add a citation occurrence."""
    _check_match(project_id, body.project_id, "project_id")
    out = await anyio.to_thread.run_sync(svc.add_reference, ctx, body)
    return out.model_dump()


@router.get("/projects/{project_id}/references/{occurrence_id}", dependencies=[Depends(require_session)])
async def get_reference(
    project_id: str,
    occurrence_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    sections: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    """Get reference occurrence details, candidate metadata, and evidence."""
    req = GetReferenceIn(project_id=project_id, occurrence_id=occurrence_id, sections=sections)  # type: ignore[arg-type]
    out = await anyio.to_thread.run_sync(svc.get_reference, ctx, req)
    return out.model_dump()


@router.post("/projects/{project_id}/references/update", dependencies=[Depends(require_csrf)])
async def update_reference(
    project_id: str,
    body: UpdateReferenceIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Apply reference repairs (edit_text, split, merge, exclude, restore, candidate selection, etc.)."""
    _check_match(project_id, body.project_id, "project_id")
    out = await anyio.to_thread.run_sync(svc.update_reference, ctx, body)
    return out.model_dump()


@router.post("/resolve", dependencies=[Depends(require_csrf)])
@router.post("/resolve-citation", dependencies=[Depends(require_csrf)])
async def resolve_citation(
    body: ResolveCitationIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Scratch lookup candidates for raw text or fields without modifying state."""
    out = await anyio.to_thread.run_sync(svc.resolve_citation, ctx, body)
    return out.model_dump()


# ---------- 6. Files, Exports, Drafts, and Artifacts ----------


@router.post(
    "/projects/{project_id}/references/{occurrence_id}/attach",
    dependencies=[Depends(require_csrf)],
)
async def attach_file(
    project_id: str,
    occurrence_id: str,
    body: AttachFileIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Attach an allowed local file to a reference occurrence."""
    _check_match(project_id, body.project_id, "project_id")
    _check_match(occurrence_id, body.occurrence_id, "occurrence_id")
    out = await anyio.to_thread.run_sync(svc.attach_file, ctx, body)
    return out.model_dump()


@router.post("/projects/{project_id}/download", dependencies=[Depends(require_csrf)])
@router.post("/projects/{project_id}/files/download", dependencies=[Depends(require_csrf)])
async def download_files(
    project_id: str,
    body: DownloadFilesIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Enqueue background download for eligible open access works."""
    _check_match(project_id, body.project_id, "project_id")
    out = await anyio.to_thread.run_sync(svc.download_files, ctx, body)
    return out.model_dump()


@router.post("/projects/{project_id}/exports", dependencies=[Depends(require_csrf)])
async def create_export(
    project_id: str,
    body: CreateExportIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Enqueue project export generation."""
    _check_match(project_id, body.project_id, "project_id")
    out = await anyio.to_thread.run_sync(svc.create_export, ctx, body)
    return out.model_dump()


@router.post("/projects/{project_id}/drafts", dependencies=[Depends(require_csrf)])
async def draft_requests(
    project_id: str,
    body: DraftRequestsIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Enqueue email draft generation for restricted works."""
    _check_match(project_id, body.project_id, "project_id")
    out = await anyio.to_thread.run_sync(svc.draft_requests, ctx, body)
    return out.model_dump()


@router.get("/artifacts", dependencies=[Depends(require_session)])
async def list_global_artifacts(
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    kind: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List artifacts globally across projects."""
    if not hasattr(svc, "list_global_artifacts"):
        raise FeatureNotAvailable("list_global_artifacts is not implemented on the service")

    in_cls = getattr(api, "ListGlobalArtifactsIn", None)
    if in_cls is not None:
        req = in_cls(kind=kind, cursor=cursor, limit=limit)
    else:
        req = {"kind": kind, "cursor": cursor, "limit": limit}
    out = await anyio.to_thread.run_sync(svc.list_global_artifacts, ctx, req)
    return out.model_dump() if hasattr(out, "model_dump") else out


@router.get("/projects/{project_id}/artifacts", dependencies=[Depends(require_session)])
async def list_artifacts(
    project_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    kind: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List export and draft artifacts for a project."""
    req = ListArtifactsIn(project_id=project_id, kind=kind, cursor=cursor, limit=limit)
    out = await anyio.to_thread.run_sync(svc.list_artifacts, ctx, req)
    return out.model_dump()


@router.get(
    "/projects/{project_id}/artifacts/{artifact_id}/text",
    dependencies=[Depends(require_session)],
)
async def read_artifact_text(
    project_id: str,
    artifact_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    offset: int = 0,
    length: int | None = None,
) -> dict[str, Any]:
    """Read a slice of text artifact content."""
    req = ReadArtifactIn(project_id=project_id, artifact_id=artifact_id, offset=offset, length=length)
    out = await anyio.to_thread.run_sync(svc.read_artifact, ctx, req)
    return out.model_dump()


@router.get(
    "/projects/{project_id}/files/{kind}/{resource_id}",
    dependencies=[Depends(require_session)],
)
async def read_project_file(
    project_id: str,
    kind: str,
    resource_id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Response:
    """Stream binary project files (source, work_file, artifact, local_copy) directly as bytes."""
    if kind not in ("source", "work_file", "artifact", "local_copy"):
        raise InvalidInput(
            f"Invalid file kind '{kind}'. Must be 'source', 'work_file', 'artifact', or 'local_copy'."
        )
    if not hasattr(svc, "read_project_file"):
        raise FeatureNotAvailable("read_project_file is not implemented on the service")

    in_cls = getattr(api, "ReadProjectFileIn", None)
    chunk_len = 65536

    def fetch_chunk(offset: int) -> Any:
        if in_cls is not None:
            r = in_cls(
                project_id=project_id,
                kind=kind,
                resource_id=resource_id,
                offset=offset,
                length=chunk_len,
            )
        else:
            r = {
                "project_id": project_id,
                "kind": kind,
                "resource_id": resource_id,
                "offset": offset,
                "length": chunk_len,
            }
        return svc.read_project_file(ctx, r)

    # Read the first chunk to obtain the descriptor (media_type, name)
    first_chunk = await anyio.to_thread.run_sync(fetch_chunk, 0)
    descriptor = first_chunk.descriptor if hasattr(first_chunk, "descriptor") else first_chunk["descriptor"]
    media_type = (
        descriptor.media_type
        if hasattr(descriptor, "media_type")
        else descriptor.get("media_type", "application/octet-stream")
    )
    name = descriptor.name if hasattr(descriptor, "name") else descriptor.get("name", "download")

    async def stream_content() -> AsyncIterator[bytes]:
        data = first_chunk.data if hasattr(first_chunk, "data") else first_chunk["data"]
        eof = first_chunk.eof if hasattr(first_chunk, "eof") else first_chunk["eof"]
        next_offset = (
            first_chunk.next_offset if hasattr(first_chunk, "next_offset") else first_chunk["next_offset"]
        )

        if data:
            yield data

        curr_offset = next_offset
        while not eof:
            chunk = await anyio.to_thread.run_sync(fetch_chunk, curr_offset)
            c_data = chunk.data if hasattr(chunk, "data") else chunk["data"]
            eof = chunk.eof if hasattr(chunk, "eof") else chunk["eof"]
            curr_offset = chunk.next_offset if hasattr(chunk, "next_offset") else chunk["next_offset"]
            if c_data:
                yield c_data

    safe_ascii, encoded_name = _sanitize_filename(name)
    cd_header = f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded_name}"
    return StreamingResponse(
        stream_content(),
        media_type=media_type,
        headers={"Content-Disposition": cd_header},
    )


# ---------- 7. Wave 2 Stubs (fail explicitly with FEATURE_NOT_AVAILABLE) ----------


@router.post("/projects/{project_id}/uploads/pdf", dependencies=[Depends(require_csrf)])
async def upload_pdf(
    project_id: str,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Upload a PDF for source import or local copy attachment."""
    content_type_header = request.headers.get("content-type", "")
    if not content_type_header.lower().startswith("multipart/form-data"):
        raise InvalidInput(f"Content-Type must be multipart/form-data, got '{content_type_header}'")

    try:
        form = await request.form(max_files=1, max_fields=10)
    except BodyTooLargeError:
        raise
    except Exception as exc:
        raise InvalidInput(f"Invalid multipart form payload: {exc}") from exc

    try:
        # Reject duplicate form fields
        seen_keys: set[str] = set()
        for k, _ in form.multi_items():
            if k in seen_keys:
                raise InvalidInput(f"Duplicate form field '{k}' is prohibited")
            seen_keys.add(k)

        # Reject unexpected form fields
        extra_keys = seen_keys - ALLOWED_UPLOAD_FIELDS
        if extra_keys:
            raise InvalidInput(f"Unexpected form fields: {', '.join(sorted(extra_keys))}")

        # Validate file presence and single file
        file_field = form.get("file")
        if not isinstance(file_field, UploadFile):
            raise InvalidInput("Missing required file field 'file'")

        # Validate MIME type
        content_type = (file_field.content_type or "").lower()
        if content_type != "application/pdf":
            raise InvalidInput(
                f"File content-type must be 'application/pdf', got '{file_field.content_type}'"
            )

        # Validate display name
        raw_name = form.get("name") or file_field.filename or "upload.pdf"
        if not isinstance(raw_name, str):
            raise InvalidInput("Form field 'name' must be a string")
        name = raw_name.strip()
        unquoted = unquote(name)
        if (
            not name
            or name in (".", "..")
            or "/" in name
            or "\\" in name
            or "/" in unquoted
            or "\\" in unquoted
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(ord(c) < 32 or ord(c) == 127 for c in unquoted)
        ):
            raise InvalidInput(
                f"Invalid filename '{raw_name}': must be a safe leaf name "
                "without path separators or control characters"
            )

        # Validate purpose
        purpose = form.get("purpose")
        if purpose not in ("source", "local_copy"):
            raise InvalidInput(f"Invalid purpose '{purpose}'. Must be 'source' or 'local_copy'.")

        # Validate declared_bytes
        declared_bytes_raw = form.get("declared_bytes")
        if declared_bytes_raw is None:
            raise InvalidInput("Missing required form field 'declared_bytes'")
        try:
            declared_bytes = int(declared_bytes_raw)  # type: ignore[arg-type]
        except (ValueError, TypeError) as err:
            raise InvalidInput(f"declared_bytes must be an integer, got '{declared_bytes_raw}'") from err
        if declared_bytes <= 0:
            raise InvalidInput(f"declared_bytes must be positive, got {declared_bytes}")

        # Validate idempotency_key
        idempotency_key = form.get("idempotency_key")
        if (
            not idempotency_key
            or not isinstance(idempotency_key, str)
            or not (8 <= len(idempotency_key) <= 128)
        ):
            raise InvalidInput("idempotency_key must be a string between 8 and 128 characters")

        # Validate occurrence_id
        occurrence_id = form.get("occurrence_id")
        if occurrence_id is not None and not isinstance(occurrence_id, str):
            raise InvalidInput("occurrence_id must be a string")
        if purpose == "local_copy" and not occurrence_id:
            raise InvalidInput("occurrence_id is required when purpose is 'local_copy'")
        if purpose == "source" and occurrence_id:
            raise InvalidInput("occurrence_id must not be set when purpose is 'source'")

        # Validate version_hint
        version_hint = form.get("version_hint")
        if version_hint is not None and not isinstance(version_hint, str):
            raise InvalidInput("version_hint must be a string")

        # Check declared_bytes against actual file bytes
        file_field.file.seek(0, 2)
        actual_bytes = file_field.file.tell()
        file_field.file.seek(0)
        if actual_bytes != declared_bytes:
            raise InvalidInput(
                f"Declared bytes ({declared_bytes}) does not match actual uploaded bytes ({actual_bytes})"
            )

        if not hasattr(svc, "upload_pdf"):
            raise FeatureNotAvailable("upload_pdf is not implemented on the service")

        req = UploadPdfIn(
            project_id=project_id,
            purpose=purpose,  # type: ignore[arg-type]
            name=name,
            declared_bytes=declared_bytes,
            occurrence_id=occurrence_id,
            version_hint=version_hint,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
        )

        out = await anyio.to_thread.run_sync(svc.upload_pdf, ctx, req, file_field.file)
        return out.model_dump() if hasattr(out, "model_dump") else out
    finally:
        await form.close()


# ---------- 8. Interactive Review and Settings (Wave 3) ----------


@router.get("/settings", dependencies=[Depends(require_session)])
async def get_settings(
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    project_id: str | None = None,
) -> dict[str, Any]:
    """Read web settings and optional project settings."""
    if not hasattr(svc, "get_settings"):
        raise FeatureNotAvailable(
            "get_settings is not implemented on the service",
            recovery_hint="Use `refcollector config show` on the CLI",
        )

    in_cls = getattr(api, "GetSettingsIn", None)
    req = in_cls(project_id=project_id) if in_cls is not None else None
    out = await anyio.to_thread.run_sync(svc.get_settings, ctx, req)
    return out.model_dump() if hasattr(out, "model_dump") else out


@router.post(
    "/settings/intent",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def prepare_web_settings(
    body: dict[str, Any],
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Prepare a settings change intent and return a confirmation token."""
    if not hasattr(svc, "prepare_web_settings"):
        raise FeatureNotAvailable(
            "prepare_web_settings is not implemented on the service",
            recovery_hint="Use `refcollector config` commands on the CLI",
        )

    in_cls = getattr(api, "UpdateWebSettingsIn", None)
    if in_cls is None:
        raise FeatureNotAvailable("UpdateWebSettingsIn contract is not available")

    req = in_cls.model_validate(body)
    out = await anyio.to_thread.run_sync(svc.prepare_web_settings, ctx, req)
    return out.model_dump() if hasattr(out, "model_dump") else out


@router.post(
    "/settings",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def confirm_web_settings(
    body: dict[str, Any],
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    x_confirmation_token: Annotated[str | None, Header(alias="X-Confirmation-Token")] = None,
) -> dict[str, Any]:
    """Confirm a settings change using the confirmation token from intent preparation."""
    if not x_confirmation_token:
        raise NotAuthorized("X-Confirmation-Token header is missing")

    if not hasattr(svc, "confirm_web_settings"):
        raise FeatureNotAvailable(
            "confirm_web_settings is not implemented on the service",
            recovery_hint="Use `refcollector config` commands on the CLI",
        )

    in_cls = getattr(api, "UpdateWebSettingsIn", None)
    if in_cls is None:
        raise FeatureNotAvailable("UpdateWebSettingsIn contract is not available")

    req = in_cls.model_validate(body)
    out = await anyio.to_thread.run_sync(svc.confirm_web_settings, ctx, req, x_confirmation_token)
    return out.model_dump() if hasattr(out, "model_dump") else out


@router.post(
    "/projects/{project_id}/references/{occurrence_id}/review-intent",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def prepare_web_review(
    project_id: str,
    occurrence_id: str,
    body: ReviewDecisionIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> dict[str, Any]:
    """Prepare an interactive reference review intent and return a confirmation token."""
    _check_match(project_id, body.project_id, "project_id")
    _check_match(occurrence_id, body.occurrence_id, "occurrence_id")

    if not hasattr(svc, "prepare_web_review"):
        raise FeatureNotAvailable(
            "prepare_web_review is not implemented on the service",
            recovery_hint="Use `refcollector review` on a terminal for interactive approval",
        )

    out = await anyio.to_thread.run_sync(svc.prepare_web_review, ctx, body)
    return out.model_dump() if hasattr(out, "model_dump") else out


@router.post(
    "/projects/{project_id}/references/{occurrence_id}/review",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def confirm_web_review(
    project_id: str,
    occurrence_id: str,
    body: ReviewDecisionIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    x_confirmation_token: Annotated[str | None, Header(alias="X-Confirmation-Token")] = None,
) -> dict[str, Any]:
    """Confirm an interactive reference review decision with a confirmation token."""
    if not x_confirmation_token:
        raise NotAuthorized("X-Confirmation-Token header is missing")

    _check_match(project_id, body.project_id, "project_id")
    _check_match(occurrence_id, body.occurrence_id, "occurrence_id")

    if not hasattr(svc, "confirm_web_review"):
        raise FeatureNotAvailable(
            "confirm_web_review is not implemented on the service",
            recovery_hint="Use `refcollector review` on a terminal for interactive approval",
        )

    out = await anyio.to_thread.run_sync(svc.confirm_web_review, ctx, body, x_confirmation_token)
    return out.model_dump() if hasattr(out, "model_dump") else out


@router.post(
    "/projects/{project_id}/settings",
    dependencies=[Depends(require_csrf)],
)
async def update_project_settings(project_id: str) -> None:
    """Direct project settings endpoint remains unavailable."""
    raise FeatureNotAvailable(
        "Direct project settings route is unavailable; use /api/settings with confirmed intent or the CLI",
        recovery_hint="Use `refcollector config network` or `refcollector config downloads` on the CLI",
    )
