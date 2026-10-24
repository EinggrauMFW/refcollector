# Live browser blocker: initial inline styles rejected by CSP

Current app is running through Jean on http://127.0.0.1:8001/ with isolated RC_HOME=.ai/live-browser-home. Preserve server PID 107776 and legacy server on 8000. Root owns live browser and evidence; do not start servers or other browsers.

Actual Chromium screenshot: .ai/live-browser-evidence/dashboard.png. Dashboard loads but navigation clicks hit modal-backdrop. Browser observation: getComputedStyle(modal-backdrop).display is block; no dialog has open attribute; elementFromPoint at Projects button returns modal-backdrop. HTML has style="display:none". CSP is style-src 'self', so the initial style attribute is rejected. Many other initial styles in index.html are also rejected.

Ownership: web/static/** only. Briefly outline approach, then fix the narrow source issue. Keep CSP unchanged and strong; no unsafe-inline, nonce shortcuts, browser security bypass, or blanket style permissions. Move static inline styles into external CSS and preserve dynamic show/hide behavior. Audit existing .style.display='' reset paths so new CSS hiding classes do not leave controls permanently hidden. Direct element.style properties remain supported under CSP; setAttribute('style', ...) and style strings in constructed HTML do not. Keep existing upload/raster/intent protections and secret handling.

Source checked: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/style-src-attr . Direct style property assignments are allowed; HTML style attributes are blocked by current policy. Inspect h() style handling too; fix only actual affected uses.

Root will reload the live browser and verify no backdrop on initial load, Projects/Create dialog usable, close/cancel hides backdrop, all initial hidden sections remain hidden, upload source type controls switch correctly, and settings confirmation opens/closes. Do not write markup/class snapshot tests. Run existing four repository client tests and four actual-source behavior scratch tests plus syntax. No full suite, backend changes, .ai/todo changes, commits, pushes, worktrees or model changes. Keep Gemini 3.8 Flash High.
