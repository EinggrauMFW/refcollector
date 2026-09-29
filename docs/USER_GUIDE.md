# Refcollector user guide

## 1. Start the app

```bash
cd refcollector
pip install -r requirements.txt      # first time only
python webapp/app.py
```

Open <http://127.0.0.1:8000> in your browser. The app runs only on your own computer.
Stop it with `Ctrl+C` in the terminal.

## 2. Settings (once)

| Field | Why |
|---|---|
| **Your email** (required) | Sent to Unpaywall, OpenAlex and Crossref so they can contact you if needed. Must be a real address. |
| **Your name, affiliation, purpose** | Filled into the request emails for paywalled papers. Optional. |
| **Library folder** | Where results are saved. Leave empty for `webapp/library/`. Applies to new jobs. |

Press **Save**.

## 3. Run a job

1. Under **New job**, choose the thesis or paper PDF.
2. Keep **Download open-access PDFs** ticked to fetch free copies, or untick it for metadata only.
3. Press **Start**. A progress bar shows how many references are processed; a thesis with 80
   references takes a few minutes.

## 4. Read the results

The table lists every reference. Click a row to open its detail: full title, journal, volume, pages,
publisher, DOI and links, authors with their institutions and ORCID, citation count, open-access
status, topics, keywords and abstract, and how it was matched.

**Filter tabs**

| Tab | Shows |
|---|---|
| all | everything |
| resolved | matched to a record |
| unresolved | nothing found |
| pdf | free PDF downloaded |
| paywalled | matched, but no free PDF was found |
| review | matched, but something is doubtful |

**Confidence**

| Label | Meaning |
|---|---|
| **high** | DOI found in the entry, or two databases agree and nothing looks off |
| **medium** | clean match, but only one database found it |
| **review** | matched, but with a warning. Hover the badge or open the row to see why, e.g. *year differs (cited 2010, record 2001)* or *author names in the citation don't match the record* |
| **web source** | a web page kept with its URL; no scholarly record exists |

Always glance at **review** rows. A wrong match is worse than a missing one.

### Fixing an unresolved entry

Open the row, paste a DOI you know into **Known DOI**, press **Look up**. The record is added and the
exports are regenerated.

## 5. Paywalled papers

For each paywalled paper the detail row has a drafted email:

- **Open in mail app** fills your mail program (add the recipient yourself).
- **Copy** copies the text.
- **Export all paywall request drafts** saves every draft as one text file.

Author emails are not in the databases. Use the DOI link (publisher page) to find the corresponding
author's address. Nothing is sent by the app; you send the email yourself.

## 6. Export

Buttons above the table:

| Button | File | Use |
|---|---|---|
| Open folder | | opens the job folder in Explorer |
| BibTeX | `references.bib` | LaTeX, Overleaf |
| CSL JSON | `references.csl.json` | imports into Zotero / Mendeley without losing fields |
| CSV (full detail) | `references.csv` | spreadsheets: abstract, citations, keywords, confidence... |
| JSON | `references.json` | everything, including the raw citation text |
| PDFs (zip) | | all downloaded PDFs |

## 7. Where files are saved

Every job gets a folder named after the paper's title:

```
library/<Paper title>/
├── source/                 your uploaded PDF
├── pdfs/                   e.g. "2012 - Babarit - Numerical benchmarking study of....pdf"
├── references/             references.bib, .csl.json, .csv, .json, unresolved.txt
├── paywalled/              paywalled.csv, request_drafts.txt
├── summary.txt             counts: found, resolved, downloaded, paywalled
└── .job.json               app state (leave alone)
```

## 8. Command line

```bash
python refcollector.py paper.pdf --email you@example.com --out output
```

Writes `references.bib`, `references.csl.json`, `references.json`, `unresolved.txt` and a `pdfs/` folder.
Add `--no-download` to skip PDFs.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| "Could not find a References section heading" | The PDF uses a different heading (e.g. "Sources"). Add that word to `HEADING_RE` in `refcollector.py`. |
| Very few references found | Check the PDF's text is selectable (not a scan). Two-column layouts can interleave; try a single-column version. |
| Entries look merged or cut off | Split heuristics missed the layout; fix DOIs by hand in the row, or report the PDF layout. |
| No PDFs downloaded | Check your email in Settings is real (Unpaywall rejects placeholders). Many papers genuinely have no free copy. |
| Semantic Scholar is slow | Anonymous requests share a rate limit. Set the `S2_API_KEY` environment variable if you have a key. |
| "Set your email in Settings first" | Save a real email in Settings, then start the job. |

## 10. Limits to keep in mind

- Only legally free (open-access) PDFs are downloaded.
- Technical reports, standards and government documents rarely exist in scholarly databases; they end
  up as *web source* or *unresolved*.
- Matching is heuristic: check **review** rows and spot-check the rest.
