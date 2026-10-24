# W3 U3: repeatable Library end-to-end verification

Backend/model: Antigravity `antigravity/gemini-3.8-flash-high`, high effort under D-49.
Outline the approach briefly, then implement and execute it.

Read plan.md, contract.md section 8, the Library specification section 11, and
W2-file-approval.md. U2 Phase D is accepted from real screenshots, request evidence,
folder hash verification, and the orchestrator's read-only Selenium rerun.

## Ownership

- `.ai/selenium-ui-check/library_flow.py` and supporting test scripts/fixtures under `.ai`.
- `.ai/live-browser-evidence/library-u3/` for evidence and collection inventory.
- Do not edit product code, UI sources, generated assets, or orchestration files.

## Required proof

Create a repeatable script covering specification acceptance items 1-5 through actual UI:
create a test collection, confirm permissions, upload the development/OA PDF, observe
citations, find metadata, accept at least one identity Check with two-step confirmation,
Download all, approve one pending PDF if required, verify folder/shelf/cited-by, open the
downloaded citation in the viewer, then repeat Download all and prove no duplicate PDF
copies by filename and SHA256 inventory. Preserve existing user collections. Use only
normal OA downloads; never interact with the fallback/Sci-Hub control.

Reuse existing Selenium helpers and fixtures, including fixtures/library_oa_thesis.pdf.
Use unique test collection names per run. Record exact requests, job IDs, screenshots,
folder paths, file hashes, and terminal outcomes. Redact confirmation tokens.
Do not replace live providers, weaken guards, synthesize verified records, or claim a
fresh network fetch when the file was already stored.

Also run the existing `/advanced` smoke check if its script has a bounded read-only mode.
Otherwise add a small read-only smoke check: route loads, project/reference controls and
existing PDF viewer work; record what was and was not tested.

Call Jean get_run_environments first and reuse http://127.0.0.1:8001/. No manual server.
Check current official Selenium documentation before new API usage. Evidence must state
the exact command, exit status, URL, and pass/fail for each acceptance item.

Write a list of test collections that the user may later remove, with IDs and folders.
Do not delete anything. Do not run the full suite. No commits or pushes.
If a real blocker needs product changes, report the smallest reproduction and stop edits.

Run the script once and report literal results. Syntax-check the script if the live run
cannot finish, but do not present syntax success as end-to-end success.

Current worker model: Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49). This overrides older model instructions in this brief.
