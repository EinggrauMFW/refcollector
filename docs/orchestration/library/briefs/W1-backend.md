# W1 backend brief: B1 + B2

Worker: Antigravity `gemini-3.8-flash-high`, high effort. Orchestrator: see `../HANDOFF.md`.
Read first: `../../../spec/library-ui-spec.md`, `../contract.md`, `../plan.md` (Ownership, Worker brief rules).

Outline your approach in 5-10 lines before editing. Work in the current checkout. No commits,
no new worktrees, no server starts. Run Python with `uv run`.

## Owned files (only these)

- `src/refcollector/web/library_api.py` (new)
- `src/refcollector/services/library_folder.py` (new)
- `src/refcollector/web/library_routes.py` (new)
- `tests/unit/services/test_library_folder.py` (new)
- `tests/unit/web/test_library_routes.py` (new)
- `tests/fakes/service.py` only for additive fake behaviour the new tests need

Do not edit `app.py` yet (B3 mounts the router). Do not edit engine code, contracts, storage,
the old static UI, docs or `.ai/todo.md`. If you need anything else, stop and report.

## B1: DTOs and folder sync

1. `library_api.py`: every DTO in contract section 4, exactly as named. Plus a pure function
   `citation_status(occ: Occurrence, has_verified_file: bool) -> CitationStatus | None`
   implementing contract section 3 (None = hidden/excluded).
2. `library_folder.py`: class `LibraryFolders(rc_home: Path, allowed_folders: Callable[[], list[Path]])`
   - `get(project_id) -> FolderRecord | None`, `create(project_id, folder: Path) -> FolderRecord`
   - `sync(project_id, files: Iterable[tuple[sha256, preferred_name, source_path]]) -> FolderRecord`
   - Rules from spec section 7 and contract section 2: atomic record write; folder created
     only when inside an allowed folder, else `PathNotAllowed`; skip a sha already recorded
     and present on disk; never overwrite; ` (2)`, ` (3)` suffix on name collision with
     different content; temp file in the target folder then `os.replace`; reject names with
     path separators or `..`; resolve symlinks before the inside-folder check.
3. Tests (behaviour, not markup): status mapping table rows 1-9; sync idempotency; no
   overwrite of a user file; collision suffix; path escape rejected; folder outside allowed
   folders rejected; record survives a reload.

## B2: read routes

`library_routes.py`: `APIRouter(prefix="/api/library")` with the GET routes from contract
section 5: `collections`, `shelf`, `papers/{pid}`, `citations/{oid}`, `jobs/{jid}`,
`files/{kind}/{id}`. Use the same dependencies the existing routes in `web/routes.py` use for
service, context and session (read `web/dependencies.py`, `web/routes.py`). Call the service
in a thread like the existing routes (`anyio.to_thread.run_sync`). Shelf search, filter and
paging run over service results; keep route functions thin by putting mapping helpers in
`library_api.py`.

Tests: build a test app that includes only this router with the fake service
(see `tests/unit/web/test_http.py` for the pattern). Cover: shelf lists uploaded and
downloaded papers with `cited_by`; paper detail statuses; 404 for an unknown collection;
file route streams a PDF with inline disposition and rejects unknown `kind`.

## Acceptance commands

```
uv run pytest -o addopts="" -q tests/unit/services/test_library_folder.py tests/unit/web/test_library_routes.py
uv run ruff check src/refcollector/web/library_api.py src/refcollector/web/library_routes.py src/refcollector/services/library_folder.py tests/unit/services/test_library_folder.py tests/unit/web/test_library_routes.py
uv run ruff format --check <same files>
```

Do not run the whole suite. Report: files changed, commands with results, contract gaps found
(do not change the contract yourself), remaining work.
