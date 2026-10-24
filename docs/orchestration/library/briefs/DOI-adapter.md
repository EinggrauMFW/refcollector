# DOI-first Library adapter worker

Outline briefly, then implement the adapter side of ../../doi-first-contract.md.
Own ONLY web/library_api.py, web/library_routes.py, tests/unit/web/test_library_*.py,
and .ai/doi-adapter-results.md. Core and UI workers edit separate files in parallel.

Use the frozen core DTO fields even if the core worker has not added them yet. Do not
edit core contracts or fake service shared files. Use test responses with the new fields.
Preserve pagination, local-copy navigation, folder guards, and job scope fixes from W3.

Prove DOI fallback action admits unresolved/needs_review metadata with exactly one cited
DOI and sends citation_doi plus ANY_ELIGIBLE. Prove normal action remains unchanged.
Prove passing citation files appear on shelf/folder and open, with metadata still pending;
failed PDF checks use file review, correct resource kind, and exactly one confirmation.
Prove no-DOI/multiple-DOI/excluded inputs cannot choose a substitute key; job mappings work.
Use service calls only, never svc.store. Reuse existing folder sync and file-read boundaries.

Add focused failing regressions before fixes. Run scoped web tests and Ruff once core
contracts are available. Do not treat missing-yet core symbols as a product defect.
No frontend/core/fallback-worker implementation edits, full suite, browser/server actions,
commits, pushes, collection deletion, or orchestration-file writes.

Current worker model: Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49). This overrides older model instructions in this brief.
