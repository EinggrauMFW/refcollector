# Library UI orchestration plan

Date: 22 October 2026. Target: [Library UI specification](../../spec/library-ui-spec.md).
Workspace: Jean base session `integration` (worktree id `9ed8e368-047d-49c1-b78a-ed72ae13141e`).
No new worktrees, commits or pushes unless the user asks.

## Roles and models

| Role | Backend and model | Effort | Scope |
|---|---|---|---|
| Orchestrator A | Claude, Opus 5.5 | medium | Briefs, contract freeze, review, integration, status |
| Orchestrator B | Codex, `gpt-6.1-sol` | medium | Same as A. Takes over when A is rate-limited, and the reverse |
| Backend worker | Follows the active orchestrator rule below | high | Python: library API, folder sync, serving, packaging, Python tests |
| UI/UX worker | Follows the active orchestrator rule below | high | `web-ui/**` only: React app, design, TypeScript tests, Selenium flow |

Worker model selection (D-49, supersedes D-47):

- Use only Antigravity `antigravity/gemini-3.8-flash-high`, high effort, for all
  worker tasks, including backend, UI, testing, and review.
- Keep the active orchestrator unchanged. This worker rule saves GPT token usage.
- Preserve completed work, ownership, briefs, and evidence when changing backends.
- Pass backend, model, and effortLevel explicitly on every dispatch and follow-up.
- Do not resume a GPT worker unless the user changes this rule.

Dispatch every worker as a Jean session in the `integration` worktree (Jean MCP
`create_session` / `send_chat_message`). Do not use in-process subagents for wave work:
the other orchestrator cannot see them. Give each worker a small, testable unit.

## Handoff protocol

Shared state lives in two files. The active orchestrator updates both after every dispatch,
review or decision.

- `docs/orchestration/library/status.md`: wave table, worker session ids, last result,
  next action. One row per work unit.
- `docs/orchestration/library/HANDOFF.md`: who is active, what is in flight, open
  questions, last verified commands. Overwrite, do not append history.

Takeover steps for the incoming orchestrator:
1. Read `HANDOFF.md`, `status.md`, this plan, the spec and `contract.md`.
2. Call Jean `list_sessions` / `get_session_status` for every in-flight worker session.
3. Read worker results with `read_session_messages`. Do not redispatch work that finished.
4. Write "Active: <orchestrator>" in `HANDOFF.md`, then continue at "Next action".

Never edit a file a running worker owns. Never run the whole suite while workers edit.

## Ownership

| Owner | Files |
|---|---|
| Orchestrators | `docs/spec/library-ui-spec.md`, `docs/orchestration/library/**`, `docs/orchestration/decisions.md` |
| Backend worker | `src/refcollector/web/library_api.py` (new), `src/refcollector/web/library_routes.py` (new), `src/refcollector/services/library_folder.py` (new), `src/refcollector/web/app.py`, `src/refcollector/web/security.py` (CSP only if needed), `pyproject.toml` (package data only), `tests/unit/web/test_library_*.py`, `tests/unit/services/test_library_folder.py` |
| UI/UX worker | `web-ui/**`, `src/refcollector/web/static/library/**` (build output only), `.ai/selenium-ui-check/library_flow.py` |
| Nobody in this project | Engine code (`services/service.py`, `contracts/**`, `storage/**`, `executor/**`, `pipeline/**`), old static UI `src/refcollector/web/static/{index.html,app.js,app.css}` except moving its route to `/advanced` |

A worker who needs a change outside its files stops and asks the orchestrator. An engine
change needs a decision row in `decisions.md` first.

## Waves

### W0: contract freeze (orchestrator)
- Read `domain/enums.py`, `contracts/api.py` and the current web routes. Map each citation
  status in spec section 5 to exact engine fields.
- Write `contract.md`: endpoint list, Pydantic DTO definitions, TypeScript mirror, status
  mapping, error shape, polling rules.
- Check current React and Vite versions in official docs and record them in `contract.md`.
- Gate: user approves `contract.md`.

### W1: parallel build
- **B1 (backend):** `library_api.py` DTOs, `library_folder.py` sync service with tests
  (idempotency, no overwrite, collision suffix, path escape rejection).
- **B2 (backend):** `library_routes.py` read routes (`collections`, `shelf`, `papers/{id}`,
  `citations/{id}`, `jobs/{id}`, `files`) with tests against the fake service.
- **U1 (UI):** Vite + React + TypeScript scaffold, design direction from the
  design-taste-frontend app-screen run, shelf, expanded paper, citation rows, Check panel,
  PDF viewer. Uses a mock API built from the TypeScript contract.
- Gate: B1 and B2 tests pass; U1 builds, lints and is reviewed on screenshots.

### W2: integration
- **B3 (backend):** action routes (create collection and permission confirm, upload,
  find-metadata, download, review and confirm), folder sync calls after jobs, `/` serves
  the library app, `/advanced` serves the old dashboard, CSP check for built assets.
- **U2 (UI):** replace the mock with the real API, polling, error states, empty states.
- Gate: narrow tests for touched files; live check on the Jean run environment
  (`http://127.0.0.1:8001/`).

### W3: verification and review
- **U3 (UI):** `.ai/selenium-ui-check/library_flow.py` covering spec section 11 steps 1-5.
- Orchestrator: run the existing `/advanced` Selenium check, the full Python suite once,
  ruff, `npm run build`, `npm run lint`. Run a code review on the diff. Update README and
  USER_GUIDE.
- Gate: every acceptance item in spec section 11 has evidence in `status.md`.

## Worker brief rules

Every brief states: owned files, inputs, exact acceptance commands, and "outline your
approach before editing". Workers read official docs before using an external API or
library. Workers report files changed, commands run with results, and remaining gaps. A
worker never starts a server by hand: use Jean `get_run_environments` /
`start_run_environment`.
