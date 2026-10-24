# Web wave 3: trusted review and settings contract freeze

Status: read-only design tasks; no code changes until the orchestrator freezes shared contracts. Current integration checkout, same Gemini 3.8 Flash High, no worktrees/commits/pushes.

Wave 2's corrected implementation passed 108 independent Python regressions and 3 API-client tests. Rotated pages render safely but explicitly suppress span highlights. Live browser verification remains blocked by the missing Jean Run command.

## Review design worker

Session: 64d933db-f79f-4c54-b501-00ad08c7bc6a. Inspect current review_decision, CallContext, approval persistence, idempotency and web SessionStore. Return the smallest concrete trusted web review contract: exact DTOs/signatures, server-only authorization binding, intent/confirmation routes, short expiry, atomic scope consumption, same-key replay without duplicate approval, stale revision handling and provenance. Ordinary web calls stay agent; no fake TTY, public approval flags, or MCP tools. Tests must cover authorization and state transitions, not mocked method calls alone.

## Settings design worker

Session: 1b0f363f-be0c-49d7-8a07-a6a64200b822. Inspect config.py, CLI config commands, project settings service and the proposed section 10.4 DTOs. Return exact get_settings/update_web_settings contracts and confirmed-intent binding. Cover safe reads, enumerated non-executable changes only, write-only keyring secrets, optimistic revisions across CLI/file/project changes, idempotency without storing raw keys, and atomic failure behavior. Existing save_settings uses direct JSON writes; identify the smallest safe concurrency change before proposing web writes. Preserve running job snapshots. No broad CLI guard relaxation or arbitrary config dictionaries. Explain the limited D23 amendment needed; no implementation yet.

Both workers outline the approach, read only relevant files, check current official documentation before library-dependent decisions, and return precise proposed file ownership and focused tests. Do not edit any files, including .ai/todo.md. The orchestrator owns task tracking. UI worker remains idle until contracts freeze.
