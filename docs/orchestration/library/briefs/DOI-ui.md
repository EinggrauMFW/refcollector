# DOI-first UI worker

Outline briefly, then implement the frontend side of ../../doi-first-contract.md.
Own web-ui/**, generated static/library/**, .ai/DOI UI/live artifacts and result report
.ai/doi-ui-results.md. No Python, core DTO, backend route, or orchestration edits.

Metadata confirmation must not block the explicit DOI download action. Mirror optional
can_download_by_doi and metadata_review_pending and PendingFile/FileReviewIn.file_kind.
Keep normal accepted-work download behavior. For DOI-capable Check/unprocessed rows,
offer a clear Download PDF by DOI action sending use_fallback=true and the citation ID.
Collection/paper fallback buttons already send use_fallback=true; explain they work before
metadata review. Do not auto-confirm candidates or claim a metadata warning is PDF failure.

Passing citation files open immediately. Show metadata warnings as informational separately.
Only reviewable failed PDF checks require FilePanel confirmation, with correct file kind.
Preserve permissions, polling, keyboard/focus, error handling, and legacy/mock compatibility.
Avoid a redesign; use existing controls/styles. Test only behavior/state, no markup tests.

Implement mock flows for pending metadata + passing DOI PDF, failed DOI PDF review, and
missing/ambiguous DOI disabled action. Run focused tests, typecheck, lint, build.
No live retrieval or robot-checkbox test yet; that will follow backend review and a fresh
action-time confirmation if the browser needs a CAPTCHA interaction. No manual server.
Check current official docs before new external API usage. No full Python suite, commits,
pushes, collection deletion, or edits outside ownership.

Current worker model: Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49). This overrides older model instructions in this brief.
