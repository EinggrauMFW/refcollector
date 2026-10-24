# Web wave 3: active core config & settings review

Status: read-only architectural inspection of core configuration and settings implementation.
Target files: `src/refcollector/config.py`, `src/refcollector/services/service.py`, `src/refcollector/services/web_settings.py`, `src/refcollector/services/web_auth.py`, `src/refcollector/cli/main.py`.

Avoids duplication of the 6 known findings in `docs/orchestration/briefs/web-app-wave-3-review.md`.

## New Architectural Findings

| # | Finding | Location | Impact | Recommended fix |
|---|---|---|---|---|
| 1 | Inverted lock hierarchy: SQLite transaction before `config_lock` in project branch vs `config_lock` before SQLite in global branch | `src/refcollector/services/service.py:2220-2227` vs `lines 2278-2300` | Classic ABBA deadlock between concurrent project settings update and global settings update | Acquire `config_lock(home)` consistently before `self.store.transaction(immediate=True)` in both branches, or load config before opening the transaction |
| 2 | Premature release of `config_lock` in project settings update | `src/refcollector/services/service.py:2227-2231` | Concurrent CLI writer during project update causes output response to return stale global configuration and obsolete revision hash | Hold `config_lock(home)` across the full revision computation and response generation in project branch |
| 3 | In-memory `self.settings` desynchronization for newly enqueued jobs | `src/refcollector/services/service.py:165-166` (`self.snapshot`) | CLI global configuration edits (e.g. email or fallback) are ignored when starting new jobs via web because web service caches startup settings | Load fresh settings (`load_settings(self.settings.rc_home)`) inside `snapshot()` when creating a new job snapshot |
| 4 | Masking keyring exceptions during pending recovery causes false `RevisionConflict` | `src/refcollector/services/service.py:2284-2286` & `2316-2320` | Transient OS keyring failures during recovery replay falsely report unrecoverable revision conflict (HTTP 409) instead of keyring integration error | Propagate `IntegrationUnavailable` during recovery rather than silently substituting `None` fingerprints |
