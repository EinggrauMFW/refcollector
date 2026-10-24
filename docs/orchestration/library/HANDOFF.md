# Library UI handoff

Active: Orchestrator B (Codex), Jean session 4f121239-2d0e-4e87-adad-f259988e1e88.
Standby: Orchestrator A (Claude Opus 5.5, medium). Date: 23 October 2026.

## Current state

Library UI W0-W3 complete. DOI-first implementation is now dispatched under D-48.
User approved immediate downloading, automatic use of passing PDFs, review only failed checks.
Requirements: ../doi-first.md. Frozen interface: ../doi-first-contract.md. No UX question pending.
Three disjoint workers, all Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49):
- Core fce6a88a-54fb-4b7f-95ee-db606dc76d28: contracts/domain/storage/service/pipeline/files.
- Adapter d8b3974e-6b90-4fa1-b00f-0279d901b594: adapter done, 73 scoped tests passed.
  Now product-read-only core review; report .ai/doi-core-review.md. No product edits.
- UI 7b818ee3-e850-4524-98f7-e294f0e56623: web-ui and built assets only.
Briefs: DOI-core.md, DOI-adapter.md, DOI-ui.md. Reports: .ai/doi-*-results.md.
Read-only analysis: .ai/doi-first-analysis.md. No fake accepted metadata; separate CitationFile.
The three runs were cancelled before final reports; the user requested continuation.
All three have been resumed. Core previously reached 278 passing affected tests; UI 66 tests,
typecheck/lint/build. Core must also fix missing browser fallback and an incidental second-page
DOI false-positive. Reviewer is saving exact reproductions in .ai/doi-core-review.md.
Do not infer metadata acceptance from permission to download. No commit or push authorized.
Preserve unrelated pre-existing changes in this shared workspace.

## Worker rule (D-47)

- All workers use Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49).
- D-49 supersedes D-47. Do not dispatch GPT workers; keep the current orchestrator.
- Always pass backend, model, effortLevel on each dispatch/follow-up.
- Use Jean sessions; preserve briefs/results across backend changes. Check status first.
- Backend/review/routes: fce6a88a-54fb-4b7f-95ee-db606dc76d28, DOI-first read-only analysis.
- UI/U3/folder: 7b818ee3-e850-4524-98f7-e294f0e56623, idle.
- All three saved worker settings: Antigravity gemini-3.8-flash-high, high effort.

## Final proof

- `uv run python scripts/check.py`: exit 0; **1434 passed, 2 warnings in 168.59s**.
  Ruff clean; 233 files formatted. .ai/library-w3-final-gate.log (UTF-16), .exit beside it.
  First run's terminal result was lost; the recorded rerun supplies verified proof.
  Do not repeat passing gates without a relevant change or unresolved concern.
- Frontend build/lint exit 0. Earlier UI tests: 51 passed.
- U3 items 1-5 PASS, exit 0, unchanged duplicate-download filename/hash inventories.
  Evidence: .ai/live-browser-evidence/library-u3/20261023T040755554269Z/.
- Existing advanced regression at /advanced: exit 0, DONE True.
  Evidence: .ai/live-browser-evidence/library-w3-advanced/.
- Final backend reloaded through Jean; HTTP 200 at http://127.0.0.1:8001/.
  Read-only Library Selenium PASS, no browser errors, matching served build.
  Live source detail returns all 250 citations: library-w3-pagination-live.json under evidence.
- F1-F5/C1 fixed: complete pages, no-replace publication/record locks, local-copy
  navigation, create replay, occurrence-job mapping, Windows filename boundaries.
  Main orchestrator inspected source/regressions. Folder 39 tests; routes 45 tests.
  Reports: .ai/library-w3-folder-fixes.md and .ai/library-w3-route-fixes.md.

## Documentation and cleanup

README and USER_GUIDE describe Library at / and dashboard at /advanced.
Acceptance map: acceptance.md. Full record: status.md. Briefs: briefs/.
Cleanup: .ai/live-browser-evidence/library-w3-collections/test-collections.md lists
16 known automation collections with IDs and folders. None were deleted.

## Limits and boundaries

- One of five OA cited PDFs succeeded in the live flow; others had provider,
  validation, or version-policy warnings. No all-PDF or fresh-network-fetch claim.
- Native hostile directory/junction-swap races and POSIX publication were not exercised.
- No fallback/Sci-Hub testing or changes in this Library wave. Preserve earlier work.
- Engine RC_HOME store stays authoritative; collection folder is a derived copy.
- One file per confirmed web approval, audited; no MCP approval access.
- No manual server. Jean uses .ai/start-live-browser.ps1, port 8001 and
  .ai/live-browser-home. Current terminal RunName library-w3-final; jean.json restored.
- No commits, pushes, new worktrees, or collection removal. Next: review actual worker changes
  and focused regression output. Do not run the full suite while workers edit. Then verify
  integrated UI/engine, obtain action-time confirmation before a new CAPTCHA test if needed,
  and run the broader final gate after edits stop. Previous W3 proof covers old behavior only.

## Latest continuation

User cancelled the previous GPT worker runs and requested Gemini to reduce GPT token usage.
All three were confirmed cancelled, switched to Antigravity Gemini 3.8 Flash High/high,
and redispatched with existing ownership. Preserve implementation; do not start over.
Core: finish browser fallback and primary DOI false-positive fixes plus report.
UI: finish offline verification/copy and report. Reviewer: finish exact read-only findings.
No new full gate or live challenge test yet. No commit/push.

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
