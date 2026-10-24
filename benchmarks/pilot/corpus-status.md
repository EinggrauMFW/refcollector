# Current pilot corpus status

Date: 2026-10-22. Status: **provisional; not frozen; no accuracy result**.

The active packet contains 34 documents: 12 development and 22 evaluation candidates.
The known live-test paper remains in development. `pilot-005` is excluded in `reserve-manifest.json` because no source was verified.
Original PDFs were not changed. Downloaded PDFs remain in ignored local storage. No PDFs are included in tracked files.

## Coverage changes

| Document | Split | Coverage | Evidence |
|---|---|---|---|
| `pilot-031` | Development | Indonesian-language, two-column digital PDF | [Publisher record](https://ejournal.brin.go.id/jurnalwave/article/view/2305), downloaded PDF, inspected title/body/reference pages |
| `pilot-032` | Evaluation candidate | Indonesian-language, two-column digital PDF | [Publisher record](https://ejournal.brin.go.id/jurnalwave/article/view/11465), downloaded PDF, inspected title/body/reference pages |
| `pilot-033` | Development, P1 supplement | Natural scan with partial existing OCR; separate manuscript bibliographies | [Archived thesis](https://www.osti.gov/etdeweb/servlets/purl/497635), full-page raster images on all 140 pages, inspected references on physical pages 32 and 68; further lists on pages 86 and 137 |
| `pilot-034` | Development, P0 | Single-column digital thesis with chapter-specific bibliography lists | [University record](https://research.rug.nl/en/publications/d3c104c9-7aae-4524-a7e5-f7e0ffd6bd00), matching title/author/DOI; inspected reference lists on physical pages 36, 65, 89, and 108 |
| `pilot-035` | Evaluation candidate, P0; extraction withheld | Compilation thesis with separate article bibliographies and mixed layouts | [University record](https://portalcientifico.uvigo.gal/documentos/69e670b365fce81e05e9c45a), matching title/author; inspected separate bibliography lists on physical pages 71 and 91 |

The two Indonesian PDFs have text on every page. The scan has text on 137 of 140 pages.
The scan does not establish support for generating OCR from image-only PDFs.
The built-in extractor returned `OCR_REQUIRED` for this supplement. It is not a successful extraction or a scored P0 case.
`pilot-034` supplies a native digital chapter-bibliography development case. Its extraction initially returned `OCR_REQUIRED`.
Detected reference ranges included blank physical pages 70, 170, and 178. The OCR check treated these empty separators as unreadable content.
The fix marks a textless page blank only when it has no paint operations and no annotations, widgets, or links.
The OCR check now exempts only these verified empty pages. Image, vector, and annotation-only pages retain the OCR/review error.
The digital thesis now produces 250 draft entries across six detected sections. This does not prove complete section detection or correct reference counts.
Because this document was used for debugging, it moved to development. `pilot-035` now supplies a fresh candidate for this cohort.
Here, fresh means not processed or used for RefCollector tuning during this review; earlier exposure remains unknown.
Do not run its extraction until the split is approved. Its cover prints 2025, while the university records a January 2026 defense.
Physical page 42 has a table column called References; it is not a confirmed bibliography. Preserve this distinction during annotation.

## Source findings resolved or recorded

| Document | Result | Remaining limit |
|---|---|---|
| `pilot-003` | Downloaded university PDF has the same SHA256 as the local source. | Redistribution permission remains unverified. |
| `pilot-004`, `pilot-006`, `pilot-019` | Archived research PDFs match the complete local extracted text after whitespace normalization. | This comparison does not prove identical bytes, layout, figures, or complete semantic equivalence. |
| `pilot-012` | [Publisher record](https://marineenergyjournal.org/imej/article/view/98) matches title and authors. | PDF prints DOI ending `349-363`; publisher record prints `349-362`. Both values remain recorded. |
| `pilot-017` | [Publisher PDF](https://submissions.ewtec.org/proc-ewtec/article/download/270/229) is byte-identical to the local PDF. | Landing-page author list differs. Document identity is resolved; the metadata discrepancy remains. |
| `pilot-018` | [Publisher record](https://submissions.ewtec.org/proc-ewtec/article/view/315) matches the work and author set. | Author order differs from the PDF. Preserve PDF order when transcribing its content. |
| `pilot-022` | Downloaded publisher PDF has the same SHA256 as the local source. | Retain the funder DOI separately from the work DOI. |
| Other active candidates | Source evidence is recorded for each document. Status distinguishes retrieved records, indexed records, and archived article/PDF evidence. | Indexed evidence is not a successful fresh origin fetch. No blanket license or redistribution claim is made. |

`provenance.csv` and `manifest.json` contain the evidence category for every active candidate.
Source identity evidence is separate from independent reference annotation and retrieval eligibility labels.

## Validation

- All 34 local source hashes match the manifest. No active pair shares a file hash or normalized title.
- Reviewer A and B templates each contain exactly the 34 active document IDs and no completed reference labels.
- The original 30-document opening-text screen found no selected cross-split match. It was a heuristic, not an exhaustive semantic audit.
- The new sources were acquired for this packet and were not used for tuning. Their draft output must not be used to tune against evaluation candidates.
- Draft extraction produced 1,977 entries across 32 active candidates. `pilot-033` still returns `OCR_REQUIRED`; `pilot-035` has no extraction output.
- Seven generated regression cases cover blank separators under automatic/manual selection and rejection of image, vector, and annotation-only pages.
- The extraction test directory passed. The combined project check passed 1,331 tests in 169.66 seconds, plus lint and formatting for 225 Python files.
- Application code changed only in blank-page layout classification and the section OCR check. Dependencies were not changed.

## Gates still open

| Gate | Required evidence |
|---|---|
| Evaluation exposure | Owner confirmation in `prior-use-review.csv`; current history remains unknown for original evaluation candidates. |
| Final corpus coverage | Full-document cohort and bibliography-boundary review; candidate `pilot-035` is present and withheld from extraction. |
| OCR scope | Separate P1 support and image-only scan coverage; do not count the OCR-required supplement as passing. |
| Gold labels | Two independent annotation passes, followed by a third adjudication pass. The specification does not prescribe human or AI annotators; record the chosen method. |
| Retrieval benchmark | Independent timestamped copy-eligibility and identity checks. |

Do not freeze the split or report quality metrics until the applicable gates are complete.
The candidate manifest hash records the current packet only; it is not a release or evaluation approval.

## Development bibliography location review

The parallel corpus audit identified three incorrect development bibliography locations.
Original PDF text and rendered pages confirmed the corrections below.
The manifest preserves the earlier values in `reference_heading_candidates_1based`.

| Document | Earlier candidates | Reviewed location | Evidence |
|---|---|---|---|
| `pilot-002` | 10, 55, 56 | 55, 56 | Page 10 is a contents entry; the bibliography begins on 55 and continues under a running heading on 56. |
| `pilot-014` | 1 | 12 | Page 1 is a contents entry; references begin below the separator on 12 without a printed section heading. |
| `pilot-031` | 8 | 7 | `DAFTAR PUSTAKA` and the first reference begin near the bottom of the right column on 7. |

These are source-location corrections, not gold occurrence labels or accuracy results.
Evaluation locations remain provisional. No evaluation extraction was run for this audit.

## Live HTTP follow-up

The configured Jean Run environment was started with its existing command at `http://127.0.0.1:8001/`.
The listening process started after the blank-page fix. No second server or guessed port was used.
The check created isolated project `prj_01M5GRW5P1KSSFNN3B79B0M115` in the existing `.ai/live-browser-home` test home.
Only development sources were uploaded: a generated blank-separator PDF, `pilot-034`, and `pilot-031`.

| Check | Result |
|---|---|
| HTML/static assets, session, executor status | Passed over live HTTP |
| Upload and extraction | 2, 250, and 60 draft occurrences respectively |
| Source downloads | All three SHA256 values match the uploaded files |
| PDF page rendering | Physical page 36 rendered as PNG; image inspected |
| Complete-audit exports | JSON and CSV each contain 312 rows; three export/manifest downloads match descriptor hashes |
| Request guards | Missing CSRF rejected with 403; invalid Host rejected with 400 |
| Browser UI | Not verified: no CUA browser was available, and Agent Browser did not complete startup |

This is live workflow evidence, not extraction accuracy or retrieval benchmark evidence.
No resolve, access-discovery, remote-download, or email tasks were requested.
Evidence remains in `.ai/benchmark-pilot/live-web/result.json` and its downloaded artifacts.

The blank-page classification follows the documented [PyMuPDF paint-operation log](https://pymupdf.readthedocs.io/en/latest/functions.html#Page.get_bboxlog)
and [annotation reference list](https://pymupdf.readthedocs.io/en/latest/page.html#Page.annot_xrefs).
