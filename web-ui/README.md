# RefCollector library UI

React 19 + Vite 8 + TypeScript. Source of the app served at `/`. Build output is committed to
`src/refcollector/web/static/library/`, so `pip install` needs no Node.

Spec: `docs/spec/library-ui-spec.md`. API contract: `docs/orchestration/library/contract.md`.
Design direction: [DESIGN.md](DESIGN.md).

## Commands

Tested with Node 22 and npm 10. `package.json` sets no `engines` range.

```
cd web-ui
npm install
npm run dev         # Vite dev server, http://localhost:5173/static/library/
npm run typecheck
npm run lint
npm run test        # Vitest: status mapping, route, permissions, API client
npm run build       # typecheck, then build into ../src/refcollector/web/static/library
```

`npm run build` empties the output folder first. Commit the result with the source.

### Dev against the mock API

```
VITE_API_MOCK=1 npm run dev           # PowerShell: $env:VITE_API_MOCK=1; npm run dev
```

The mock (`src/api/mock.ts`) is an in-memory `LibraryApi` with two collections, uploaded and
downloaded papers, citations in every status and jobs that finish after a few seconds. Add to the
page URL: `?mock=error` makes the shelf fail, `?mock=slow` adds 3 seconds of latency.
The mock is a build-time branch: a normal build ships none of it.

### Dev against the real server

Start RefCollector (`http://127.0.0.1:8001`) and run `npm run dev`. Vite proxies `/api` to it. The
server checks `Host` and `Origin` exactly, so the proxy (`vite.config.ts`) sets both to the target.

## Layout

| Path | Content |
|---|---|
| `src/api/types.ts` | Mirror of contract section 4. Wire format, snake_case |
| `src/api/api.ts` | `LibraryApi` interface (one method per contract section 5 route), `ApiError` |
| `src/api/client.ts` | Real client: `GET /api/session` bootstrap, CSRF header, `X-Confirmation-Token`, error parsing |
| `src/api/mock.ts` | In-memory implementation |
| `src/api/index.ts` | Picks mock or client from `VITE_API_MOCK` |
| `src/lib/` | Status mapping, hash route, permission rows, formatting, hooks (async data, job polling, theme) |
| `src/components/` | Sidebar, shelf, paper row, citation and Check panel, file approval panel (`FilePanel`), permission panel, PDF viewer, collection create dialog (`CollectionDialog`), collection delete dialog (`DeleteCollectionDialog`), Library \| Advanced switch (`ModeSwitch`) |
| `src/styles.css` | Tokens (light and dark through `light-dark()`) and all styles |

## Rules that come from the server

- CSP for the Library page is `default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; frame-src 'self'` (`src/refcollector/web/security.py`). Responses of the PDF file route use `frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN` instead, so the viewer iframe can show them.
  The built `index.html` has no inline script or style. Components never use a `style` attribute:
  widths are SVG attributes or native `<progress>`. No remote fonts and no font packages: the type
  is a system stack.
- Mutations send `X-CSRF-Token` from `GET /api/session`. A refused mutation fetches a new token
  and retries once. Confirm routes also send `X-Confirmation-Token`.
- Intent and confirm calls reuse one `idempotency_key`.

## TypeScript version

The contract names TypeScript 7.0.2. The lint stack does not support it: `typescript-eslint` 8.72.0
(latest) declares `typescript >=4.8.4 <6.1.0`. TypeScript is pinned to 6.0.3, the newest 6.0 release.
Move to 7.x when `typescript-eslint` supports it.

## URL

`#/<collection>?paper=<id>&citation=<id>&pdf=<id>`. The hash keeps the app independent of the
server's path fallback. The expanded paper, the open citation and the PDF viewer all live in it,
so a reload or a shared link restores them.
