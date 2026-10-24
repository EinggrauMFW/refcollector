# Web application wave 1: frozen execution brief

Date: 21 October 2026. Orchestrator: Codex. Workers: Jean Antigravity, `antigravity/gemini-3.8-flash-high`, High effort.

Authority: user approved web orchestration and parallel workers. Target: [web specification](../../spec/web-app-spec.md). Continue in integration; no new worktrees, commits, pushes, or model substitutions.

## Shared rules

Use one bootstrap handle, in-process service calls, one executor owner per home, and no route-level storage/pipeline logic. Preserve existing CLI/MCP callers and TOOLS. Read current official docs before external APIs. Use UTF-8 for repository text. Do not edit README.md, docs/USER_GUIDE.md, the specification, another worker's files, or .ai/todo.md.

Workers must outline their approach before editing. Use focused regression tests for behavior changes. No server starts by hand. Call Jean get_run_environments before live checks; use only configured Jean Run commands. No whole-suite runs during parallel edits. Report exact files, commands, results, and remaining gaps. A completed design is not completed implementation.

## Exclusive ownership

| Worker/session | Owned files |
| --- | --- |
| Contracts/service: 64d933db-f79f-4c54-b501-00ad08c7bc6a | contracts/api.py, contracts/ports.py, services/service.py, storage/repos.py, executor/queue.py, executor/lock.py, executor/loop.py, bootstrap.py, additive storage migration if required; tests/unit/services/test_web_monitoring.py, tests/unit/services/test_web_files.py, tests/unit/executor/test_web_status.py; tests/fakes/service.py only if necessary |
| HTTP/security: 1b0f363f-be0c-49d7-8a07-a6a64200b822 | web Python files only, excluding static assets; cli/main.py; pyproject.toml; uv.lock; tests/unit/web/test_http.py and test_security.py |
| UI: 95e31820-96e3-41f1-8725-63704475beb6 | src/refcollector/web/static/** only |
| Orchestrator | briefs, decisions, spec corrections, integration review, subsequent shared enum/settings changes |

Paths without a prefix are under src/refcollector. Ask the orchestrator before expanding ownership. Do not overwrite another worker's changes while formatting. Test fixtures may be read but are not owned.

## Contract freeze

Section 10 DTOs and names are the public contract. Monitoring worker adds list_jobs, list_global_artifacts, get_executor_status, read_project_file. Add six complete count maps, download_enabled, classification_is_null, retry_wait/cancelled, and next_offset. All new optional inputs default to None. Existing output additions need defaults where existing fakes/callers construct DTOs.

Do not edit the initial migration. Add a migration only if a real heartbeat is implemented. If authoritative owner health cannot be proven, return unknown/stale with nullable heartbeat. Never probe status by acquiring/releasing the lock. PID liveness alone is insufficient. Any reduced status capability must be reported as an acceptance gap.

Keep existing StageCounts.queued semantics compatible. Do not double count retry_wait when the new UI computes totals. If exact disjoint counts require changing queued semantics, report the compatibility conflict before changing it.

File reads accept project/kind/resource ID only. Enforce project ownership, canonical containment, read bounds and storage integrity. HTTP writes returned bytes directly; never JSON/base64. Preserve existing artifact descriptor paths for CLI but never expose them as browser-serving authority.

Browser upload, trusted review/settings, and source-page raster preview form wave 2. Do not silently implement them by bypassing current guards. Phase-1 UI shows explicit unavailable controls where contracts remain missing. They remain required for final acceptance.

## HTTP route freeze

Use the HTTP worker's 32-route table with the following corrections. Existing calls retain exact DTO bodies. If path/body IDs are both present, validate equality. List filters use named query fields; repeated states/sections use repeated query parameters. Return service DTOs as JSON except file bytes.

GET /api/session, /api/capabilities, /api/executor/status, /api/projects, /api/projects/{project_id}, /api/jobs, /api/artifacts, and project source/reference/job/artifact detail/list routes follow the design. Text reads use /api/projects/{project_id}/artifacts/{artifact_id}/text. Binary reads use /api/projects/{project_id}/files/{kind}/{resource_id}. POST import/sections/jobs/control/references/update/attach/download/exports/drafts/resolve follow the design. The UI uses these exact names.

Implement typed routes without generic arbitrary method dispatch. Missing proposed wave-2 methods return a structured FEATURE_NOT_AVAILABLE response, not AttributeError or success. Do not create fake trusted context or import nonexistent enums.

Session and every read/write must enforce exact Host/port and reject foreign Origin when present. POST requires exact Origin, session, and CSRF. Deny cross-site session bootstrap and framing. No permissive CORS. Bound bodies at ASGI receive, including before multipart parse. One worker, no reload. Choose explicit loopback launch defaults, expose them in CLI help, and configure no server automatically.

Use FastAPI's compatible dependency selection; do not independently choose conflicting Starlette lower bounds. Lock dependencies. Confirm versions/current documentation. Keep optional import behavior for core users. Preserve non-web serve behavior and check its existing tests later.

## Review of rejected design details

Review replay must succeed for the same authorized idempotent request without a second approval. Rejecting every reused nonce before service idempotency is incorrect. Settings authorization must bind the exact change and explicit confirmation; automatically authorizing arbitrary settings POST is insufficient. These are wave-2 constraints.

Do not use sandbox="allow-scripts allow-same-origin" for a same-origin untrusted viewer. A canvas does not render PDF by itself. Prefer server-owned PyMuPDF raster pages in wave 2, exposed through an explicit authorized service contract. Phase 1 may provide safe attachment download and a clear preview-unavailable state. Do not claim span-view acceptance.

The global download setting is an alternative grant: files:download AND retrieval mode AND (project grant OR global grant). No project-off revocation claim while the global grant remains enabled.

## Wave 1 exit

All workers return changed files and focused evidence. Orchestrator checks ownership, contract compatibility and security, then assigns wave-2 upload/review/settings/raster contracts and implementations. Full web acceptance, integrated regression checks and actual browser verification occur after that work. No release-readiness claim before those gates.
