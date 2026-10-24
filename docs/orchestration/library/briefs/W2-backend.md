# W2 backend brief: B3

Worker: Antigravity `gemini-3.8-flash-high`, high effort. Same rules as `W1-backend.md`.
Read first: `../contract.md` (sections 5-8, section 8 overrides), `../../../spec/library-ui-spec.md`.
Outline your approach in 5-10 lines before editing.

## Owned files (only these)

- `src/refcollector/web/library_api.py`, `src/refcollector/web/library_routes.py`,
  `src/refcollector/services/library_folder.py`
- `src/refcollector/web/app.py` (mount router, serving), `src/refcollector/web/security.py`
  (framing and CSP for library routes only)
- `tests/unit/web/test_library_routes.py`, `tests/unit/services/test_library_folder.py`,
  new `tests/unit/web/test_library_serving.py`, `tests/fakes/service.py` (additive only)

Do not touch the old static UI files, `web-ui/**`, engine code, contracts or docs.

## Tasks

1. **DTO amendments** (contract section 8, items 2, 3, 5, 6): `RunIn.citation_id`,
   `Intent.description`, `JobStatus.kind` and `paper_ids`, defaults route.
2. **Fix from W1 review:** `Citation.order` from `ref_detail.occurrence.order`, list sorted by it
   (item 9). Check that `reasons` come from the occurrence's `identity_reasons`.
3. **Action routes** (contract section 5 and 6): `POST /collections`,
   `permissions/intent`, `permissions/confirm`, `papers` (multipart upload), `find-metadata`,
   `download`, `citations/{oid}/review/intent`, `review/confirm`, `sync`, `GET /defaults`.
   Reuse the existing service methods the current routes in `web/routes.py` call
   (`create_project`, `prepare_web_settings`, `confirm_web_settings`, `upload_pdf` flow from
   the existing `/uploads/pdf` route, `start_job`, `download_files`, `prepare_web_review`,
   `confirm_web_review`). Never confirm an intent on the server's own initiative.
   `download` returns `Accepted(job_id=None, disabled_reason=...)` when no citation is
   downloadable or permissions do not allow it.
4. **Folder sync wiring:** sync the source after upload; sync new verified work files when
   `GET /shelf` or `GET /papers/{pid}` runs (contract section 6). A sync error never fails the
   request; the card shows `folder_path = None`.
5. **Serving** (contract section 7): mount the library router; `GET /` and unknown non-API,
   non-static paths serve `static/library/index.html`; `GET /advanced` serves the old
   `static/index.html`; existing `/static/*` URLs keep working.
6. **Framing and CSP** (item 8): library file route allows same-origin framing; library page
   CSP adds `frame-src 'self'`; nothing else changes. Keep strict script/style rules.

## Tests (behaviour)

Permission intent then confirm with token (missing token rejected); upload creates a paper
and an extract job; download with nothing downloadable returns `disabled_reason`;
`citation_id` scope; review confirm needs token; `/` returns the library HTML, `/advanced`
returns the old dashboard HTML; file route headers allow same-origin framing and other
routes still send `DENY`.

## Acceptance

```
uv run pytest -o addopts="" -q tests/unit/web tests/unit/services/test_library_folder.py
uv run ruff check src/refcollector/web src/refcollector/services/library_folder.py tests/unit/web tests/unit/services/test_library_folder.py
uv run ruff format --check src/refcollector/web src/refcollector/services/library_folder.py tests/unit/web tests/unit/services/test_library_folder.py
```

`tests/unit/web` includes the existing web tests: they must still pass. Report files, commands
with results, contract gaps, remaining work.
