# Full regression repair

Approved scope: fix failures from the full gate (79 failed, 1237 passed in 196.89s), then verify the entire gate. Keep Gemini 3.8 Flash High and existing sessions. No commits, pushes, new worktrees, model substitutions, or live servers.

## Core ownership

Own services/service.py and focused new regression tests only; request coordination before touching another worker's files. snapshot() currently unconditionally load_settings(rc_home), assigns self.settings, and therefore replaces explicit Settings supplied to open_app with disk defaults when config.json is absent. This causes missing email, lost allowlist, changed provider configuration, and cascading pipeline/acceptance failures.

Preserve explicit caller settings when no later persisted update exists. Detect actual subsequent CLI configuration updates at a defined boundary. Existing queued/running/resumed jobs retain their saved ConfigSnapshot. Do not save fixtures to disk merely to hide the issue. Do not weaken assertions, catch all errors silently, or add a config lock inside an existing SQLite write transaction. Preserve trusted review/settings fixes and exact idempotent replay.

Add narrow behavioral regressions for supplied settings with no config.json, actual later persisted changes, and queued snapshot preservation. Reproduce with tests/unit/pipeline/test_acceptance.py::test_r04_r07_at18_w09_stage_time_planning_and_two_source_dedup. Run focused tests and pipeline/acceptance gate; root owns final full gate. Briefly outline approach before editing. Report actual commands/results.

## HTTP ownership

Own tests/unit/web/test_http.py only. test_wave_2_stubs_explicitly_fail assumes review/settings methods do not exist; FakeService now implements these methods and raises when no response is configured. Replace obsolete stub assertions with explicit unavailable-service coverage using a fake that actually lacks the methods, or configured FeatureNotAvailable responses where appropriate. Preserve strict error/session/CSRF/token assertions. Implemented route behavior already has dedicated route tests. Do not weaken product guards or change FakeService globally to hide missing test setup. Run web test gate and lint/format owned file. Briefly outline approach before editing.

## Root ownership and final proof

Root formats only tests/unit/services/test_web_preview.py, reviews worker changes, and runs focused independent checks followed by one full Python suite. Require zero failures, eight actual-source/client JavaScript checks, syntax, src/tests lint and format checks. Save full pytest output to ignored .ai/ for review. Live browser acceptance remains separate and blocked by absent Jean Run configuration.

Root added independent read-only regression assertions in tests/unit/services/test_snapshot_settings_boundary.py. All three cases initially fail: no config file loses supplied email on second job; unchanged preexisting config incorrectly overrides supplied email; later CLI email addition is rejected before snapshot() can refresh it. Load subsequent configuration before prerequisite checks. These tests must remain unchanged by workers.

## Final verification

The final idle-worker checkout passed `uv run --extra web --frozen python -m pytest tests --tb=short --show-capture=no`: **1323 passed in 182.35s**. The ignored full output is `.ai/regression-final.log`. Eight JavaScript regressions passed: four repository client tests and four production-source VM tests in the UI worker's scratch directory. `node --check src/refcollector/web/static/app.js`, `ruff check src tests`, and `ruff format --check src tests` passed (223 Python files formatted).

The fix preserves supplied settings until a later persisted configuration change, refreshes email before job prerequisites, and preserves saved job snapshots. HTTP tests now distinguish absent optional methods from configured fake responses and retain token guards. New test handles close in finally blocks. No commits or pushes. This proves automated regression behavior; live browser acceptance remains pending because no Jean Run command is configured.
