# P0 workflow acceptance evidence

Source: `docs/spec/refcollector-spec-v3.md`, sections 15.3 and 16.

## Evidence boundary

Wave C integration at `13b2bfd` passed lint, formatting and 938 tests.
That result proves the existing suite, not the complete workflow acceptance gate.
WP-10 merged at `0d41c8e` after an independent 1,005-test worktree gate.
The integration also includes the explicit-scope cap correction in `6a3d1a1`.
The runs below prove public workflows with the stated fixture and readback limits.

Use separate temporary RC_HOME folders for CLI and MCP scenarios. Keep production
services, storage, extractor, resolver, discovery, retrieval, executor and renderers.
Replace external I/O only with deterministic transport fixtures. Reject unexpected
requests. A FakeService test is adapter evidence, not complete workflow evidence.

MCP acceptance can use the installed SDK's in-memory Client against build_server,
with the real application handle. This does not establish stdio client compatibility.
Graceful reopen does not establish process-kill recovery. Mocked console APIs do not
establish a real TTY approval flow. Keep these evidence classes separate.

## Workflow checklist

Partial means the executed workflow does not prove every part of the criterion.
Native-only marks the surface explicitly required by the individual criterion.

| ID | Required proof | CLI | MCP |
|---|---|---|---|
| W-01 | Allowed PDF import; page count and detected sections | Partial; import/count pass, section readback gap | Passed |
| W-02 | Duplicate import returns the same source and reuse indication | Passed | Passed |
| W-03 | Outside path rejected | Passed | Passed |
| W-04 | Reopened client finds project and source | Partial; project passes, source list gap | Passed |
| W-05 | Extraction completes and reports occurrence count | Passed | Passed |
| W-06 | Job filter cursor traversal finds every occurrence | Passed | Passed |
| W-07 | Raw text and page-labelled spans | Passed | Passed |
| W-08 | Missing heading yields inference or the documented warning/error | Pending; source readback gap | Passed |
| W-09 | Resolution completes; no unprocessed occurrence remains | Passed | Passed |
| W-10 | DOI/title conflict requires review | Passed | Passed |
| W-11 | No unattended user acceptance | Passed | Passed |
| W-12 | Edit keeps raw text and increments revision | Passed | Passed |
| W-13 | Split successors retain predecessor links | Partial; creation/exclusion pass, lineage readback gap | Passed |
| W-14 | Merge succeeds; stale merge changes nothing | Passed | Passed |
| W-15 | Added reference belongs to selected source | Passed | Passed |
| W-16 | Exclusion affects accepted export but not complete audit | Partial; exclusion passes, artifact readback gap | Passed |
| W-17 | MCP candidate selection records accepted_agent | Native-only: MCP | Passed |
| W-18 | Genuine TTY approval records accepted_user and matching revision | Passed; native Windows PTY | Native-only: CLI TTY; MCP cannot approve |
| W-19 | Later edit invalidates approval and retains audit history | Pending; CLI edit after genuine approval not run | Passed; genuine TTY setup, SDK edit |
| W-20 | Discovery records consulted/failed sources and time | Passed | Passed |
| W-21 | One source succeeds, one fails: eligible_found and partial | Pending; mixed-provider CLI case not run | Passed |
| W-22 | Valid attachment satisfies local copy; no download | Passed | Passed |
| W-23 | Wrong attachment fails identity; eligible retrieval proceeds | Passed | Passed |
| W-24 | Verified hashes persisted; wrong response remains needs_review | Partial; verified hash passes, wrong response not run | Passed |
| W-25 | Current-revision export and manifest match actual bytes | Partial; descriptors pass, artifact readback gap | Passed |
| W-26 | Old-revision export rejected | Passed | Passed |
| W-27 | Complete audit retains excluded and unresolved occurrences | Pending; artifact readback gap | Passed |
| W-28 | Audit report can be read in bounded UTF-8 chunks | Pending; artifact read command gap | Passed |
| W-29 | Kill/restart executor; succeeded work not repeated | Passed; real child kill and CLI restart | Passed; real child kill and SDK restart |
| W-30 | Edits, locks and approvals survive restart | Passed in shared native-TTY/process workflow | Passed in shared native-TTY/process workflow; SDK also proves edits/locks reopen |
| W-31 | Verified file identity/hash/state survive restart | Passed; killed executor, CLI restart, stored-byte proof | Passed; killed executor, SDK restart, stored-byte proof |

## Interface and specification limits

| # | Finding | Impact | Handling |
|---|---|---|---|
| 1 | Section 16 asks for every criterion on both surfaces, but W-17 explicitly requires MCP and W-18 requires CLI TTY. | A literal two-surface all-pass statement would be false. | Evaluate native-surface criteria separately and report the exception. Never expose or forge approval through MCP. |
| 2 | CLI has no public list_sources/get_source/list_artifacts/read_artifact wrappers; import output omits detected sections. | Several criteria lack complete CLI-only readback. | Keep these rows pending until public readback exists or an explicit acceptance interpretation is recorded. Do not substitute direct storage reads and call them CLI evidence. |
| 3 | MCP cannot change project network mode or grant download permission. | Retrieval scenarios need trusted setup. | Configure through the CLI/settings boundary before MCP calls. Do not add an MCP permission tool. |
| 4 | Public workflow tests are offline and MCP uses the SDK in memory. | They do not prove live provider reliability or stdio client compatibility. | Retain separate native TTY and killed-process evidence; do not claim live-provider or stdio coverage. |

## Acceptance scenarios

Use small generated PDFs, fixed provider payloads and an explicit MockTransport.
The ProviderHttpClient must remain its concrete production class for retrieval.
Use numeric public HTTPS download URLs in socket-free fixtures to avoid DNS while
keeping URL checks active. Production DNS pinning has separate transport tests.

Group scenarios by import/extraction, resolution/repair, discovery/local files,
exports/drafts, and restart/recovery. Keep assertions individually named by W/AT ID.
Read IDs, candidate IDs and revisions from public outputs; do not assume fixed values.
Use unique valid idempotency keys. Page list calls to exhaustion.

For read_artifact, advance offset by the UTF-8 byte length of returned text, not
Python character count. A request too small to hold one character must fail with
actionable INVALID_INPUT instead of returning an empty nonfinal chunk.

P1-only acceptance cases AT-21, AT-22 and AT-27 remain out of P0 scope. Every other
AT scenario needs explicit evidence or a clearly recorded gap. Existing unit tests
remain component evidence even when their names contain an AT identifier.

## Executed final workflow runs

Source baseline: integration `0d41c8e`, including `6a3d1a1`, `1eb1987`, `ba9e0c2`
and `7aba5d4`. Acceptance fixtures and tests are committed separately after execution.

| Command | Result | Evidence boundary |
|---|---|---|
| `uv run pytest tests/acceptance/test_cli_workflow.py -q --tb=short` | 4 passed | Real CLI, default root, one app lifecycle per invocation; HTTP/DNS fixtures only. Public artifact descriptors do not establish artifact contents. |
| `uv run pytest tests/acceptance/test_mcp_workflow.py -q --tb=short` | 5 passed | Real SDK Client/server/service/root; HTTP/DNS fixtures; trusted settings enable retrieval. Export file URI is used only to verify actual bytes/hashes. |
| `uv run pytest tests/acceptance/test_process_recovery.py -q --tb=short` | 2 passed | Real child process killed during resolve; separate CLI and SDK restart cases; succeeded step checkpoint unchanged; verified files and bytes unchanged. Internal queue/store reads supply checkpoint and byte evidence. |
| `uv run python -m tests.acceptance.tty_approval_smoke` in a Windows PTY | Exit 0 | Native `msvcrt.getwch`, genuine stdin/stdout TTY, one `a` key. No TTY/input patch or fabricated approval context. Public service setup/readback, real CLI approval, child kill/restart, real SDK edit. |
| `uv run python scripts/check.py` | Lint and format passed; 1,017 tests passed in 86.36s | Final combined integration gate, including all 11 automated public workflow/process cases. Manual TTY script is separate. |

Native script output: W-18 `passed_native_cli_tty`, W-19 `passed_mcp_sdk_edit`,
W-30 `passed_killed_executor_restart`; approval surface `cli_tty`, revision 5.
The key was supplied by the test driver in an isolated temporary RC_HOME.
This proves the native approval code path, not a human usability study.

The first CLI acceptance run hung because two test socket guards wrapped each
other and blocked Windows asyncio's internal socket pair. Removing the duplicate
guard restored execution. The shared guard still rejects provider sockets and DNS.

Jean reported no running environment for the integration worktree. No web server
was started. All tests used isolated temporary directories; no URL or port was used.
