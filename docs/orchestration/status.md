# Status

States: planned, dispatched, in_review, changes_requested, merged, blocked.

| WP | Title | Wave | Branch | Session / log | State | Tests | Notes |
|---|---|---|---|---|---|---|---|
| C | Contracts freeze v1 | 1 | main | orchestrator | merged | check green: 20 passed | commit "contracts: freeze v1" |
| 01 | storage | A | wp/01 | 252bef5f-1bb1-48b2-8a5f-d27c6ff83f27 | merged | 39 passed in worktree; integration 121 passed | Claude subagent (D-25); commit ca1094b; merged --no-ff into integration |
| 02 | executor | A | wp/02 | 287686b7-6c0a-4566-ae61-8b16a259a6cf | merged | 62 passed in worktree; integration 121 passed | Claude subagent (D-25); commit 180a6d0 + fix 3d095b1; merged --no-ff into integration |
| 03 | extraction | A | wp/03 | 9256c3c8-3378-42e2-9840-8d7dc402b147 | merged | 329 passed in worktree; integration 739 passed | 82b5391 + fix 2ffac17 (preflight warns instead of raising; Sonnet subagent) |
| 04 | providers | A | wp/04 | 5ec5f7a2-d396-4140-8146-bfc3b5a952b4 | merged | 60 passed in worktree; integration 121 passed | Claude subagent (D-25); commit 26b46ad; merged --no-ff into integration |
| 05 | outputs | A | wp/05 | 1a23156f-2cba-4e7c-a8ea-f5ba78dc72c0 | merged | independently verified: 225 passed; integration 327 passed | gpt-6.1-sol completed; commits 5c4c442 + c9a8ea1; merged --no-ff into integration |
| 06 | identity | B | wp/06 | c2cce524-10b9-495f-b206-e2e58ba2e054 | merged | 459 passed in worktree; integration 739 passed | 3f6e51a, 110d41b + fix 8cdbe0b (no invented DOIs; unresolved per 9.1 step 7, D-31) |
| 07 | access | B | wp/07 | 09a55586-2910-4752-8357-7ac15d93b5d8 | merged | 432 passed in worktree; integration 739 passed | 76073bb, d1e3a00; Sonnet review verdict MERGE |
| 08 | files | B | wp/08 | 7404fcf8-4e0b-417a-a6a4-6d9cc670b465 | merged | 500 passed in worktree; integration 739 passed | bac12c3, d315d79 (DNS pinning), bd9034a (title match, off-loop verify, version reason, site-local) |
| 09 | services | C | wp/09 | — | planned | not run | needs 01 |
| 10 | pipeline | C | wp/10 | — | planned | not run | needs 01–09 |
| 11 | mcp | C | wp/11 | — | planned | not run | fake service |
| 12 | cli | C | wp/12 | — | planned | not run | fake service |
| D | integration + acceptance | D | integration | orchestrator | planned | not run | |

## Orchestrator handoff - 2026-10-20

Codex continues orchestration after Claude reached its usage limit.
Manual dispatch (mode A) and worker model 9router/ag/gemini-3.8-flash remain unchanged.
First batch briefs are ready: `briefs/wp-01.md`, `briefs/wp-02.md`, `briefs/wp-04.md`.
These packages remain planned; no workers have been dispatched by Codex.
Baseline verified: `uv run python scripts/check.py` passed lint, format, and 20 tests.
Jean MCP discovery failed with `Transport closed`; live session status is unverified.

## First batch dispatch - 2026-10-20

The user authorized sending the existing briefs through jean-bridge.
WP-01, WP-02, and WP-04 were sent to the existing OpenCode sessions with model 9router/ag/gemini-3.8-flash.
All three clean worker branches were fast-forwarded from 13771b4 to b21ff67 before dispatch. No existing commits were rewritten.
The bridge returned started for each session. Completion and tests remain unverified.

## Wave A, part 1 - 2026-10-20

Antigravity runs cancelled (1-2 min per shell command). WP-01, WP-02, WP-04 built by Claude subagents in the Jean worktrees (D-25).
Review gate: owned paths only; orchestrator ran `uv run python scripts/check.py` in each worktree (39, 61, 60 passed).
WP-02 changes requested once: output reuse crossed projects; fixed in 3d095b1 with a regression test (62 passed).
Merged into `integration` (--no-ff); full check on integration: 121 passed. Next: WP-03 extraction, WP-05 outputs.

## Wave A, part 2 handoff - 2026-10-20

The user runs WP-03 (extraction) and WP-05 (outputs) with Codex.
Briefs: `briefs/wp-03.md`, `briefs/wp-05.md`. Branches `wp/03` and `wp/05` start from `integration` (this commit).
Review gate unchanged: owned paths only, orchestrator reads the diff and runs `uv run python scripts/check.py`, then merges --no-ff into `integration`.

## Wave A completion - 2026-10-20

Reviewed WP-03 and WP-05 production changes and requirement tests. No merge-blocking defect found in reviewed code. Verified owned-path boundaries. Independently ran the required check in both worker worktrees: 223 and 225 passed. Merged both branches into integration with --no-ff. Combined check: lint and formatting passed; 327 passed in 18.57s. No push. Wave B packages remain planned.

## Wave B dispatch - 2026-10-20

WP-06, WP-07 and WP-08 dispatched through Jean to Codex gpt-6.1-sol. Jean created worktrees from integration at aeb736a60ea507cdea855ba68ecaf1670b32eee3. All three branches were clean at that base before dispatch. Each worker received its committed brief, owned boundaries, check commands and completion rules. Jean returned started for all three. WP-09 remains planned in Wave C to keep max parallel workers at three. No implementation results verified yet.

## Wave B gap correction - 2026-10-20

All three packages committed owned work but reported interface gaps. User authorized corrections. Sent bounded follow-ups with non-overlapping expanded ownership (D-30), compatibility and regression requirements, no push or merge. Jean returned started for all three on gpt-6.1-sol. Worker-reported passing tests do not resolve these gaps or constitute independent verification.

## Wave B completion - 2026-10-20 (Claude orchestrator, Sonnet subagents)

Codex quota ran out. Claude took over with Sonnet (effort high) subagents (D-32).
WP-03 follow-up: preflight no longer raises REFERENCE_SECTION_NOT_FOUND; it returns a warning (2ffac17).
WP-06: review found invented DOIs from spaced text and too few `unresolved`; fixed in 8cdbe0b (D-31).
WP-07: review verdict MERGE.
WP-08: cancelled Codex follow-up finished (d315d79); review found strict title match, on-loop parsing,
wrong version reason and site-local IPv6; fixed in bd9034a.
The orchestrator ran `uv run python scripts/check.py` in every worktree and after every merge.
Integration b1fd48d: 739 passed.
Known limits: `restricted` unreachable with P0 OA providers; OA locations not deduplicated by URL;
managed reuse needs a detected version; strict `is_repaired` and octet-stream rejection;
providers/files import cycle held by a lazy import.
Next: Wave C (WP-09 services, WP-11 MCP, WP-12 CLI, then WP-10 pipeline).
