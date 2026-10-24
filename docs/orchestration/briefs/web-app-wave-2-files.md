# Web wave 2: managed PDF upload and safe source preview

Status: frozen design; dispatch only after wave-1 HTTP corrections pass. Same current integration checkout, Gemini 3.8 Flash High, no worktrees/commits/pushes.

## Service worker ownership

Continue contracts/api.py, services/service.py, and narrowly needed service helpers. Add tests/unit/services/test_web_upload.py and test_web_preview.py. Existing other ownership boundaries remain. Do not edit HTTP/static/config/CLI files.

Implement upload_pdf from web-app-spec.md section 10.3. BinaryIO input is an in-process port, not a JSON/base64 tool. Keep existing unavailable source_type=upload behavior unchanged. No TOOLS additions.

Requirements:

1. Require source import scope or file attach scope by purpose. Validate project and local-copy occurrence/accepted identity before expensive parsing where practical.
2. Restrict display names to safe leaf names; reject traversal/control/path separator input. Do not use the display name as storage path.
3. Stream to a unique service-generated staging file with an actual byte cap. Validate declared size against actual bytes. Hash actual bytes and include hash in idempotency fingerprint. Same key/different bytes conflicts. Same-key identical replay returns existing output without duplicate rows.
4. Validate a real PDF through existing preflight/local-copy algorithms, not only magic or MIME. Reject encrypted/invalid/oversize files under existing rules. Source upload returns ImportSourceOut in the source branch; attachment returns AttachFileOut in attachment branch. Exactly one branch.
5. Share existing import/attach persistence and validation helpers. Do not duplicate pipeline algorithms or temporarily widen settings.allow_folders. Any internal managed-path exception must be private, per-invocation, restricted to that generated file and purpose. No public input can assert that a path is managed.
6. Preserve attachment revision/identity rechecks and all five local-copy checks. Do not relabel unknown versions/licences or rejected copies.
7. Clean staging on every success/failure/cancellation. Failed requests must leave no source/local-copy rows. Never clean another active request's staging files.
8. Add focused real-PDF tests for source success/deduplication, attachment behavior, hash-conflicting replay, invalid/encrypted/oversize/truncated bodies, traversal names, missing scopes, wrong project, and cleanup. Use existing fixture builders and isolated homes, no live network.

## Source raster contract

```python
render_source_page(ctx, req: RenderSourcePageIn) -> RenderSourcePageOut
# In: project_id: str, source_id: str, page_index: int >= 0,
#     scale: float = 1.0, bounded 0.5 <= scale <= 2.0 and finite
# Out: data: bytes, media_type: Literal["image/png"],
#      page_index: int, width: int, height: int
```

Require references:read. Use an authorized project source and content-store integrity validation. Reject non-PDF sources, missing/cross-project IDs and invalid page bounds. Render with existing PyMuPDF in a service worker, never inside a route. Check raster dimensions before allocation; cap at 8 million pixels and reject outliers. Never execute PDF scripts, external actions, or embedded attachments. PNG bytes are the only HTTP preview output.

Consult current official PyMuPDF documentation before renderer implementation. Coordinate spans/page rotation with the existing extractor; document the coordinate convention and test a rotated page or fail explicitly if unsupported. Return exact output dimensions for fractional bbox overlays. Do not create a same-origin iframe with both allow-scripts and allow-same-origin.

## HTTP worker ownership after service freeze

Implement POST /api/projects/{project_id}/uploads/pdf using bounded multipart file plus purpose, occurrence_id?, version_hint?, declared_bytes and idempotency_key. Enforce session/Origin/CSRF before parsing. Multipart overhead has a small bounded allowance; the service still enforces exact PDF byte limits. Offload BinaryIO service calls; close upload resources on all paths. Return UploadPdfOut JSON.

Implement GET /api/projects/{project_id}/sources/{source_id}/pages/{page_index}?scale=1 as PNG bytes through render_source_page. Preserve project/session/read scopes, nosniff, no-store and body/memory bounds. Never open file paths in routes. Add focused HTTP mapping/security tests. No real server starts.

## UI worker ownership after HTTP freeze

Enable browser PDF source upload and local-copy attachment through the exact multipart route. Preserve stable idempotency on uncertain responses and explicit source/local-copy results. Show validation failures without claiming success. Preview spans using returned raster pages in an img/canvas display with accessible citation text; use returned dimensions and normalized bbox fractions. No PDF execution, remote renderer/CDN, or unsafe iframe. Update only static assets and validate changed JavaScript. Live browser verification remains a separate gate.

## Exit

All focused tests and independent service/HTTP workflow checks must pass. Record D-12's narrow upload amendment before dispatch. Trusted web review/settings are still required next; do not silently enable their unavailable controls or claim full specification acceptance.
