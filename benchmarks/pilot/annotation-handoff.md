# Independent annotation handoff

This packet is ready for reviewer preparation, not final evaluation scoring.
Complete the prior-use and corpus coverage gates before evaluation annotation in `corpus-status.md`.
Development annotation can start earlier. Record the approved manifest fingerprint before evaluation annotation starts.
Do not treat `candidate-manifest.sha256` as approval.

## Reviewer A and reviewer B

Give each reviewer:

1. The approved document list and permitted access to the original PDFs.
2. Only that reviewer's blank CSV: `reviewer-a.csv` or `reviewer-b.csv`.
3. These instructions and the annotation field definitions in `README.md`.

Keep system extraction drafts, provider resolutions, and the other reviewer's work hidden until both passes finish.
The empty templates are preparation files. They do not prove that independent review has occurred.
The specification does not prescribe human or AI annotators. Record the chosen method and each reviewer's identity.
For AI annotation, record the model and run identity, and use separate contexts without shared draft labels.

For each document, independently identify every bibliography section and every reference occurrence.
Replace the placeholder row with one row per occurrence. Keep separate occurrences when a work appears in multiple manuscript bibliographies.
Use physical PDF page numbers, starting at 1. Transcribe the PDF before checking identity evidence.
Record uncertainty, missing fields, and OCR errors. Do not silently repair source text or copy a provider's guessed identity.
Record title, author list, year, DOI when supported, evidence URLs, reviewer identity, and UTC review time.
If a document contains no references or cannot be reviewed, record that outcome in a separate signed review note.

## Adjudicator

Wait for both independent passes. Preserve their original files unchanged.
Compare section boundaries, occurrence counts, transcription, and work identities.
Use `adjudication.csv` to record each disagreement, final decision, evidence, adjudicator, and UTC time.
Produce a separate final gold occurrence table using the reviewer CSV columns.
Do not derive gold labels from system extraction drafts.

## Owner before scoring

- Complete `prior-use-review.csv`. Exclude evaluation works previously used for tuning or debugging.
- Freeze and record the final manifest after any replacements. Reissue templates if document IDs change.
- Verify both independent reviews and adjudication are complete.
- Build the timestamped retrieval reference set separately in `retrieval-reference.csv`.
- Score P0 candidates separately from the P1 scanned supplement. Report unsupported and excluded cases explicitly.

Local source paths are recorded in ignored `.ai/benchmark-pilot/source-map.json`.
Share source URLs or obtain permission where PDF redistribution is not established.
No reviewer messages or external file transfers have been sent by this preparation task.
