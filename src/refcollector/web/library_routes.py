"""Library HTTP API routes (Wave 1 & Wave 2, B1 - B3).

Implements endpoints under /api/library:
- GET /defaults
- GET /collections
- POST /collections
- POST /collections/{cid}/permissions/intent
- POST /collections/{cid}/permissions/confirm
- GET /collections/{cid}/shelf
- POST /collections/{cid}/papers
- GET /collections/{cid}/papers/{pid}
- POST /collections/{cid}/find-metadata
- POST /collections/{cid}/download
- GET /collections/{cid}/citations/{oid}
- POST /collections/{cid}/citations/{oid}/review/intent
- POST /collections/{cid}/citations/{oid}/review/confirm
- POST /collections/{cid}/sync
- GET /collections/{cid}/jobs/{jid}
- GET /collections/{cid}/files/{kind}/{id}
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import tempfile
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile

from refcollector.contracts import api
from refcollector.contracts.api import CallContext, RefCollectorService
from refcollector.domain.enums import (
    ACCEPTED_IDENTITIES,
    IdentityState,
    JobState,
    LocalCopyState,
    NetworkMode,
    RetrievalState,
    Stage,
    VersionPolicy,
)
from refcollector.errors import InvalidInput, NotAuthorized, NotFound, RcError
from refcollector.services.library_folder import FolderRecord, LibraryFolders
from refcollector.web.dependencies import (
    get_call_context,
    get_service,
    require_csrf,
    require_session,
)
from refcollector.web.library_api import (
    Accepted,
    Author,
    Candidate,
    Citation,
    CitationDetail,
    Collection,
    CollectionConsequences,
    CreateCollectionIn,
    DefaultsOut,
    FileReviewIn,
    Intent,
    JobStatus,
    PaperCard,
    PaperDetail,
    PendingFile,
    PermissionIntentIn,
    Permissions,
    RenameCollectionIn,
    ReviewIn,
    RunIn,
    ShelfPage,
    citation_status,
    filter_shelf_papers,
    format_author,
    is_downloadable,
    paginate_shelf_papers,
)
from refcollector.web.routes import _sanitize_filename

router = APIRouter(prefix="/api/library")
logger = logging.getLogger(__name__)


async def _all_pages(
    method: Callable[[CallContext, Any], Any], ctx: CallContext, req: Any, field: str
) -> Any:
    """Collect a service listing without changing its filters or sort order."""
    page = await anyio.to_thread.run_sync(method, ctx, req)
    rows = list(getattr(page, field))
    cursor = page.next_cursor
    while cursor is not None:
        page = await anyio.to_thread.run_sync(method, ctx, req.model_copy(update={"cursor": cursor}))
        rows.extend(getattr(page, field))
        cursor = page.next_cursor
    return page.model_copy(update={field: rows, "next_cursor": None})


async def _job_paper_ids(
    svc: RefCollectorService, ctx: CallContext, cid: str, job: api.GetJobOut
) -> list[str]:
    """Recover occurrence-scoped jobs through the service's job reference filter."""
    if job.results.source_ids:
        return list(job.results.source_ids)
    references = await _all_pages(
        svc.list_references,
        ctx,
        api.ListReferencesIn(project_id=cid, filter=api.ReferenceFilter(job_id=job.job_id)),
        "occurrences",
    )
    return sorted({o["source_id"] for o in references.occurrences if o.get("source_id")})


def _available_pdfs(detail: api.GetReferenceOut) -> Iterable[tuple[str, dict[str, Any]]]:
    """Keep work-file and local-copy resource identities for viewer navigation."""
    for file in detail.files or []:
        if file.get("state") in (RetrievalState.VERIFIED, RetrievalState.SATISFIED_BY_LOCAL_COPY):
            yield "work_file", file
    for file in getattr(detail, "citation_files", None) or []:
        if file.get("state") == RetrievalState.VERIFIED:
            yield "citation_file", file
    for copy in detail.local_copies or []:
        if copy.get("state") == LocalCopyState.USABLE:
            yield "local_copy", copy


def _downloaded_card(
    cid: str,
    kind: str,
    file: dict[str, Any],
    detail: api.GetReferenceOut,
    references: list[dict[str, Any]],
    folder: FolderRecord | None,
) -> PaperCard:
    resource_id = file[f"{kind}_id"]
    name = file.get("export_name") or f"{resource_id}.pdf"
    work = None if kind == "citation_file" else detail.work
    work_id = detail.occurrence.work_id
    cited_by = sorted(
        {o["source_id"] for o in references if work_id and o.get("work_id") == work_id and o.get("source_id")}
    )
    if kind == "citation_file":
        cited_by = sorted(
            {
                o["source_id"]
                for o in references
                if o.get("citation_file_id") == resource_id and o.get("source_id")
            }
        )
    if not cited_by:
        cited_by = [detail.occurrence.source_id]
    sha = file.get("sha256")
    path = (
        str(folder.folder / folder.files[sha]).replace("\\", "/") if folder and sha in folder.files else None
    )
    return PaperCard(
        paper_id=resource_id,
        kind="downloaded",
        title=work.title if work and work.title else name,
        authors=[Author(display=format_author(p).display) for p in work.authors] if work else [],
        year=work.year if work else None,
        venue=work.venue if work else None,
        doi=file.get("target_doi") if kind == "citation_file" else (work.doi if work else None),
        file_name=name,
        folder_path=path,
        citation_count=0,
        status_counts={},
        cited_by=cited_by,
        active_job_id=None,
        file_url=f"/api/library/collections/{cid}/files/{kind}/{resource_id}",
    )


def _get_folders(
    request: Request,
    settings_out: Any,
    svc: RefCollectorService | None = None,
    ctx: CallContext | None = None,
) -> LibraryFolders:
    """Retrieve or construct the LibraryFolders instance for this application."""

    def get_allowed() -> list[Path]:
        if svc is not None and ctx is not None:
            with contextlib.suppress(Exception):
                s = svc.get_settings(ctx, api.GetSettingsIn())
                return [Path(p) for p in getattr(s, "allow_folders", [])]
        return [Path(p) for p in getattr(settings_out, "allow_folders", [])]

    if hasattr(request.app.state, "library_folders") and request.app.state.library_folders is not None:
        folders = request.app.state.library_folders
        folders.allowed_folders = get_allowed
        return folders

    rc_home = Path(settings_out.rc_home)
    folders = LibraryFolders(rc_home, get_allowed)
    request.app.state.library_folders = folders
    return folders


def _is_inside(folder: Path, allowed_folders: list[str]) -> bool:
    """Check if folder is inside any of the allowed folder roots."""
    target = folder.resolve()
    for root_str in allowed_folders:
        try:
            root = Path(root_str).resolve()
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


async def _build_collection(
    cid: str,
    name: str | None,
    svc: RefCollectorService,
    ctx: CallContext,
    folders: LibraryFolders,
    settings_out: Any | None = None,
) -> Collection:
    """Construct Collection DTO with current project, folder, counts, and permissions."""
    proj = await anyio.to_thread.run_sync(svc.get_project, ctx, api.GetProjectIn(project_id=cid))
    folder_rec = folders.get(cid)
    folder_path = str(folder_rec.folder.resolve()).replace("\\", "/") if folder_rec is not None else None

    if settings_out is None or getattr(settings_out, "project", None) is None:
        try:
            settings_out = await anyio.to_thread.run_sync(
                svc.get_settings, ctx, api.GetSettingsIn(project_id=cid)
            )
        except Exception:
            if settings_out is None:
                settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())

    folder_granted = False
    if folder_rec is not None:
        folder_granted = _is_inside(folder_rec.folder, settings_out.allow_folders)

    network_mode = proj.network_mode
    proj_download = getattr(proj, "download_enabled", False)
    global_download = getattr(settings_out, "download_enabled_global", False)
    downloads_enabled = bool(proj_download or global_download)

    if proj_download:
        downloads_source = "project"
    elif global_download:
        downloads_source = "global"
    else:
        downloads_source = "none"

    can_download = bool(folder_granted and network_mode == "retrieval" and downloads_enabled)

    refs_out = await _all_pages(svc.list_references, ctx, api.ListReferencesIn(project_id=cid), "occurrences")
    check_count = 0
    downloaded_work_ids: set[str] = set()

    for o in refs_out.occurrences:
        ret_val = (
            o.get("retrieval", {}).get("value")
            if isinstance(o.get("retrieval"), dict)
            else getattr(o.get("retrieval"), "value", str(o.get("retrieval", "")))
        )
        has_verified = ret_val in (
            RetrievalState.VERIFIED.value,
            RetrievalState.SATISFIED_BY_LOCAL_COPY.value,
        )
        st = citation_status(o, has_verified_file=has_verified)
        if st == "check":
            check_count += 1
        if has_verified and o.get("work_id"):
            downloaded_work_ids.add(o["work_id"])
        elif o.get("citation_file_state") == RetrievalState.VERIFIED and o.get("citation_file_id"):
            downloaded_work_ids.add(o["citation_file_id"])

    paper_count = proj.sources_count + len(downloaded_work_ids)

    return Collection(
        collection_id=cid,
        name=name if name is not None else proj.name,
        folder=folder_path,
        paper_count=paper_count,
        check_count=check_count,
        permissions=Permissions(
            folder_granted=folder_granted,
            network_mode=network_mode,
            downloads_enabled=downloads_enabled,
            downloads_source=downloads_source,
            can_download=can_download,
        ),
        settings_revision=settings_out.revision,
    )


def _dedupe_reasons(raw_reasons: Iterable[Any]) -> list[str]:
    """Deduplicate reason codes preserving original order with raw code values."""
    seen: set[str] = set()
    result: list[str] = []
    for r in raw_reasons:
        val = r.value if hasattr(r, "value") else str(r)
        key = val.strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(val)
    return result


def _has_cited_doi(occ: Any) -> bool:
    """Use the service-computed current cited DOI only."""
    if isinstance(occ, dict):
        return bool(occ.get("citation_doi")) and occ.get("extraction") != "excluded"
    return bool(getattr(occ, "citation_doi", None)) and getattr(occ, "extraction", None) != "excluded"


def _build_citation(
    ref_detail: Any,
    has_verified: bool,
    dl_id: str | None,
    svc: RefCollectorService | None = None,
    cid: str | None = None,
    fallback_configured: bool = False,
) -> Citation:
    """Build Citation DTO ensuring order, text, and identity_reasons are populated."""
    occ = ref_detail.occurrence
    st = citation_status(occ, has_verified_file=has_verified) or "extracted"
    if dl_id is None and st == "downloaded":
        st = "pdf_available" if is_downloadable(occ) else "extracted"
    citation_file_id = getattr(occ, "citation_file_id", None)
    citation_file_state = getattr(occ, "citation_file_state", None)
    if getattr(occ, "extraction", None) != "excluded":
        if citation_file_id and citation_file_state == RetrievalState.VERIFIED:
            st, dl_id = "downloaded", citation_file_id
        elif citation_file_id and citation_file_state == RetrievalState.NEEDS_REVIEW:
            st = "check"
    work_md = ref_detail.work

    citation_text = (
        getattr(occ, "edited_text", None) or getattr(occ, "raw_text", None) or getattr(occ, "text", "")
    )

    raw_reasons = getattr(
        occ,
        "identity_reasons",
        getattr(occ.identity, "reasons", []),
    )
    reasons = _dedupe_reasons(raw_reasons)

    order_val = getattr(occ, "order", 0.0)

    check_kind: Literal["identity", "file"] | None = None
    if st == "check":
        ret = getattr(occ, "retrieval", None)
        ret_val = (
            ret.get("value")
            if isinstance(ret, dict)
            else (ret.value if hasattr(ret, "value") else str(ret or ""))
        )
        ident = getattr(occ, "identity", None)
        if isinstance(ident, dict):
            ident_val = ident.get("state") or ident.get("value") or ""
            if hasattr(ident_val, "value"):
                ident_val = ident_val.value
        elif hasattr(ident, "state"):
            ident_st = ident.state
            ident_val = ident_st.value if hasattr(ident_st, "value") else str(ident_st or "")
        elif hasattr(ident, "value"):
            ident_val = ident.value
        else:
            ident_val = str(ident or "")

        if ret_val == RetrievalState.NEEDS_REVIEW.value:
            check_kind = "file"
        elif ident_val == IdentityState.NEEDS_REVIEW.value:
            check_kind = "identity"

    if st == "check" and citation_file_id and citation_file_state == RetrievalState.NEEDS_REVIEW:
        check_kind = "file"
    ident = getattr(occ, "identity", None)
    ident_state = ident.get("state") if isinstance(ident, dict) else getattr(ident, "state", ident)
    return Citation(
        citation_id=occ.occurrence_id,
        order=float(order_val or 0.0),
        text=citation_text,
        status=st,
        reasons=reasons,
        title=work_md.title if work_md else None,
        authors=(
            [Author(display=format_author(p).display) for p in work_md.authors]
            if work_md and work_md.authors
            else []
        ),
        year=work_md.year if work_md else None,
        venue=work_md.venue if work_md else None,
        doi=work_md.doi if work_md else getattr(occ, "citation_doi", None),
        can_download_by_doi=fallback_configured and _has_cited_doi(occ),
        metadata_review_pending=ident_state not in ACCEPTED_IDENTITIES,
        can_download=is_downloadable(occ),
        downloaded_paper_id=dl_id,
        revision=occ.revision,
        check_kind=check_kind,
    )


def _sync_work_files(
    cid: str,
    folders: LibraryFolders,
    svc: RefCollectorService,
    files: list[dict[str, Any]],
    ctx: CallContext,
    kind: str = "work_file",
) -> None:
    """Sync verified work files into collection folder when present on disk."""
    folder_rec = folders.get(cid)
    if folder_rec is None:
        return
    for f in files:
        f_state = f.get("state")
        f_state_val = f_state.value if hasattr(f_state, "value") else str(f_state or "")
        if f_state_val in (
            RetrievalState.VERIFIED.value,
            RetrievalState.SATISFIED_BY_LOCAL_COPY.value,
        ):
            sha = f.get("sha256")
            if not sha or sha in folder_rec.files:
                continue
            wfid = f.get(f"{kind}_id")
            if not wfid:
                continue
            try:
                source_path = svc.project_file_path(ctx, cid, kind, wfid)
                export_name = f.get("export_name") or f"{wfid}.pdf"
                folders.sync(cid, [(sha, export_name, source_path)])
            except Exception as exc:
                logger.warning(
                    "Folder sync failed for work file '%s' (%s): %s",
                    wfid,
                    sha,
                    exc,
                    exc_info=True,
                )


# ============================================================================
# Defaults
# ============================================================================


@router.get("/defaults", dependencies=[Depends(require_session)])
async def get_defaults() -> DefaultsOut:
    """Return default root folder for collection creation dialog."""
    default_root = (Path.home() / "RefCollector").resolve()
    return DefaultsOut(default_root=str(default_root).replace("\\", "/"))


# ============================================================================
# Collections
# ============================================================================


@router.get("/collections", dependencies=[Depends(require_session)])
async def list_collections(
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> list[Collection]:
    """Return all collections with folder path, counts, and permissions."""
    projects_out = await _all_pages(svc.list_projects, ctx, api.ListProjectsIn(), "projects")
    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())
    folders = _get_folders(request, settings_out, svc, ctx)

    results: list[Collection] = []
    for p in projects_out.projects:
        coll = await _build_collection(
            cid=p.project_id,
            name=p.name,
            svc=svc,
            ctx=ctx,
            folders=folders,
            settings_out=None,
        )
        results.append(coll)

    return results


@router.post(
    "/collections",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def create_collection(
    body: CreateCollectionIn,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Collection:
    """Create a new project and folder record; grants no permissions automatically."""
    create_out = await anyio.to_thread.run_sync(
        svc.create_project,
        ctx,
        api.CreateProjectIn(name=body.name, idempotency_key=body.idempotency_key),
    )
    cid = create_out.project_id

    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())
    folders = _get_folders(request, settings_out, svc, ctx)
    folders.create(cid, Path(body.folder))

    return await _build_collection(
        cid=cid,
        name=body.name,
        svc=svc,
        ctx=ctx,
        folders=folders,
        settings_out=None,
    )


@router.patch(
    "/collections/{cid}",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def rename_collection(
    cid: str,
    body: RenameCollectionIn,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Collection:
    """Rename a collection."""
    new_name = body.name.strip()
    if not new_name:
        raise InvalidInput("Collection name cannot be empty.")

    await anyio.to_thread.run_sync(
        svc.rename_project, ctx, api.RenameProjectIn(project_id=cid, name=new_name)
    )

    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())
    folders = _get_folders(request, settings_out, svc, ctx)
    return await _build_collection(
        cid=cid,
        name=new_name,
        svc=svc,
        ctx=ctx,
        folders=folders,
        settings_out=None,
    )


@router.get(
    "/collections/{cid}/consequences",
    dependencies=[Depends(require_session)],
)
async def get_collection_consequences(
    cid: str,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> CollectionConsequences:
    """Return record and file counts before collection deletion."""
    consequences = await anyio.to_thread.run_sync(
        svc.get_project_consequences, ctx, api.GetProjectConsequencesIn(project_id=cid)
    )
    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())
    folders = _get_folders(request, settings_out, svc, ctx)
    folder_rec = folders.get(cid)
    file_count = len(folder_rec.files) if folder_rec else 0
    folder_path = str(folder_rec.folder.resolve()).replace("\\", "/") if folder_rec else None

    msg = (
        f"Deleting this collection will permanently remove its {consequences.paper_count} papers, "
        f"{consequences.citation_count} citations, and review decisions from the database. "
        f"The {file_count} files in your folder on disk will NOT be deleted."
    )
    return CollectionConsequences(
        collection_id=cid,
        name=consequences.name,
        paper_count=consequences.paper_count,
        citation_count=consequences.citation_count,
        file_count=file_count,
        folder_path=folder_path,
        message=msg,
        has_active_jobs=consequences.has_active_jobs,
    )


@router.delete(
    "/collections/{cid}",
    status_code=204,
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def delete_collection(
    cid: str,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> None:
    """Delete a collection, removing all database records and sidecar metadata cleanly."""
    await anyio.to_thread.run_sync(svc.delete_project, ctx, api.DeleteProjectIn(project_id=cid))
    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())
    folders = _get_folders(request, settings_out, svc, ctx)
    await anyio.to_thread.run_sync(folders.delete, cid)


# ============================================================================
# Permissions
# ============================================================================


@router.post(
    "/collections/{cid}/permissions/intent",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def prepare_permission_intent(
    cid: str,
    body: PermissionIntentIn,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Intent:
    """Prepare a permissions change intent and return confirmation token with description."""
    expected_rev = body.expected_settings_revision

    proj_settings = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn(project_id=cid))
    global_settings = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())

    folders = _get_folders(request, proj_settings, svc, ctx)
    folder_rec = folders.get(cid)

    if folder_rec is None and body.change == "add_allowed_folder":
        raise NotFound(f"Collection folder not configured for '{cid}'")

    if body.change == "add_allowed_folder":
        assert folder_rec is not None
        path_str = str(folder_rec.folder.resolve()).replace("\\", "/")
        change = api.AddAllowedFolderChange(path=path_str)
        description = [f"Allow RefCollector to write to {path_str}"]
        if expected_rev in (proj_settings.revision, global_settings.revision):
            expected_rev = global_settings.revision
    elif body.change == "set_project_network":
        change = api.SetProjectNetworkChange(project_id=cid, mode=NetworkMode.RETRIEVAL)
        description = ["Set network access to retrieval for downloading papers"]
        if expected_rev in (proj_settings.revision, global_settings.revision):
            expected_rev = proj_settings.revision
    elif body.change == "set_project_downloads":
        change = api.SetProjectDownloadsChange(project_id=cid, enabled=True)
        description = ["Enable paper downloads for this collection"]
        if expected_rev in (proj_settings.revision, global_settings.revision):
            expected_rev = proj_settings.revision
    else:
        raise InvalidInput(f"Unknown change '{body.change}'")

    intent_out = await anyio.to_thread.run_sync(
        svc.prepare_web_settings,
        ctx,
        api.UpdateWebSettingsIn(
            expected_settings_revision=expected_rev,
            change=change,
            idempotency_key=body.idempotency_key,
        ),
    )

    return Intent(
        confirmation_token=intent_out.confirmation_token,
        expires_at=intent_out.expires_at,
        summary=intent_out.summary,
        description=description,
    )


@router.post(
    "/collections/{cid}/permissions/confirm",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def confirm_permission(
    cid: str,
    body: PermissionIntentIn,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    x_confirmation_token: Annotated[str | None, Header(alias="X-Confirmation-Token")] = None,
) -> Collection:
    """Confirm a permission change using the confirmation token from intent preparation."""
    if not x_confirmation_token:
        raise NotAuthorized("X-Confirmation-Token header is missing")

    expected_rev = body.expected_settings_revision

    proj_settings = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn(project_id=cid))
    global_settings = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())

    folders = _get_folders(request, proj_settings, svc, ctx)
    folder_rec = folders.get(cid)

    if body.change == "add_allowed_folder":
        if folder_rec is None:
            raise NotFound(f"Collection folder not configured for '{cid}'")
        path_str = str(folder_rec.folder.resolve()).replace("\\", "/")
        change = api.AddAllowedFolderChange(path=path_str)
        if expected_rev in (proj_settings.revision, global_settings.revision):
            expected_rev = global_settings.revision
    elif body.change == "set_project_network":
        change = api.SetProjectNetworkChange(project_id=cid, mode=NetworkMode.RETRIEVAL)
        if expected_rev in (proj_settings.revision, global_settings.revision):
            expected_rev = proj_settings.revision
    elif body.change == "set_project_downloads":
        change = api.SetProjectDownloadsChange(project_id=cid, enabled=True)
        if expected_rev in (proj_settings.revision, global_settings.revision):
            expected_rev = proj_settings.revision
    else:
        raise InvalidInput(f"Unknown change '{body.change}'")

    await anyio.to_thread.run_sync(
        svc.confirm_web_settings,
        ctx,
        api.UpdateWebSettingsIn(
            expected_settings_revision=expected_rev,
            change=change,
            idempotency_key=body.idempotency_key,
        ),
        x_confirmation_token,
    )

    refreshed_settings = await anyio.to_thread.run_sync(
        svc.get_settings, ctx, api.GetSettingsIn(project_id=cid)
    )
    return await _build_collection(
        cid=cid,
        name=None,
        svc=svc,
        ctx=ctx,
        folders=folders,
        settings_out=refreshed_settings,
    )


# ============================================================================
# Shelf
# ============================================================================


@router.get("/collections/{cid}/shelf", dependencies=[Depends(require_session)])
async def get_shelf(
    cid: str,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    q: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ShelfPage:
    """Return paged paper cards (uploaded and downloaded) for a collection."""
    await anyio.to_thread.run_sync(svc.get_project, ctx, api.GetProjectIn(project_id=cid))

    sources_out = await _all_pages(svc.list_sources, ctx, api.ListSourcesIn(project_id=cid), "sources")
    refs_out = await _all_pages(svc.list_references, ctx, api.ListReferencesIn(project_id=cid), "occurrences")
    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn(project_id=cid))
    folders = _get_folders(request, settings_out, svc, ctx)
    folder_rec = folders.get(cid)

    # Active jobs map
    active_source_jobs: dict[str, str] = {}
    try:
        jobs_out = await _all_pages(
            svc.list_jobs,
            ctx,
            api.ListJobsIn(project_id=cid, states=[JobState.QUEUED, JobState.RUNNING]),
            "jobs",
        )
        for j in jobs_out.jobs:
            job_full = await anyio.to_thread.run_sync(
                svc.get_job, ctx, api.GetJobIn(project_id=cid, job_id=j.job_id)
            )
            for sid in await _job_paper_ids(svc, ctx, cid, job_full):
                active_source_jobs[sid] = j.job_id
    except (RcError, AttributeError, ValueError, AssertionError):
        pass

    # Group references by source_id
    refs_by_source: dict[str, list[dict[str, Any]]] = {}
    for o in refs_out.occurrences:
        sid = o.get("source_id", "")
        refs_by_source.setdefault(sid, []).append(o)

    paper_cards: list[PaperCard] = []

    # 1. Uploaded papers
    for s in sources_out.sources:
        sid = s.source_id
        src_refs = refs_by_source.get(sid, [])

        st_counts: Counter[str] = Counter()
        for o in src_refs:
            st = citation_status(o)
            if st is not None:
                st_counts[st] += 1

        sha = getattr(s, "sha256", None)
        if sha is None and hasattr(s, "preflight"):
            sha = getattr(s.preflight, "sha256", None)
        folder_path = None
        if folder_rec is not None and sha and sha in folder_rec.files:
            folder_path = str(folder_rec.folder / folder_rec.files[sha]).replace("\\", "/")

        paper_cards.append(
            PaperCard(
                paper_id=sid,
                kind="uploaded",
                title=s.label,
                authors=[],
                year=None,
                venue=None,
                doi=None,
                file_name=s.label,
                folder_path=folder_path,
                citation_count=sum(st_counts.values()),
                status_counts=dict(st_counts),
                cited_by=[],
                active_job_id=active_source_jobs.get(sid),
                file_url=f"/api/library/collections/{cid}/files/source/{sid}",
            )
        )

    # 2. Downloaded papers
    seen_work_files: dict[str, PaperCard] = {}
    for o in refs_out.occurrences:
        retrieval = o.get("retrieval")
        if isinstance(retrieval, dict):
            retrieval = retrieval.get("value")
        if (
            retrieval not in (RetrievalState.VERIFIED, RetrievalState.SATISFIED_BY_LOCAL_COPY)
            and o.get("citation_file_state") != RetrievalState.VERIFIED
        ):
            continue
        ref_detail = await anyio.to_thread.run_sync(
            svc.get_reference,
            ctx,
            api.GetReferenceIn(
                project_id=cid,
                occurrence_id=o["occurrence_id"],
                sections=["files", "local_copies", "citation_files"],
            ),
        )
        _sync_work_files(cid, folders, svc, ref_detail.files or [], ctx)
        _sync_work_files(
            cid, folders, svc, getattr(ref_detail, "citation_files", None) or [], ctx, "citation_file"
        )
        pdfs = list(_available_pdfs(ref_detail))
        work_hashes = {file.get("sha256") for file_kind, file in pdfs if file_kind == "work_file"}
        for file_kind, file in pdfs:
            if file_kind == "local_copy" and file.get("sha256") and file["sha256"] in work_hashes:
                continue
            card = _downloaded_card(cid, file_kind, file, ref_detail, refs_out.occurrences, folders.get(cid))
            key = file.get("sha256") or card.paper_id
            if key in seen_work_files:
                seen_work_files[key].cited_by = sorted(set(seen_work_files[key].cited_by + card.cited_by))
            else:
                seen_work_files[key] = card

    paper_cards.extend(seen_work_files.values())

    filtered = filter_shelf_papers(paper_cards, q=q, kind=kind, status=status)
    return paginate_shelf_papers(filtered, cursor=cursor, limit=limit)


# ============================================================================
# Paper Upload
# ============================================================================


def _sync_uploaded_file(
    folders: LibraryFolders, cid: str, sha256: str, name: str, contents: bytes
) -> str | None:
    """Write temporary file and synchronize into collection folder."""
    with tempfile.NamedTemporaryFile("w+b", delete=False) as tmp_fp:
        tmp_fp.write(contents)
        tmp_fp.flush()
        tmp_path = Path(tmp_fp.name)
    try:
        rec = folders.sync(cid, [(sha256, name, tmp_path)])
        if sha256 in rec.files:
            return str(rec.folder / rec.files[sha256]).replace("\\", "/")
    except Exception as exc:
        logger.warning(
            "Failed to sync uploaded file '%s' (%s) for collection '%s': %s",
            name,
            sha256,
            cid,
            exc,
            exc_info=True,
        )
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
    return None


@router.post(
    "/collections/{cid}/papers",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def upload_paper(
    cid: str,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> PaperCard:
    """Upload a PDF, start citation extraction, and sync source into collection folder."""
    form = await request.form(max_files=1, max_fields=10)
    try:
        file_field = form.get("file")
        if not isinstance(file_field, UploadFile):
            raise InvalidInput("Missing required file field 'file'")

        idempotency_key = form.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not (8 <= len(idempotency_key) <= 128):
            raise InvalidInput("idempotency_key must be a string between 8 and 128 characters")

        content_type = (file_field.content_type or "").lower()
        if content_type != "application/pdf":
            raise InvalidInput(f"Content-type must be 'application/pdf', got '{content_type}'")

        name = file_field.filename or "upload.pdf"

        contents = await file_field.read()
        declared_bytes = len(contents)
        sha256 = hashlib.sha256(contents).hexdigest()

        upload_io = io.BytesIO(contents)
        upload_out = await anyio.to_thread.run_sync(
            svc.upload_pdf,
            ctx,
            api.UploadPdfIn(
                project_id=cid,
                purpose="source",
                name=name,
                declared_bytes=declared_bytes,
                idempotency_key=idempotency_key,
            ),
            upload_io,
        )

        assert upload_out.source is not None
        source_id = upload_out.source.source_id
        reused = upload_out.source.reused

        active_job_id: str | None = None
        if not reused:
            start_job_out = await anyio.to_thread.run_sync(
                svc.start_job,
                ctx,
                api.StartJobIn(
                    project_id=cid,
                    stages=[Stage.EXTRACT],
                    scope=api.SourceScope(source_ids=[source_id]),
                    idempotency_key=f"{idempotency_key}_extract",
                ),
            )
            active_job_id = start_job_out.job_id

        # Folder sync for the uploaded source
        settings_out = await anyio.to_thread.run_sync(
            svc.get_settings, ctx, api.GetSettingsIn(project_id=cid)
        )
        folders = _get_folders(request, settings_out, svc, ctx)
        folder_path = await anyio.to_thread.run_sync(
            _sync_uploaded_file, folders, cid, sha256, name, contents
        )

        return PaperCard(
            paper_id=source_id,
            kind="uploaded",
            title=name,
            authors=[],
            year=None,
            venue=None,
            doi=None,
            file_name=name,
            folder_path=folder_path,
            citation_count=0,
            status_counts={},
            cited_by=[],
            active_job_id=active_job_id,
            file_url=f"/api/library/collections/{cid}/files/source/{source_id}",
        )
    finally:
        await form.close()


# ============================================================================
# Paper Detail
# ============================================================================


@router.get("/collections/{cid}/papers/{pid}", dependencies=[Depends(require_session)])
async def get_paper(
    cid: str,
    pid: str,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> PaperDetail:
    """Return detail view of an uploaded or downloaded paper."""
    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn(project_id=cid))
    folders = _get_folders(request, settings_out, svc, ctx)
    folder_rec = folders.get(cid)

    # First attempt: treat as source_id (uploaded paper)
    try:
        src_out = await anyio.to_thread.run_sync(
            svc.get_source, ctx, api.GetSourceIn(project_id=cid, source_id=pid)
        )
    except NotFound:
        src_out = None

    if src_out is not None:
        s = src_out.source
        refs_out = await _all_pages(
            svc.list_references,
            ctx,
            api.ListReferencesIn(project_id=cid, filter=api.ReferenceFilter(source_id=pid)),
            "occurrences",
        )

        citations: list[Citation] = []
        st_counts: Counter[str] = Counter()

        for o in refs_out.occurrences:
            oid = o["occurrence_id"]
            ref_detail = await anyio.to_thread.run_sync(
                svc.get_reference,
                ctx,
                api.GetReferenceIn(
                    project_id=cid, occurrence_id=oid, sections=["files", "local_copies", "citation_files"]
                ),
            )
            files = (getattr(ref_detail, "citation_files", None) or []) + (ref_detail.files or [])
            _sync_work_files(cid, folders, svc, files, ctx)
            _sync_work_files(
                cid, folders, svc, getattr(ref_detail, "citation_files", None) or [], ctx, "citation_file"
            )

            dl_id: str | None = None
            for f in files:
                f_state = f.get("state")
                f_state_val = f_state.value if hasattr(f_state, "value") else str(f_state or "")
                if f_state_val in (
                    RetrievalState.VERIFIED.value,
                    RetrievalState.SATISFIED_BY_LOCAL_COPY.value,
                ):
                    dl_id = f.get("citation_file_id") or f.get("work_file_id")
                    break

            if dl_id is None:
                for lc in ref_detail.local_copies or []:
                    lc_state = lc.get("state") if isinstance(lc, dict) else getattr(lc, "state", None)
                    lc_state_val = lc_state.value if hasattr(lc_state, "value") else str(lc_state or "")
                    if lc_state_val == LocalCopyState.USABLE.value:
                        dl_id = (
                            lc.get("local_copy_id")
                            if isinstance(lc, dict)
                            else getattr(lc, "local_copy_id", None)
                        )
                        break

            has_verified = bool(dl_id)
            c = _build_citation(
                ref_detail,
                has_verified=has_verified,
                dl_id=dl_id,
                fallback_configured=bool(getattr(settings_out, "pdf_fallback_url", None)),
            )
            st = c.status
            st_counts[st] += 1
            citations.append(c)

        # Sort citations by order (Amendment 9)
        citations.sort(key=lambda c: c.order)

        sha = getattr(s, "sha256", None)
        if sha is None and hasattr(src_out, "preflight"):
            sha = getattr(src_out.preflight, "sha256", None)
        if sha is None and hasattr(src_out, "sha256"):
            sha = getattr(src_out, "sha256", None)
        folder_path = None
        if folder_rec is not None and sha and sha in folder_rec.files:
            folder_path = str(folder_rec.folder / folder_rec.files[sha]).replace("\\", "/")

        paper_card = PaperCard(
            paper_id=pid,
            kind="uploaded",
            title=s.label,
            authors=[],
            year=None,
            venue=None,
            doi=None,
            file_name=s.label,
            folder_path=folder_path,
            citation_count=len(citations),
            status_counts=dict(st_counts),
            cited_by=[],
            active_job_id=None,
            file_url=f"/api/library/collections/{cid}/files/source/{pid}",
        )

        pages = src_out.preflight.pages if src_out.preflight else None
        imported_at = getattr(s, "imported_at", None)

        return PaperDetail(
            paper=paper_card,
            pages=pages,
            imported_at=imported_at,
            citations=citations,
        )

    # Second attempt: check if pid is a downloaded work file
    refs_out = await _all_pages(svc.list_references, ctx, api.ListReferencesIn(project_id=cid), "occurrences")
    for o in refs_out.occurrences:
        retrieval = o.get("retrieval")
        if isinstance(retrieval, dict):
            retrieval = retrieval.get("value")
        if (
            retrieval not in (RetrievalState.VERIFIED, RetrievalState.SATISFIED_BY_LOCAL_COPY)
            and o.get("citation_file_state") != RetrievalState.VERIFIED
        ):
            continue
        ref_detail = await anyio.to_thread.run_sync(
            svc.get_reference,
            ctx,
            api.GetReferenceIn(
                project_id=cid,
                occurrence_id=o["occurrence_id"],
                sections=["files", "local_copies", "citation_files"],
            ),
        )
        for file_kind, file in _available_pdfs(ref_detail):
            resource_id = file.get(f"{file_kind}_id")
            if resource_id == pid:
                card = _downloaded_card(cid, file_kind, file, ref_detail, refs_out.occurrences, folder_rec)
                return PaperDetail(paper=card, pages=None, imported_at=None, citations=[])

    raise NotFound(f"Paper '{pid}' not found in collection '{cid}'")


# ============================================================================
# Find Metadata & Download
# ============================================================================


@router.post(
    "/collections/{cid}/find-metadata",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def find_metadata(
    cid: str,
    body: RunIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Accepted:
    """Run resolve and discover_access stages for a citation, paper, or collection."""
    if body.citation_id:
        scope: api.SourceScope | api.OccurrenceScope = api.OccurrenceScope(occurrence_ids=[body.citation_id])
    elif body.paper_id:
        scope = api.SourceScope(source_ids=[body.paper_id])
    else:
        sources_out = await _all_pages(svc.list_sources, ctx, api.ListSourcesIn(project_id=cid), "sources")
        if not sources_out.sources:
            return Accepted(job_id=None, disabled_reason="No papers in collection")
        scope = api.SourceScope(source_ids=[s.source_id for s in sources_out.sources])

    job_out = await anyio.to_thread.run_sync(
        svc.start_job,
        ctx,
        api.StartJobIn(
            project_id=cid,
            stages=[Stage.RESOLVE, Stage.DISCOVER_ACCESS],
            scope=scope,
            idempotency_key=body.idempotency_key,
        ),
    )

    return Accepted(job_id=job_out.job_id, disabled_reason=None)


@router.post(
    "/collections/{cid}/download",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def download(
    cid: str,
    body: RunIn,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Accepted:
    """Download verified PDF files for citations with eligible copies."""
    proj = await anyio.to_thread.run_sync(svc.get_project, ctx, api.GetProjectIn(project_id=cid))
    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn())
    folders = _get_folders(request, settings_out)
    folder_rec = folders.get(cid)

    folder_granted = folder_rec is not None and _is_inside(folder_rec.folder, settings_out.allow_folders)
    downloads_enabled = bool(getattr(proj, "download_enabled", False) or settings_out.download_enabled_global)
    network_mode = proj.network_mode

    if not folder_granted:
        return Accepted(job_id=None, disabled_reason="Collection folder permission not granted")
    if network_mode != "retrieval":
        return Accepted(job_id=None, disabled_reason="Network mode must be set to retrieval")
    if not downloads_enabled:
        return Accepted(job_id=None, disabled_reason="Downloads are not enabled")

    if body.use_fallback and not settings_out.pdf_fallback_url:
        return Accepted(
            job_id=None, disabled_reason="Configure a PDF fallback preset in local settings first"
        )
    version_policy = VersionPolicy.ANY_ELIGIBLE if body.use_fallback else proj.policy.get("version_policy")

    eligible = _has_cited_doi if body.use_fallback else is_downloadable

    # Determine candidate occurrences
    if body.citation_id:
        ref_detail = await anyio.to_thread.run_sync(
            svc.get_reference,
            ctx,
            api.GetReferenceIn(project_id=cid, occurrence_id=body.citation_id),
        )
        target_oids = [body.citation_id] if eligible(ref_detail.occurrence) else []
    elif body.paper_id:
        refs_out = await _all_pages(
            svc.list_references,
            ctx,
            api.ListReferencesIn(project_id=cid, filter=api.ReferenceFilter(source_id=body.paper_id)),
            "occurrences",
        )
        target_oids = [o["occurrence_id"] for o in refs_out.occurrences if eligible(o)]
    else:
        refs_out = await _all_pages(
            svc.list_references, ctx, api.ListReferencesIn(project_id=cid), "occurrences"
        )
        target_oids = [o["occurrence_id"] for o in refs_out.occurrences if eligible(o)]

    if not target_oids:
        return Accepted(job_id=None, disabled_reason="No downloadable citations found")

    job_out = await anyio.to_thread.run_sync(
        svc.download_files,
        ctx,
        api.DownloadFilesIn(
            project_id=cid,
            scope=api.OccurrenceScope(occurrence_ids=target_oids),
            version_policy=version_policy,
            retrieval_basis="citation_doi" if body.use_fallback else "accepted_work",
            idempotency_key=body.idempotency_key,
        ),
    )

    return Accepted(job_id=job_out.job_id, disabled_reason=None)


# ============================================================================
# Citation Detail & Review
# ============================================================================


@router.get("/collections/{cid}/citations/{oid}", dependencies=[Depends(require_session)])
async def get_citation(
    cid: str,
    oid: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> CitationDetail:
    """Return detail view of a citation including candidates."""
    ref_detail = await anyio.to_thread.run_sync(
        svc.get_reference,
        ctx,
        api.GetReferenceIn(
            project_id=cid,
            occurrence_id=oid,
            sections=["candidates", "files", "local_copies", "citation_files"],
        ),
    )

    files = (getattr(ref_detail, "citation_files", None) or []) + (ref_detail.files or [])
    dl_id: str | None = None
    for f in files:
        f_state = f.get("state")
        f_state_val = f_state.value if hasattr(f_state, "value") else str(f_state or "")
        if f_state_val in (
            RetrievalState.VERIFIED.value,
            RetrievalState.SATISFIED_BY_LOCAL_COPY.value,
        ):
            dl_id = f.get("citation_file_id") or f.get("work_file_id")
            break

    if dl_id is None:
        for lc in ref_detail.local_copies or []:
            lc_state = lc.get("state") if isinstance(lc, dict) else getattr(lc, "state", None)
            lc_state_val = lc_state.value if hasattr(lc_state, "value") else str(lc_state or "")
            if lc_state_val == LocalCopyState.USABLE.value:
                dl_id = (
                    lc.get("local_copy_id") if isinstance(lc, dict) else getattr(lc, "local_copy_id", None)
                )
                break

    settings = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn(project_id=cid))
    citation = _build_citation(
        ref_detail,
        has_verified=bool(dl_id),
        dl_id=dl_id,
        fallback_configured=bool(getattr(settings, "pdf_fallback_url", None)),
    )

    candidates = [
        Candidate(
            candidate_id=c.candidate_id,
            rank=c.rank,
            title=c.metadata.title,
            authors=[Author(display=format_author(p).display) for p in c.metadata.authors],
            year=c.metadata.year,
            venue=c.metadata.venue,
            doi=c.metadata.doi,
            reasons=_dedupe_reasons(c.reasons),
            rejected=c.rejected,
        )
        for c in (ref_detail.candidates or [])
    ]

    pending_file: PendingFile | None = None
    if citation.check_kind == "file":
        for f in files:
            f_state = f.get("state")
            f_state_val = f_state.value if hasattr(f_state, "value") else str(f_state or "")
            if f_state_val == RetrievalState.NEEDS_REVIEW.value:
                size_val = f.get("bytes", f.get("size", 0))
                failed = f.get("reasons", [])
                pending_file = PendingFile(
                    work_file_id=f.get("citation_file_id") or f.get("work_file_id", ""),
                    file_kind="citation_file" if f.get("citation_file_id") else "work_file",
                    size=int(size_val or 0),
                    source_url=f.get("source_url") or f.get("url"),
                    failed_checks=_dedupe_reasons(failed),
                )
                break

    return CitationDetail(citation=citation, candidates=candidates, pending_file=pending_file)


@router.post(
    "/collections/{cid}/citations/{oid}/review/intent",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def prepare_review_intent(
    cid: str,
    oid: str,
    body: ReviewIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Intent:
    """Prepare a citation review intent and return confirmation token with description."""
    req = api.ReviewDecisionIn(
        project_id=cid,
        occurrence_id=oid,
        action=body.action,
        candidate_id=body.candidate_id,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
    )
    intent_out = await anyio.to_thread.run_sync(svc.prepare_web_review, ctx, req)

    ref_detail = await anyio.to_thread.run_sync(
        svc.get_reference,
        ctx,
        api.GetReferenceIn(project_id=cid, occurrence_id=oid, sections=["candidates"]),
    )
    occ = ref_detail.occurrence
    citation_text = (
        getattr(occ, "edited_text", None) or getattr(occ, "raw_text", None) or getattr(occ, "text", "") or ""
    )
    snippet = citation_text[:120]

    cand_str = f"candidate {body.candidate_id}"
    for c in ref_detail.candidates or []:
        if c.candidate_id == body.candidate_id and c.metadata:
            m = c.metadata
            title = m.title or ""
            authors = m.authors or []
            first_author = ""
            if authors:
                first_p = authors[0]
                first_author = getattr(first_p, "family", "") or format_author(first_p).display
            year_str = str(m.year) if m.year is not None else ""
            details: list[str] = []
            if first_author:
                details.append(first_author)
            if year_str:
                details.append(year_str)
            paren = f" ({', '.join(details)})" if details else ""
            cand_str = f"'{title}'{paren}" if title else f"candidate {body.candidate_id}{paren}"
            break

    description = [f"{body.action.capitalize()} {cand_str} for citation: {snippet}"]

    return Intent(
        confirmation_token=intent_out.confirmation_token,
        expires_at=intent_out.expires_at,
        summary=intent_out.summary,
        description=description,
    )


@router.post(
    "/collections/{cid}/citations/{oid}/review/confirm",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def confirm_review(
    cid: str,
    oid: str,
    body: ReviewIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    x_confirmation_token: Annotated[str | None, Header(alias="X-Confirmation-Token")] = None,
) -> Citation:
    """Confirm a citation review decision using the confirmation token."""
    if not x_confirmation_token:
        raise NotAuthorized("X-Confirmation-Token header is missing")

    req = api.ReviewDecisionIn(
        project_id=cid,
        occurrence_id=oid,
        action=body.action,
        candidate_id=body.candidate_id,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
    )
    await anyio.to_thread.run_sync(svc.confirm_web_review, ctx, req, x_confirmation_token)

    ref_detail = await anyio.to_thread.run_sync(
        svc.get_reference,
        ctx,
        api.GetReferenceIn(project_id=cid, occurrence_id=oid, sections=["files", "citation_files"]),
    )
    files = (getattr(ref_detail, "citation_files", None) or []) + (ref_detail.files or [])
    dl_id: str | None = None
    for f in files:
        f_state = f.get("state")
        f_state_val = f_state.value if hasattr(f_state, "value") else str(f_state or "")
        if f_state_val in (
            RetrievalState.VERIFIED.value,
            RetrievalState.SATISFIED_BY_LOCAL_COPY.value,
        ):
            dl_id = f.get("citation_file_id") or f.get("work_file_id")
            break

    return _build_citation(ref_detail, has_verified=bool(dl_id), dl_id=dl_id)


@router.post(
    "/collections/{cid}/citations/{oid}/file-review/intent",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def prepare_file_review_intent(
    cid: str,
    oid: str,
    body: FileReviewIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Intent:
    """Prepare a file review intent and return confirmation token with description."""
    req = api.ReviewFileDecisionIn(
        project_id=cid,
        occurrence_id=oid,
        work_file_id=body.work_file_id,
        file_kind=body.file_kind,
        action=body.action,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
    )
    intent_out = await anyio.to_thread.run_sync(svc.prepare_web_file_review, ctx, req)

    file_info = intent_out.summary.get("file", {})
    size = file_info.get("size", 0)
    host = file_info.get("host") or "unknown host"
    failed_reasons = file_info.get("failed_reasons", [])
    failed_str = ", ".join(failed_reasons) if failed_reasons else "none"
    work_title = intent_out.summary.get("work_title") or "Unknown title"
    act_str = body.action.capitalize()

    description = [
        f"{act_str} PDF ({size} bytes from {host}) for '{work_title}' with failed checks: {failed_str}"
    ]

    return Intent(
        confirmation_token=intent_out.confirmation_token,
        expires_at=intent_out.expires_at,
        summary=intent_out.summary,
        description=description,
    )


@router.post(
    "/collections/{cid}/citations/{oid}/file-review/confirm",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def confirm_file_review(
    cid: str,
    oid: str,
    body: FileReviewIn,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
    x_confirmation_token: Annotated[str | None, Header(alias="X-Confirmation-Token")] = None,
) -> Citation:
    """Confirm a file review decision using the confirmation token."""
    if not x_confirmation_token:
        raise NotAuthorized("X-Confirmation-Token header is missing")

    req = api.ReviewFileDecisionIn(
        project_id=cid,
        occurrence_id=oid,
        work_file_id=body.work_file_id,
        file_kind=body.file_kind,
        action=body.action,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
    )
    await anyio.to_thread.run_sync(svc.confirm_web_file_review, ctx, req, x_confirmation_token)

    ref_detail = await anyio.to_thread.run_sync(
        svc.get_reference,
        ctx,
        api.GetReferenceIn(project_id=cid, occurrence_id=oid, sections=["files", "citation_files"]),
    )
    files = (getattr(ref_detail, "citation_files", None) or []) + (ref_detail.files or [])
    dl_id: str | None = None
    for f in files:
        f_state = f.get("state")
        f_state_val = f_state.value if hasattr(f_state, "value") else str(f_state or "")
        if f_state_val in (
            RetrievalState.VERIFIED.value,
            RetrievalState.SATISFIED_BY_LOCAL_COPY.value,
        ):
            dl_id = f.get("citation_file_id") or f.get("work_file_id")
            break

    return _build_citation(ref_detail, has_verified=bool(dl_id), dl_id=dl_id)


# ============================================================================
# Sync
# ============================================================================


@router.post(
    "/collections/{cid}/sync",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def retry_sync(
    cid: str,
    request: Request,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Collection:
    """Retry folder sync for all collection sources and verified work files."""
    settings_out = await anyio.to_thread.run_sync(svc.get_settings, ctx, api.GetSettingsIn(project_id=cid))
    folders = _get_folders(request, settings_out, svc, ctx)

    files_to_sync: list[tuple[str, str, Path]] = []
    sync_errors: list[Exception] = []

    # 1. Sources (uploaded PDFs)
    try:
        sources_out = await _all_pages(svc.list_sources, ctx, api.ListSourcesIn(project_id=cid), "sources")
    except (AssertionError, AttributeError):
        sources_out = api.ListSourcesOut(sources=[], next_cursor=None)
    for s in sources_out.sources:
        sid = s.source_id
        sha = getattr(s, "sha256", None)
        if sha is None and hasattr(s, "preflight"):
            sha = getattr(s.preflight, "sha256", None)

        if not sha:
            continue

        try:
            src_path = svc.project_file_path(ctx, cid, "source", sid)
            files_to_sync.append((sha, s.label, src_path))
        except Exception as exc:
            sync_errors.append(exc)
            logger.warning(
                "Cannot resolve file path for source '%s' (%s): %s",
                sid,
                sha,
                exc,
                exc_info=True,
            )

    # 2. Verified work files (downloaded citations)
    refs_out = await _all_pages(svc.list_references, ctx, api.ListReferencesIn(project_id=cid), "occurrences")
    for o in refs_out.occurrences:
        ret_val = (
            o.get("retrieval", {}).get("value")
            if isinstance(o.get("retrieval"), dict)
            else getattr(o.get("retrieval"), "value", str(o.get("retrieval", "")))
        )
        if (
            ret_val
            in (
                RetrievalState.VERIFIED.value,
                RetrievalState.SATISFIED_BY_LOCAL_COPY.value,
            )
            or o.get("citation_file_state") == RetrievalState.VERIFIED
        ):
            ref_detail = await anyio.to_thread.run_sync(
                svc.get_reference,
                ctx,
                api.GetReferenceIn(
                    project_id=cid, occurrence_id=o["occurrence_id"], sections=["files", "citation_files"]
                ),
            )
            files = (getattr(ref_detail, "citation_files", None) or []) + (ref_detail.files or [])
            for f in files:
                f_state = f.get("state")
                f_state_val = f_state.value if hasattr(f_state, "value") else str(f_state or "")
                if f_state_val in (
                    RetrievalState.VERIFIED.value,
                    RetrievalState.SATISFIED_BY_LOCAL_COPY.value,
                ):
                    sha = f.get("sha256")
                    if not sha:
                        continue
                    kind = "citation_file" if f.get("citation_file_id") else "work_file"
                    wfid = f.get(f"{kind}_id")
                    if not wfid:
                        continue
                    try:
                        src_path = svc.project_file_path(ctx, cid, kind, wfid)
                        exp_name = f.get("export_name") or f"{wfid}.pdf"
                        files_to_sync.append((sha, exp_name, src_path))
                    except Exception as exc:
                        sync_errors.append(exc)
                        logger.warning(
                            "Cannot resolve file path for work file '%s' (%s): %s",
                            wfid,
                            sha,
                            exc,
                            exc_info=True,
                        )

    sync_failed_exc: Exception | None = None
    try:
        folders.sync(cid, files_to_sync)
    except Exception as exc:
        sync_failed_exc = exc
        logger.warning(
            "Folder sync failed for collection '%s': %s",
            cid,
            exc,
            exc_info=True,
        )

    if sync_failed_exc is not None:
        if isinstance(sync_failed_exc, RcError):
            raise sync_failed_exc
        raise RcError(code="SYNC_FAILED", message=str(sync_failed_exc)) from sync_failed_exc

    if sync_errors and not files_to_sync:
        first_err = sync_errors[0]
        if isinstance(first_err, RcError):
            raise first_err
        raise RcError(code="SYNC_FAILED", message=str(first_err)) from first_err

    return await _build_collection(
        cid=cid,
        name=None,
        svc=svc,
        ctx=ctx,
        folders=folders,
        settings_out=settings_out,
    )


# ============================================================================
# Job Status
# ============================================================================


@router.get("/collections/{cid}/jobs/{jid}", dependencies=[Depends(require_session)])
async def get_job_status(
    cid: str,
    jid: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> JobStatus:
    """Return status of a running or completed job with kind and paper_ids."""
    job_out = await anyio.to_thread.run_sync(svc.get_job, ctx, api.GetJobIn(project_id=cid, job_id=jid))

    terminal = job_out.state in (
        JobState.COMPLETED,
        JobState.COMPLETED_WITH_WARNINGS,
        JobState.FAILED,
        JobState.CANCELLED,
    )

    state_str = job_out.state.value if hasattr(job_out.state, "value") else str(job_out.state)

    stage_names = [s.name.lower() for s in job_out.stages]
    if any(name in ("retrieve", "retrieve_doi", "download") for name in stage_names):
        kind = "download"
    elif any(name in ("extract",) for name in stage_names):
        kind = "extract"
    else:
        kind = "find_metadata"

    paper_ids = await _job_paper_ids(svc, ctx, cid, job_out)

    return JobStatus(
        job_id=job_out.job_id,
        state=state_str,
        terminal=terminal,
        stages=job_out.stages,
        warnings=job_out.warnings,
        next_poll_after_ms=job_out.next_poll_after_ms or 2000,
        kind=kind,
        paper_ids=paper_ids,
    )


# ============================================================================
# File Streaming
# ============================================================================


@router.get("/collections/{cid}/files/{kind}/{id}", dependencies=[Depends(require_session)])
async def stream_file(
    cid: str,
    kind: str,
    id: str,
    svc: Annotated[RefCollectorService, Depends(get_service)],
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> StreamingResponse:
    """Stream a source or work_file PDF with inline Content-Disposition."""
    if kind not in ("source", "work_file", "local_copy", "citation_file"):
        raise InvalidInput(f"Unknown kind '{kind}'; must be 'source', 'work_file', or 'local_copy'")

    chunk_len = 65536

    def fetch_chunk(offset: int) -> api.ReadProjectFileOut:
        req = api.ReadProjectFileIn(
            project_id=cid,
            kind=kind,  # type: ignore[arg-type]
            resource_id=id,
            offset=offset,
            length=chunk_len,
        )
        return svc.read_project_file(ctx, req)

    first_chunk = await anyio.to_thread.run_sync(fetch_chunk, 0)
    descriptor = first_chunk.descriptor
    media_type = (
        descriptor.media_type
        if hasattr(descriptor, "media_type")
        else descriptor.get("media_type", "application/pdf")
    )
    name = descriptor.name if hasattr(descriptor, "name") else descriptor.get("name", "document.pdf")

    async def stream_content() -> AsyncIterator[bytes]:
        data = first_chunk.data
        eof = first_chunk.eof
        next_offset = first_chunk.next_offset

        if data:
            yield data

        curr_offset = next_offset
        while not eof:
            chunk = await anyio.to_thread.run_sync(fetch_chunk, curr_offset)
            eof = chunk.eof
            curr_offset = chunk.next_offset
            if chunk.data:
                yield chunk.data

    safe_ascii, encoded_name = _sanitize_filename(name)
    cd_header = f"inline; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded_name}"

    return StreamingResponse(
        stream_content(),
        media_type=media_type,
        headers={"Content-Disposition": cd_header},
    )
