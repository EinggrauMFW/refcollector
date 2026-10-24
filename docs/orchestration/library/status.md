# Library UI status

| Unit | Owner | Session | State | Last result | Next action |
|---|---|---|---|---|---|
| Spec draft | Orchestrator A | this session | done | `docs/spec/library-ui-spec.md` written | Approved by user 22 Oct |
| W0 contract | Orchestrator A | this session | done | `contract.md` frozen; versions from npm | - |
| B1 DTOs + folder sync | Backend (Gemini 3.8 Flash High) | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | done | 21 tests pass, ruff clean (verified by orchestrator) | - |
| B2 read routes | Backend (Gemini 3.8 Flash High) | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | done | GET routes; order bug fixed in B3 | - |
| U1 UI scaffold + shelf | UI (Sonnet 5.5 high) | 7b818ee3-e850-4524-98f7-e294f0e56623 | done | typecheck, lint, 13 tests, build pass; 40 screenshots in `.ai/live-browser-evidence/library-u1/`; TS pinned 6.0.3 | - |
| B3 action routes + serving | Backend (Gemini 3.8 Flash High) | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | done | 80 web tests pass, ruff clean; confirm routes need token; create grants nothing (verified by orchestrator) | - |
| U2 real API | UI (Sonnet 5.5 high) | 7b818ee3-e850-4524-98f7-e294f0e56623 | Phase B done | Live flow works except download (no eligible PDFs in test doc) and folder sync; 4 UI bugs fixed, 38 tests; 7 server bugs reported | Re-check grants, sync, download after B4 |
| B4 live server fixes | Backend (Gemini 3.8 Flash High) | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | done | 86 tests, ruff clean; 7 bugs fixed but routes read svc.store (13 places) | - |
| B5 remove store access | Backend (Gemini 3.8 Flash High) | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | done | No svc.store in routes; new `project_file_path` keeps scope and RC_HOME guards; 296 tests (web, services, contracts), ruff clean on src/tests | - |
| U2 Phase C live re-check + download e2e | UI (Sonnet 5.5 high) | 7b818ee3-e850-4524-98f7-e294f0e56623 | done | All B4 fixes pass live; 38 UI tests; download blocked by S1 (can_download always false), S2 (no file-review path); OA fixture `fixtures/library_oa_thesis.pdf` | Re-run download + viewer after B6 |
| B6 OA download path | Backend (Gemini; run reported model antigravity/auto) | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | done | S1 fixed (`OccurrenceSummary.work_id`), S3 fixed (local copy mapped), S2 partial: `check_kind`, `pending_file` shown, no engine method to approve a file; 304 tests, ruff clean | User decision on S2; restart; U2 download re-check |
| B7 file approval engine + routes | Backend (latest completed work: Gemini) | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | accepted by B | Follow-up verified; 324 passed, 2 warnings in 23.12s; Ruff clean; 231 files formatted | Done; idle session now Codex Sol medium |
| U2 Phase D file panel and live check | UI (Codex gpt-6.1-sol medium; earlier mock: Sonnet) | 7b818ee3-e850-4524-98f7-e294f0e56623 | accepted by B | Screenshots and folder hash checked; read-only Selenium rerun PASS, no browser errors | Complete |
| U3 Selenium flow | Codex gpt-6.1-sol medium | 7b818ee3-e850-4524-98f7-e294f0e56623 | complete | Items 1-5 PASS; duplicate folder proof; final read-only UI verification PASS | None |
| W3 independent code review | Codex gpt-6.1-sol medium | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | complete; fixes running | Five confirmed defects; precise briefs dispatched | Verify F1-F5 fixes |
| W3 final gate + docs | Orchestrator B | 4f121239-2d0e-4e87-adad-f259988e1e88 | complete | 1434 passed, 2 warnings; Ruff clean, 233 formatted; build/lint and advanced Selenium pass; docs updated | No commit/push; cleanup list only |
| W3 route fixes F1/F3/F5 | Codex gpt-6.1-sol medium | fce6a88a-54fb-4b7f-95ee-db606dc76d28 | complete | Source reviewed; 45 focused tests pass; full gate pass; live 250-citation check | None |
| W3 folder fixes F2/F4/C1 | Codex gpt-6.1-sol medium | 7b818ee3-e850-4524-98f7-e294f0e56623 | complete | 39 focused tests; races, create replay, Windows boundary fixed; full gate pass | None |

## W3 review and U3 results - 23 October 2026

U3 returned exit 0 and PASS for items 1-5. Inspected actual script/results and independently
verified the 254899-byte PDF hash. Duplicate-download evidence records unchanged names/hashes.
The read-only advanced smoke passed, but does not replace the full old-dashboard check.
Evidence: `.ai/live-browser-evidence/library-u3/20261023T040755554269Z/`.

| Review finding | Action |
|---|---|
| F1: service pages truncated at 25 | Route worker |
| F2: os.replace can overwrite a concurrently created destination; mappings can race | Folder worker |
| F3: local-copy paper IDs fail detail/viewer navigation | Route worker |
| F4: collection-create replay resets folder mappings | Folder worker |
| F5: occurrence-scoped jobs omit source IDs | Route worker |
| C1: Windows drive/ADS filename boundary | Folder worker |

Both dispatched with explicit Codex gpt-6.1-sol medium and disjoint ownership.
Full suite deferred until fixes stop. No commits, pushes, or collection deletion.

## Advanced regression and documentation - 23 October 2026

Ran the existing dashboard Selenium check with only the root URL changed to `/advanced`
and a separate output directory, plus a nonzero exit for failed checks:
`uv run --no-project --with selenium python -I .ai/selenium-ui-check/advanced_w3_regression.py`.
Exit 0, `DONE True`. All checks passed: CSS/JS/CSP, session, project create options,
source list, PDF raster preview, reference review screen, settings readback, actual
491111-byte export download, executor/job refresh, and final console/CSP errors.
Evidence: `.ai/live-browser-evidence/library-w3-advanced/results.json` and screenshots.
Created only the automation project `selenium-check-1792729155`; nothing deleted.

README now describes Library at `/` and the dashboard at `/advanced`. USER_GUIDE now
covers collection grants, uploads, identity/file confirmation, downloads, and troubleshooting,
with the legacy instructions retained separately. Documentation checks: diff whitespace clean.
No tests run for documentation edits. Final suite still deferred while fix workers edit.

Frontend final checks completed independently: `npm run build` exited 0 (TypeScript and
Vite); `npm run lint` exited 0. No frontend source changed in the fix assignments.
Current cleanup inventory contains 16 known automation collections at
`.ai/live-browser-evidence/library-w3-collections/test-collections.md` (IDs and folders).
This is a review list only; no removal occurred.

## Review fixes accepted; final gate running

Inspected final source and regression reports after both workers became idle.
Folder fixes: 39 focused tests passed in 3.44s. Route fixes: 45 tests passed,
2 existing warnings in 6.33s, including 13 new regressions. Both scoped Ruff checks passed.
F1-F5 and C1 are corrected. C2 native directory/junction replacement remains an explicit
untested local-race limit; POSIX publication branch was not executed on this Windows host.

Started `uv run python scripts/check.py` once after product edits stopped. Lint passed;
233 files pass formatting; full pytest is running. Frontend build/lint already passed and
frontend sources are unchanged. Reloaded the backend through Jean, restored jean.json,
verified HTTP 200, and reran the read-only Phase D Selenium check: exit 0, PASS,
matching served bundle, no page/console errors. Final pytest result pending.

The first full-gate process ended, but its terminal no longer provided the final result.
No passing claim is made for that process. Restarted the gate with durable output:
`.ai/library-w3-final-gate.log` and `.ai/library-w3-final-gate.exit`.
Read-only live check after reload returned all 250 citations for the development source;
evidence: `.ai/live-browser-evidence/library-w3-pagination-live.json`.

## Orchestrator B takeover review ? 23 October 2026

Both B7 and U2 were idle at takeover. Read the required handoff, plan, contract (including
section 8), B7 brief, status, and D-41 through D-46. Verified B7 service/test files directly.
The confirmation code binds session, principal, purpose, project, occurrence, file, action,
revision, idempotency key, and fingerprint; it checks pending-token expiry. No file-review
methods appear in the MCP source registrations. One work_file_id per input.

Independent commands:
- `uv run pytest -o addopts="" -q tests/unit/web tests/unit/services tests/contracts`: **313 passed, 2 warnings in 28.42s**.
- `uv run ruff check src tests`: **All checks passed!**
- `uv run ruff format --check src tests`: **231 files already formatted**.

Review withheld pending required negative tests, explicit accepted-identity checks, and
registry purpose literal correction. See B7 brief follow-up. U2 remains idle; no live go sent.
No full suite, server changes, commits, or pushes during this takeover step.

## Worker model policy update - 23 October 2026 (D-47)

All future worker tasks follow the active orchestrator:
- A (Claude Opus 5.5): Claude claude-sonnet-5-5, high effort only.
- B (Codex gpt-6.1-sol): Codex gpt-6.1-sol, medium effort only.

The unit table preserves historical model attribution. B7 and U2 were both idle when
this policy changed. Their next turns under B use Codex gpt-6.1-sol, medium effort.
B7's latest Gemini follow-up has now passed independent review; see the entry below.
No new implementation task was dispatched as part of this policy edit.

## B7 accepted and Jean Run verified - 23 October 2026

Independent follow-up review verified request binding, wrong-purpose and other-file tokens,
MCP/CLI/no-session rejection, revision changes after prepare, accepted identity in both
methods, and file_review registry purpose types. No MCP tool exposure.

- `uv run pytest -o addopts="" -q tests/unit/web tests/unit/services tests/contracts`: **324 passed, 2 warnings in 23.12s**.
- `uv run ruff check src tests`: **All checks passed!**
- `uv run ruff format --check src tests`: **231 files already formatted**.

Started the configured Jean Run for worktree 9ed8e368-047d-49c1-b78a-ed72ae13141e.
Verified HTTP 200 at http://127.0.0.1:8001/ and both file-review POST routes in
`/api/openapi.json`. U2 Phase D live dispatched with backend=codex, model=gpt-6.1-sol,
effortLevel=medium. Required evidence: Download all, two-step single-PDF approval,
collection-folder PDF, downloaded shelf entry with cited by, and viewer.
Evidence directory: `.ai/live-browser-evidence/library-u2d/`. Await independent live review.

## Phase D accepted and W3 dispatched - 23 October 2026

Inspected actual approval-description and downloaded-viewer screenshots, request/job evidence,
and confirmed the collection PDF exists at 254899 bytes with the recorded SHA256.
Orchestrator reran `uv run --no-project --with selenium python -I .ai/selenium-ui-check/library_u2_file_panel_verify.py`:
exit 0, PASS; downloaded shelf has Cited by 1 paper, citation is Downloaded, no page/console
errors, and served bundle matches the local build. The stored PDF came from Phase C;
do not claim a fresh network fetch. Worker also reported typecheck/lint/build and 51 UI tests passed.

U3 and independent review dispatched in parallel through the existing Jean sessions,
both with backend=codex, model=gpt-6.1-sol, effortLevel=medium. No product edits authorized
for these tasks. No full-suite run yet. No commits, pushes, or collection removal.

## W3 complete - final verified result

`uv run python scripts/check.py`: exit 0; Ruff All checks passed; 233 files already
formatted; **1434 passed, 2 warnings in 168.59s (0:02:48)**; check: all steps passed.
Evidence: .ai/library-w3-final-gate.log (PowerShell UTF-16) and .ai/library-w3-final-gate.exit.
Warnings are the existing HTTPX per-request-cookie deprecations in the framing test.
Frontend build/lint passed. Existing advanced Selenium: exit 0, DONE True.
Final backend read-only Library Selenium: exit 0, PASS, no browser errors, matching build.
Live large-paper detail returned 250/250 citations. Review F1-F5/C1 source and regressions
were inspected; no confirmed review defect remains open within this wave.
C2 hostile local directory-swap proof and POSIX execution remain verification limits.
README, USER_GUIDE, acceptance.md, and HANDOFF.md updated. Sixteen automation collections
listed for later cleanup; none removed. Both workers idle. No commit, push, or worktree.

## New user request: DOI-first retrieval

User reports that metadata confirmation prevents DOI-based fallback downloads. Root gates
confirmed in service download_files/_downloadable and retrieval pipeline. New read-only
design analysis dispatched to backend session fce6a88a-54fb-4b7f-95ee-db606dc76d28 using
Codex gpt-6.1-sol medium. Report: .ai/doi-first-analysis.md. No product changes yet.
User preference requested: review only PDFs failing checks, or manually approve every DOI-only PDF.
Completed Library W3 evidence remains valid for its existing behavior.

User explicitly selected immediate downloading and review only for PDFs failing checks.
Recorded D-48 and requirements in `../doi-first.md`. Backend analysis is still running;
implementation briefs follow once its interface is concrete. No new product behavior yet.

DOI-first interface frozen in `../doi-first-contract.md`. Implementation dispatched in
parallel: core fce6a88a-54fb-4b7f-95ee-db606dc76d28; adapter d8b3974e-6b90-4fa1-b00f-0279d901b594;
UI 7b818ee3-e850-4524-98f7-e294f0e56623. All Codex gpt-6.1-sol medium. Separate ownership;
no full suite/server/live retrieval while implementation is in flight. D-48 behavior approved.

DOI adapter completed: 73 scoped tests passed, Ruff clean, 2 existing cookie warnings;
report `.ai/doi-adapter-results.md`. That session is now assigned a product-read-only
core integration review, report `.ai/doi-core-review.md`, while core/UI finish.
All dispatches remain Codex gpt-6.1-sol medium. No acceptance claim for the full feature yet.

User requested continuation after all three worker runs were cancelled. Resumed all three
with explicit model/medium effort, preserving their files. Last progress: core affected gate
278 passed; UI 66 tests/typecheck/lint/build passed; offline UI proof/report pending.

| Confirmed integration gap | Action |
|---|---|
| DOI handler omitted configured browser-assisted fallback | Core follow-up with offline browser/profile/cancellation regressions |
| An incidental second-page DOI could auto-verify another paper | Core follow-up for conservative primary publication DOI evidence |

Reviewer is finalizing exact probe evidence; no final acceptance claim yet.

## D-49 Gemini continuation

User cancelled the GPT runs and requested Gemini workers to save GPT token usage.
All three sessions were cancelled; saved backend/model/effort changed to Antigravity
antigravity/gemini-3.8-flash-high, high. Existing core/UI/review tasks redispatched.
Historical model attribution remains historical; future dispatch uses D-49.
No implementation or test result is claimed by this model switch.

### Worker check: Gemini recovery
- Core and UI runs had crashed after Jean closed; their responses were lost. Both tasks were sent again with Antigravity Gemini 3.8 Flash High, high effort. Existing files must be preserved.
- Reviewer completed `.ai/doi-core-review.md`. It reports two unresolved defects: missing browser fallback in DOI retrieval and incidental second-page DOI auto-verification.
- Core must fix both defects and save focused regression results. UI must finish deterministic mock-browser proof. Feature completion remains unverified.

### DOI worker review follow-up
- Core report exists: 100 focused tests passed (worker report). UI report exists: 67 tests, typecheck, lint, and offline mock browser passed (worker report). These results still need independent integration proof.
- Dispatched independent core/adapter review to the existing Gemini reviewer, including viewer currentness, folder kinds, fallback guards, and first-page incidental DOI checks.
- Source review shows primary DOI extraction still accepts any first-page DOI before abstract/references. Sent core worker a precise follow-up for conservative publication evidence and missing first-page regressions.
- All dispatched workers use Antigravity Gemini 3.8 Flash High, high effort. No live external test or final full gate yet.

### DOI final review in progress
- Core follow-up report now records 113 focused tests plus touched-file Ruff checks passing. New helper checks page-one DOI declarations. This remains worker evidence until independent review.
- Reviewer dispatched to recheck the new helper with bare and marked incidental citations. Broad final gate is authorized only if no blocking defect remains.
- Source inspection identified a narrative-word denylist in the helper; reviewer received additional marked-DOI prose probes. Do not treat this heuristic as proven yet.
- Jean reports existing Run environments on configured port 8001, but URL/ports are absent from status. No new server started and no live DOI flow claimed.

### Final gate and independent counterexample
- Durable final gate: `.ai/doi-final-gate.exit` = 0; log records 1553 passed, 2 existing warnings, `check: all steps passed`.
- Gate does not establish feature completion. Root directly ran `_extract_primary_dois` against `Results extend https://doi.org/10.1234/target findings.` and `Smith et al. https://doi.org/10.1234/target`; both incorrectly returned the target as primary evidence.
- Reviewer omitted these exact requested cases and its no-blocker conclusion is invalid. Sent correction and independent PDF repro request.
- Core dispatched a minimal positive-declaration fix and exact regression PDFs; no further broad gate until needed. All workers remain Gemini 3.8 Flash High/high. Live flow still pending.

### Latest worker status
- Core completed latest declaration-only fix and reports 115 focused tests passing with clean touched-file lint/format.
- Reviewer had completed before that fix and still recorded the previous defect. Dispatched independent verification of the latest source and exact PDF probes. No completion claim until that review finishes.
- UI remains complete with saved offline proof. Previous broad gate: 1553 passed, before this final verifier fix. Live flow remains pending.

### Independent latest-fix review completed
- Gemini reviewer completed latest-source checks: exact two marked-prose PDF probes now return needs_review/DOI_EVIDENCE_MISSING; valid standalone DOI probes verify.
- Reviewer reports 153 focused tests passing, touched-file Ruff clean and 6 files formatted. Root separately confirmed helper rejects both exact prose strings and accepts standalone doi declaration.
- Earlier broad gate passed 1553 tests before final narrow fix. UI report remains 67 tests and offline browser pass. Live backend Library DOI flow is still pending; feature is not fully signed off.

### Live verification dispatched
- User authorized verification phase. Dispatched existing UI worker with Antigravity Gemini 3.8 Flash High/high to verify actual backend Library flow on configured port 8001.
- Worker must verify current server/schema/assets, unresolved-metadata DOI download, actual PDF bytes/hash, viewer, folder export, and pending-file confirmation where available.
- No manual server or product edits. Preserve collections/config. Separate reused-file/mock evidence from real external retrieval.
- Worker must stop before automatic robot-challenge interaction and request fresh action-time confirmation through root. Evidence target: `.ai/doi-live-results.md` and `.ai/live-browser-evidence/doi-live/`.

### Live worker status and bridge recovery
- Worker stopped with stale backend/schema/migration diagnosis; live flow not executed.
- Root independently called Jean bridge: get_run_environments succeeded (empty), start_run_environment succeeded started=true/reused=false, terminal run-f751c67c-77b9-48fd-bd74-c763120470cd, configured port8001 command.
- Sent Gemini worker follow-up to inspect listener/schema/migration, resolve only validated old app process if port conflict, and continue verification using bridge only. Do not require user manual restart while bridge works.

### Approved Library management update dispatched
- User approved design-taste-frontend app-screen proposal: collection rename/delete/pin, stable sidebar, auto-collapse citation after successful metadata acceptance, no refresh blink, reduced-motion and phone access.
- Dispatched existing UI worker using Antigravity Gemini 3.8 Flash High/high. Worker must establish boundaries against Claude Advanced dashboard redesign and preserve existing files.
- Library components/assets and dedicated Library routes/tests only; shared core/service and Advanced files excluded unless a precise scope extension is reviewed first.
- Require actual deletion consequence inspection, persisted pins, success/error/focus/scroll behavior, before/after browser evidence at 1440/768/390. Report `.ai/library-management-results.md`.
- Prior live DOI robot-check remains paused; this approval does not authorize CAPTCHA interaction. No commit/push.

## W4 advanced harmonize (D-50), Orchestrator A, 23 October 2026

Two Sonnet 5.5 subagents in parallel. Advanced: Library tokens, 224 px sidebar, shared theme key `rc-library-theme`, Light/Dark/System, Library|Advanced switch with `/#/<id>` and `#project=<id>`; `advanced_w3_regression.py` DONE True; `advanced_w4_check.py` DONE True (re-run by A); inspect.mjs --app 0 FAIL, 2 WARN. Library: ModeSwitch with `/advanced#project=<id>`; 69 Vitest tests, lint, typecheck, build pass (tests and lint re-run by A). Evidence: `.ai/live-browser-evidence/advanced-w4/`, `library-w4/`. Known limits: Advanced switch in a top header vs Library switch under the logo in the sidebar; possible dark flash on Advanced load; `#project=` preselects only the first 50 projects.

### Claude and Library integration check dispatched
- Read Claude session c549d50b latest completed redesign report and D-50. Claude changed Advanced static files and Library Sidebar/ModeSwitch; Library management worker also changed Sidebar and rebuilt Library assets.
- Root source search confirms current Sidebar still renders ModeSwitch; Advanced retains shared rc-library-theme and project hash handling. This is structural evidence only.
- Dispatched independent Gemini reviewer for current-source and live combined regression: context round trip, shared theme, management actions, citation acceptance, built assets, and focused checks. Report `.ai/library-advanced-integration-review.md`.
- Review must distinguish Claude-known limits (theme flash, first-50 project lookup, table overflow, different switch positions) from new integration regressions. No product edits or external DOI/CAPTCHA traffic.

### Integration status check during correction
- Library management correction worker still running. Independent review run crashed after saving a partial report; restarted Gemini review with existing evidence preserved.
- Partial review found frontend lint errors and management test failures while the writer was changing service APIs. Treat as provisional until writer stops and checks rerun on stable files.
- Earlier report mislabeled 34 tests as the entire web package; reviewer collected 89. Final report must state actual command scope.
- Combined live check remains incomplete. Reviewer again used direct Jean stdio despite bridge instruction; follow-up requires bridge only and must not infer root bridge unavailable.

### Stable correction ready for independent review
- Collection worker now completed: reports 126 web tests,4 seeded management tests,72 UI tests passing; source search confirms no svc.store remains in Library routes.
- Reviewer crashed before writer completion. Restarted final read-only review against stable files, with focused lint/typecheck/service-storage-web checks. Earlier mid-edit findings remain provisional.
- Jean bridge lists configured environment running but provides no URL/port health proof. Combined live verification still pending.

### Independent integration results saved
- Reviewer crashed after saving final stable report. Report records126 backend web tests and72 frontend tests passing, typecheck passing, and preserved mode/theme/context source contracts.
- Frontend lint still fails:3 errors and1 warning. Sent Gemini writer targeted fixes plus existing active-job consequence field propagation. No push readiness claim.
- Final combined browser check remains pending: worker reports port8001 inactive despite Jean environment flag. Root bridge remains callable; environment metadata alone is not server health proof.

### Final verification started on current live server
- User authorized final verification. Root GET port8001 initially failed despite stale Jean running flag.
- Started configured Run through Jean bridge using unique RunName library-integration-final; terminal run-48c186cd-88d2-4db7-b61b-80aac5e82be0. Original jean.json restored from backup.
- Root GET http://127.0.0.1:8001/ returned200.
- Dispatched Gemini independent reviewer for final latest-source lint/typecheck/focused tests and actual combined Library–Advanced browser checks. No product edits/external DOI/CAPTCHA/commits/push.

### Latest final integration evidence received
- Reviewer run crashed after saving updated report and actual final-integration results/screenshots. Root read results.json and confirmed six screenshot artifacts exist.
- Saved review records frontend lint/typecheck/72 tests passing; backend web126, service projects15 and seeded management4 tests passing; Advanced W4 script11 assertions and combined script10 assertions passing.
- Actual combined JSON proves project round trip, theme sharing, pin persistence, rename cancellation, delete-dialog preflight/cancellation, no console/CSP errors. Responsive screenshots are captures, not full visual approval.
- Combined run does not exercise actual rename save/delete, active-job warning (false in fixture), or candidate acceptance/collapse. Earlier worker evidence covers some actions but latest-build full behavior is not proved by these10 assertions. DOI external fallback remains separately pending confirmation.

### Remaining live actions dispatched
- User authorized continuation. Dispatched Gemini UI worker for latest-build actual rename save/reload, disposable collection deletion with disk/audit/isolation proof, active-job warning/disable/backend rejection, and citation acceptance success/failure/collapse/focus/scroll behavior.
- Existing combined browser checks remain passed; do not rerun broad checks. Use new disposable local fixtures only and no external DOI/CAPTCHA traffic. No product edits without concrete defect report.
- Evidence target `.ai/library-final-actions-results.md` and `.ai/live-browser-evidence/library-final-actions/`. No commit/push.

### Remaining live-action evidence completed
- Gemini worker completed successfully. Root read actual results.json: six groups passed for latest-build live fixture actions at port8001.
- Proven: rename save/reload/database name; paused-job warning/disabled button and direct DELETE400; disposable deletion with disk PDF/audit preservation and other project count preserved; failed revision confirmation stays open/error; successful acceptance target collapsed, parent open, focus restored, scroll712->712. Seven screenshot files exist.
- Local fixture data was used; no external DOI/CAPTCHA test. Script comment claims DOM identity/no blink but executable check records scroll and state, not frame-level flicker/identity proof. Do not overclaim that detail. Responsive captures alone are not a full visual quality gate.
- Prior Library–Advanced integration checks passed. Separate external DOI fallback remains pending fresh confirmation. No commit/push.

### External fallback attempt authorized
- User explicitly instructed continuation of external DOI fallback download and robot-check attempt. This is fresh authorization for one bounded prepared target attempt, DOI10.1371/journal.pone.0000308 through configured Sci-Hub preset.
- Dispatched Gemini UI worker to perform actual Library action and preserve exact challenge/download/verification/folder/viewer evidence. No repeated attempts, mirror switching, weakened checks or approval of mismatched PDFs.
- Must distinguish browser_attempted from proven robot interaction and fresh external file from reuse. Report `.ai/doi-live-results.md`. No commit/push.

### External DOI attempt completed successfully
- Gemini job job_01M5MSQG6TD2CBRS2KXJFX876B completed; stored citation file verified for DOI10.1371/journal.pone.0000308,91408bytes,5pages,metadata identity unprocessed/work_id null.
- Browser watcher saved worker result clicked=true (not merely browser_attempted). Reviewer must confirm artifact same-job provenance before final challenge claim.
- Root independently hashed CAS and collection-folder files: both b52ca44dfd09d240543b80315f0cd43fe9ba7946f2b5650d5580213ed5d9186c.
- Sent worker read-only follow-up for actual shelf cited-by/viewer proof and artifact provenance. No new external attempt authorized. Fixture citation title is inaccurate relative to actual DOI paper; verification is DOI-only, not title confirmation.

### External fallback final read-only UI proof received
- Gemini worker completed read-only shelf/viewer follow-up. Saved ui_shelf_viewer_proof.json and five additional screenshots.
- Report records shelf Cited by1paper, downloaded paper toolbar, viewer from shelf and citation row. Browser stream200/application-pdf,91408bytes,SHA256 b52ca44dfd09d240543b80315f0cd43fe9ba7946f2b5650d5580213ed5d9186c, matching root-verified CAS/folder hashes.
- Artifact provenance report matches target DOI/time/PDF URL/hash with same retrieval job; helper clicked=true captured. One successful bounded external attempt, no general CAPTCHA reliability claim.
- Fixture citation title inaccurate; verification explicitly DOI-only and metadata remains unconfirmed. No bibliographic title match claimed. No commit/push.

### Beta publication authorized and verified
- User authorized README update and GitHub beta push. Prepared version0.2.0b1, updated README/USER_GUIDE, tagged-source installation instructions, and beta limits.
- Final broad gate:1559passed,2existing warnings,239formatted files,exit0. Root final web API version-label correction passed15focused HTTP tests and Ruff.
- Gemini reports72frontend tests,lint/typecheck,4Node client tests and clean wheel CLI installation passing. Root independently inspected built archives for migration/static assets/licenses and excluded local directories.
- Publication review found no real credentials; root removed two local filesystem references and marked benchmark evidence as local/not distributed. Local .ai/.taste-skill/jean.json/build outputs/PDFs remain excluded from Git.
- User authorization supersedes earlier no-commit/no-push instructions for this beta. Publish normal fast-forward main plus v0.2.0b1 prerelease; no PyPI publication.

### README screenshots and architecture diagrams published
- Added real Selenium screenshots of the running beta Library and Advanced References views, using a new illustrative demo collection. Images are in docs/images/ and embedded near the top of README.
- Added source-checked Mermaid diagrams for stdio MCP integration and the loopback web/PDF workflow. Both parsed and rendered successfully with Mermaid11. Image relative paths and git diff whitespace checks passed.
- Clarified MCP human-approval limits and rejection of malformed/unsafe PDFs. Captions describe actual illustrative data; no copyrighted PDF page images were published.
- README/image commit1428d10 pushed to main; root verified remote commit and screenshot HTTP200. Application code unchanged; application tests skipped for this documentation-only update.
