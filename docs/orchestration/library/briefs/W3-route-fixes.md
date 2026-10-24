# W3 route fixes: F1, F3, F5

Worker: Antigravity antigravity/gemini-3.8-flash-high, high effort. Outline briefly, then implement.
Read `.ai/library-w3-code-review.md` and the frozen Library contract including section 8.

Owned files: `src/refcollector/web/library_routes.py`, `library_api.py` only if needed,
`tests/unit/web/test_library_routes.py` and other `test_library_*.py` regression tests.
Do not edit library_folder.py, service/engine/contracts, frontend, or orchestration files.

Fix confirmed review findings:
- F1: consume all cursors for complete project/source/reference aggregation and action
  scopes. Preserve filters between pages; keep shelf pagination after aggregation.
  Test at least 26 references, sources, and projects, including bulk download/find/sync.
- F3: usable local-copy IDs must resolve through paper detail and shelf, with correct
  local-copy file URL and cited-by source IDs. Preserve service scope/RC_HOME guards.
  Prove citation-to-paper-detail navigation at the route level.
- F5: occurrence-scoped metadata/download jobs must map to source IDs for job paper_ids,
  shelf active_job_id, and reload polling. Test the real job-result shape.

Use service APIs only; no svc.store access or new engine methods. Keep code simple.
Do not touch fallback behavior. Preserve unrelated workspace changes and comments.
Write focused regressions before fixes; report initial failures and final results.
Run only affected web tests and scoped Ruff checks. No full suite or server actions.
No commits/pushes. Write `.ai/library-w3-route-fixes.md` with files and actual results.

Current worker model: Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49). This overrides older model instructions in this brief.
