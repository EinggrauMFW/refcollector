# RefCollector P0 backend: orchestration plan

Source of truth: `docs/spec/refcollector-spec-v3.md`. Scope: P0 backend, spec sections 5–12 and 14.1; proven by 15.3 (P0 tests) and the 16 checklist (W-01..W-31). P1/P2 out; P1 enum values stay out of P0 schemas (spec 3). Decisions: `decisions.md`.

Settings: `DISPATCH_MODE = A` (user relays briefs to Jean/OpenCode), `WORKER_MODEL = 9router/ag/gemini-3.8-flash`, `MAX_PARALLEL = 3`, `BASE_BRANCH = main`, `INTEGRATION_BRANCH = integration`. Check command: `python scripts/check.py` (D-02).

P1-only acceptance tests, not built: AT-21, AT-22, AT-27 (Zotero).

---

## Package layout (frozen in Phase 1, D-04)

```
pyproject.toml                    contract   deps from spec 14.2; dev: pytest, respx, ruff
scripts/check.py                  contract   ruff check + ruff format --check + pytest
src/refcollector/
  __init__.py, __main__.py        contract
  errors.py                       contract   11.5 codes as typed exceptions (RcError → code, retryable, recovery_hint, details)
  ids.py                          contract   ULID generator, prefixed IDs (prj_, src_, occ_, cand_, job_, stp_, art_, ...)
  config.py                       contract   Settings, RC_HOME (platformdirs), scopes, network modes, limits (8.1, 11.2), TTLs (6.6)
  domain/enums.py                 contract   7.3 dimensions, 7.4 origin/channel, job/step states (6.3), reason codes (9.3), P0 source types/stages/formats
  domain/models.py                contract   Pydantic v2 entities 7.2, AccessCheck 7.5, Approval 7.4, ConfigSnapshot 6.6
  contracts/ports.py              contract   Protocols: ExtractionAdapter, HttpClient, provider adapters, IdentityResolver, AccessDiscoverer, LocalCopyChecker, Retriever, NetGuard, Exporter, DraftRenderer, repositories, FileStore, JobQueue, StepHandler, ApprovalPolicy
  contracts/api.py                contract   tool In/Out DTOs (11.4, extra="forbid") + RefCollectorService Protocol (one method per tool)
  storage/db.py                   contract   connect (WAL), migrate, transaction(immediate=True)
  storage/migrations/0001_init.sql contract  every table for 7.2 + executor, step, response_cache, idempotency
  storage/repos.py, files.py, audit.py, idempotency.py              WP-01
  executor/ lock.py, loop.py, queue.py, retry.py, snapshot.py         WP-02
  extraction/ builtin.py, layout.py, headings.py, split.py, preflight.py   WP-03
  providers/ http.py, cache.py, limiter.py, crossref.py, datacite.py, doiorg.py, openalex.py, semanticscholar.py, unpaywall.py, openlibrary.py   WP-04
  outputs/ bibtex.py, csl.py, csvout.py, jsonout.py, audit_report.py, manifest.py, drafts.py, templates/   WP-05
  identity/ identifiers.py, normalize.py, features.py, policy_v1.py, resolver.py, reasons.py   WP-06
  access/ discovery.py, classify.py, eligibility.py, versions.py      WP-07
  files/ netguard.py, retrieval.py, verify.py, local_copies.py        WP-08
  services/ *.py                                                      WP-09
  pipeline/ handlers.py, registry.py                                  WP-10
  mcp/ server.py, tools.py, prompts.py                                WP-11
  cli/ main.py, review.py, render.py                                  WP-12
tests/conftest.py, tests/fakes/, tests/fixtures/pdfs.py   contract   tmp RC_HOME, in-memory fake repos, fake service, PDF builder (D-07)
tests/unit/<area>/                                        per WP
tests/fixtures/http/<provider>/                           per WP (recorded JSON for respx)
tests/acceptance/                                         orchestrator (Wave D)
legacy/                                                   baseline, read-only (D-03)
```

---

## Commit sequence on `main`

1. `docs: add spec v3 and orchestration plan` (spec, `docs/orchestration/`, `.gitignore` ignores `.ai/`). Done before Phase 1.
2. `chore: move baseline to legacy/` (`git mv` only, keeps history; plus `.gitignore` path updates). Phase 1.
3. `contracts: freeze v1` (C2–C9). Phase 1. Ends with `uv run python scripts/check.py` green on this host (D-16).

## Phase 1 contract list (orchestrator, on `main`, commits 2 and 3 above)

| # | Contract | Content |
|---|---|---|
| C1 | Repo moves | baseline → `legacy/` (D-03); `.gitignore` paths updated |
| C2 | `pyproject.toml`, `scripts/check.py`, ruff + pytest config | package `refcollector`, entry point `refcollector = refcollector.cli.main:app`, Python ≥ 3.11 |
| C3 | `errors.py` | one exception class per 11.5 code; `to_payload()` → `{code, message, retryable, recovery_hint, details}`; typed library errors (`ReferenceSectionNotFound`, `EncryptedDocument`, `OcrRequired`, ...) (6.6) |
| C4 | `domain/enums.py`, `domain/models.py` | all 7.x entities and state enums; P0 values only (spec 3) |
| C5 | `storage/db.py` + `0001_init.sql` | full schema incl. `executor`, `job`, `step` (state, attempt, executor_id, fingerprint, snapshot_hash), `response_cache`, `idempotency_record`, `audit_event`, `file_blob`, `file_grant` |
| C6 | `contracts/ports.py` | every adapter, repository, executor and policy interface named in the layout |
| C7 | `contracts/api.py` | 22 tool DTOs, `RefCollectorService` Protocol, `Principal`/`Channel` context object (never from tool args, 7.4) |
| C8 | Test kit | `tests/conftest.py` (tmp `RC_HOME`, db fixture, `anyio` backend), `tests/fakes/` (in-memory repos, fake HTTP, fake service), `tests/fixtures/pdfs.py` (build PDFs: single/two-column, headings in contents page, per-chapter "Daftar Pustaka", no heading, encrypted, image-only, supplement, title page with DOI) |
| C9 | Empty modules for every WP path, each with a docstring naming its spec section |

---

## Work packages

Columns: AT/W = acceptance IDs the WP must cover with unit or component tests. Wave D proves them end to end.

| WP | Goal | Spec | AT / W | OWNED | READ-ONLY (beyond contracts) | Depends on | Size |
|---|---|---|---|---|---|---|---|
| 01 storage | SQLite repositories for every project entity; project + occurrence revision counters; audit events; idempotency records (7-day TTL); content-addressed file store (tmp → verify → sha256 path, orphan indexing, stray-tmp cleanup); FileGrant reads; project scoping | 6.4, 7.1, 7.2, 7.5 (supersede), 11.3 (storage side) | AT-12a (store), AT-18, AT-42, AT-43, W-04, W-30, W-31 | `src/refcollector/storage/{repos,files,audit,idempotency}.py`, `tests/unit/storage/` | `legacy/` | contracts | M |
| 02 executor | `portalocker` lock on `RC_HOME/executor.lock`, executor table, client 5 s lock poll; step claim in `BEGIN IMMEDIATE`; job and step state machines; stale-step recovery with `RECOVERED_AFTER_EXECUTOR_LOSS`; pause/cancel; retry classes with backoff + jitter (max 5); `retry_failed`; config snapshot + output reuse (`OUTPUT_REUSED`); 120 s step timeout | 6.1–6.6 | AT-10b, AT-28, AT-29, AT-30, AT-31, AT-32, W-29 | `src/refcollector/executor/`, `tests/unit/executor/` | — | contracts | L |
| 03 extraction | Built-in PyMuPDF adapter: blocks with bbox in column order, page labels; multilingual heading dictionaries (en, id incl. Daftar Pustaka/Referensi, de, es, fr); ignore contents page and running headers; all sections; density-scored `inferred` fallback; entry split (port v0.1 `split_references` patterns); spans; warnings `POSSIBLE_MERGED_REFERENCE`, `POSSIBLE_TRUNCATED_REFERENCE`, `COLUMN_ORDER_SUSPECT`; preflight; `ENCRYPTED_DOCUMENT`, `OCR_REQUIRED`, `LIMIT_EXCEEDED`, `REFERENCE_SECTION_NOT_FOUND`; pasted-text source path | 8.1, 8.2, G4–G6 | AT-01, AT-02, AT-14 (text is data), W-01 (preflight), W-07, W-08 | `src/refcollector/extraction/`, `tests/unit/extraction/` | `legacy/refcollector.py` (`pdf_text`, `split_references`, `HEADING_RE`) | contracts | L |
| 04 providers | `httpx.AsyncClient` with `refcollector/<ver> (mailto:…)` UA; network-mode guard (offline = zero sockets); response cache by request fingerprint with TTLs (30 d identifiers, 7 d searches/OA); per-host `aiolimiter`; `Retry-After` honour; host blocked for the job when `Retry-After` > 60 s (`PROVIDER_UNAVAILABLE`); adapters Crossref, DataCite, doi.org CSL, OpenAlex, Semantic Scholar, Unpaywall, Open Library (ISBN), each mapping to a normalised `ProviderRecord`; recorded fixtures | 2 (Retain), 6.5, 6.6, 9.1 steps 2–3, 13 | AT-08a, AT-20 (client side) | `src/refcollector/providers/`, `tests/unit/providers/`, `tests/fixtures/http/{crossref,datacite,doiorg,openalex,semanticscholar,unpaywall,openlibrary}/` | `legacy/refcollector.py` (`Resolver.get`, `*_to_item`) | contracts | M |
| 05 outputs | Pure renderers from models: BibTeX (`bibtexparser` v2), CSL JSON, CSV (formula-injection neutralised), full JSON, unresolved list, audit report (Markdown), `manifest.json`; selections `accepted` / `reviewed` / `complete_audit` (unresolved marked incomplete); export file-name template `{year}_{first_author}_{short_title}.pdf`; drafts `.txt` bundle + `.eml` (en, id), neutral greeting, blank recipient, scoped availability wording, optional group by author | 12.1, 12.2, 10.4 (file names), G10 | AT-06 (export side), AT-07, AT-16, AT-17, AT-38, W-16, W-25, W-27 | `src/refcollector/outputs/`, `tests/unit/outputs/` | `legacy/refcollector.py` (`bib_key`, `unique_keys`, `to_csl`, `to_bibtex`), `legacy/webapp/app.py` (`build_drafts`) | contracts | M |
| 06 identity | Identifier reading (DOI, arXiv, ISBN, PMID); candidate gathering per 9.1; score every candidate incl. the DOI-linked one (fixes G1, G2); port `assess()` + normalisation unchanged; features incl. `rapidfuzz`; top-5 ranking, top-two comparison; acceptance policy v1; reason codes with plain sentences + values; grey-literature fallback; metadata precedence with locks; `resolve_citation` core | 9.1–9.4, G1, G2 | AT-04, AT-05, AT-06, AT-07 (parse), AT-11 (precedence), W-10 | `src/refcollector/identity/`, `tests/unit/identity/`, `tests/fixtures/http/identity/` | `legacy/refcollector.py`, `legacy/tests/test_matching.py` (ported as golden tests) | 04 | L |
| 07 access | Discovery over Unpaywall + OpenAlex locations; `AccessCheck` with `sources_consulted`, `sources_failed`, `checked_at`, policy version; discovery `complete/partial/failed`; classification `eligible_found/none_found/restricted/unknown`; eligibility v1 (D-11); licence stored separately; version policy + `VERSION_DIFFERENCE`; `none_found` wording; never "paywalled" | 10.1, 10.2, 7.5, G3 | AT-08b, AT-35, AT-36, AT-37, W-20, W-21 | `src/refcollector/access/`, `tests/unit/access/`, `tests/fixtures/http/access/` | `legacy/refcollector.py` (`pdf_url`) | 04 | M |
| 08 files | Net guard (HTTPS only, block private/loopback/link-local/metadata IPs, re-check every redirect, max 5, DNS-rebinding guard); streamed download to `RC_HOME/tmp`, 100 MB / 120 s limits; verification steps 2–6 (content type, `%PDF`, full parse, first-two-page DOI/title evidence, compare, supplement/wrong-work detection); five local-copy checks; `user_attached` / `managed_retrieved` origins; `satisfied_by_local_copy`; managed-store reuse | 10.3, 10.4, 13 (retrieval), G9 | AT-09a, AT-09b, AT-09c, AT-10a, AT-15, AT-19, AT-23, AT-24, AT-25, AT-26, W-22, W-23, W-24 | `src/refcollector/files/`, `tests/unit/files/`, `tests/fixtures/http/files/` | `legacy/refcollector.py` (`download`) | 01 (FileStore impl), 04 (HttpClient) | L |
| 09 services | One method per tool in `RefCollectorService`: validation, scopes (`NOT_AUTHORIZED`), path allow-list with symlink/traversal checks (`PATH_NOT_ALLOWED`), network-mode checks, idempotency (scope, fingerprint, `IDEMPOTENCY_CONFLICT`), `expected_revision` checks (`REVISION_CONFLICT`, nothing applied), all `update_reference` change types, add/split/merge with predecessor links, provenance (principal, channel, origin; reject forged fields), approval record + staleness (`APPROVAL_STALE`), job intents (enqueue only), export `REVISION_NOT_CURRENT`, P1 values → `FEATURE_NOT_AVAILABLE`, cursors (D-13), `get_capabilities` | 5, 7.4, 11.2, 11.3, 11.4 (semantics), 11.5, 13 (files) | AT-03 (enqueue), AT-12a, AT-12b, AT-13, AT-33, AT-34, AT-39, AT-40, AT-41, W-02, W-03, W-11..W-19 | `src/refcollector/services/`, `tests/unit/services/` | — | 01 | L |
| 10 pipeline | Step handlers wiring executor → adapters → repositories: `extract` (per source), `resolve` (per occurrence), `discover_access` (per occurrence), `retrieve` (per file, local copies first), `export` (one read transaction), `drafts`; fingerprints for output reuse; one-transaction commits with outputs; ProjectWork dedup by link keys per project; locks respected on re-resolve | 6.2, 6.4, 6.6, 7.1, 9.4, 10.3, 12 | AT-03 (only new re-resolved), AT-11, AT-18, AT-19, AT-30, W-05, W-06, W-09 | `src/refcollector/pipeline/`, `tests/unit/pipeline/` | — | 01, 02, 03, 05, 06, 07, 08, 09 (ApprovalPolicy) | M |
| 11 mcp | Official `mcp` SDK stdio server; 22 tools with strict JSON Schema (unknown fields rejected), `structuredContent` + one-line text, `isError` with error payload, `meta.request_id`; channel `mcp`, origin `agent`; three prompts; text-is-data notes in tool descriptions; logs to stderr; executor start/lock on boot | 11.1–11.6, 13 | AT-33 (schema), AT-41, W-01..W-31 via MCP (Wave D) | `src/refcollector/mcp/`, `tests/unit/mcp/` | — | contracts (C7); fake service | L |
| 12 cli | Typer CLI: `project create|list|show`, `import`, `sections`, `run --stages`, `jobs`, `list`, `show`, `review` (TTY approval surface: accept, choose, reject, set DOI, edit, split, merge, exclude, add; keypress per decision; refuses when not a TTY), `edit`, `split`, `merge`, `add`, `exclude`, `attach`, `download`, `export --revision N`, `requests`, `serve [mcp|web]` (web → D-05), `config`; non-interactive channel `cli`, origin `agent`; `SystemExit` only at entry point | 14.1, 7.4, 6.6 | W-18 (TTY), W-11 (no TTY), W-01..W-31 via CLI (Wave D) | `src/refcollector/cli/`, `tests/unit/cli/` | — | contracts (C7); fake service | M |

---

## Waves

Inside a wave: no dependencies between WPs and no overlapping owned paths. At most 3 workers at a time.

| Wave | WPs | Dispatch order | Exit gate |
|---|---|---|---|
| A | 01, 02, 03, 04, 05 | first 01, 02, 04 (critical path); then 03, 05 as slots free | all merged to `integration`, full suite green |
| B | 06, 07, 08 | all three together | merged, full suite green |
| C | 09, 11, 12, then 10 | 09, 11, 12 together (11 and 12 build against the frozen service Protocol and the fake service); 10 when a slot frees and 09 is merged | merged, full suite green |
| D | orchestrator | integration glue; `tests/acceptance/` for P0 AT-xx; W-01..W-31 once via CLI, once via MCP | `docs/orchestration/acceptance.md` complete |

Critical path: contracts → 01 → 08 → 09 → 10 → Wave D.

---

## Review gate (every WP)

1. Read `WP_REPORT.md` as a claim.
2. `git diff --name-only <base>...wp/<id>`: any file outside OWNED → changes requested.
3. Read the full diff against cited spec sections, AT/W IDs, contracts, error codes, state names.
4. Run `python scripts/check.py` in that worktree myself.
5. MERGE (`--no-ff` into `integration`, full suite, revert on red) / CHANGES REQUESTED (numbered fixes with file:line) / TAKE OVER after 2 failed rounds.

## Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | Contracts too thin → many change requests in Wave B/C | C6/C7 include every method signature used across WPs; fake repos + fake service shipped in C8 |
| 2 | `portalocker` lock behaviour on Windows vs POSIX | WP-02 tests run two real processes; Wave D AT-28 on this Windows host |
| 3 | Generated PDFs do not reflect real theses | extraction tests also use per-cohort layouts from C8; real-corpus benchmark (15.1) is M0/M1, out of this run |
| 4 | MCP SDK API drift | WP-11 brief requires a WebSearch of current `mcp` SDK docs and pins the version; record in 11.1 at M2 |
| 5 | PyMuPDF AGPL licence (14.3) | not a P0 build blocker; release gate only, noted for the user |
