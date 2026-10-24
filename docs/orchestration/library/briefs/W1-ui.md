# W1 UI brief: U1

Worker: Claude Sonnet 5.5, high effort. Orchestrator: see `../HANDOFF.md`.
Read first: `../../../spec/library-ui-spec.md`, `../contract.md`, `../plan.md` (Ownership, Worker brief rules).

Outline your approach in 5-10 lines before editing. Work in the current checkout. No commits,
no new worktrees, no server starts by hand.

## Owned files (only these)

- `web-ui/**` (new)
- `src/refcollector/web/static/library/**` (build output only)

Do not edit Python, docs, the old static UI or `.ai/todo.md`. If you need anything else, stop
and report.

## Task

1. **Scaffold.** Vite + React + TypeScript in `web-ui/` with the versions in contract section 1
   (check typescript compatibility with the lint stack first). `vite.config.ts`:
   `build.outDir = ../src/refcollector/web/static/library`, `emptyOutDir: true`,
   `base: "/static/library/"`, dev proxy `/api` to `http://127.0.0.1:8001`. Scripts:
   `dev`, `build`, `lint`, `typecheck`, `test` (Vitest only for logic). Add a `web-ui/README.md`
   with the dev and build commands.
2. **API layer.** `src/api/types.ts` mirrors contract section 4 exactly. `src/api/client.ts`
   with typed functions for every contract section 5 route: session bootstrap via
   `GET /api/session` (read `src/refcollector/web/static/app.js` to see how the CSRF token and
   header are obtained today), CSRF header on mutations, `X-Confirmation-Token` on confirm
   routes, error parsing into `{code, message, recovery_hint}`. `src/api/mock.ts`: an
   in-memory implementation of the same interface with realistic data (2 collections, a few
   uploaded theses/articles, citations in every status, downloaded papers with `cited_by`).
   Select mock vs real with `VITE_API_MOCK=1`.
3. **Design.** Run the design-taste-frontend app-screen flow
   (the installed `design-taste-frontend/SKILL.md`, then
   `references/app-screens.md`, `draw.mjs --app`, `inspect.mjs --app`). Brief: "a calm
   personal library for academic papers, theses, articles and dissertations; paper-first,
   readable". Record the chosen direction in `web-ui/DESIGN.md`. Light and dark theme.
4. **Screens (against the mock).** Collection sidebar with create-collection dialog and the
   three-row permission panel (contract section 6). Shelf with search (`/` focuses), kind and
   status filters, upload drop zone. Paper row that expands in place (one at a time, URL keeps
   the id): metadata, "Find metadata", "Download all", citation list with status badges
   (text plus icon, never colour only). Citation row expands to the Check panel (candidates,
   reasons, accept/reject with two-step confirm). PDF viewer overlay for downloaded papers
   (iframe to `file_url`) plus "Copy path". Job progress on the card with polling per
   contract section 6. Empty, loading and error states for every view.
5. **Accessibility.** Semantic list and disclosure markup, focus moves into an expanded row and
   back, visible focus, keyboard: arrows between papers, Enter expands, Esc closes.
6. **CSP.** Built output must have no inline scripts or styles. Check `dist` HTML.

## Acceptance

```
cd web-ui && npm install && npm run typecheck && npm run lint && npm run test && npm run build
```

Screenshots of shelf, expanded paper, Check panel, permission panel and PDF viewer in both
themes at 1400 and 900 px widths, taken from `npm run dev` with `VITE_API_MOCK=1` (you may run
the Vite dev server for this and must stop it after). Save them in
`.ai/live-browser-evidence/library-u1/`. Report: files, commands with results, design
direction chosen, contract gaps (do not change the contract yourself).
