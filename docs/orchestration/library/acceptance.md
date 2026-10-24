# Library UI acceptance evidence

Date: 23 October 2026. Active orchestrator: B. No commit or push authorized.
All eight acceptance items have evidence. Final review fixes passed the full gate and live readback.

| Specification item | Evidence | State |
|---|---|---|
| 11.1 Create collection, grant permissions, upload, citations | U3 results item 1; five citations and three separately confirmed grants | Passed; final readback/regressions pass |
| 11.2 Metadata and confirmed identity Check | U3 results item 2; five individually confirmed DOI matches | Passed; final readback/regressions pass |
| 11.3 Download, folder, shelf, cited by | U2 Phase D and U3 item 3; single-file approval; folder hash checked independently | Passed; final readback/regressions pass |
| 11.4 Downloaded citation viewer | U2/U3 rendered screenshots and HTTP/file hash proof | Passed; F3 local-copy route/stream regressions also pass |
| 11.5 Repeat download without duplicate copies | U3 item 5; source and downloaded PDF names, sizes, and hashes unchanged | Passed; final readback/regressions pass |
| 11.6 Existing advanced dashboard Selenium | advanced_w3_regression.py uses the existing check at /advanced; exit 0, DONE True | Passed |
| 11.7 Python/full suite/Ruff/build/lint | Final full gate: 1434 passed, 2 warnings; Ruff clean; 233 formatted; frontend build/lint pass | Passed |
| 11.8 Repeatable Selenium script for 1-5 | .ai/selenium-ui-check/library_flow.py; completed one live run, exit 0 | Passed; final readback/regressions pass |

## Evidence locations

- Phase D: `.ai/live-browser-evidence/library-u2d/`.
- U3: `.ai/live-browser-evidence/library-u3/20261023T040755554269Z/`.
- Existing advanced regression: `.ai/live-browser-evidence/library-w3-advanced/`.
- Review: `.ai/library-w3-code-review.md`.
- Route corrections: `.ai/library-w3-route-fixes.md` when the worker finishes.
- Folder corrections: `.ai/library-w3-folder-fixes.md` when the worker finishes.
- Cleanup candidates: `.ai/live-browser-evidence/library-w3-collections/test-collections.md`.

The current cleanup list contains 16 known automation collections, with IDs and folders.
No collection was deleted. Other collection names are not classified as cleanup candidates.

## Limits

One of five OA citation PDFs was available for approval and verified. Provider failures,
version-policy skips, and validation failures affected other references. Do not claim all
papers downloaded, or that a new network transfer occurred when stored/cache reuse was possible.
No fallback controls were tested as part of this Library wave.

Review found truncation above service page limits, a concurrent overwrite/record race,
local-copy navigation, create replay, and occurrence-scoped job mapping defects. All confirmed findings were corrected and regression-tested before final acceptance. Native hostile directory-swap races
were not exercised; no such protection claim is made by this acceptance record.

## Final gate and final-backend proof

- `uv run python scripts/check.py`: exit 0; 1434 passed, 2 warnings in 168.59s.
- Frontend build/lint: exit 0. Existing `/advanced` regression: exit 0, DONE True.
- Read-only Library Selenium after backend reload: exit 0, PASS, no browser errors.
- Live large-paper detail: HTTP 200, 250 citations returned and counted.
- Folder corrections: 39 focused tests pass. Route corrections: 45 tests pass.

Final log: `.ai/library-w3-final-gate.log`; exit file: `.ai/library-w3-final-gate.exit`.
Pagination proof: `.ai/live-browser-evidence/library-w3-pagination-live.json`.
The full live mutation flow was not repeated after review fixes. Final readback, the live
250-citation check, focused regressions, and full-suite execution cover those changes.
