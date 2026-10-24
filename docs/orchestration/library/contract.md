# Library UI contract (W0 freeze)

Date: 22 October 2026. Frozen for W1. Changes need an orchestrator decision row in
`../decisions.md` and a note in `HANDOFF.md`. Spec: [library-ui-spec.md](../../spec/library-ui-spec.md).

## 1. Versions

Checked with `npm view` on 22 October 2026.

| Package | Version | Note |
|---|---|---|
| react, react-dom | 19.3.1 | |
| vite | 8.3.5 | |
| @vitejs/plugin-react | 6.1.2 | |
| typescript | 7.0.2 | Use 7.x only if the chosen lint stack supports it. If not, pin the newest version it supports and record why in `status.md` |

Node on this host: v22.23.2, npm 10.9.8. No other runtime dependency without an orchestrator
decision. Tests: Vitest if needed for logic (status mapping, API client); no markup snapshot tests.

## 2. Collection folder record

The engine `Project` model does not change. `services/library_folder.py` owns a small JSON
record per collection at `RC_HOME/library/<project_id>.json`:

```json
{"folder": "C:/Users/me/RefCollector/Thesis", "files": {"<sha256>": "Smith_2020_Title.pdf"}}
```

Write it atomically (temp file plus `os.replace`). `folder` is absolute and resolved.
The folder directory is created at the first sync, and only if `folder` is inside an
allowed folder from settings (`allow_folders`). Otherwise sync returns `PATH_NOT_ALLOWED`.

## 3. Citation status mapping

The server computes `status` per citation from `Occurrence` fields and the work file
lookup. First matching rule wins.

| # | Rule | Status |
|---|---|---|
| 1 | `extraction == failed` or `retrieval == failed` | `failed` |
| 2 | `extraction == excluded` | not returned (hidden) |
| 3 | `retrieval in {verified, satisfied_by_local_copy}` | `downloaded` |
| 4 | `identity == needs_review` or `retrieval == needs_review` or `extraction == needs_review` | `check` |
| 5 | `identity in {unresolved, rejected}` | `no_match` |
| 6 | `identity == unprocessed` | `extracted` |
| 7 | accepted identity and `discovery == not_checked` | `matched` |
| 8 | accepted identity and `classification == eligible_found` | `pdf_available` |
| 9 | accepted identity, discovery checked, other classification | `no_pdf` |

Accepted identity = `ACCEPTED_IDENTITIES` in `domain/enums.py`. `retrieval == queued` maps
by the other rules; the UI shows `working` client-side for rows in the scope of an active job.
`working` is never sent by the server. Download is allowed exactly when the service rule
`_downloadable(o)` holds; the server sends it as `can_download`.

## 4. DTOs

Pydantic models live in `src/refcollector/web/library_api.py`. The TypeScript mirror is
`web-ui/src/api/types.ts`; names and fields match one to one (snake_case on the wire).

```python
PaperKind = Literal["uploaded", "downloaded"]
CitationStatus = Literal["extracted", "check", "no_match", "matched",
                         "pdf_available", "no_pdf", "downloaded", "failed"]

class Author(BaseModel):          # from Person: "Family, Given" or literal
    display: str

class Permissions(BaseModel):
    folder_granted: bool
    network_mode: str             # NetworkMode value
    downloads_enabled: bool       # effective: project or global grant
    downloads_source: Literal["project", "global", "none"]
    can_download: bool            # all three requirements hold

class Collection(BaseModel):
    collection_id: str            # = project_id
    name: str
    folder: str | None            # None until the folder record exists
    paper_count: int
    check_count: int
    permissions: Permissions
    settings_revision: str        # pass back in permission intents

class PaperCard(BaseModel):
    paper_id: str                 # source_id or work_file_id
    kind: PaperKind
    title: str                    # metadata title, else file name
    authors: list[Author]
    year: int | None
    venue: str | None
    doi: str | None
    file_name: str
    folder_path: str | None       # None when not synced
    citation_count: int           # uploaded only; 0 for downloaded
    status_counts: dict[CitationStatus, int]   # uploaded only
    cited_by: list[str]           # downloaded only: uploaded paper_ids
    active_job_id: str | None
    file_url: str                 # library file route for this PDF

class ShelfPage(BaseModel):
    papers: list[PaperCard]
    next_cursor: str | None
    total: int

class Citation(BaseModel):
    citation_id: str              # occurrence_id
    order: float
    text: str                     # Occurrence.text
    status: CitationStatus
    reasons: list[str]            # identity_reasons values
    title: str | None
    authors: list[Author]
    year: int | None
    venue: str | None
    doi: str | None
    can_download: bool
    downloaded_paper_id: str | None   # work_file_id when downloaded
    revision: int

class PaperDetail(BaseModel):
    paper: PaperCard
    pages: int | None             # uploaded only, from preflight
    imported_at: datetime | None
    citations: list[Citation]     # uploaded: its citations; downloaded: []

class Candidate(BaseModel):
    candidate_id: str
    rank: int
    title: str | None
    authors: list[Author]
    year: int | None
    venue: str | None
    doi: str | None
    reasons: list[str]
    rejected: bool

class CitationDetail(BaseModel):
    citation: Citation
    candidates: list[Candidate]

class JobStatus(BaseModel):
    job_id: str
    state: str                    # JobState value
    terminal: bool
    stages: list[StageCounts]     # existing contracts.api.StageCounts
    warnings: list[str]
    next_poll_after_ms: int       # GetJobOut value, default 2000

class Intent(BaseModel):          # wraps WebIntentOut
    confirmation_token: str
    expires_at: datetime
    summary: dict[str, Any]

class CreateCollectionIn(BaseModel):
    name: str                     # 1..200 chars
    folder: str                   # absolute path
    idempotency_key: str

class PermissionIntentIn(BaseModel):
    change: Literal["add_allowed_folder", "set_project_network", "set_project_downloads"]
    expected_settings_revision: str
    idempotency_key: str

class RunIn(BaseModel):           # find-metadata and download
    paper_id: str | None = None   # None = whole collection
    idempotency_key: str

class ReviewIn(BaseModel):
    action: Literal["accept", "reject"]
    candidate_id: str
    expected_revision: int
    idempotency_key: str

class Accepted(BaseModel):        # action routes that start work
    job_id: str | None
    disabled_reason: str | None   # set instead of job_id when the action cannot run
```

## 5. Endpoints

Prefix `/api/library`. Router `src/refcollector/web/library_routes.py`, mounted in `app.py`
with the existing session, CSRF and Host dependencies. Mutations need the existing CSRF
header. Confirm routes need the `X-Confirmation-Token` header, as the existing review and
settings confirm routes do.

| Method and path | Body / query | Response |
|---|---|---|
| `GET /defaults` | | `DefaultsOut` (`default_root`, see section 8) |
| `GET /collections` | | `list[Collection]` |
| `POST /collections` | `CreateCollectionIn` (`name`, `folder`, `idempotency_key`) | `Collection` (creates project and folder record; grants nothing) |
| `PATCH /collections/{cid}` | `RenameCollectionIn` (`name`) | `Collection` |
| `GET /collections/{cid}/consequences` | | `CollectionConsequences` (paper, citation and file counts; `has_active_jobs`) |
| `DELETE /collections/{cid}` | | `204`, no body. Removes the project and folder record. Files on disk stay. |
| `POST /collections/{cid}/permissions/intent` | `PermissionIntentIn` | `Intent` |
| `POST /collections/{cid}/permissions/confirm` | `PermissionIntentIn` + token header | `Collection` |
| `GET /collections/{cid}/shelf` | query `q`, `kind`, `status`, `cursor`, `limit` (1-100, default 50) | `ShelfPage` |
| `POST /collections/{cid}/papers` | multipart `file` (`application/pdf`) + `idempotency_key` (8-128 characters) | `PaperCard` with `active_job_id` |
| `GET /collections/{cid}/papers/{pid}` | | `PaperDetail` |
| `POST /collections/{cid}/find-metadata` | `RunIn` (`paper_id?`, `citation_id?`, `use_fallback`, `idempotency_key`) | `Accepted` |
| `POST /collections/{cid}/download` | `RunIn` (`use_fallback=true` selects the DOI-first path, section 9) | `Accepted` |
| `GET /collections/{cid}/citations/{oid}` | | `CitationDetail` |
| `POST /collections/{cid}/citations/{oid}/review/intent` | `ReviewIn` | `Intent` |
| `POST /collections/{cid}/citations/{oid}/review/confirm` | `ReviewIn` + token header | `Citation` |
| `POST /collections/{cid}/citations/{oid}/file-review/intent` | `FileReviewIn` (`work_file_id`, `file_kind`, `action`, `expected_revision`, `idempotency_key`) | `Intent` |
| `POST /collections/{cid}/citations/{oid}/file-review/confirm` | `FileReviewIn` + token header | `Citation` |
| `POST /collections/{cid}/sync` | | `Collection` (retry folder sync) |
| `GET /collections/{cid}/jobs/{jid}` | | `JobStatus` |
| `GET /collections/{cid}/files/{kind}/{id}` | `kind in {source, work_file, local_copy, citation_file}` | PDF, `inline` disposition |

Every route needs a session. Every mutation (`POST`, `PATCH`, `DELETE`) also needs a CSRF token.

Errors: existing body `{code, message, retryable, recovery_hint, details?, retry_after?}`
and status codes from `app.py` `rc_error_handler`.

## 6. Flows

**Permissions.** A collection needs three settings changes. Each change is one intent and
one confirmation, in this order: `add_allowed_folder` (path = collection folder),
`set_project_network` (mode `retrieval`), `set_project_downloads` (enabled true). The UI
shows all three with their state and a "Grant" button per row. Each button calls intent,
shows the summary, and confirms on a second click. The server never confirms on its own.
After each confirm the UI uses the new `settings_revision`.

**Upload.** `upload_pdf(purpose="source")`, then `start_job(stages=[extract],
scope=SourceScope(source))`, then folder sync of the source. Duplicate PDFs (same hash)
return the existing paper.

**Find metadata.** `start_job(stages=[resolve, discover_access])` with source scope (one
paper) or all sources (collection).

**Download.** `download_files` with an occurrence scope of every citation where
`can_download` is true. With no such citations, or no permission, return
`Accepted(job_id=None, disabled_reason=...)`. After the job reaches a terminal state, the
next `GET /shelf` or `GET /papers/{pid}` runs folder sync for new verified work files.

**Review.** Intent then confirm with the token, using the existing
`prepare_web_review` / `confirm_web_review` service methods. One citation per confirmation.

**Polling.** While a job is not terminal, the UI polls `GET /jobs/{jid}` after
`next_poll_after_ms`, then refreshes the affected paper. Stop polling when the view
unmounts.

## 7. Serving

`GET /` and unknown non-API paths under `/` serve `static/library/index.html`. Assets
under `/static/library/`. `GET /advanced` serves the existing `static/index.html`; the old
dashboard's asset URLs keep working. No inline script or style in built output.

## 8. Amendments after W1 (22 October 2026, D-44)

These override sections 1-7 where they differ.

1. **TypeScript:** pinned to 6.0.3, because `typescript-eslint` 8.72.0 supports only `<6.1.0`.
   Move to 7.x when it supports it.
2. **Defaults:** new `GET /api/library/defaults` returns `{"default_root": "<abs path>"}`, where
   the default root is `Path.home() / "RefCollector"`. The create dialog proposes
   `<default_root>/<name>`.
3. **Run scope:** `RunIn` gains `citation_id: str | None = None`. Precedence: `citation_id`, then
   `paper_id`, then the whole collection. Find metadata uses an occurrence scope for one citation.
4. **Shelf status filter:** an uploaded paper matches when at least one of its citations has
   that status. A downloaded paper matches only `status=downloaded`.
5. **Intent:** gains `description: list[str]`, readable lines built by the server, for example
   "Allow RefCollector to write to C:/Users/me/RefCollector/Thesis". The UI shows
   `description`; `summary` stays for debugging.
6. **JobStatus:** gains `kind: Literal["extract", "find_metadata", "download"]` and
   `paper_ids: list[str]` (the uploaded papers in scope). Every card in `paper_ids` gets
   `active_job_id` while the job is not terminal. The server derives `kind` from the job
   stages; download jobs are the retrieve stage.
7. **CSRF:** the server returns `NOT_AUTHORIZED` for a missing or invalid CSRF token
   (`web/dependencies.py`). The client may refresh the session and retry once, never more.
8. **PDF framing:** responses of `GET /collections/{cid}/files/...` send
   `X-Frame-Options: SAMEORIGIN` and a CSP with `frame-ancestors 'self'`. Every other response
   keeps `DENY` / `'none'`. The library page CSP allows `frame-src 'self'`.
9. **Citation order:** `Citation.order` comes from the occurrence (`get_reference` detail),
   not from the summary row, which has no `order`. The list is sorted by it.

## 9. DOI-first extension (D-48, 23 October 2026)

The user approved immediate retrieval by a clear cited DOI, without metadata approval.
Passing PDFs become available automatically. Only reviewable failed PDF checks require
confirmation. This overrides the metadata-acceptance prerequisite for the explicit DOI
path only; normal accepted-work calls keep their default behavior.

The additive engine, adapter, and frontend interface is frozen in
[doi-first-contract.md](../doi-first-contract.md). Requirements and verification sequence:
[doi-first.md](../doi-first.md). Separate CitationFile records preserve metadata state.
Permissions, structural/network validation, version policy, stale-input protection, and
single-file confirmed review remain required. No automatic candidate acceptance is permitted.
