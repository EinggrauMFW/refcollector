# Wave 2 files: independent review corrections

Existing 38 new Python tests and 3 API-client tests passed independently. These findings still block acceptance. Same model, checkout, and exclusive ownership; no new worktrees/commits/pushes/server.

## Core service worker

- upload_pdf repeats the existing import-source persistence and attach-file transaction logic. Factor small private helpers used by both existing path methods and uploads. Preserve permission, idempotency, audit, revision, local-copy and identity recheck behavior. Do not create a generic pipeline abstraction or widen folders. Run focused original and upload regressions after this behavior-preserving change.
- render_source_page estimates pixels with round(), while raster allocation can round up. Derive conservative integer bounds before allocation, including fractional dimensions and scales. Add a threshold regression where rounding down would admit more than eight million pixels.
- The rotated-page test checks dimensions only. The current extraction/layout.py normalizes unrotated text bboxes using rotated page.rect dimensions and clamps values; direct overlay mapping on rotated PNGs is not proven and can lose coordinates. Do not claim that mapping is correct. Preserve existing extraction policy in this interface task. Add RenderSourcePageOut.span_overlay_supported: bool = True; return False for rotated pages. Keep the correct full-page PNG, report the limitation, and test it. Do not silently change stored spans or extraction policy. Source-span overlay acceptance for rotated pages remains explicitly limited.

## HTTP worker

- Multipart duplicate/extra/missing-file validation occurs before file_field's try/finally. Parsed UploadFile objects can leak on those rejection paths. Close the entire FormData on every path after successful parse, including validation failures. Add regression cases with a real spooled file that is closed after duplicate/extra-field errors.
- Preserve BodyTooLargeError from parsing instead of converting every parsing exception to INVALID_INPUT. Oversize streaming bodies must return 413, not 400. Test body framing above the cap.
- Expose X-Span-Overlay-Supported as 1/0 from RenderSourcePageOut.span_overlay_supported. Avoid dynamic fallback dictionaries now that core types exist, where removal is safe and small.

## UI worker

- When PNG header X-Span-Overlay-Supported is 0, render the page without span boxes and show a clear rotated-page highlight limitation. Keep accessible citation text and navigation. Do not draw misleading boxes.
- FormData retry fingerprint currently uses only file metadata. A newly selected different file can share name/size/mtime. Bind pending retry state to the actual File object identity (for example a WeakMap-generated identity) plus request fields. Same File object retains key; new file selection gets a new key even with identical metadata. Do not store bytes/secrets in browser storage. Keep source/occurrence/version/name/declared-size fields in the fingerprint.

Each worker must outline the fix, modify only its owned paths, and return precise focused test/lint evidence and remaining limits. Wait for idle before sending follow-ups; Jean did not retain busy-session messages in this run.
