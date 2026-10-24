# Pilot benchmark preparation

This packet contains 34 active candidate documents, not a validated benchmark or accuracy result.
The provisional split contains 12 development documents and 22 evaluation documents.
There are 33 P0 candidates and one P1 scanned supplement. See `corpus-status.md` for current evidence and open gates.
The known live-test paper is `pilot-001`, in development.

PDFs, local source paths, system extraction text, and rendered pages remain outside this packet.
Do not distribute PDFs without permission. `redistribution_permission` is unverified for every candidate.
Metadata titles are not verified bibliographic identities. See `title_from_pdf`, source status, and `provenance-review.md` for reviewed evidence.
Empty source URLs require verification before external handoff. Candidate URLs are not confirmed source records.

## Local files (not distributed)

The `.ai/` artifacts below describe the maintainer's local review evidence. They
are not included in the public repository or beta release.

- `.ai/benchmark-pilot/inventory.json`: inventory of 93 files and 89 distinct SHA256 values.
- `.ai/benchmark-pilot/source-map.json`: document IDs mapped to original files in the maintainer's local source folder.
- `.ai/benchmark-pilot/layout-review.pdf`: two sampled pages per document.
- `.ai/benchmark-pilot/contact-1.png` through `contact-6.png`: inspected layout sheets.
- `.ai/benchmark-pilot/drafts/`: automatic extraction outputs. These are not gold labels.
- `.ai/benchmark-pilot/prepare.py`: historical initial preparation script. Do not rerun it against the current packet; it replaces templates and manifest edits.
- `.ai/benchmark-pilot/validation-current.json`: current corpus checks and extraction outcomes.
- `reserve-manifest.json`: excluded `pilot-005`, retained for source follow-up.
- `prior-use-review.csv`: owner confirmation needed before split freezing.
- `candidate-manifest.sha256`: candidate manifest fingerprint; this does not mean the split is frozen.

Initial selection used SHA256 order within nine thesis candidates and twenty article/report candidates.
The known live-test paper formed the remaining initial candidate. Article candidates had 6–40 pages and a detected reference heading.
The current packet excludes `pilot-005` because its source could not be verified, and adds `pilot-031` through `pilot-035` for coverage.
Figures, short records, known live-download folders, and byte-identical copies were excluded from candidate selection.
This convenience sample mainly covers marine energy. Do not generalize results to all research fields.

## Required review before evaluation

1. Verify document identity, canonical source URL, and provenance for each candidate.
2. Check semantic duplicates, versions of the same work, and previous development use. SHA256 checks only identical bytes.
3. Replace evaluation candidates with prior development exposure. Freeze the split before tuning or scoring.
4. Confirm full-document layout and bibliography boundaries. Current cohorts describe sampled pages only.
5. Review the Indonesian-language candidates `pilot-031` and `pilot-032`. The scanned supplement `pilot-033` has an incomplete existing OCR layer.
6. Review chapter-bibliography evaluation candidate `pilot-035`. Its RefCollector extraction is withheld until split freezing. `pilot-034` remains a development case after debugging.

The current evaluation split is provisional because historical use and semantic duplicates are not fully audited.
Initial automatic extraction supplied draft outputs. `pilot-034` was rerun for debugging and moved to development.
Do not use evaluation drafts to tune the extractor. No RefCollector extraction output exists for `pilot-035`.

## Independent annotation

Give each reviewer the original PDFs, manifest, and only that reviewer's CSV.
Do not show system drafts or the other reviewer's labels before both reviews are complete.
Each CSV starts with one placeholder row per document. Replace each placeholder with one row per reference occurrence.
Record complete verbatim references, bibliography section, physical PDF page numbers, identity evidence, reviewer, and UTC timestamp.
Use stable reference IDs within each document. Preserve repeated occurrences across chapter bibliographies.
Record uncertainty instead of inventing an identity or DOI. Mark documents with no references explicitly in a separate review note.

A third reviewer resolves disagreements in `adjudication.csv` after both independent passes.
Record final labels, evidence, and the adjudicator. Keep the original independent annotations unchanged.
No reviewer or adjudicator work has been completed in this packet.
Specification 15.1 requires two annotators and a third adjudication pass, without specifying human or AI annotators.
Record the chosen annotation method and reviewer identities. For AI reviewers, also record the model and run identity.
Development annotation can start before evaluation is frozen. Evaluation annotation and scoring must use the frozen document list.

## Retrieval reference set

Use `retrieval-reference.csv` to record independently verified eligible copies.
Record the work identity, source URL, eligibility evidence, check time, and downloaded PDF hash.
Record vanished copies separately and exclude them from the current eligible-copy denominator.
Keep credentials and full PDFs outside tracked files. Do not treat a provider hit or HTTP success as identity proof.

## Scoring gate

Follow specification section 15. Report counts per cohort and 95% Wilson intervals for proportions.
Score extraction precision/recall against adjudicated occurrences, not against system output.
Report accepted-auto precision together with coverage. Keep conflict acceptance and wrong-work downloads separate.
Retrieval scoring requires the timestamped independent reference set. Offline timing uses recorded provider fixtures.
Live timing is a separate report. Offline mode must make zero outbound connections.
No quality metric or publication-readiness claim follows from this preparation packet.

## Verification

Preparation produced draft entries for 32 active candidates. `pilot-033` returned `OCR_REQUIRED` and remains unscored.
`pilot-034` initially returned `OCR_REQUIRED` on blank separators. After the regression fix it produces 250 draft entries and remains in development.
Validation checked unique hashes, split counts,
source hashes, template document IDs, and the explicit unreviewed status of all candidates.
The contact sheets and added cohort samples were visually inspected. This does not replace full-document independent annotation.
The follow-up fixed blank-separator handling in the built-in PDF extractor. Dependencies were not changed.
