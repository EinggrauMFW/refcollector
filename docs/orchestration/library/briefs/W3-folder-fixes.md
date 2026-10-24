# W3 folder fixes: F2, F4, C1

Worker: Antigravity antigravity/gemini-3.8-flash-high, high effort. Outline briefly, then implement.
Read `.ai/library-w3-code-review.md`, D-42, and the Library folder contract.

Owned files: `src/refcollector/services/library_folder.py`,
`tests/unit/services/test_library_folder.py` only. No routes, frontend, engine, or
orchestration-file edits. The other worker owns route fixes in parallel.

Fix F2: copied-file publication must never replace an existing destination, even when
an external writer creates it after the existence check. Use an atomic no-replace
publication and retry a collision suffix. Protect the full record read/update/save
sequence against concurrent sync/create writers; atomic JSON replacement alone is
insufficient. Preserve temp cleanup, grant/containment rules, and idempotent hashes.
Add deterministic controlled-interleaving and concurrent-record regression tests.

Fix F4: create must preserve an existing same-folder record and mappings. Reject a
changed folder for the same project rather than resetting the record. Test both replays.

Address C1 at the same filename boundary: reject Windows drive/anchor/alternate-stream
names on every platform, and validate the final destination containment. Test drive-
relative, rooted, ADS, separators, and normal valid filenames. Do not claim a native
junction-race proof for C2; record the remaining limit rather than redesigning the store.

Use the smallest maintainable solution and existing dependencies. Check current official
documentation before relying on unfamiliar filesystem API semantics. Add regression tests
first and record initial failures. Run the focused folder test file and scoped Ruff.
No full suite, browser/server actions, commits, pushes, collection deletion, or fallback work.
Write `.ai/library-w3-folder-fixes.md` with files, exact results, and verification limits.

Current worker model: Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49). This overrides older model instructions in this brief.
