# DOI-first core worker

Outline briefly, then implement the core side of ../../doi-first-contract.md under D-48.
Read ../../doi-first.md and .ai/doi-first-analysis.md. User chose automatic download,
automatic availability for passing PDFs, and review only for failed reviewable checks.

Own contracts/api.py, contracts/ports.py, domain/models.py and enums.py, ids.py,
identity/identifiers.py, storage/repos.py plus new additive migration, pipeline state/
registry/retrieval handlers (new DOI handler allowed), files retrieval/verify only as needed,
services/service.py and matching core tests. No web adapter/UI/orchestration edits.

Implement the frozen names/fields in the contract. Retain legacy public defaults and
accepted-work semantics. Use a separate CitationFile ownership record; no fake accepted
metadata or provisional accepted work. Preserve all network/scoping/transaction/replay
boundaries, version policy, stale-input handling, and deduplication. Add no dependency.
Read current official docs before unfamiliar library APIs. Use the existing verification
and staged-publication code rather than duplicating transfer logic.

Map each numbered requirement in ../../doi-first.md to a deterministic offline regression,
including provider disagreement + no work + no discovery, multiple DOI forms/ambiguity,
matching primary DOI vs references-only/wrong/missing DOI, corrupt PDFs, pending review,
edit/cancel rollback, permissions, project grants, replay, and no metadata mutation.
Test current file reads/reviews and later metadata processing without destroying DOI files.

Run only affected core tests and scoped Ruff; no full suite/server/browser actions.
Coordinate through the contract, not other workers' files. If a frozen field cannot work,
report the exact smallest additive change to the orchestrator before changing the interface.
Write .ai/doi-core-results.md with files, failing-first proof, final commands/results and limits.
Do not stop at a plan. No commits, pushes, or collection removal.

Current worker model: Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49). This overrides older model instructions in this brief.
