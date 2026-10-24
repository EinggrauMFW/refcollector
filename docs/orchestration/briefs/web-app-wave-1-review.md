# Web wave 1: remaining HTTP review corrections

Owner: HTTP/security session `1b0f363f-be0c-49d7-8a07-a6a64200b822`. Same Gemini 3.8 Flash High model and original owned paths. The orchestrator also permits narrow edits to existing web serve tests in tests/unit/cli/test_commands.py.

This checklist is the authoritative follow-up. Do not claim completion until every item has evidence or an explicit blocker.

- [ ] Add classification_is_null to the actual references query route, including false semantics and conflicting classification validation.
- [ ] Replace tests/unit/cli/test_commands.py::test_serve_web_not_available with new D38 delegation tests that stub the server entry point. Never run it unmodified: it could start a real server. Test missing extra and invalid host/port without opening a listener.
- [ ] Sanitize Content-Disposition filenames: quotes, CR/LF, control characters, path separators and non-ASCII. Use safe ASCII fallback and encoded filename*. Add regression cases.
- [ ] SessionStore must be genuinely thread-safe and have absolute expiry, not unlimited sliding lifetime. Validate positive capacity/TTL. Keep bounded sessions and reuse valid session bootstrap.
- [ ] Validation errors must emit only safe field/type/message data. Do not serialize input or ctx ValueError objects. Catch Pydantic ValidationError raised while constructing DTOs inside routes, not only FastAPI RequestValidationError. Invalid list limit/sort/filter conflicts must return structured 400, not INTERNAL.
- [ ] API error handling preserves retryable and retry hints. Use existing RcError payload contract.
- [ ] Web-only tests must skip cleanly when the optional web extra is absent. Do not turn optional runtime dependencies into mandatory core test imports.
- [ ] Run changed focused tests and owned-path lint/format checks. Do not format other workers' tests. No full suite, server, commits or worktrees.

Root-owned integration test tests/unit/web/test_service_workflow.py is read-only to this worker. It passed real text extraction/export/hash download and executor cleanup before these changes. Root will rerun it only after relevant changes settle.

Use official FastAPI/Starlette documentation before changing framework behavior. The exact Host/Origin/session/CSRF boundary remains mandatory. No fake TTY or CLI provenance. Context uses Channel.WEB_UI; trusted review/settings remain unavailable until wave 2.
