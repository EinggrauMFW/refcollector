# W4 brief: harmonize the advanced dashboard with the Library (D-50)

User approval: 23 October 2026, option 1 (Orchestrator A, Sonnet 5.5 subagents in parallel).

## Shared contract (both workers must follow it exactly)

- **Theme storage:** `localStorage` key `rc-library-theme`, values `"light"` or `"dark"`; key absent =
  system. Applied as `data-theme="light|dark"` on `<html>`; no attribute = follow
  `prefers-color-scheme`. Both apps read it on load and write it on change. Library already does this
  (`web-ui/src/lib/hooks.ts:99`).
- **Mode switch:** a two-option segmented control in the top-left header next to the logo, label
  "Library" and "Advanced", built from two `<a>` links, the current one with `aria-current="page"`.
  Same look in both apps (36 px height, 6 px radius, ink-on-stone selected state).
- **Context links:**
  - Library to Advanced: `/advanced#project=<collection_id>` (collection id = project id). With no
    open collection: `/advanced`.
  - Advanced to Library: `/#/<project_id>` for the selected project; with none: `/`.
  - Advanced reads `#project=<id>` on load and selects that project if it exists.
- **Palette and type:** the Library tokens in `web-ui/src/styles.css` (`--ground`, `--surface`,
  `--raised`, `--border`, `--rule`, `--control`, `--text`, `--muted`, `--faint`, `--primary`,
  `--on-primary`, `--data`, `--focus`, status bg/ink pairs, radii 8/6/4, control 36 px, sidebar 224 px,
  `--font`, `--serif`, `--mono`). Blue (`--data`) only for data (progress, counts) and focus. Read
  `web-ui/DESIGN.md` for the rules.

## Worker A: advanced dashboard

Owned files: `src/refcollector/web/static/index.html`, `src/refcollector/web/static/app.css`,
`src/refcollector/web/static/app.js`, new `.ai/selenium-ui-check/advanced_w4_check.py`,
evidence `.ai/live-browser-evidence/advanced-w4/`.

1. Replace the slate/blue tokens with the Library tokens (same names where possible, or map old
   names to the new values) with light and dark via `light-dark()` and `color-scheme`, as the Library.
2. Move the 7 view tabs from the top bar into a left sidebar (224 px), project picker at the top of
   the sidebar, mode switch in the header. Keep every existing element ID, `data-view` value, form
   field name and button the JS and the Selenium check use.
3. Theme: replace the broken toggle (`app.js` ~3488 toggles `body.theme-dark`, CSS reads
   `:root.theme-light`) with a Light / Dark / System control on the shared key.
4. Reference titles and citation text in the serif; paths, DOIs, IDs in mono.
5. Do not change any fetch call, route, or behaviour. Do not restyle or change the PDF fallback /
   pdf-browser settings controls beyond inheriting the new tokens.
6. Strict CSP stays: no inline script or style, no remote fonts.

Checks: run `.ai/selenium-ui-check/advanced_w3_regression.py` against `/advanced` (must still exit 0,
`DONE True`); new `advanced_w4_check.py` takes screenshots light/dark at 1400 and 900 px, checks the
mode switch link to `/#/<id>`, `#project=<id>` preselection, theme persistence across reload, and no
console/CSP errors. Also run the skill's `inspect.mjs --app` if it runs on this host.

## Worker B: Library

Owned files: `web-ui/**`, `src/refcollector/web/static/library/**` (build output).

1. Add the mode switch to the header area (top-left, next to the logo), replacing the sidebar
   "Advanced dashboard" link. Link target follows the context rule above.
2. Keep the theme key and behaviour unchanged (it is the contract).
3. Vitest for the link builder only. Run `npm run typecheck`, `lint`, `test`, `build`.
4. Screenshot the switch light/dark at 1400 and 900 px into `.ai/live-browser-evidence/library-w4/`.

## Both

Live server: Jean run env `http://127.0.0.1:8001/` (do not start or stop it; static files are read
from disk, so no restart is needed). Use Selenium with headless Edge:
`uv run --no-project --with selenium python -I <script>`. No commits. Report files, commands with
results, screenshots, and anything you could not keep identical to the contract.
