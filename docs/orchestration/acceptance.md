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
| W-01 | Allowed PDF import; page count and detected sections | Passed; sections in `import` and `source` (`test_w01_import_and_source_show_detected_sections`) | Passed |
| W-02 | Duplicate import returns the same source and reuse indication | Passed | Passed |
| W-03 | Outside path rejected | Passed | Passed |
| W-04 | Reopened client finds project and source | Passed; `sources` after reopen (`test_w04_reopen_sources_lists_source_and_project`) | Passed |
| W-05 | Extraction completes and reports occurrence count | Passed | Passed |
| W-06 | Job filter cursor traversal finds every occurrence | Passed | Passed |
| W-07 | Raw text and page-labelled spans | Passed | Passed |
| W-08 | Missing heading yields inference or the documented warning/error | Passed; inferred section via `source` (`test_w08_missing_heading_yields_inferred_section_via_source`) | Passed |
| W-09 | Resolution completes; no unprocessed occurrence remains | Passed | Passed |
| W-10 | DOI/title conflict requires review | Passed | Passed |
| W-11 | No unattended user acceptance | Passed | Passed |
| W-12 | Edit keeps raw text and increments revision | Passed | Passed |
| W-13 | Split successors retain predecessor links | Passed with limit; `show` lists split successors and merge predecessors from history (`test_w13_split_and_merge_lineage_readback_via_show`). Successor-to-original link is in no public output (finding 5) | Passed |
| W-14 | Merge succeeds; stale merge changes nothing | Passed | Passed |
| W-15 | Added reference belongs to selected source | Passed | Passed |
| W-16 | Exclusion affects accepted export but not complete audit | Passed; accepted export read via `artifact read` (`test_w16_w25_w27_w28_artifacts_read_through_cli`) | Passed |
| W-17 | MCP candidate selection records accepted_agent | Native-only: MCP | Passed |
| W-18 | Genuine TTY approval records accepted_user and matching revision | Passed; native Windows PTY | Native-only: CLI TTY; MCP cannot approve |
| W-19 | Later edit invalidates approval and retains audit history | Passed; native Windows PTY approval, then `refcollector edit` (`python -m tests.acceptance.tty_approval_smoke cli`). Not a pytest case; `test_w19_cli_surface_boundary_without_tty` pins the no-TTY boundary only | Passed; genuine TTY setup, SDK edit |
| W-20 | Discovery records consulted/failed sources and time | Passed | Passed |
| W-21 | One source succeeds, one fails: eligible_found and partial | Passed; `show` reports eligible_found and partial (`test_w21_one_provider_answers_other_fails_eligible_found_partial`) | Passed |
| W-22 | Valid attachment satisfies local copy; no download | Passed | Passed |
| W-23 | Wrong attachment fails identity; eligible retrieval proceeds | Passed | Passed |
| W-24 | Verified hashes persisted; wrong response remains needs_review | Passed; verified hash (`test_w20_w22_w23_w24_w25_job_w26_w31_file_reopen`); wrong response stays needs_review in `show` (`test_w24_wrong_paper_download_stays_needs_review_never_verified`) | Passed |
| W-25 | Current-revision export and manifest match actual bytes | Passed; manifest and files read via `artifact read`, SHA-256 of CLI bytes matches (`test_w16_w25_w27_w28_artifacts_read_through_cli`) | Passed |
| W-26 | Old-revision export rejected | Passed | Passed |
| W-27 | Complete audit retains excluded and unresolved occurrences | Passed; complete_audit read via `artifact read` keeps excluded and unresolved (`test_w16_w25_w27_w28_artifacts_read_through_cli`) | Passed |
| W-28 | Audit report can be read in bounded UTF-8 chunks | Passed; `--length 16` chunks equal `--all`; 1-byte window on a multibyte character fails INVALID_INPUT (`test_w16_w25_w27_w28_artifacts_read_through_cli`) | Passed |
| W-29 | Kill/restart executor; succeeded work not repeated | Passed; real child kill and CLI restart | Passed; real child kill and SDK restart |
| W-30 | Edits, locks and approvals survive restart | Passed in shared native-TTY/process workflow | Passed in shared native-TTY/process workflow; SDK also proves edits/locks reopen |
| W-31 | Verified file identity/hash/state survive restart | Passed; killed executor, CLI restart, stored-byte proof | Passed; killed executor, SDK restart, stored-byte proof |

## Interface and specification limits

| # | Finding | Impact | Handling |
|---|---|---|---|
| 1 | Section 16 asks for every criterion on both surfaces, but W-17 explicitly requires MCP and W-18 requires CLI TTY. | A literal two-surface all-pass statement would be false. | Evaluate native-surface criteria separately and report the exception. Never expose or forge approval through MCP. |
| 2 | Closed by WP-13: the CLI now has `sources`, `source`, `artifacts`, `artifact read`, and `import` prints detected sections. Plain `artifact read` output is cleaned for the terminal (CRLF and control characters dropped), so byte-exact checks use `--json`. | W-01, W-04, W-08, W-13, W-16, W-25, W-27 and W-28 have CLI-only readback. | Never substitute direct storage reads for CLI evidence. |
| 3 | MCP cannot change project network mode or grant download permission. | Retrieval scenarios need trusted setup. | Configure through the CLI/settings boundary before MCP calls. Do not add an MCP permission tool. |
| 4 | Public workflow tests are offline and MCP uses the SDK in memory. | They do not prove live provider reliability or stdio client compatibility. | Retain separate native TTY and killed-process evidence; do not claim live-provider or stdio coverage. |
| 5 | `GetReferenceOut` has no predecessor_ids field; lineage is only in history payloads (`created` on the split original, `merged` on the merged result). | A successor cannot name its original through any public surface (CLI or MCP). | W-13 is accepted on the history evidence, as for MCP. A contract change (outside WP-13) would add the reverse link. |

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
| `uv run pytest tests/acceptance/test_cli_gaps.py -q` | 8 passed | WP-13 CLI readback cases; real CLI, root, executor and store per invocation; HTTP/DNS fixtures only. Artifact bytes come from `artifact read`. |
| `python -m tests.acceptance.tty_approval_smoke cli` in a Windows ConPTY | Exit 0 | Same native path as above, but the final edit that stales the approval is the real `refcollector edit` command. Output: W-18 `passed_native_cli_tty`, W-19 `passed_cli_edit`, W-30 `passed_killed_executor_restart`, revision 5. The key was sent by a throwaway pywinpty driver outside the repo (not a project dependency). |
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
