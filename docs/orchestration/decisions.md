# Orchestration decisions

Smallest reasonable reading where the spec or the brief is ambiguous. Each entry: decision, reason, where it matters.

| ID | Decision | Reason | Affects |
|---|---|---|---|
| D-01 | Spec moved from the repo root (untracked) to `docs/spec/refcollector-spec-v3.md`. | The orchestration brief names that path. | all briefs |
| D-02 | `make` is not installed on this Windows host. The check command is `python scripts/check.py` (ruff check, ruff format --check, pytest). Briefs say "make check" = this script. | No new tool needed; works on Windows and POSIX. | all WPs |
| D-03 | Baseline moves unchanged to `legacy/` (`legacy/refcollector.py`, `legacy/webapp/`, `legacy/tests/`). Its relative `sys.path` imports keep working with no code edit. | A root `refcollector.py` would shadow the new `refcollector` package on `sys.path`. Legacy code stays as the reference for the port. | Phase 1 |
| D-04 | Spec 14.2 lists a stack, not a package layout. The orchestrator defines a `src/refcollector/` layout (plan.md, "Package layout"). | Needed to assign owned paths. | Phase 1, all WPs |
| D-05 | No web app port in P0 (user, 2026-10-20). The web app moves to P1 (with the three-pane review UI). Until then the v0.1 web app runs unchanged from `legacy/webapp/` (`python legacy/webapp/app.py`) on the legacy module, not on the new services. `refcollector serve web` fails with `FEATURE_NOT_AVAILABLE`. FastAPI is not a P0 runtime dependency. | User decision on Q1. | WP-12, pyproject |
| D-06 | MCP resources (11.6) are "optional convenience" and are not built. The three P0 prompts are built. | Tools cover every workflow step. | WP-11 |
| D-07 | `*.pdf` is git-ignored, so test PDFs are generated at test time with PyMuPDF by a builder in `tests/fixtures/pdfs.py`. | No binary fixtures in git; no `.gitignore` exception. | WP-03, WP-08, Wave D |
| D-08 | Concurrency model: application services and repositories are synchronous (`sqlite3`, WAL, short transactions). The executor runs one asyncio loop in a background thread; providers use `httpx.AsyncClient` + `aiolimiter`. Step handlers are `async`; they call repositories through `anyio.to_thread`. | `aiolimiter` (spec 14.2) needs asyncio; SQLite stays simple and sync. | WP-01, 02, 04, 10 |
| D-09 | Dev-only dependencies: `pytest`, `respx`, `ruff`. Async tests use the `anyio` pytest plugin (ships with httpx). ULIDs come from a small in-repo generator (`ids.py`), not a package. | No runtime dependency outside 14.2. | Phase 1 |
| D-10 | Identity policy v1 reproduces v0.1 `assess()` thresholds. `rapidfuzz` token-set ratio is computed and stored as a feature; in v1 it only gates `MULTIPLE_CLOSE_CANDIDATES` (closeness margin 0.05 on the combined score). | Spec 9.2: "initial thresholds reproduce v0.1 assess()". | WP-06 |
| D-11 | Eligibility policy v1 "recognised repository": a location that Unpaywall or OpenAlex lists with `host_type = repository`. No separate repository list in P0. | Spec Appendix A, R5 leaves the list open for M1. | WP-07 |
| D-12 | New projects default to network mode `metadata`. `retrieval` must be set explicitly. The import allow-list is empty by default; `refcollector config allow-folder PATH` adds a folder. | Least privilege; W-03 needs a deny path. | WP-09, WP-12 |
| D-13 | Opaque list cursors encode (sort key, last id, issued_at); expired after 24 h → `INVALID_INPUT` with recovery hint. | Spec 11.2. | WP-09 |
| D-14 | Executor polling: clients poll for the lock every 5 s (spec 6.1); the executor polls `step` for queued work every 0.5 s. | Spec gives the first value only. | WP-02 |
| D-15 | `WORKER_MODEL = 9router/ag/gemini-3.8-flash` (user, 2026-10-20). | User answer to Q2. | all briefs |
| D-16 | Dev environment: the system Python (3.13.14, Microsoft Store) has no project deps. Phase 1 creates a project `.venv` with `uv sync` (uv 0.12.17); the check command is `uv run python scripts/check.py`. Host versions found 2026-10-20: Python 3.13.14, uv 0.12.17, pytest 9.1.1 (system), ruff 0.17.1 (via uvx), OpenCode 1.19.3 (Jean-managed, `opencode.cmd` shim on PATH in PowerShell, not in Git Bash), git 2.54.0. | Reproducible env; workers use the same command. | Phase 1, all WPs |
