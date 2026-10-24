# W2 backend brief: B6 (download path for open-access copies)

Worker: Antigravity `gemini-3.8-flash-high`, high effort. Outline first. Find the root cause before editing;
one regression test per bug that fails before the fix. Do not start or stop the server.

Evidence: U2 Phase C, `.ai/live-browser-evidence/library-u2c/` (`oa-engine-state.json`,
`oa2-engine-access.json`, `results-final.json`). Live collection with 5 `eligible_found` citations
from `fixtures/library_oa_thesis.pdf`.

| # | Bug | Expected |
|---|---|---|
| S1 | `can_download` is false for every citation, so Download answers "No downloadable citations found" although all 5 are `eligible_found`. `is_downloadable` in `library_api.py` needs `work_id`, which the summary/list rows may not carry. Another agent may already have changed this; check the current code first and report if it is already fixed. | `can_download` equals the service rule `_downloadable` (accepted identity, work_id set, discovery checked, not excluded), using data the service DTOs expose. If `work_id` is missing from `OccurrenceSummary`, add it as an additive field with default `None` (same pattern as D-45). |
| S2 | Citations with `retrieval == needs_review` show as Check, but the Check panel only offers identity candidates, so a file review cannot be resolved from the library. | Contract addition: `Citation.check_kind: Literal["identity", "file"] | None`. For `file`, `GET /citations/{oid}` returns the pending file (work_file id, size, source URL, failed checks), and the review routes accept the file decision through the existing engine review path for files. If the engine has no web path for file review, stop and report the exact service method needed instead of adding one. |
| S3 | `downloaded` status with `downloaded_paper_id: null` when retrieval is `satisfied_by_local_copy` without a managed file. | Either map that case to the local copy (kind `local_copy` in the file route), or keep `null` and set status `downloaded` only when a file exists. Pick the smaller change and state it. |

Scope: only the open-access download path above. Do not modify, extend or test any configured PDF
fallback behaviour; leave that code as it is.

Owned files: `library_api.py`, `library_routes.py`, `contracts/api.py` (additive fields only),
`services/service.py` (fill fields only), `tests/fakes/service.py`, `tests/unit/web/test_library_*.py`.

## Acceptance

```
uv run pytest -o addopts="" -q tests/unit/web tests/unit/services tests/contracts
uv run ruff check src tests
uv run ruff format --check src tests
```

Report root cause per bug, files, results, and whether the server needs a restart.
