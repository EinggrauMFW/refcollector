# Refcollector

Give it a thesis or paper as a PDF. It finds the reference list, looks up full metadata for every
entry, downloads the open-access PDFs it can find, and lists the paywalled ones with a drafted
email you can send to the authors.

Runs locally: a command-line script and a small web app on `http://127.0.0.1:8000`.

## What it does

1. **Extracts the reference list** from the PDF (finds the References/Bibliography heading and splits
   the entries; numbered and author-year styles).
2. **Resolves each entry** to a real record:
   - DOI in the entry: Crossref, then DataCite (Zenodo, figshare...), then doi.org
   - otherwise title search: Crossref, OpenAlex, Semantic Scholar
   - web pages with a URL but no DOI are kept as *web-source* records
3. **Checks every match** field by field (title, year, authors) and flags doubtful ones as *review*
   with the reason, so a wrong match is visible instead of silent.
4. **Downloads open-access PDFs** via Unpaywall, OpenAlex and Semantic Scholar.
5. **Exports** BibTeX, CSL JSON (Zotero), CSV, JSON, and a list of paywalled papers with email drafts.

## Install

Requires Python 3.10+.

```bash
git clone <this repo>
cd refcollector
pip install -r requirements.txt
```

## Quick start: web app

```bash
python webapp/app.py
```

Open <http://127.0.0.1:8000>, enter your email in Settings, upload a PDF, press Start.
See the [user guide](docs/USER_GUIDE.md) for a walk-through.

## Quick start: command line

```bash
python refcollector.py paper.pdf --email you@example.com --out output
python refcollector.py paper.pdf --email you@example.com --no-download   # metadata only
```

## Your email address

Unpaywall, OpenAlex and Crossref ask API users to identify themselves with a real email address.
Refcollector sends yours only to those services, as their documentation asks. It is stored locally
in `webapp/data/config.json`, which is git-ignored. Placeholder addresses such as `@example.com` are
rejected by Unpaywall.

## Optional: Semantic Scholar API key

Works without a key. Anonymous requests share a rate limit, so the tool retries with back-off and is
slower. If you have a key, set the `S2_API_KEY` environment variable.

## Tests

```bash
python tests/test_matching.py                      # offline checks of matching and exports
python tests/batch_extract.py  <folder-of-pdfs>    # how many references are extracted per PDF
python tests/batch_resolve.py  <folder-of-pdfs> you@example.com 60   # resolve, first 60 per PDF
```

## Limitations

- **Open access only.** Paywalled papers are not downloaded; they are listed with a drafted request
  email. Sources such as Sci-Hub are deliberately not supported.
- **Grey literature** (technical reports, standards, theses, government documents) mostly has no DOI
  in any database. Those entries stay as *web source* (if they have a URL) or *unresolved*.
- **Extraction depends on the PDF layout.** Two-column PDFs, unusual heading names, or one reference
  list per chapter can produce merged, split or missed entries. Check `unresolved.txt`.
- **Matching is heuristic.** Trust *high*; glance at *review*; expect the odd miss.

## Project layout

```
refcollector.py        core: extraction, matching, resolvers, exporters (also the CLI)
webapp/app.py          FastAPI server
webapp/static/         the single-page UI
tests/                 offline tests and batch scripts
docs/USER_GUIDE.md     how to use the web app
```
