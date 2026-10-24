# DOI-first PDF retrieval

User decision: 23 October 2026, D-48. Implementation is authorized.

## Required behavior

1. A citation containing exactly one unambiguous normalized DOI can start the configured
   DOI fallback immediately. Metadata identity acceptance, candidate selection, provider
   agreement, and prior access discovery must not be prerequisites for this path.
2. Downloading does not mark disputed metadata accepted or create a human approval.
   Metadata work may continue separately and must not discard a usable DOI download.
3. A valid PDF that passes identity and file checks becomes available automatically.
   Check the DOI in the document's primary identity area; an incidental bibliography
   mention is insufficient. Do not compare unconfirmed provider fields as trusted facts.
4. A parseable PDF with missing/conflicting identity evidence or another reviewable failed
   check is stored pending review. Only that PDF needs the existing two-step confirmation.
   Malformed/encrypted/oversized or otherwise unsafe input remains a failed retrieval;
   no approval button may override structural/network validation.
5. The user can open/download a passing PDF even while metadata has Provider disagreement.
   The shelf, citation controls, viewer, folder copy, and polling must represent this state.
6. Existing network mode, download scope/grants, configured endpoint opt-in, SSRF/DNS/TLS
   controls, budgets, cancellation, snapshot consistency, and atomic staging remain active.
7. Multiple distinct DOIs, excluded citations, or no DOI must not choose an arbitrary
   fallback key. Repeated downloads are idempotent; edits cannot silently reuse a PDF for
   a changed DOI. Preserve legacy callers and the normal accepted-work download path.

## Implementation sequence

- [x] Finish read-only backend analysis: `.ai/doi-first-analysis.md`.
- [x] Freeze additive interfaces in doi-first-contract.md; no fake identity acceptance.
- [x] Dispatch core, Library adapter, and UI in parallel after the interface is concrete, with
  disjoint files and Codex gpt-6.1-sol medium under Orchestrator B.
- [ ] Add offline deterministic regressions for each numbered behavior before implementation.
- [ ] Review actual source and test outputs; run the narrow affected checks.
- [ ] Verify the actual Library DOI-first action on the Jean environment, including a
  passing PDF and an unresolved-metadata case. Do not infer success from homepage HTTP 200.
- [ ] Run the broader final gate once after cross-cutting changes stop; update docs/handoff.

## Working rules

No commits, pushes, new worktrees, or collection deletion. Existing Library W3 remains
complete for its old behavior; this is a new engine behavior change, not a repeat of W3.
Use existing resolver/verifier/downloader and storage patterns where possible. Do not add
a framework, a provider SDK, or an automatic metadata-approval workaround.
The additive interface is frozen in doi-first-contract.md; implementation is in flight. No further
approval is required for routine implementation choices within these requirements.
