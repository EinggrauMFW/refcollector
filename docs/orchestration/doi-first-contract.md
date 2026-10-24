# DOI-first implementation contract (D-48)

User behavior is approved. This additive contract supersedes metadata prerequisites only
for the explicit citation-DOI retrieval path. Existing accepted-work APIs retain defaults.

## Engine interface (owned by core worker)

- Add `unique_doi(text: str | None) -> str | None` in identity/identifiers.py. It returns
  one distinct normalized DOI, including duplicate syntactic forms; none for zero/multiple.
  Do not change legacy normalize_doi/read_identifiers behavior. Use Occurrence.text.
- `DownloadFilesIn.retrieval_basis: Literal["accepted_work", "citation_doi"] = "accepted_work"`.
  Citation-DOI requests use ANY_ELIGIBLE explicitly and the configured trusted fallback.
  Missing scope/grants/network/config remains an error. No metadata acceptance/discovery needed.
- Add internal StepType.RETRIEVE_DOI = "retrieve_doi", occurrence-bound. Do not expose it
  as a new public StartJob stage. Preserve the legacy work-based retrieve path.
- Add CitationFile with citation_file_id, project_id, occurrence_id, target_doi,
  citation_revision, citation_text_sha256, verification_basis="citation_doi", job_id,
  sha256, bytes, source_url, final_url, state, reasons, export_name, retrieved_at,
  verification_evidence. Reuse RetrievalState and existing verified/reviewable outcomes.
  Add an ID prefix and a new migration/table/repository. Never fabricate ProjectWork.
- Add `OccurrenceSummary.citation_doi: str | None = None`,
  `citation_file_id: str | None = None`, `citation_file_state: RetrievalState | None = None`.
  These reflect the current citation text/DOI only; stale or excluded links are not current.
- Add `GetReferenceOut.citation_files: list[dict[str, Any]] | None = None` and
  ReferenceSection "citation_files". Return current target files only in that section.
  Do not put provisional metadata in GetReferenceOut.work or alter identity/candidates.
- Add kind "citation_file" to service file read/descriptor/path boundaries. Apply all
  existing project, grant, RC_HOME, hash, and scope checks. No route storage access.
- Add `ReviewFileDecisionIn.file_kind: Literal["work_file", "citation_file"] = "work_file"`.
  Keep work_file_id as the resource ID field for compatibility. The new kind validates
  occurrence ownership/current text/DOI and pending state; it does not require accepted
  metadata. Legacy work_file guards stay unchanged. Both methods retain all token checks,
  one-file scope, exact fingerprint/revision binding, replay, channel rules, and audit.
  Confirm output keeps work_file_id and retrieval fields, referring to the chosen resource.

CitationFile state is independent of Occurrence.retrieval/identity. Capture and validate
the occurrence revision/text/DOI before publication. Changing text cannot silently retarget
queued work. Later metadata acceptance must not erase or falsely promote citation files.
Reuse existing HTTP/verification/staging logic; extract the small ownership-neutral core
if needed, retaining the existing Retriever entry point. No duplicate network implementation.
Verify with DOI-only WorkMetadata; never substitute disputed provider titles as trusted facts.
Require the exact primary DOI and no reviewable identity/version conflicts for auto-verification.
Malformed/unsafe input remains failed, not manually approvable. Preserve budgets and cancellation.
The configured browser-assisted resolver remains part of this path: if static resolution
or static PDF retrieval fails and the trusted browser helper is configured, use the existing
bounded BrowserPdfFallback and scoped PDF User-Agent behavior. Do not regress to static-only
resolution. Preserve endpoint origin/guard restrictions; no new CAPTCHA technique is added.

## Library adapter interface (owned by adapter worker)

- Existing RunIn.use_fallback=true explicitly selects retrieval_basis="citation_doi" and
  ANY_ELIGIBLE. Select every non-excluded citation with occurrence.citation_doi, regardless
  of metadata identity/discovery. Normal use_fallback=false preserves accepted-work behavior.
- Add `Citation.can_download_by_doi: bool = False` and
  `Citation.metadata_review_pending: bool = False`. DOI availability uses the computed DOI
  and trusted configured endpoint. Permissions remain enforced separately and at enqueue.
- Keep can_download for normal accepted-work downloads. Set doi from cited DOI when no
  accepted work metadata exists. A verified citation file produces Downloaded and
  downloaded_paper_id=citation_file_id; pending citation files produce file Check.
  Preserve metadata_review_pending/reasons separately, without blocking file actions.
- `PendingFile.file_kind: Literal["work_file", "citation_file"] = "work_file"`.
  `FileReviewIn.file_kind` uses the same literal/default and passes it to the service.
  The existing work_file_id field carries the chosen resource ID. FilePanel URLs use kind.
- Shelf/detail recognize citation_file IDs, source cited-by links, current verified file
  state, and `/files/citation_file/{id}` URLs. Sync only verified citation files through
  the existing folder service; do not export pending files. Avoid duplicate SHA256 cards.
- Classify retrieve_doi jobs as download, recovering source IDs through the existing
  complete service job/reference listings. No svc.store or new engine methods in routes.

## Frontend interface (owned by UI worker)

Mirror additive fields, using optional defaults for older/mock responses. The existing
collection/paper Download with fallback action becomes DOI-first without metadata review.
Offer a DOI download action on metadata Check/unprocessed rows with can_download_by_doi,
sending use_fallback=true for that citation. Do not auto-select/confirm metadata candidates.
Verified DOI files open immediately; metadata pending is informational and not a file gate.
Only pending failed PDF checks show required file confirmation. FilePanel sends file_kind
and builds the corresponding viewer URL, preserving legacy work_file behavior.

## Ownership and verification

Core: contracts/domain/storage/pipeline/files/identity/service and matching core tests.
Adapter: web/library_api.py, web/library_routes.py, matching web tests only.
UI: web-ui/**, generated static/library assets, UI/live test artifacts only.
Orchestrator: this contract, requirements/briefs/status/handoff/docs. No worker edits others' files.
All workers: Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49). Focused regressions first, scoped checks, no full
suite while workers edit. No commits/pushes/worktrees/deletions. Report actual proof.
