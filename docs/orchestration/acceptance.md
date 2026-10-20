# P0 workflow acceptance evidence

Source: `docs/spec/refcollector-spec-v3.md`, sections 15.3 and 16.

## Evidence boundary

Wave C integration at `13b2bfd` passed lint, formatting and 938 tests.
That result proves the existing suite, not the complete workflow acceptance gate.
WP-10 remains in progress. No row below is marked passed without a recorded run.

Use separate temporary RC_HOME folders for CLI and MCP scenarios. Keep production
services, storage, extractor, resolver, discovery, retrieval, executor and renderers.
Replace external I/O only with deterministic transport fixtures. Reject unexpected
requests. A FakeService test is adapter evidence, not complete workflow evidence.

MCP acceptance can use the installed SDK's in-memory Client against build_server,
with the real application handle. This does not establish stdio client compatibility.
Graceful reopen does not establish process-kill recovery. Mocked console APIs do not
establish a real TTY approval flow. Keep these evidence classes separate.

## Workflow checklist

Pending means not yet executed as a complete workflow on the integrated WP-10 code.
Native-only marks the surface explicitly required by the individual criterion.

| ID | Required proof | CLI | MCP |
|---|---|---|---|
| W-01 | Allowed PDF import; page count and detected sections | Pending; section readback gap | Pending |
| W-02 | Duplicate import returns the same source and reuse indication | Pending | Pending |
| W-03 | Outside path rejected | Pending | Pending |
| W-04 | Reopened client finds project and source | Pending; source list gap | Pending |
| W-05 | Extraction completes and reports occurrence count | Pending | Pending |
| W-06 | Job filter cursor traversal finds every occurrence | Pending | Pending |
| W-07 | Raw text and page-labelled spans | Pending | Pending |
| W-08 | Missing heading yields inference or the documented warning/error | Pending; source readback gap | Pending |
| W-09 | Resolution completes; no unprocessed occurrence remains | Pending | Pending |
| W-10 | DOI/title conflict requires review | Pending | Pending |
| W-11 | No unattended user acceptance | Pending | Pending |
| W-12 | Edit keeps raw text and increments revision | Pending | Pending |
| W-13 | Split successors retain predecessor links | Pending | Pending |
| W-14 | Merge succeeds; stale merge changes nothing | Pending | Pending |
| W-15 | Added reference belongs to selected source | Pending | Pending |
| W-16 | Exclusion affects accepted export but not complete audit | Pending; artifact readback gap | Pending |
| W-17 | MCP candidate selection records accepted_agent | Native-only: MCP | Pending |
| W-18 | Genuine TTY approval records accepted_user and matching revision | Pending; real TTY required | Native-only: CLI TTY; MCP cannot approve |
| W-19 | Later edit invalidates approval and retains audit history | Pending; depends on W-18 | Pending; trusted TTY setup required |
| W-20 | Discovery records consulted/failed sources and time | Pending | Pending |
| W-21 | One source succeeds, one fails: eligible_found and partial | Pending | Pending |
| W-22 | Valid attachment satisfies local copy; no download | Pending | Pending |
| W-23 | Wrong attachment fails identity; eligible retrieval proceeds | Pending | Pending |
| W-24 | Verified hashes persisted; wrong response remains needs_review | Pending | Pending |
| W-25 | Current-revision export and manifest match actual bytes | Pending; artifact readback gap | Pending |
| W-26 | Old-revision export rejected | Pending | Pending |
| W-27 | Complete audit retains excluded and unresolved occurrences | Pending; artifact readback gap | Pending |
| W-28 | Audit report can be read in bounded UTF-8 chunks | Pending; artifact read command gap | Pending |
| W-29 | Kill/restart executor; succeeded work not repeated | Pending; real child process required | Pending; real child process required |
| W-30 | Edits, locks and approvals survive restart | Pending; trusted lock/approval setup required | Pending; trusted TTY setup required |
| W-31 | Verified file identity/hash/state survive restart | Pending | Pending |

## Interface and specification limits

| # | Finding | Impact | Handling |
|---|---|---|---|
| 1 | Section 16 asks for every criterion on both surfaces, but W-17 explicitly requires MCP and W-18 requires CLI TTY. | A literal two-surface all-pass statement would be false. | Evaluate native-surface criteria separately and report the exception. Never expose or forge approval through MCP. |
| 2 | CLI has no public list_sources/get_source/list_artifacts/read_artifact wrappers; import output omits detected sections. | Several criteria lack complete CLI-only readback. | Keep these rows pending until public readback exists or an explicit acceptance interpretation is recorded. Do not substitute direct storage reads and call them CLI evidence. |
| 3 | MCP cannot change project network mode or grant download permission. | Retrieval scenarios need trusted setup. | Configure through the CLI/settings boundary before MCP calls. Do not add an MCP permission tool. |
| 4 | Current unit tests simulate terminal input and executor loss. | They do not prove native console approval or actual killed-process recovery. | Retain unit evidence; add separate native/process evidence before those rows pass. |

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

None recorded yet. Record the exact integrated commit, commands, surface, fixture
boundaries, final output and any exception for each completed run.
