# Pilot provenance review — 2026-10-22

Historical first-pass snapshot. The current packet is documented in [corpus-status.md](corpus-status.md).
In the follow-up, source evidence resolved document identity for `pilot-017`; its landing-page author metadata remains inconsistent.
Current counts and source statuses are in `manifest.json` and `provenance.csv`.

This review records document identities and source evidence. It does not annotate bibliography references or establish accuracy.
See `provenance.csv` for all 30 candidates and `manifest.json` for structured evidence fields.

## Source evidence

| Status | Documents | Meaning |
|---|---:|---|
| Primary record title and author match | 9 | Retrieved repository or publisher record matches the local document identity. This does not prove byte equality or redistribution rights. |
| Candidate source only | 19 | URL appears in the PDF or a search result, or a primary URL could not be retrieved. |
| Primary record author conflict | 1 | Title and DOI match, but author lists differ. Manual review is required. |
| No source found | 1 | `pilot-005` has local title evidence but no verified canonical URL. |

Confirmed source records cover `pilot-002`, `pilot-011`, `pilot-014`, `pilot-020`, `pilot-021`,
`pilot-023`, `pilot-024`, `pilot-025`, and `pilot-029`.
For example, the [TU Delft record](https://repository.tudelft.nl/record/uuid:711543f2-497c-4bfb-abd9-5dcef88d8786)
supports the printed Anwari thesis title rather than its stale embedded PDF metadata.
The [HERO preprint record](https://research-hub.nlr.gov/en/publications/advancing-the-hero-wec-through-integrated-modeling-testing-and-fi/)
also matches local report number `NLR/CP-5700-98797`.

Source status describes evidence retrieved in this review. Fetch failure does not establish an access restriction.
Search results can be stale. Candidate URLs remain separate from confirmed `source_url` values.
All redistribution permission fields remain unverified; no PDFs were copied into tracked files.

## Findings

| Document | Finding | Action |
|---|---|---|
| `pilot-001` | Another local file has different bytes but identical normalized opening text and the same printed title. | Keep this work and all versions in development. The alternate file is not a selected evaluation candidate. |
| `pilot-002` | Embedded metadata names a different title from the printed title. | Preserve original metadata; use `title_from_pdf` and the primary repository evidence for identity. |
| `pilot-008` | Four unrelated theses share copyright-page text. Their title pages identify different works. | Reject these four screening matches as shared boilerplate, not same-work versions. |
| `pilot-017` | [Publisher record](https://submissions.ewtec.org/proc-ewtec/article/view/270) lists three authors; the PDF lists five with different order and spelling. | Hold identity review open. Do not overwrite either author list or treat this as an adjudicated identity. |
| `pilot-022` | First page contains both a funder DOI and a work DOI. | Retain both as raw candidates; record `10.36688/ewtec-2023-531` as the DOI printed for the work. |
| `pilot-023` | [Repository record](https://strathprints.strath.ac.uk/88272/) spells James Carroll; PDF spells James Carrol. | Preserve this spelling difference for annotation. |
| `pilot-026` | Indonesian subject matter appears in an English-language PDF. | Do not count this document as Indonesian-language coverage. |

## Duplicate screen

Compared every selected candidate against the 93-file local inventory using normalized seven-word shingles
from the first two physical pages. Flagged opening-text containment of at least 0.65, with at least 30 shingles.
The screen found one byte-identical copy, one different-byte version of the development paper, and four boilerplate matches.
No selected cross-split pair was flagged. This screen cannot prove the absence of semantic duplicates.
Historical development exposure remains unaudited except for the known live-test paper.

Local evidence files remain in `.ai/benchmark-pilot/identity-evidence.json` and `duplicate-screen.json`.
Full PDF text and absolute local paths are excluded from this tracked review.

## Remaining release-quality evidence

The split remains provisional. Complete source checks, prior-use review, and full-document cohort checks before freezing it.
Indonesian-language and scanned-document cohorts remain missing. Chapter-specific bibliography coverage remains unconfirmed.
Two independent annotation passes and a third adjudication pass are still required. The specification does not prescribe human or AI annotators.
No reference labels, eligibility labels, reviewer identities, or review timestamps were fabricated.

Validation checked all 30 original hashes, the 10/20 split, unchanged blank reviewer templates,
and the rule that only matching primary records populate `source_url`.
Application code was not changed. Application tests were not rerun for this data and documentation update.
