# Web wave 3: trusted review and settings execution brief

Status: approved execution. Same integration checkout and Gemini 3.8 Flash High workers. No new worktrees, commits, pushes, model changes, real servers, or full-suite runs during parallel edits.

The [web specification](../../spec/web-app-spec.md) remains authoritative. This brief resolves the read-only designs. Do not copy their authorization boolean, consume-before-commit sequence, shared .tmp filename, unkeyed secret hashes, or route-level idempotency/storage access.

## Exclusive ownership

| Worker | Owned paths |
| --- | --- |
| Core 64d933db-f79f-4c54-b501-00ad08c7bc6a | contracts/api.py; services/service.py; services/changes.py; small services/web_auth.py and services/web_settings.py helpers if useful; domain/enums.py; config.py; CLI config command bodies only in cli/main.py; tests/fakes/service.py; new tests/unit/services/test_web_review.py, test_web_settings.py, tests/unit/test_config_concurrency.py; narrowly affected existing config/CLI tests |
| HTTP 1b0f363f-be0c-49d7-8a07-a6a64200b822 | web Python files; tests/unit/web/test_review_routes.py, test_settings_routes.py; existing owned HTTP tests |
| UI 95e31820-96e3-41f1-8725-63704475beb6 | web/static/** only |
| Orchestrator | briefs, decisions, specification, independent tests and integration review |

Paths without prefix are under src/refcollector. No worker may edit .ai/todo.md or another owner's paths. Outline approach before editing. Read current official documentation before library changes. Use UTF-8. Return exact changed files, commands/results, and unresolved limits.

## Frozen DTOs

All public inputs inherit Dto and forbid unknown fields. Existing ReviewDecisionIn stays unchanged. No public context/approval/grant fields. Use discriminator `type` for SettingChange.

```python
class GetSettingsIn(Dto):
    project_id: str | None = None

class BrowserStatus(Dto):
    enabled: bool
    configured: bool

class ProjectSettingsView(Dto):
    project_id: str
    network_mode: NetworkMode
    download_enabled: bool
    version_policy: VersionPolicy

class GetSettingsOut(Out):
    revision: str
    rc_home: str
    email: str | None
    allow_folders: list[str]
    download_enabled_global: bool
    pdf_fallback_url: str | None
    browser: BrowserStatus
    enabled_providers: list[str]
    mcp_scopes: list[str]
    limits: dict[str, int | float]
    keys: dict[str, bool]
    project: ProjectSettingsView | None
    warnings: list[str] = []

# Strict discriminated variants:
# SetEmailChange: type='set_email', value: str
# AddAllowedFolderChange: type='add_allowed_folder', path: str
# SetProviderKeyChange: type='set_provider_key',
#   provider: Literal['openalex_key','s2_key'], value: SecretStr
# SetProjectNetworkChange: type='set_project_network', project_id: str, mode: NetworkMode
# SetProjectDownloadsChange: type='set_project_downloads', project_id: str, enabled: bool
# SetPdfFallbackChange: type='set_pdf_fallback', value: str | None
# Fallback values: box/su/st/ru/custom HTTPS {doi} template/off/None; use current validator.

class UpdateWebSettingsIn(Dto):
    expected_settings_revision: str
    change: SettingChange
    idempotency_key: IdempotencyKey

class WebIntentOut(Out):
    confirmation_token: str
    expires_at: datetime
    purpose: Literal['review','settings']
    summary: dict[str, Any]  # Safe before/after data; no secret values/digests.
```

Add optional server-only web_session_id to CallContext, default None. Add optional opaque server-owned review/settings authorization objects, default None. Never accept boolean authorization or reconstruct objects from public JSON. Ordinary WEB_UI contexts stay agent, even with tty=True. Only the service's validated review path creates interactive provenance. Add ApprovalSurface.WEB_UI, preserving CLI_TTY behavior.

## Frozen service methods

```python
prepare_web_review(ctx, req: ReviewDecisionIn) -> WebIntentOut
confirm_web_review(ctx, req: ReviewDecisionIn, confirmation_token: str) -> UpdateReferenceOut
get_settings(ctx, req: GetSettingsIn) -> GetSettingsOut
prepare_web_settings(ctx, req: UpdateWebSettingsIn) -> WebIntentOut
confirm_web_settings(ctx, req: UpdateWebSettingsIn, confirmation_token: str) -> GetSettingsOut
update_web_settings(ctx, req: UpdateWebSettingsIn) -> GetSettingsOut
```

All remain absent from TOOLS. Existing review_decision and update_project_settings retain their signatures. Raw update_project_settings stays CLI-only. update_web_settings requires a service-validated settings authorization object; ordinary callers cannot invoke it successfully.

get_settings and intent/confirmation methods require Channel.WEB_UI plus a server-established nonempty web_session_id. Safe reads require projects:read; review requires references:write; settings writes require projects:write plus their protected intent. Verify project/occurrence/candidate relationships and expected revisions in the service, never through route storage access.

## Intent and review transaction rules

Use a small locked service-owned registry, bounded capacity, random opaque tokens, 60-second pending expiry, and the service clock. Bind principal, validated session ID, purpose, exact request fingerprint, target project/occurrence/candidate/action/revision, and idempotency key. A token for one action must never authorize another.

Preparation returns safe current review data or settings diff. Every settings write, including email and keys, requires explicit confirmation. Never return or log secret values in intent summaries.

Confirmation revalidates the active session and exact binding. Pending scope validation and mutation must be serialized with the service transaction; do not delete a token before commit and then lose valid retries. Record completion only after a successful mutation. An exact same-key/fingerprint retry returns the stored response without a second Approval. Changed payload/key, wrong session/project/action, expiry before first mutation, or stale revision fails without effects. Completed replay must not be rejected solely because the pending token was consumed. Define bounded completed-token retention and safe re-preparation after restart/expiry using durable idempotency; no new approval on replay.

The web approval is revision-bound and records surface web_ui. Reject produces a decision without Approval. Preserve stale-approval invalidation and audit reasons. A rejected or replaced candidate cannot be approved by an old intent. CLI real-TTY behavior and all ordinary accepted_agent decisions remain unchanged. This is a local policy boundary, not proof of human authorship.

## Settings concurrency, secrets, and recovery

Implement section 9's enumerated writes only. Executable paths, provider lists, MCP scopes, global download grant and arbitrary dictionaries remain read-only. Show the alternative project OR global download grant accurately.

Use a shared sidecar configuration lock for all CLI/global web writers, including read-modify-write. The lock must survive config.json replacement. Save through a unique same-directory temporary file, flush/close and os.replace; never a shared config.tmp. Existing save_settings remains compatible. Avoid nested non-reentrant file-lock deadlocks. Lock project state in the SQLite mutation transaction when comparing its revision. Atomic replacement alone is not an optimistic concurrency check.

GetSettingsOut.revision covers fresh canonical global config, target project state, and key changes. CLI edits must invalidate stale web revisions. Do not truncate revision digests unnecessarily. If keyring is unavailable, report a safe warning and fail key writes closed. Refresh the shared service settings after successful changes so future jobs use new settings; existing job snapshots remain unchanged.

Key values use SecretStr, never appear in responses, errors, repr logs, audit or persisted idempotency/journal payloads. SecretStr's masked JSON is not a sufficient fingerprint: different secrets must conflict. Use stable keyed HMAC for actual secret input, with a service-owned persisted key or equivalent OS-keyring secret. Never use plain SHA(secret). Keep secret values only in memory until keyring write.

Project-only changes can commit their effect and idempotency in one SQLite transaction. Global JSON/keyring effects cannot share a SQLite transaction. Use the smallest recoverable pending/completed record with existing idempotency storage: only request digest, safe result and before/after revision digests, no raw secret. Persist pending before external effect; complete after effect. On retry, recognize already-applied target revision and finish the record without repeating the effect. If state matches neither before nor after, fail closed with conflict. Do not silently claim cross-store atomicity from os.replace alone. Test failures between external write and durable completion. Do not leave hidden grants applied after a reported validation failure.

Global settings records use the existing empty project scope, not a fabricated project. Existing _mutate assumes a real project after its callback; do not call it blindly for global writes. Preserve seven-day principal/project/tool/key replay scope.

## HTTP freeze

Build CallContext.web_session_id from require_session's validated Session, never body/header claims. Keep existing Host/Origin/CSRF controls for all POST routes.

| Route | Body/service |
| --- | --- |
| GET /api/settings?project_id=... | get_settings(GetSettingsIn) |
| POST /api/settings/intent | prepare_web_settings(UpdateWebSettingsIn) |
| POST /api/settings | confirm_web_settings(UpdateWebSettingsIn, X-Confirmation-Token header) |
| POST /api/projects/{p}/references/{o}/review-intent | prepare_web_review(ReviewDecisionIn), validate path/body IDs |
| POST /api/projects/{p}/references/{o}/review | confirm_web_review(ReviewDecisionIn, X-Confirmation-Token header), same ID checks |

Do not add /review-decision as a competing route. Keep raw project-settings stub unavailable. No HTTP call may invoke private _replay, storage, keyring, or config writers. No route creates arbitrary trusted grants. The service owns all intent checks and transactions. Missing token returns structured NOT_AUTHORIZED. Existing error mapping applies. Ensure secret-bearing validation errors and unexpected logs do not expose input values.

## UI freeze

UI displays safe settings and CLI-only instructions. Enumerated write forms first request an intent, display its safe diff, then require a separate explicit confirm button. Use the same body/idempotency key for both steps. Confirmation sends X-Confirmation-Token, never an approval boolean. Clear key input after success/cancel; do not store secrets in browser storage, pending fingerprint strings, or logs.

Extend api-client fingerprinting for secret changes without retaining raw keys: ephemeral keyed digest or a per-form private identity plus stable intent request/key. Retain logical keys after uncertain responses. Add API-client regressions if shared request handling changes.

Candidate review is separate from ordinary agent selection. Show current candidate/reasons/revision before intent confirmation. Never auto-rebase stale approval. Expired/stale intent requires a fresh review/diff. UI updates changed/created reference IDs from the service response.

## Focused verification and exit

Core: test all change variants, CLI compatibility, fresh/stale revisions, fake keyring failures, no secret in stored records, same-key/different-key replay, interrupted external-write recovery, snapshot preservation, forged/missing/expired/wrong-target intent, no duplicated approval, ordinary web/TTY/MCP guards. Use isolated homes and fake keyring, no live secrets/network.

HTTP: test session/CSRF-before-service, exact route/body/header mapping, secret-redacted errors, conflicting IDs, wrong-session tokens and existing stubs. UI: existing API-client regressions and changed logic tests; browser remains unverified until Jean Run exists.

No worker may claim local dev environment verification from pytest/TestClient alone. Orchestrator performs combined checks after all workers finish, then full cross-cutting regression once. Review all remaining limits before any release claim.

Documentation references checked for this freeze: https://portalocker.readthedocs.io/en/latest/quickstart.html ; https://docs.python.org/3/library/tempfile.html ; https://docs.python.org/3/library/os.html#os.replace ; https://docs.pydantic.dev/latest/concepts/serialization/ . Verify installed versions/API shape before use.

Root added independent tests/unit/services/test_web_authority_http.py (read-only to workers). It exercises real HTTP/service review replay and write-only keyring settings with real SQLite persistence and a fake keyring. Its initial red result is expected while routes remain stubs. Root will run it after implementation; workers must not change its assertions or claim it passed before execution.
