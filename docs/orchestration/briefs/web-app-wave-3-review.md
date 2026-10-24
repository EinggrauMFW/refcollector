# Wave 3: independent review findings

These checks are independent of worker-owned tests. Do not edit root-owned assertions.

| Finding | Evidence | Required correction |
| --- | --- | --- |
| config_lock tracks one thread-local depth for all homes. Holding home A therefore skips acquiring home B, even when another thread owns B. | tests/unit/test_web_config_atomicity.py::test_nested_different_home_cannot_bypass_another_homes_lock fails: no LockException. The first two atomic-save tests pass. | Track reentrancy by resolved home identity, not global depth. Preserve same-home nesting and real cross-home locking. Test timeout/failure cleanup. |
| Raw review_decision trusts a fabricated ReviewGrant with matching IDs, without registry proof. | tests/unit/services/test_web_channel.py::test_forged_review_grant_cannot_create_user_approval reproduces accepted_user from a never-issued token. | Require an active service-owned authorization bound to registry/session/principal/purpose/payload/key, serialized with transaction. A dataclass's presence is not authorization. |
| Raw update_web_settings trusts a fabricated SettingsGrant even on Channel.MCP. | test_forged_mcp_settings_grant_cannot_change_global_config reproduces a global email write. | Reject non-WEB_UI channels and missing sessions unconditionally. Validate service-issued authorization, expiry and exact token binding before mutation. |
| prepare_web_review reads occ.candidates, but Occurrence has no candidates field. | Independent real HTTP workflow raises AttributeError. | Fetch candidates through the existing scoped candidate repository; verify association and rejection state. |
| Settings replay re-runs _meta and changes the stored response's request ID. | Independent HTTP key-write replay differs from the original response. | Return the durable cached DTO unchanged, consistent with existing idempotent methods. Transport request IDs can be separate. |
| Keyring failure interpolates the backend exception, which may contain the raw secret. | New independent backend-echo test in test_web_authority_http.py. | Return a generic keyring error with no secret-bearing cause in client payloads or logs. Do not log raw backend exception text/traceback. |

Root-owned checks: tests/unit/services/test_web_authority_http.py; tests/unit/test_web_config_atomicity.py; tests/unit/web/api_client.test.cjs. The authority workflow initially failed on unimplemented routes; rerun after core completion. Four client checks currently pass, including secret-free pending fingerprints.

Dispatch this review only after the relevant worker becomes idle. Jean omitted busy-session followups earlier in this run. Do not claim the final gate until these checks pass against the completed implementation.

Additional independent evidence: the real keyring workflow now injects failure in the completed idempotency record after the external key write. Recovery reaches the stored key without repeating the write, but exact replay still fails because `_meta` changes the response request ID. Two separate wrong-session/expired-intent HTTP checks pass. These checks use fake credentials and in-process ASGI only.

The same keyring leak also affects reads: `get_settings` interpolates `{e}` into a warning. `test_keyring_read_failure_returns_safe_warning` requires a generic warning without backend exception text or traceback. Cover both read and write failures; do not change root-owned assertions.

Also inspect the read-only findings in `web-app-wave-3-extra-review.md`: project settings currently enter a SQLite write transaction before acquiring config_lock, while global settings take the reverse order. The project branch also releases config_lock before comparing revisions and forming its output. Fix lock ordering across all affected paths, including any snapshot refresh. Do not blindly add a config lock inside snapshot(), which runs inside existing mutation transactions. The stale CLI-settings snapshot observation needs a focused test and a compatible ordering solution; confirmed web writes already update self.settings. Keyring failures during pending recovery must not become a false revision conflict.
