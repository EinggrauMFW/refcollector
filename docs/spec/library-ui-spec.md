# RefCollector Library UI specification

Date: 22 October 2026. Status: draft for W0 contract freeze. Owner decisions: user, 22 October 2026.

## 1. Goal

Give RefCollector a simple library interface for academic papers, theses, articles and
dissertations. The user uploads a PDF, gets the list of all its citations, finds their
metadata, downloads their PDFs, and opens each downloaded PDF from its citation.

The engine (extract, resolve, discover_access, retrieve, export) stays unchanged. This
specification adds a new UI, a thin library HTTP API and a collection-folder sync. The
existing web dashboard moves to `/advanced` and keeps working.

## 2. Owner decisions

| Topic | Decision |
|---|---|
| Shelf items | Uploaded papers and downloaded cited papers, in the same collection |
| Grouping | Named collections. One collection = one engine project |
| Storage | Each collection has a real folder on disk. Uploaded and downloaded PDFs are copied there |
| Manual steps | Citation extraction runs automatically after upload. "Find metadata" and "Download all" are user buttons |
| Review | Automatic acceptance where the engine allows it. Uncertain citations get a "Check" badge and are fixed in the expanded row. No separate review screen |
| Stack | React + Vite. Built assets are committed into the Python package |
| Old UI | Kept at `/advanced` |

## 3. Concepts

| User term | Engine object | Notes |
|---|---|---|
| Collection | `Project` plus a collection folder | Folder path is stored with the collection |
| Uploaded paper | `Source` (type PDF) | Metadata v1: file name, pages, import date, citation count |
| Downloaded paper | Verified `WorkFile` with its `ProjectWork` metadata | One shelf item per work, even if several uploaded papers cite it |
| Citation | `Occurrence` plus its resolved work, candidates and access state | Shown inside an expanded uploaded paper |
| Check badge | Citation that needs a human: conflict, close candidates, needs_review retrieval | Fixed one at a time |
| Cited by | Link from a downloaded paper to every uploaded paper that cites its work | |

## 4. User flow

1. **Create collection.** Name and folder. Default folder: `~/RefCollector/<name>/`. The
   dialog shows the permissions the collection needs: folder grant, project download grant,
   and retrieval network mode. The user confirms once. The server applies them through the
   existing trusted web-settings intent (`prepare_web_settings` / `update_web_settings`).
   No permission is granted without this confirmation (D-23 amendment, web spec section 9).
2. **Upload.** Drag PDFs onto the shelf or use the upload button. Each upload calls
   `upload_pdf(purpose="source")` and then `start_job(stages=[extract])` for that source.
   The card shows progress and then "N citations".
3. **Expand a paper.** One click expands the card in place. It shows metadata and the
   citation list. Only one card is expanded at a time. The URL keeps the expanded paper id.
4. **Find metadata.** Per paper or per collection. Runs `start_job(stages=[resolve,
   discover_access])` on the source scope. Rows update while the job runs.
5. **Check.** A citation with a Check badge expands to show the candidates, scores and
   reasons. Accept or reject uses `prepare_web_review` and an explicit confirmation. One
   citation per confirmation; no bulk approval (web spec section 8).
6. **Download all.** Per paper or per collection. Calls `download_files` for every
   citation whose access state allows retrieval. Disabled with a clear reason when the
   download grant or network mode does not allow retrieval.
7. **Folder sync.** After an upload or download job finishes, the server copies the new
   PDFs into the collection folder (section 7). Downloaded papers then show on the shelf.
8. **Open PDF.** Clicking a downloaded citation's title, or a paper card's "Open"
   action, opens the PDF in an in-app viewer (browser PDF viewer in an iframe or new tab,
   from the library file route). "Copy path" copies the file's path in the collection folder.
9. **Search and filter.** Text search over title, authors, year, venue and DOI. Filters:
   kind (uploaded / downloaded), status (needs check, missing PDF, downloaded).

## 5. Citation status

One status per citation row. The W0 contract freeze maps each status to exact engine fields
and records the mapping in `docs/orchestration/library/contract.md`.

| Status | Meaning | Primary action |
|---|---|---|
| `extracted` | Citation text found; no metadata yet | Find metadata |
| `working` | A job is processing this citation | none (spinner) |
| `matched` | Work resolved and accepted | Download |
| `check` | Needs a human decision | Open check panel |
| `no_match` | Resolve finished without an acceptable work | Show candidates if any |
| `pdf_available` | Eligible copy found, not downloaded | Download |
| `no_pdf` | No eligible copy found | none; show reason |
| `downloaded` | Verified file exists | Open PDF |
| `failed` | Last step failed | Show error and retry |

### 5.1 File approval (D-46)

A downloaded PDF that fails the identity checks (engine spec section 9, checks 4-6) shows a Check
badge of kind `file`. The user opens the PDF, reads the failed checks, and approves or rejects it
with a two-step confirmation. Approval makes the file verified, copies it to the collection folder
and puts it on the shelf. This amends engine spec section 9 for explicit user approval only.

## 6. Library HTTP API

Prefix: `/api/library`. JSON only, except the file route and upload. Same session cookie,
CSRF header, Host check and CSP as the existing web routes. Routes call service methods
only: no storage, pipeline or filesystem logic in route functions except the
folder-sync service call.

| Method and path | Service calls | Returns |
|---|---|---|
| `GET /collections` | `list_projects`, `get_settings` | Collections with folder, counts, effective download/network state |
| `POST /collections` | `create_project`, `prepare_web_settings` | Collection id plus a permission intent to confirm |
| `POST /collections/{cid}/permissions/confirm` | `update_web_settings` | Effective permissions |
| `GET /collections/{cid}/shelf?q=&kind=&status=&after=` | `list_sources`, `list_references`, work files | Paper cards (paged) |
| `POST /collections/{cid}/papers` (multipart) | `upload_pdf`, `start_job(extract)`, folder sync | Paper card plus job id |
| `GET /collections/{cid}/papers/{pid}` | `get_source` / work lookup, `list_references` | Paper detail with citations |
| `POST /collections/{cid}/find-metadata` body `{paper_id?}` | `start_job(resolve, discover_access)` | Job id |
| `POST /collections/{cid}/download` body `{paper_id?}` | `download_files` | Job id or disabled reason |
| `GET /collections/{cid}/citations/{oid}` | `get_reference` | Candidates, scores, reasons, revision |
| `POST /collections/{cid}/citations/{oid}/review` | `prepare_web_review` | Review intent to confirm |
| `POST /collections/{cid}/citations/{oid}/review/confirm` | existing confirmed web review path | Updated citation |
| `GET /collections/{cid}/jobs/{jid}` | `get_job` | Progress, stage counts, terminal state |
| `GET /collections/{cid}/files/{kind}/{id}` | `read_project_file` | PDF stream, `Content-Disposition: inline` |

DTOs are Pydantic models in `src/refcollector/web/library_api.py` and mirrored as
TypeScript types in `web-ui/src/api/types.ts`. W0 freezes both. Error bodies reuse the
existing web error shape (`code`, `message`, `recovery_hint`).

Paper card fields: `paper_id`, `kind`, `title`, `authors`, `year`, `venue`, `doi`,
`file_name`, `folder_path`, `citation_count`, `status_counts`, `cited_by`, `active_job_id`.
Uploaded papers without parsed metadata use the file name as title.

## 7. Collection folder sync

- The engine file store under `RC_HOME` stays the single source of truth.
- The collection folder is a derived copy. The `RC_HOME` read guard in
  `read_project_file` does not change; the file route reads from the engine store.
- Sync copies uploaded sources (original file name) and verified work files
  (`WorkFile.export_name`) into the collection folder root.
- Idempotent: a file whose SHA256 already exists in the folder is not copied again.
- Never overwrite: a name collision with different content gets ` (2)`, ` (3)` suffixes.
- Writes only inside the confirmed collection folder. Resolve the path and reject
  symlink or `..` escapes. Write to a temp file in the same folder, then rename.
- A sync failure does not fail the job. The paper card shows "Not in folder" with a retry.
- Sync records its mapping (sha256 → folder file name) so the UI can show `folder_path`.

## 8. UI and UX

- App screen, not a landing page. Follow the app-screen rules of the design-taste-frontend
  skill (`references/app-screens.md`, `draw.mjs --app`, `inspect.mjs --app`).
- Library feel: calm, paper-first, readable type, generous row height, no dashboard noise.
- Layout: collection sidebar (left), shelf list (centre), PDF viewer opens as an overlay or
  side panel. Keyboard: arrows move between papers, Enter expands, `/` focuses search.
- Every async action shows progress and a clear terminal state. Errors show the server
  `message` and `recovery_hint`.
- Accessibility: semantic list and disclosure markup, focus management on expand, visible
  focus, WCAG AA contrast, no colour-only status.
- Light and dark theme.

## 9. Packaging and serving

- Source: `web-ui/` (React, Vite, TypeScript). Use the current stable Vite major and
  React; the W0 brief records exact versions from official docs.
- Build output: `src/refcollector/web/static/library/`, committed. `pip install` needs no Node.
- `GET /` serves the library app. `GET /advanced` serves the existing dashboard unchanged.
- Development: Vite dev server proxies `/api` to the running FastAPI server.
- CSP stays strict: no inline scripts or styles in built output.

## 10. Out of scope (v1)

Tags and notes, duplicate merging across collections, cloud sync, OCR for scans, editing
citation text, resolving the uploaded paper's own metadata, opening the OS file explorer
from the server.

## 11. Acceptance

1. Create a collection, confirm permissions, upload a development PDF: citations appear.
2. Find metadata: statuses change; at least one Check row can be accepted with confirmation.
3. Download all: verified PDFs appear in the collection folder and on the shelf with
   "cited by".
4. Click a downloaded citation: the PDF opens in the viewer.
5. Re-run Download all: no duplicate files in the folder.
6. `/advanced` still passes the existing Selenium check.
7. Python tests for the library API and folder sync pass; the full suite passes once at
   the end; ruff passes; `npm run build` and `npm run lint` pass.
8. A Selenium end-to-end script covers steps 1 to 5 against the Jean run environment.
