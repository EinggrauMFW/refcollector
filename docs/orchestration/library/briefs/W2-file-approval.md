# W2 brief: file approval (S2, option 2, D-46)

User decision, 23 October 2026: a PDF held at `needs_review` (spec 9, checks 4-6) can be approved
by the user through a confirmed web step. Approval makes the file `verified` and records an audit
event. This amends spec section 9 ("never `verified`") for explicit, confirmed user approval only.

## Engine contract (backend B7)

```python
class ReviewFileDecisionIn(Dto):
    project_id: str
    occurrence_id: str
    work_file_id: str
    action: Literal["approve", "reject"]
    expected_revision: int          # occurrence revision
    note: str | None = None
    idempotency_key: IdempotencyKey

class ReviewFileDecisionOut(Out):
    occurrence_id: str
    work_file_id: str
    retrieval: RetrievalState
    revision: int
```

Service methods, mirroring `prepare_web_review` / `confirm_web_review` exactly (same intent registry,
session, principal, expiry, project/occurrence/revision binding, idempotent replay):

- `prepare_web_file_review(ctx, req: ReviewFileDecisionIn) -> WebIntentOut` (`purpose="file_review"`;
  extend the `WebIntentOut.purpose` literal). Summary and description name the file (size, source
  host), its failed check reasons, and the citation's accepted work title.
- `confirm_web_file_review(ctx, req, confirmation_token) -> ReviewFileDecisionOut`

Rules:
- Only `Channel.WEB_UI` with an active web session; MCP and generic calls get `NOT_AUTHORIZED`.
- The work file must belong to the occurrence's accepted work and be `needs_review`; else
  `INVALID_INPUT`. Revision mismatch: `REVISION_CONFLICT`.
- `approve`: `WorkFile.state = verified` (keep its `reasons` for audit), occurrence
  `retrieval = verified`, bump occurrence revision, audit events `FILE_APPROVED` with
  `ApprovalSurface.WEB_UI`, principal, file sha256 and the reasons that were overridden.
- `reject`: `WorkFile.state` stays `needs_review` but is excluded from the occurrence
  (`retrieval = failed`, reason `FILE_REJECTED_BY_USER`), audit `FILE_REJECTED`. A later download
  job may try other locations.
- One file per confirmation. No bulk approval. Add the two audit event types and the reason code.
- Export and folder sync treat an approved file like any verified file.

Library routes (backend B7): `POST /collections/{cid}/citations/{oid}/file-review/intent` and
`.../file-review/confirm` (token header), body `{work_file_id, action, expected_revision,
idempotency_key}`, returning `Intent` and the updated `Citation`. After approval the next shelf read
syncs the file to the folder and shows the downloaded paper with `cited_by`.

Backend owned files: `contracts/api.py`, `domain/enums.py` (new event types and reason code only),
`services/service.py` (the two methods only), `services/web_auth.py` (add `file_review` to the two existing purpose literals only), `web/library_api.py`, `web/library_routes.py`,
`tests/fakes/service.py`, `tests/unit/services/test_web_file_review.py` (new),
`tests/unit/web/test_library_routes.py`.

Backend tests: approve and reject happy paths; missing/expired/mismatched token; wrong session;
non-web channel; file not `needs_review`; file of another work; stale revision; idempotent replay
without a second audit event; approved file appears in the export file list.

Acceptance:
```
uv run pytest -o addopts="" -q tests/unit/web tests/unit/services tests/contracts
uv run ruff check src tests
uv run ruff format --check src tests
```

## UI (U2 Phase D)

`check_kind == "file"` rows open a file panel: the PDF in the viewer (`GET /files/work_file/{id}`),
the failed check reasons in plain words, the cited work title, and Approve PDF / Reject PDF with
the two-step confirm that shows `Intent.description`. After approve: row becomes Downloaded and the
shelf refreshes. Mock and types first, Vitest for the panel state, then live after the orchestrator
says B7 is served. Do not touch fallback-related UI.

UI owned files: `web-ui/**`, `src/refcollector/web/static/library/**`.

## Orchestrator B review follow-up (23 October)

Initial independent gate: 313 passed, 2 warnings in 28.42s; Ruff passed; 231 files already formatted.
Do not start U2 live until this review follow-up passes.

- Add individually named or parametrized confirmation request-binding tests: change project,
  occurrence, work file, action, revision, idempotency key, and note after intent creation.
  Require NOT_AUTHORIZED and prove no file/occurrence/audit mutation.
- Test wrong token purpose and a token prepared for another file. Prove one-file binding.
- Test confirm (not only prepare) rejects MCP, CLI, and missing web session. Assert neither
  method is exposed through contracts.api.TOOLS or the MCP tool surface.
- Test occurrence revision changed after prepare produces REVISION_CONFLICT at confirm.
- Add file_review to IntentRecord.purpose and IntentRegistry.register purpose literals;
  this is the narrowly authorized web_auth.py extension under existing D-46.
- Check accepted identity explicitly in both new service methods, not only nonempty work_id,
  and test rejection of an unaccepted identity with a retained work_id.

Preserve all existing token checks, replay behavior, ownership, and unrelated changes.
No fallback work. Do not start or stop the server. No commit or push.
