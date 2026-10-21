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
| 09 | services | C | wp/09 | reviewed by Codex | merged | initial independent 823 passed; corrected 841 passed; integration 938 passed | 1af065f + a0130ca + 0228b85; approval staleness, transactional preconditions, UTF-8 paging fixes |
| 10 | pipeline | C | wp/10 | 8bcfe954-ed5a-4657-96ff-c01fdd183a68 | merged | integration 1017 passed | Codex gpt-6.1-sol/high fallback after Claude session quota; base 6270605; briefs/wp-10.md |
| 11 | mcp | C | wp/11 | reviewed by Codex | merged | independent 764 passed; integration 938 passed | d4502dc; no confirmed review defect |
| 12 | cli | C | wp/12 | reviewed by Codex | merged | initial independent 803 passed; corrected 811 passed; integration 938 passed | b222f87 + 88fca3c; terminal input and live-candidate paging fixes |
| 13 | CLI readback + acceptance gaps | D | wp/13 | Sonnet subagent | merged | 1036 passed | c18f66e, 5c6e92d, dbfe6bf |
| D | integration + acceptance | D | integration | orchestrator | in_review | 1036 passed | W-01..W-31 evidenced on CLI and MCP (W-17 MCP-only, W-18 CLI TTY-only); see acceptance.md |

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

## Wave C completion - 2026-10-20

Independent initial required checks: WP-09 823, WP-11 764, WP-12 803 passed.
Separate reviewers checked each package against its brief and contracts.
Confirmed defects received failing regressions before fixes:

- Services: invalidate/audit approvals after shared metadata changes, DOI candidate replacement and approved-candidate rejection.
- Services: validate job permissions/prerequisites in the write transaction; reject stale attachment evaluations.
- Services: reject a UTF-8 chunk too small to make progress with an actionable INVALID_INPUT.
- CLI: consume Windows extended-key suffix once; clear pending POSIX paste before a line prompt; page all live candidates with matching display and selection.

Corrected full worktree checks: WP-09 841 passed; WP-12 811 passed. Second reviews found no remaining confirmed blocker.
All three branches merged with --no-ff into integration. Root required check on e26389c:
lint passed, 157 files formatted, 938 passed in 58.03s, check: all steps passed.
Native terminal regression tests simulated the OS APIs; no real terminal session was tested.
The earlier POSIX repeated-approval claim was not retained: setcbreak already flushes before the next key read.
WP-10 brief is briefs/wp-10.md; dispatch follows from this verified source state. No push.

## WP-10 dispatch - 2026-10-20

Jean created wp/10 from clean integration base 6270605466d1a66648c982ad1955f1fabd802c29. Sent the committed pipeline/composition brief to session 8bcfe954-ed5a-4657-96ff-c01fdd183a68, backend claude, model sonnet, effort high per D-32. Jean returned started. All Wave C branches are merged; combined required check passed 938 tests. WP-10 implementation and final acceptance remain pending. No push.


## WP-10 launch fallback - 2026-10-20

The first Sonnet launch failed before any source edit. Jean.log records a Claude API session-limit error with reset 02:40 Asia/Bangkok; Jean represented this as cancelled. Retried the same worktree/session and committed brief with backend codex, model gpt-6.1-sol, effort high. No user cancellation was inferred. This is a WP-10 launch fallback, not a change to earlier source verification. Implementation remains pending.

## WP-10 completion and workflow verification - 2026-10-21

WP-10 is merged at `0d41c8e`. Implementation `1eb1987` composes the six handlers,
real services, executor, SQLite, provider clients and renderers. Native PDF calls use
one shared owner thread in `ba9e0c2`; all AppHandles share the same native boundary.
Root review corrected conflicting canonical keys in `7aba5d4` after two failing
regressions. Every matched DOI/OpenAlex key must agree before a work is merged.
Root also corrected explicit occurrence-scope cap result links in `6a3d1a1`.

The reviewed fixes cover automatic unresolved provenance, shared-work mutation,
approval staleness, downstream access reset, terminal/empty-stage client cleanup,
cancellation-safe client closure, native PDF concurrency and canonical conflicts.
Independent final WP-10 worktree gate: lint/format passed, 1,005 tests passed in 43.34s.

Final integration gate: `uv run python scripts/check.py` passed lint, formatting and
1,017 tests in 86.36s. This includes four public CLI workflows, five SDK MCP workflows
and two real process-kill cases with separate CLI and SDK restarts. Checkpoints prove
succeeded work was not repeated; verified file hashes and state survived the kill.

A separate native Windows PTY run used real `msvcrt.getwch`, without TTY/input patches
or a fabricated approval context. One test-driver `a` key created a `cli_tty` approval
at occurrence revision 5. Edits, locks and that approval survived executor loss; a later
SDK edit invalidated the approval and retained its decision/audit history.

Acceptance is not an all-pass release claim. CLI source/artifact readback is incomplete,
and several CLI-specific scenarios remain partial or pending. MCP approval stays
forbidden; W-17 and W-18 use their required native surfaces. Full evidence and remaining
gaps are in `acceptance.md`. No live providers, stdio clients or web environment were
tested. No push occurred. Next work: close CLI readback gaps and remaining CLI cases,
then complete the release/license and benchmark gates before a P0 release claim.
