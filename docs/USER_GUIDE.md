# Refcollector user guide

This guide covers the current Library UI. The older application is documented
in the legacy section below. For CLI commands and error codes, see the
[CLI reference](CLI.md). For installation and setup, see the [README](../README.md).

## Open the Library

Install the optional web dependencies and start the app:

```powershell
uv sync --extra web --frozen
uv run --extra web --frozen python -m refcollector serve web --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/>. In Jean, use the configured Run command instead
of starting a second server. Use the **Library | Advanced** switch to move between the
Library and the Advanced dashboard. The dashboard is at `/advanced`.

## Create a collection and grant access

1. Choose **New collection** and enter its name and folder. The dialog proposes
   `~/RefCollector/<name>`, where `~` is your home folder. You can choose another folder.
2. In the collection sidebar, open the options menu of a collection to rename it, pin it
   to the top of the list, or delete it.
   - Pins are stored in this browser only. Another browser, or cleared site data, shows no pins.
   - **Delete** shows how many papers and citations it removes. You must type the
     collection name to confirm. Delete is blocked while jobs of the collection are
     running or queued. Wait for them or cancel them first.
   - Deleting a collection removes the project from RefCollector, but preserves
     the collection folder and all its files on disk.
3. Open **Review permissions** for the collection.
4. Grant folder access, retrieval network mode, and downloads. Each change has
   its own description and confirmation. Cancel leaves that setting unchanged.

The folder contains copies of your uploaded and verified PDFs. RefCollector
keeps its authoritative files under `RC_HOME`. Changing or deleting a folder
copy does not change the stored reference record.

## Upload and check references

1. Choose **Upload PDFs**, or drop a PDF onto the shelf. The file must be a PDF.
   Uploading the same file again returns the existing paper and does not start a second
   extraction.
2. Wait for extraction. Expand the uploaded paper to inspect its citations.
3. To get PDFs immediately, choose **Download PDFs by DOI**. You do not need to
   run **Find metadata** or approve a metadata match first.
4. Optionally choose **Find metadata** for the collection or paper.
5. To resolve metadata warnings, open the candidate rows. Compare title, authors, year, and DOI with the
   candidate before accepting it. Confirm each identity decision separately. Accepting a candidate
   automatically collapses the candidate selection and returns focus to the citation row.

**Provider disagreement** is a metadata warning. It does not block DOI downloading
or opening a PDF that passed its checks. You can leave metadata unresolved.

| Citation state | Meaning and next action |
|---|---|
| Working | A job is processing this citation. Wait. The row updates when the job ends. |
| Extracted | Citation text exists. Find metadata. |
| Check | Identity, extraction, or a downloaded PDF needs inspection. Open the row. |
| No match | No accepted identity. Inspect candidates or use **Advanced** for repair. |
| Matched | Identity is accepted; access discovery has not completed. |
| PDF available | Discovery found an eligible location. Download permission and version policy still apply. |
| No PDF | Discovery found no eligible copy under the current result. This does not prove paywall status. |
| Downloaded | A verified file or usable local copy is available. Open the citation to view it. |
| Failed | An operation failed. Read its reason before retrying. |

## Download and approve PDFs

For a citation with one clear DOI, choose **Download PDF by DOI** in its row.
Use **Download PDFs by DOI** to process eligible citations in a paper or collection.
The DOI service must first be configured through the trusted local CLI; see README.
No metadata confirmation is required. Duplicate forms of the same DOI count as one;
multiple different DOIs require correction rather than guessing a target.

Passing PDFs become available automatically. Metadata suggestions remain independent.
Only a PDF that fails reviewable checks needs **Approve PDF** or **Reject PDF**.
An unsafe or unreadable response fails retrieval and cannot be approved as a usable PDF.

**Download all** retains the normal accepted-metadata workflow. Wait for its terminal result. Review warnings even
when the job completes. A successful job does not mean every citation has a PDF.

A PDF that fails an identity check stays pending. Its file panel names the
cited work, file size, source host, and failed checks. Open the PDF first.
Choose **Approve PDF** only when you have checked that it is the intended work,
then confirm the description. One confirmation approves one file. Approval marks
the file verified and records the overridden checks in the audit. **Reject PDF**
keeps the file unverified and records the rejection.

A **Check** row shows **Review file** when a PDF waits for your decision. It shows
**Review** when a metadata identity waits for your choice.

Verified PDFs appear on the shelf with **Cited by**. Click a downloaded citation
to open its PDF. The PDF opens in a viewer panel with three controls:
**Copy path** copies the file path in the collection folder (it is disabled until the file
is in the folder), **New tab** opens the PDF in a browser tab, and **Close** closes
the panel. Esc also closes it. The collection folder receives a copy. Repeating **Download all**
does not intentionally create duplicates; existing usable copies take priority.
File approval is available through the confirmed web flow, not through MCP.

## Find papers and resolve problems

Search the shelf by title, author, year, venue, or DOI. Use the kind and citation
status filters to narrow the list. The status filter offers Needs check, Missing PDF,
Downloaded and Failed.

The theme has three options: **Light**, **Dark** and **System**. **System** is the default.
It follows your operating system. The choice is shared with the Advanced dashboard
(`/advanced`).

Keyboard shortcuts:

| Key | Action |
|---|---|
| `/` | Focus the search box (when you are not typing in a field). |
| Esc in the search box | Clear the search. |
| Up and Down arrows, Home, End | Move between papers on the shelf. |
| Enter or Space | Expand or collapse the focused paper. |
| Esc inside an expanded paper | Close the open citation first, then the paper. |

| Problem | Action |
|---|---|
| Download is unavailable | Check folder access, retrieval mode, and download permission under **Review permissions**. |
| A card says **Not in folder** | Restore folder access and retry sync. The stored PDF remains available independently of the folder copy. |
| A PDF is waiting for approval | Inspect the PDF and its failed checks, then use the two-step file decision. |
| Metadata shows **Provider disagreement** | You may download by a clear cited DOI immediately. Resolve metadata separately if needed. |
| DOI download is unavailable | Check that the citation has one clear DOI and that the trusted DOI service is configured. |
| A job finishes with warnings | Read the per-citation reasons. Provider failures and version-policy skips can affect individual papers. |
| A confirmation expires or the reference changes | Reopen the action and inspect a new confirmation. |
| An upload fails | Read the message. Common causes are a file that is not a PDF, a PDF over 100 MB or 1,500 pages, a PDF with a password, and a server that has stopped. The error codes are in the [CLI reference](CLI.md). |
| A scanned PDF gives no citations | The bibliography pages have no text layer. RefCollector has no OCR. Use a PDF with selectable text, or add references in **Advanced**. |
| A file is refused as "not a PDF" | The browser must send the file as `application/pdf`. Check that the file opens in a PDF reader. Upload the PDF itself, not a web page or an image. |
| You upload a file twice | Nothing breaks. RefCollector finds the same content and shows the existing paper. It does not extract again. |
| **Delete collection** stays disabled | Type the exact collection name. The button also stays disabled while jobs of the collection run or wait in the queue. Wait or cancel them. |
| Pinned collections are gone | Pins live in this browser only. Pin them again in the browser you use. |
| Export or detailed reference repair is needed | Choose **Advanced** in the **Library \| Advanced** switch. CLI usage is in the [CLI reference](CLI.md). |

## Legacy application reference

The remaining sections describe only `legacy/`. Its data, settings, commands,
and port are separate from the current Library UI.

### 1. Start the app

```bash
cd refcollector
uv run --with-requirements legacy/requirements.txt --with "pymupdf<2" python legacy/webapp/app.py
```

This command supplies the legacy web dependencies separately from the core
CLI environment. A plain core `uv run` does not include them. See the
[uv dependency documentation](https://docs.astral.sh/uv/guides/scripts/#running-a-script-with-dependencies).

Open <http://127.0.0.1:8000> in your browser. The app runs only on your own computer.
Stop it with `Ctrl+C` in the terminal.

### 2. Settings (once)

| Field | Why |
|---|---|
| **Your email** (required) | Sent to Unpaywall, OpenAlex and Crossref so they can contact you if needed. Must be a real address. |
| **Your name, affiliation, purpose** | Filled into the request emails for paywalled papers. Optional. |
| **Library folder** | Where results are saved. Leave empty for `legacy/webapp/library/`. Applies to new jobs. |

Press **Save**. The web app stores these settings in
`legacy/webapp/data/config.json`; it does not read the current CLI's `RC_HOME`
settings. A configured CLI email does not configure the legacy web app.

### 3. Run a job

1. Under **New job**, choose the thesis or paper PDF.
2. Keep **Download open-access PDFs** ticked to fetch free copies, or untick it for metadata only.
3. Press **Start**. A progress bar shows how many references are processed; a thesis with 80
   references takes a few minutes.

### 4. Read the results

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

#### Fixing an unresolved entry

Open the row, paste a DOI you know into **Known DOI**, press **Look up**. The record is added and the
exports are regenerated.

### 5. Paywalled papers

For each paywalled paper the detail row has a drafted email:

- **Open in mail app** fills your mail program (add the recipient yourself).
- **Copy** copies the text.
- **Export all paywall request drafts** saves every draft as one text file.

Author emails are not in the databases. Use the DOI link (publisher page) to find the corresponding
author's address. Nothing is sent by the app; you send the email yourself.

### 6. Export

Buttons above the table:

| Button | File | Use |
|---|---|---|
| Open folder | | opens the job folder in Explorer |
| BibTeX | `references.bib` | LaTeX, Overleaf |
| CSL JSON | `references.csl.json` | imports into Zotero / Mendeley without losing fields |
| CSV (full detail) | `references.csv` | spreadsheets: abstract, citations, keywords, confidence... |
| JSON | `references.json` | everything, including the raw citation text |
| PDFs (zip) | | all downloaded PDFs |

### 7. Where files are saved

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

### 8. Command line

```bash
python legacy/refcollector.py paper.pdf --email you@example.com --out output
```

Writes `references.bib`, `references.csl.json`, `references.json`, `unresolved.txt` and a `pdfs/` folder.
Add `--no-download` to skip PDFs.

### 9. Troubleshooting

| Problem | Fix |
|---|---|
| "Could not find a References section heading" | The PDF uses a different heading (e.g. "Sources"). Add that word to `HEADING_RE` in `legacy/refcollector.py`. |
| Very few references found | Check the PDF's text is selectable (not a scan). Two-column layouts can interleave; try a single-column version. |
| Entries look merged or cut off | Split heuristics missed the layout; fix DOIs by hand in the row, or report the PDF layout. |
| No PDFs downloaded | Check your email in Settings is real (Unpaywall rejects placeholders). Many papers genuinely have no free copy. |
| Semantic Scholar is slow | Anonymous requests share a rate limit. Set the `S2_API_KEY` environment variable if you have a key. |
| "Set your email in Settings first" | Save a real email in Settings, then start the job. |

### 10. Limits to keep in mind

- Only legally free (open-access) PDFs are downloaded.
- Technical reports, standards and government documents rarely exist in scholarly databases; they end
  up as *web source* or *unresolved*.
- Matching is heuristic: check **review** rows and spot-check the rest.
