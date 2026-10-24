# W2 backend cleanup brief: B5 (remove direct storage access from library routes)

Worker: Antigravity `gemini-3.8-flash-high`, high effort. Outline your approach first.

## Problem

B4 fixed the live bugs, but `src/refcollector/web/library_routes.py` now reads storage directly
(`svc.store.occurrences`, `svc.store.sources`, `svc.store.files`, 13 places, guarded by
`hasattr`). Plan rule: routes call service methods only. The `hasattr` guards also mean the fake
service silently takes a different path than the real one, so tests do not prove the live fix.

## Root cause

Two service DTOs lack fields the library needs. Add them (additive, with defaults; D-45):

- `contracts/api.py` `OccurrenceSummary`: `order: float = 0.0`
- `contracts/api.py` `SourceListItem`: `sha256: str | None = None`

Fill them in `services/service.py` where these DTOs are built. Update `tests/fakes/service.py`
to fill them too.

## Tasks

1. Add the two fields and fill them in the service and the fake.
2. In `library_routes.py`, delete every `svc.store` access and `hasattr(svc, "store")` guard.
   Use `occurrence.order` and `source.sha256` from the DTOs.
3. For folder sync source bytes, use one existing path: `svc.read_project_file` (kind `source` /
   `work_file`) streamed to the sync temp file, or add a small service method
   `project_file_path(ctx, project_id, kind, resource_id) -> Path` that reuses the
   `read_project_file` lookup and `RC_HOME` guard. Pick the smaller diff; state which.
4. Keep every B4 behaviour and its regression tests passing.

## Owned files

`library_routes.py`, `library_api.py`, `services/library_folder.py`, `contracts/api.py`
(the two fields only), `services/service.py` (fill the fields, optional file-path method only),
`tests/fakes/service.py`, `tests/unit/web/test_library_*.py`, `tests/unit/services/test_library_folder.py`.

## Acceptance

```
grep -n "svc.store\|hasattr(svc" src/refcollector/web/library_routes.py   # must print nothing
uv run pytest -o addopts="" -q tests/unit/web tests/unit/services tests/contracts
uv run ruff check src tests
uv run ruff format --check src tests
```

Report files changed and command results. Do not start or stop the server.
