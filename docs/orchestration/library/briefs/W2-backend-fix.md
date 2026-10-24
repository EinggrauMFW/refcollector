# W2 backend fix brief: B4 (live findings from U2 Phase B)

Worker: Antigravity `gemini-3.8-flash-high`, high effort. Same rules and owned files as
`W2-backend.md`, plus `src/refcollector/web/dependencies.py` (error hint text only).
Outline your approach first. Find each root cause before you edit; add a regression test per bug
that fails before the fix.

Evidence: `.ai/live-browser-evidence/library-u2/results.json`, script
`.ai/selenium-ui-check/library_u2_live.py`. Live server: `http://127.0.0.1:8001/` (Jean run env,
do not stop or start it). Live data: collection `U2 live 1022-222036`, folder under
`.ai/live-browser-home/library-test/`.

| # | Bug | Expected |
|---|---|---|
| 1 | `Collection.settings_revision` from `GET /collections` differs from the revision `permissions/intent` accepts, so the next grant fails with `REVISION_CONFLICT`. Seen: listed `dd32e157…`, expected `bc59f0a8…`, then `1a8341c1…`. | `settings_revision` is exactly the value `prepare_web_settings` checks against (same source as `get_settings` / the old `/api/settings` route). Test: list, intent, confirm, list, intent again succeeds. |
| 2 | `settings_revision` and `permissions` do not change after `permissions/confirm`. | The confirm response and the next `GET /collections` show the new revision and the granted permission. |
| 3 | Folder sync never copies the uploaded PDF. `folder_path` stays null, record `files` stays empty, `POST /sync` returns 200 and copies nothing. `_sync_uploaded_file` swallows errors. | Uploaded and verified files are copied once the folder grant exists. Sync failures are logged with the exception (`logging`), and `POST /sync` returns the error (`code`, `message`) when nothing could be copied because of an error. Test with a real temp folder that is an allowed folder. |
| 4 | `Citation.order` is 0.0 for every citation (contract 8.9 not met). | Order from the occurrence; list sorted by it. Test with occurrences of distinct orders through the real route mapping, not a fake that already has the field. |
| 5 | Reason codes come uppercase and duplicated (`PROVIDER_DISAGREEMENT` twice). | `reasons` deduplicated, order kept; raw code values (the UI formats them). |
| 6 | Review intent description is a bare id ("Accept candidate cand_… for citation"). | Description names the candidate title, first author and year, and the citation text (first 120 characters). |
| 7 | Missing session cookie returns `recovery_hint` "Grant the scope in the config." | Hint: "Reload the page to start a new session." for session-cookie errors only. |

Not a bug: `Origin` required on mutations (CSRF defence). Keep it.

## Acceptance

```
uv run pytest -o addopts="" -q tests/unit/web tests/unit/services/test_library_folder.py
uv run ruff check src/refcollector/web src/refcollector/services/library_folder.py tests/unit/web tests/unit/services/test_library_folder.py
uv run ruff format --check src/refcollector/web src/refcollector/services/library_folder.py tests/unit/web tests/unit/services/test_library_folder.py
```

Then tell the orchestrator the server needs a restart (Python changes). Report root cause per bug,
files, commands with results.
