# Live PNG preview blocked by default CSP

Bounded ownership: src/refcollector/web/security.py only. Root owns browser/server on 8001. Do not start, stop or restart servers or browsers. No .ai/todo edits, model changes, commits or worktrees.

Live source src_01M5FAK1ZP9Y7399NEQHVCMWZ2 raster GET succeeds, UI creates blob:http://127.0.0.1:8001/... PNG URL, image.complete=true and naturalWidth=0. UI caption incorrectly appears rendered, but actual image does not load. Current CSP lacks img-src and therefore falls back to default-src 'self', which Chromium rejects for this blob image.

Add only the necessary image-source directive, img-src 'self' blob:, to the CSP response header. Keep default-src/script-src/style-src self, object-src none, frame-ancestors none and all other security guards unchanged. Do not permit data:, external image hosts, unsafe-inline, blob scripts or blob frames. This is a narrow image-only compatibility rule for authenticated service-generated PNG bytes, not a policy-wide bypass.

Current primary references checked: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/img-src and https://developer.mozilla.org/en-US/docs/Web/API/Response/blob . Blob display is used by existing production raster client.

At most one targeted source read, apply minimal patch, run existing web test suite and owned lint/format. Do not add tests that only assert a header constant. Root will restart through Jean after ongoing browser checks, verify actual PNG dimensions and visible image, and retain negative Host/Origin/CSRF checks. Return actual evidence.
