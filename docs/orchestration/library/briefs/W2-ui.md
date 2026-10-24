# W2 UI brief: U2

Worker: Claude Sonnet 5.5, high effort. Same rules and owned files as `W1-ui.md`.
Read first: `../contract.md` section 8 (amendments, overrides earlier sections).
Outline your approach in 5-10 lines before editing.

## Phase A (now, against the mock)

1. Update `types.ts`, `client.ts` and `mock.ts` for contract section 8: `GET /defaults`,
   `RunIn.citation_id` (add per-citation Find metadata / Download / Retry actions),
   `Intent.description` (show these lines in confirm steps), `JobStatus.kind` and `paper_ids`
   (drive the "Working" mark from them, also after reload), single CSRF retry.
2. Create dialog proposes `<default_root>/<name>` from `/defaults`.
3. Fix the W1 remaining items: deep link to a paper beyond the first shelf page (fetch it with
   `GET /papers/{pid}`), upload retry keeps the same idempotency key per file.
4. Vitest for logic only: job polling stop/continue rules, status filter, Check-panel state.
5. Run the acceptance commands below, then report "Phase A done" and stop. Do not start Phase B
   until the orchestrator sends the go message.

## Phase B (after the orchestrator says B3 is merged)

1. Get the Jean run environment (`get_run_environments`, worktree
   `9ed8e368-047d-49c1-b78a-ed72ae13141e`); ask the orchestrator to restart it if it serves old
   code. Do not start servers by hand except `npm run dev` for the proxy check.
2. Against `http://127.0.0.1:8001/` (built app) check: session and CSRF, create collection,
   three grants, upload a small development PDF (from `.ai/live-browser-home` evidence or a
   generated PDF; never a file from outside the repo), find metadata, open a check, open a PDF
   in the viewer iframe. Fix UI bugs you find in owned files; report server bugs.
3. Rebuild into `src/refcollector/web/static/library/` and re-run acceptance.

## Acceptance

```
cd web-ui && npm run typecheck && npm run lint && npm run test && npm run build
```

Report: files, commands with results, live findings table (Phase B), remaining work.
