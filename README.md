# Refcollector

Turn a paper's bibliography into a checked, traceable reference library.
Refcollector extracts references from PDFs, resolves their metadata, finds PDF
locations, checks downloaded files against the accepted work, and exports a
library with an audit trail. The main workflow needs no LLM.

**Status: Beta source release (`0.2.0b1`).** The application provides a CLI, an
MCP server over stdio, and a local web interface (Library UI and Advanced
dashboard). These interfaces share application services and `RC_HOME`. The older
web app remains in `legacy/` and uses separate code and settings. Original project
code uses **AGPL-3.0-only**; see [LICENSE](LICENSE) and [licensing notes](LICENSING.md).
The beta wheel and source archive build successfully, and a clean Windows wheel
installation passed the CLI smoke test. Public package release on PyPI remains
pending the quality benchmark and final release review. Installation below uses
the tagged source release.

## Features

- Import PDFs with a text layer or paste reference text.
- Resolve metadata with Crossref, DataCite, DOI.org, OpenAlex, Semantic Scholar
  and Open Library, subject to provider availability and configuration.
- Discover access locations through Unpaywall and OpenAlex.
- Review uncertain identities, edit references, and record explicit user decisions.
- Reuse verified local PDFs before making download requests.
- Validate PDF structure and identity evidence; keep uncertain files as
  `needs_review` instead of silently treating them as verified.
- Export BibTeX, CSL JSON, CSV, JSON, unresolved references, audit reports and manifests.
- Create copy-request drafts. Refcollector does not send messages.
- Optionally use a DOI landing-page fallback and an isolated visible browser.

## Requirements and installation

Python **3.11 or newer**, Git, and [uv 0.13.4 or newer](https://docs.astral.sh/uv/getting-started/installation/)
are required. Clean installations were checked on Windows with Python 3.11 and
3.13. Other operating systems were not verified in that installation matrix. Optional browser
workers on Windows require standalone Python, not Microsoft Store Python.

```powershell
git clone --branch v0.2.0b1 https://github.com/EinggrauMFW/refcollector.git
cd refcollector
uv sync --frozen
uv run python -m refcollector --help
```

Run the commands below from the repository folder. They work through the package
entry point, without activating the virtual environment. Published `uvx` or PyPI
installation is not yet an available release path; this beta release
requires installation from this source repository or a locally built wheel.

## Quick start: a PDF reference library

The examples use PowerShell and a project named `my-library`. Replace the email
with your real contact address. Replace `C:/papers` with an existing folder and
`thesis.pdf` with your document. Only allow-listed folders can supply input files.

```powershell
uv run python -m refcollector config set email "you@your-institution.org"
uv run python -m refcollector config allow-folder "C:/papers"
uv run python -m refcollector project create my-library
uv run python -m refcollector --project my-library import "C:/papers/thesis.pdf"
uv run python -m refcollector --project my-library run --wait
uv run python -m refcollector --project my-library list
```

`run` processes all sources in the selected project by default. Its stages are
`extract,resolve,discover_access`. To process one source, copy its `src_...` ID
from the import output:

```powershell
uv run python -m refcollector --project my-library run --source SOURCE_ID --wait
uv run python -m refcollector --project my-library sources
uv run python -m refcollector --project my-library source SOURCE_ID --json
uv run python -m refcollector --project my-library show OCCURRENCE_ID --json
```

Replace `SOURCE_ID`, `OCCURRENCE_ID`, and other uppercase placeholders with actual
IDs. Source readback includes extraction sections and warnings. Check possible
truncation warnings before relying on a reconstructed bibliography.

For pasted references, use `import --text "REFERENCE TEXT"` instead of a PDF path.
For extraction without provider requests, run with `--stages extract --wait`.

## Review uncertain matches

```powershell
uv run python -m refcollector --project my-library list --identity needs_review
uv run python -m refcollector --project my-library list --identity unresolved
uv run python -m refcollector --project my-library review
```

Interactive review requires a terminal. Normal CLI automation and MCP tools
cannot manufacture a recorded human approval. Automatic acceptance is an
assessment, not a guarantee; inspect doubtful records and their reasons.

## Download reference PDFs

Projects start in metadata network mode. Enable retrieval and the project
download grant explicitly:

```powershell
uv run python -m refcollector --project my-library config network retrieval
uv run python -m refcollector --project my-library config downloads on
uv run python -m refcollector --project my-library download --max-files 10 --max-bytes 100000000 --wait
```

Without a source or occurrence filter, `download` selects the project's accepted
references. The limits bound saved file count and bytes for the job; they are
not a total network-traffic quota. Existing usable local copies take priority.
Use `--source SOURCE_ID` or repeat `--occurrence OCCURRENCE_ID` to narrow the scope.

Default version policy is `prefer_published`: published version, then accepted
manuscript. `any_eligible` permits other eligible versions. `cited_version_only`
requires matching version evidence. Unknown-version fallback copies require an
explicit `--version-policy any_eligible`.

```powershell
uv run python -m refcollector --project my-library list --retrieval verified
uv run python -m refcollector --project my-library list --retrieval needs_review
uv run python -m refcollector --project my-library config downloads off
```

A saved review PDF does not trigger another automatic DOI fallback. Review it
before treating it as the cited work. Reachability does not establish an
open-access classification, a licence, or redistribution rights.

## Optional DOI and browser fallback

Fallback is **disabled by default**. Choose one built-in endpoint:

```powershell
uv run python -m refcollector config pdf-fallback st
uv run python -m refcollector --project my-library download --source SOURCE_ID --version-policy any_eligible --wait
uv run python -m refcollector config pdf-fallback off
```

| Option | DOI URL template |
|---|---|
| `box` | `https://sci-hub.box/{doi}` |
| `su` | `https://sci-hub.su/{doi}` |
| `st` | `https://sci-hub.st/{doi}` |
| `ru` | `https://sci-hub.ru/{doi}` |

Custom HTTPS templates with one `{doi}` path placeholder also work. No endpoint
rotation occurs. The obsolete `.se` endpoint is not a preset.

For pages that need JavaScript, the optional browser worker uses SeleniumBase
**4.56.2** in a separate Python environment and an explicit visible Chromium
binary. It inspects the page, then clicks the recognized robot checkbox once.
No LLM or generic CAPTCHA solver runs. Configuration takes this form:

```powershell
uv run python -m refcollector config pdf-browser "C:/path/to/browser-env/Scripts/python.exe" --binary "C:/path/to/chrome.exe"
uv run python -m refcollector config pdf-browser off
```

These executable paths must exist. On Windows, Microsoft Store Python is rejected
for browser workers because Chromium could escape process cleanup. The core CLI
can still use Microsoft Store Python. See the [full fallback setup and limits](docs/orchestration/pdf-fallback.md)
for isolated dependency installation, transport checks and verified behavior.

Live browser verification against a configured Sci-Hub fallback endpoint confirmed
one bounded automated robot-challenge interaction and valid primary DOI PDF retrieval.
However, upstream remote mirrors frequently change, experience downtime, or return
`/storage/zero/` error links. Therefore, no reliability, uptime, or availability guarantee
exists. A passed checkbox does not guarantee a working or valid PDF. Browser cookies are
not exported to the PDF downloader. Settings changes apply to new jobs; existing
jobs retain their configuration snapshots.

## Export and inspect results

```powershell
uv run python -m refcollector --project my-library export --format bibtex --format csl_json --format csv --format json --wait
uv run python -m refcollector --project my-library export --audit --wait
uv run python -m refcollector --project my-library artifacts --json
```

Default exports select accepted references. `--audit` selects the complete audit
view and writes an audit report. Export artifacts include a manifest with hashes;
`artifacts` reports their names and local paths. Review-only PDFs remain distinct
from verified files.

To read a text artifact through the CLI:

```powershell
uv run python -m refcollector --project my-library artifact read ARTIFACT_ID
```

## Jobs and recovery

```powershell
uv run python -m refcollector --project my-library jobs
uv run python -m refcollector --project my-library jobs JOB_ID --json
uv run python -m refcollector --project my-library jobs JOB_ID --pause
uv run python -m refcollector --project my-library jobs JOB_ID --resume
uv run python -m refcollector --project my-library jobs JOB_ID --retry-failed
uv run python -m refcollector --project my-library jobs JOB_ID --cancel
```

Run without `--wait` to queue work and receive a job ID. A long-lived executor can
continue queued work with `uv run python -m refcollector serve`. Only one executor
owns a data home at a time. Jobs preserve checkpoints and frozen settings.

## MCP server

Start the stdio server with:

```powershell
uv run python -m refcollector serve mcp
```

For clients that use a `mcpServers` configuration, adapt this example. Replace the
repository and data paths with absolute paths on your computer:

```json
{
  "mcpServers": {
    "refcollector": {
      "command": "uv",
      "args": [
        "--directory", "C:/path/to/refcollector",
        "run", "python", "-m", "refcollector", "serve", "mcp"
      ],
      "env": {
        "RC_HOME": "C:/path/to/refcollector-data"
      }
    }
  }
}
```

Use the same `RC_HOME` for CLI setup and MCP when sharing projects. Default MCP
scopes exclude `files:download`; a project download grant alone does not add that
scope. MCP cannot raise its own scopes, enable retrieval, select browser
executables, or record human approval. Configure trusted local permissions
before a client starts. To permit MCP downloads, edit `RC_HOME/config.json`
locally and append `"files:download"` to its `mcp_scopes` list, retaining the other
entries. Restart the server after editing. The current server uses stdio, not a
hosted HTTP endpoint.

## Settings, storage and credentials

`RC_HOME` selects the application data folder. Without it, Refcollector uses the
platform's user-data directory. It contains `config.json`, a SQLite database,
temporary files, stored file bytes and artifacts. Keep this folder outside the
source repository. To select a separate data home in PowerShell:

```powershell
$env:RC_HOME = "C:/path/to/refcollector-data"
uv run python -m refcollector config show --json
```

Set `RC_HOME` before creating projects or changing settings. Switching it selects
a different library. Project decisions and file grants remain project-scoped.
Use `config network offline` for cache-only operation; metadata mode permits
provider lookup, and retrieval mode permits downloads when grants also allow them.

The configured contact email appears in provider requests and the normal HTTP
User-Agent. API keys are stored in the operating-system keyring. Supported key
names are `openalex_key` and `s2_key`; input is hidden:

```powershell
uv run python -m refcollector config set-key openalex_key
uv run python -m refcollector config set-key s2_key
```

A provider without a required credential, or one that imposes rate limits, can
reduce lookup coverage. Inspect warnings instead of treating missing results as
proof that a work or copy does not exist.

## Verification and development

```powershell
uv sync --extra web --frozen
uv run --extra web --frozen python scripts/check.py
node --test tests/unit/web/api_client.test.cjs
```

The check script runs Ruff, formatting checks and pytest. Node.js is needed for
the separate JavaScript client tests. Install the `web` extra to include the HTTP
interface tests.

The beta `scripts/check.py` gate on **24 October 2026** passed **1,559 Python
tests**, Ruff lint, and formatting checks for 239 Python files. Two existing
HTTP client deprecation warnings remain. The final web API version-label change
also passed 15 focused HTTP tests. The Library passed 72 frontend tests,
TypeScript checking, ESLint, and four Node API client tests.

Live Windows browser checks covered collection controls, candidate confirmation,
Library/Advanced context switching, shared themes, and PDF viewing. One authorized
DOI-first fallback attempt downloaded and verified a five-page PDF without metadata
approval. Its stored, exported, and browser-streamed hashes matched. This is one
successful attempt, not a retrieval success-rate benchmark.

Earlier blank-separator checks passed fresh core-only wheel installations
on Windows Python 3.11.17 and 3.13.15, and a source installation on 3.13.15. Each
case passed 11 CLI commands, native PDF extraction, MCP module import, and the
real digital-thesis reproduction. These follow-up checks did not retest live web behavior.

A subsequent live HTTP check at <http://127.0.0.1:8001/> passed PDF upload,
extraction, page rendering, source downloads, and complete-audit JSON/CSV exports
with 312 rows each. Source and artifact download hashes matched. This follow-up
did not complete a browser UI test. See [current corpus status](benchmarks/pilot/corpus-status.md).

Earlier clean wheel installations passed on Windows Python 3.11.17 and 3.13.15. A source
archive installation and a core-only wheel installation also passed on Python
3.13.15. The checks ran outside the checkout and covered CLI extraction/export,
native PDF rendering, MCP module import and, with the web extra, in-process HTTP
checks. Fresh dependency resolution selected FastAPI 0.159.1; its 49 focused web
tests passed. These checks do not measure extraction accuracy, identity precision
or live retrieval coverage. See [release readiness](docs/orchestration/release-readiness.md).

Useful references:

- [Current specification](docs/spec/refcollector-spec-v3.md)
- [Integration status and verification limits](docs/orchestration/status.md)
- [PDF fallback setup](docs/orchestration/pdf-fallback.md)

## Known limits and distribution status

- Scanned bibliographies need OCR, which the current application does not provide.
- PDF layout can split, merge or truncate references. Inspect extraction warnings.
- Automatic identities and downloaded PDFs can require review.
- Provider outages, credentials, rate limits and mirror changes affect live results.
- The browser fallback is not a general challenge solver or a guaranteed downloader.
- The project owner selected AGPLv3-only licensing. Source-and-wheel licence files,
  dependency notices and clean Windows installations were checked. Quality
  benchmarks and final release review remain open. Bundled installers and hosted
  deployments need separate compliance checks. See [licensing notes](LICENSING.md),
  [third-party notices](THIRD_PARTY_NOTICES.md) and the
  [dependency review](docs/orchestration/license-dependency-review.md).

## Local web app (beta)

Install the optional web dependencies and start the current interface on port
8001. This leaves port 8000 available for the legacy app:

```powershell
uv sync --extra web --frozen
uv run --extra web --frozen python -m refcollector serve web --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/>. Without `--port`, the current server defaults to
8000, which is also the legacy default. Use a free port for each server.
The current server binds to loopback and uses the same `RC_HOME` as the CLI/MCP
when started with the same environment.

For a separate test library, set `RC_HOME` before starting the server:

```powershell
$env:RC_HOME = "C:/path/to/refcollector-web-test-data"
uv run --extra web --frozen python -m refcollector serve web --host 127.0.0.1 --port 8001
```

This separates library data and configuration. API keys still use the operating-
system keyring; `RC_HOME` does not create a separate credential store.

The root page opens the **Library**. The existing project dashboard remains at
<http://127.0.0.1:8001/advanced>. The frontend assets are included in the Python
package; running the installed web app does not require Node.js.

### Library workflow

1. Select **New collection**, enter a name, and choose its folder. Existing collections
   can be renamed, pinned to the top of the sidebar, or deleted. Deleting a collection
   removes the project record from RefCollector, but preserves the confirmed collection
   folder and all its files on disk.
2. Open **Review permissions**. Confirm folder access, retrieval network mode,
   and download permission separately. Creating a collection grants none of them.
3. Choose **Upload PDFs** or drop a PDF onto the shelf. Wait for extraction,
   then expand the uploaded paper to inspect its citations.
4. To download from cited DOIs immediately, choose **Download PDFs by DOI** for
   the collection or paper, or **Download PDF by DOI** for one citation. The configured
   DOI service must be enabled. Metadata approval and provider agreement are not required.
   A citation must contain one unambiguous DOI. Passing PDFs become available automatically.
5. **Find metadata** remains available separately. Its candidate warnings do not block
   DOI downloads or opening a passing PDF. When accepting a suggested candidate, the
   candidate panel automatically collapses and returns focus to the citation row.
   Identity approval does not approve a PDF.
6. **Download all** retains the normal accepted-metadata path. Inspect terminal warnings; some citations may have
   no eligible copy or may fail validation.
7. Only a PDF that fails reviewable file checks needs the file panel. Open it, read the failed
   checks, then choose **Approve PDF** or **Reject PDF** and confirm the decision.
   Each confirmation applies to one file. Approval is recorded in the audit.
8. Verified PDFs appear on the shelf with **Cited by** links and are copied to the
   collection folder. Open a downloaded citation or paper card to view its PDF in the
   in-app viewer. Repeating a download reuses existing copies rather than creating duplicate files.
9. Switch between the **Library** and the **Advanced dashboard** (`/advanced`) at any time.
   Theme settings (Light / Dark) are shared across both interfaces.

DOI-first downloads verify against the DOI in the citation, independently of disputed
provider metadata. Network and PDF safety checks still apply. A DOI found only in a
PDF's reference list does not establish that the PDF is the requested paper.

The engine store under `RC_HOME` remains authoritative. The collection folder is
a derived copy. If a card says **Not in folder**, check folder access and retry sync.
For status descriptions and troubleshooting, see the [user guide](docs/USER_GUIDE.md).

### Advanced dashboard workflow

Open <http://127.0.0.1:8001/advanced> for project, source, job and reference monitoring,
managed PDF uploads, safe raster page previews, reference repair and review,
confirmed settings changes, exports and request drafts. Rotated pages display
without citation highlight overlays.

1. Open **Projects**, create a project, and select it in the header.
2. Open **Sources** and choose **Import Source**. Paste reference text or upload
   a PDF. Browser uploads do not require adding the original folder to the server
   allow-list.
3. Open **Jobs** and start extraction for the source. Add metadata resolution and
   access discovery when needed. Inspect the terminal state and warnings.
4. Open **References** to search, inspect and repair citations. Attachment requires
   an accepted identity. Select an accurate version hint when attaching a PDF;
   an unknown version can fail the version check. Inspect the resulting copy state.
5. Use **Download Copy** to save attached PDF bytes. A successful download does not
   change an unusable copy into a usable one.
6. Open **Artifacts**, choose **Create Export**, select formats and scope, then
   queue the job. Preview supported text artifacts or download the output files.

Settings changes require a separate confirmation. Cancel leaves the stored value
unchanged. A project selection is required for project network and download
settings. Export preparation reads the current project revision; a later conflict
requires reopening the dialog instead of silently retrying a changed project.

### Verified browser coverage

Live Chromium checks on **21–22 October 2026** used <http://127.0.0.1:8001/> with
a separate data home. They covered project creation, text import, PDF upload,
extraction, raster page and scale changes, reference search, settings cancellation
and confirmation, one-citation resolution, attachment, exports and downloads.
Direct navigation to References also loaded the system status badges.

The test PDF had 18 pages and 24,910,024 bytes. Extraction produced 119 references
with a possible truncated-reference warning. A separate text citation produced
one reference. The published-version attachment became usable. The downloaded
PDF matched the original SHA256. BibTeX, CSL JSON, CSV and manifest creation
completed; CSL JSON preview and a hash-matched BibTeX download passed.

Resolution completed with OpenAlex and Semantic Scholar warnings. This browser
run did not prove successful author-request draft generation, access discovery,
remote PDF retrieval or browser fallback challenge handling. It verified draft
prerequisite rejection. These results do not establish full web acceptance or
release readiness.

When using Jean, configure the web startup command as a Run command before browser
verification, then start it through Jean's Run environment. Keep the port and
`RC_HOME` consistent with the configured command. The
[web specification](docs/spec/web-app-spec.md) defines the acceptance requirements.

## Legacy web app

The previous application remains available separately. From the repository root,
run it with its web dependencies in a uv overlay:

```powershell
uv run --with-requirements legacy/requirements.txt --with "pymupdf<2" python legacy/webapp/app.py
```

Plain `uv run python legacy/webapp/app.py` uses only the core dependencies and
can fail with `ModuleNotFoundError: No module named 'requests'`. The additional
requirements are invocation-specific; they are not added to the core package.

Open <http://127.0.0.1:8000>. These commands run the legacy implementation, not the
current CLI/MCP services or browser fallback. Web settings are separate from
`RC_HOME`: open Settings, enter your contact email, and press Save before uploading
a PDF. The web app requires that email even if the CLI already has one.
The [legacy user guide](docs/USER_GUIDE.md) describes that interface.

## Repository layout

```text
src/refcollector/       Current CLI, MCP server, services and executor
src/refcollector/web/   Optional local HTTP interface and static assets
src/refcollector/files/ PDF verification, storage checks and fallback resolvers
tests/                 Offline unit, contract, pipeline and acceptance tests
scripts/check.py       Combined development gate
docs/spec/             Specification and release requirements
docs/orchestration/    Decisions, integration evidence and work-package records
legacy/                Previous CLI and web application
```
