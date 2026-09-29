"""Extract the reference list from a PDF, resolve each entry to metadata,
and download open-access PDFs.

Usage:
    python refcollector.py paper.pdf --email you@example.com [--out output] [--no-download]

Outputs (in --out):
    references.bib   BibTeX for every resolved reference
    references.json  full metadata + status per reference
    unresolved.txt   raw reference strings that could not be matched
    pdfs/            downloaded open-access PDFs (named by DOI)
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pymupdf
import requests

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
HEADING_RE = re.compile(
    r"^\s*(?:\d+\.?\s*)?(references|bibliography|works cited|literature cited)\s*$", re.I
)
MATCH_THRESHOLD = 0.75  # share of a candidate's title words that must appear in the reference


# ---------- 1. extract reference strings from the PDF ----------

def pdf_text(path):
    with pymupdf.open(path) as doc:
        text = "\n".join(page.get_text() for page in doc)
    text = unicodedata.normalize("NFKC", text)  # ligatures: 'ﬁ' -> 'fi', 'ﬀ' -> 'ff'
    return re.sub("[´¨˜ˆ¸ˇ˙]", "", text)  # stray accent marks


def split_references(text):
    lines = text.splitlines()
    heads = [i for i, l in enumerate(lines) if HEADING_RE.match(l)]
    if not heads:
        raise SystemExit("Could not find a References section heading in the PDF.")
    # Skip table-of-contents hits: take the first heading in the back 40% of the
    # document, else the last one. Running page headers repeat the word, so drop them.
    late = [i for i in heads if i > len(lines) * 0.6]
    start = late[0] if late else heads[-1]
    body = "\n".join(l for l in lines[start + 1:] if not HEADING_RE.match(l) and not l.strip().isdigit())

    # Numbered styles: [1] ... or 1. ...
    parts = re.split(r"\n\s*(?=\[\d+\]|\d{1,3}\.\s+[A-Z])", "\n" + body)
    if len(parts) < 5:
        # Author-year styles: a new entry starts on a line like "Surname, First" or
        # "Surname, X." when the previous line finished an entry (ends in . or ) ).
        parts = re.split(
            r"\n(?=[A-Z][^\s,()]*(?:\s[^\s,()]+){0,3},\s+[A-Z]"
            r"|\u2014\s\(|[A-Z][^\s,()]*\s\((?:[A-Z][a-z]{2}\.\s)?(?:\d{4}|\[\])\))",
            body,
        )
    refs = []
    for p in parts:
        p = re.sub(r"\s+", " ", p.replace("-\n", "")).strip()
        p = re.sub(r"^(\[\d+\]|\d{1,3}\.)\s*", "", p)
        if len(p) > 25:
            refs.append(p)
    return refs


def find_doi(raw):
    """DOI from an entry, tolerating PDF-introduced spaces ('10 . 1016 / j.x')."""
    m = re.search(r"\bdoi:\s*(10\s*\.\s*\d.*)", raw, re.I)
    if m:
        tail = re.split(r"\s(?:url|issn|isbn)\s*:|\(visited|\u2014", m.group(1), flags=re.I)[0]
        return re.sub(r"\s+", "", tail).rstrip(".,;)")
    m = DOI_RE.search(raw)
    return m.group(1).rstrip(".,;)") if m else None


def clean_ref(raw):
    """Drop link/ISSN/visited noise so the text works as a bibliographic query."""
    s = re.sub(r"\b(?:url|doi|issn|isbn)\s*:.*?(?=\s(?:url|doi|issn|isbn)\s*:|\(visited|$)", "", raw, flags=re.I)
    s = re.sub(r"\(visited[^)]*\)", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def guess_title(raw):
    """Quoted title if present, else the sentence after the '(year).' marker."""
    m = re.search(r"[“\"](.{8,}?)[”\"]", raw)
    if m:
        return m.group(1).strip(" .,")
    m = re.search(
        r"\((?:[A-Z][a-z]{2,8}\.?\s)?(?:\d{4}[a-z]?(?:,\s*[A-Z][a-z]+\.?\s*\d{0,2})?|n\.d\.)\)\.?\s*(.{8,}?)\.\s",
        raw,
    )
    return m.group(1).strip() if m else None


def _fold(s):
    """Lowercase and strip accents so 'Sepúlveda' and 'Sepulveda' compare equal."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)
    )


def _norm(s):
    """Comparable form of a title/reference: NFKC, HTML entities, accents, '&' -> 'and'."""
    return _fold(unescape(unicodedata.normalize("NFKC", s))).replace("&", " and ")


def _stem(w):
    """Cheap normalisation so spelling variants compare equal (applied to both sides)."""
    w = re.sub(r"is(ation|ing|ed|e)$", lambda m: "iz" + m.group(1), w)  # optimisation
    w = re.sub(r"yse$", "yze", w)  # analyse
    w = re.sub(r"our(s?)$", r"or\1", w)  # behaviour, harbour
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]  # waves -> wave
    return w


def _tokens(s, join_hyphens=False):
    """Word set. Hyphens split words ('wave-power' -> wave, power) or, with join_hyphens,
    join them ('pre-trained' -> pretrained)."""
    s = re.sub(r"[-‐-―]", "" if join_hyphens else " ", _norm(s))
    return {_stem(w) for w in re.findall(r"[a-z0-9]{3,}", s)}


def match_score(title, raw):
    """Share of the candidate title's words that also appear in the reference string.
    Tries the title with hyphens split and joined, and lets long words match inside a
    run-together form of the reference (so 'pretrained' matches 'pre-trained')."""
    raw_tokens = _tokens(raw)
    squashed = re.sub(r"[^a-z0-9]", "", _norm(raw))
    best = 0.0
    for join in (False, True):
        t = _tokens(title, join)
        if t:
            found = sum(1 for w in t if w in raw_tokens or (len(w) >= 6 and w in squashed))
            best = max(best, found / len(t))
    return best


BAD_TYPES = {"component", "peer-review", "grant", "posted-content-figure"}


def assess(item, raw):
    """Compare a candidate record with a reference string, field by field.

    Returns {'accepted': bool, 'title_score', 'author_overlap', 'year_delta', 'flags': [...]}.
    'flags' are human-readable reasons to double-check an accepted match."""
    title = (item.get("title") or [""])[0]
    out = {"accepted": False, "title_score": 0.0, "author_overlap": None,
           "year_delta": None, "flags": []}
    if not title or item.get("type") in BAD_TYPES:
        return out
    score = out["title_score"] = round(match_score(title, raw), 2)
    if score < MATCH_THRESHOLD:
        return out
    ntokens = len(_tokens(title))
    folded = _fold(raw)

    families = [_fold(a.get("family", "")) for a in item.get("author", [])]
    families = [f for f in families if len(f) >= 3]
    found = sum(1 for f in families if f in folded)
    author_ok = found >= 1
    if families:
        # Citations often list only the first few authors, so judge the overlap against
        # at most the first four on record.
        out["author_overlap"] = round(found / min(len(families), 4), 2) if found else 0.0
        out["author_overlap"] = min(out["author_overlap"], 1.0)

    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", raw)]
    year = item_year(item)
    if years and year is not None:
        out["year_delta"] = min(abs(year - y) for y in years)
    year_ok = not years or (out["year_delta"] is not None and out["year_delta"] <= 1)

    strong_title = score >= 0.95
    if not year_ok:
        # Thesis citing the wrong year: tolerate it only for a complete, specific title
        # with a matching author. Anything weaker is treated as a different work.
        if not (author_ok and strong_title and ntokens >= 5):
            return out
        detail = "record has no year" if year is None else f"cited {sorted(set(years))[0]}, record {year}"
        out["flags"].append(f"year differs ({detail})")
    if families:
        # Author lists in theses are often garbled ('F. d. O. Antonio' for Falcão); a full,
        # specific title with the right year is enough evidence on its own.
        if not (author_ok or (strong_title and ntokens >= 6)):
            return out
        if not author_ok:
            out["flags"].append("author names in the citation don't match the record")
        elif len(families) >= 3 and out["author_overlap"] < 0.5:
            # Only one of several authors matches: fine for a distinctive title, a red
            # flag for a generic one.
            if not (strong_title and ntokens >= 5):
                return out
            out["flags"].append(f"only {found} of {min(len(families), 4)} authors found in the citation")
    else:
        # No authors on the candidate (standards, web pages, book parts): demand a full,
        # long title, and skip figure/appendix-style fragments.
        if not (strong_title and ntokens >= 5 and not re.match(
                r"(?:appendix|figure|table|chapter|part|section|preface|index)\b", title, re.I)):
            return out
        out["flags"].append("record lists no authors to compare")
    if score < 0.9:
        out["flags"].append(f"title only {int(score * 100)}% matched")
    out["accepted"] = True
    return out


def is_match(item, raw):
    return assess(item, raw)["accepted"]


def item_year(item):
    for k in ("issued", "published-print", "published-online"):
        parts = (item.get(k) or {}).get("date-parts", [[None]])[0]
        if parts and parts[0]:
            return parts[0]
    return None


def csl_to_item(csl, doi):
    """Map CSL-JSON (doi.org content negotiation) onto the Crossref item shape."""
    title = csl.get("title")
    if not title:
        raise ValueError("no title")
    kind = {"article-journal": "journal-article", "paper-conference": "proceedings-article"}.get(
        csl.get("type"), csl.get("type")
    )
    return {
        "DOI": csl.get("DOI") or doi,
        "title": [title if isinstance(title, str) else " ".join(title)],
        "author": [
            {"family": a.get("family") or a.get("literal", ""), "given": a.get("given", "")}
            for a in csl.get("author", [])
        ],
        "issued": csl.get("issued", {}),
        "container-title": [csl["container-title"]] if csl.get("container-title") else [],
        "volume": csl.get("volume"),
        "issue": csl.get("issue"),
        "page": csl.get("page"),
        "publisher": csl.get("publisher"),
        "type": kind,
        "URL": csl.get("URL"),
    }


def datacite_to_item(a, doi):
    """Map a DataCite record (api.datacite.org) onto the Crossref item shape."""
    titles = [t["title"] for t in a.get("titles", []) if t.get("title")]
    if not titles:
        raise ValueError("no title")
    year = a.get("publicationYear")
    return {
        "DOI": a.get("doi") or doi,
        "title": [titles[0]],
        "author": [
            {"family": c.get("familyName") or c.get("name", ""), "given": c.get("givenName", "")}
            for c in a.get("creators", [])
        ],
        "issued": {"date-parts": [[int(year)]]} if year else {},
        "container-title": [(a.get("container") or {}).get("title", "")]
        if (a.get("container") or {}).get("title") else [],
        "publisher": a.get("publisher") if isinstance(a.get("publisher"), str)
        else (a.get("publisher") or {}).get("name"),
        "type": ((a.get("types") or {}).get("resourceTypeGeneral") or "misc").lower(),
        "URL": a.get("url"),
    }


def s2_to_item(p):
    """Map a Semantic Scholar paper onto the Crossref item shape (DOI may be absent)."""
    authors = []
    for a in p.get("authors", []):
        parts = (a.get("name") or "").split()
        if parts:
            authors.append({"family": parts[-1], "given": " ".join(parts[:-1])})
    ids = p.get("externalIds") or {}
    types = p.get("publicationTypes") or []
    kind = ("journal-article" if "JournalArticle" in types
            else "proceedings-article" if "Conference" in types else "misc")
    return {
        "DOI": (ids.get("DOI") or "").lower(),
        "title": [p.get("title") or ""],
        "author": authors,
        "issued": {"date-parts": [[p["year"]]]} if p.get("year") else {},
        "container-title": [p["venue"]] if p.get("venue") else [],
        "type": kind,
        "URL": f"https://www.semanticscholar.org/paper/{p['paperId']}" if p.get("paperId") else None,
        "_s2": {
            "id": p.get("paperId"),
            "pdf": (p.get("openAccessPdf") or {}).get("url"),
            "citations": p.get("citationCount"),
            "abstract": p.get("abstract"),
        },
    }


def find_url(raw):
    """Link from 'url: ...', repaired for PDF line-break spaces; else any http(s) URL."""
    m = re.search(r"\burl\s*:\s*(https?:.*?)(?=\s*\(visited|\s(?:doi|issn|isbn)\s*:|$)", raw, re.I)
    if m:
        return re.sub(r"(%20|\s)+", "", m.group(1)).rstrip(".,;")
    m = re.search(r"https?://\S+", raw)
    return m.group(0).rstrip(".,;)") if m else None


def web_item(raw, url):
    """Record for a web-only source (no DOI): title from the entry, URL kept."""
    title = guess_title(raw) or clean_ref(raw)[:120]
    year = re.search(r"\b(?:19|20)\d{2}\b", raw)
    author = re.match(r"([^(“\"]+?)\s*\(", raw)
    return {
        "DOI": "",
        "title": [title],
        "author": [{"family": author.group(1).strip(), "given": ""}] if author else [],
        "issued": {"date-parts": [[int(year.group(0))]]} if year else {},
        "type": "webpage",
        "URL": url,
    }


# ---------- 2. resolve to metadata ----------

class Resolver:
    def __init__(self, email, openalex_key=None, s2_key=None):
        self.email = email
        self.openalex_key = openalex_key or os.environ.get("OPENALEX_API_KEY")
        self.s2_key = s2_key or os.environ.get("S2_API_KEY")
        self.blocked = set()   # hosts that hit a long rate limit: skipped for the rest of the run
        self.warnings = []     # human-readable notes about skipped sources
        self.s = requests.Session()
        self.s.headers["User-Agent"] = f"refcollector/0.1 (mailto:{email})"

    def get(self, url, **kw):
        host = urlparse(url).netloc
        if host in self.blocked:
            return None
        if host == "api.openalex.org" and self.openalex_key:
            kw["params"] = {**(kw.get("params") or {}), "api_key": self.openalex_key}
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=30, **kw)
            except requests.RequestException:
                time.sleep(1 + attempt)
                continue
            if r.status_code != 429:
                return r
            try:
                wait = int(r.headers.get("Retry-After", 0))
            except ValueError:
                wait = 0
            if wait > 60:  # a daily budget is spent: retrying is pointless, so stop using it
                self.blocked.add(host)
                self.warnings.append(
                    f"{host} rate limit reached (resets in about {wait // 3600 + 1} h); "
                    "that source was skipped for the rest of this run."
                )
                return None
            time.sleep(min(wait or 2 ** attempt, 30))
        return None

    # -- lookups by DOI: Crossref first, then doi.org (covers DataCite/Zenodo, mEDRA, ...) --

    def by_doi(self, doi):
        r = self.get(f"https://api.crossref.org/works/{doi}")
        if r is not None and r.ok:
            return r.json()["message"]
        r = self.get(f"https://api.datacite.org/dois/{doi}")  # Zenodo, figshare, Dryad...
        if r is not None and r.ok:
            try:
                return datacite_to_item(r.json()["data"]["attributes"], doi)
            except (ValueError, KeyError):
                pass
        r = self.get(
            f"https://doi.org/{doi}",
            headers={"Accept": "application/vnd.citationstyle.csl+json"},
        )
        if r is not None and r.ok:
            try:
                return csl_to_item(r.json(), doi)
            except ValueError:
                return None

    # -- lookups by text --

    def _crossref_candidates(self, query, rows=3):
        r = self.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": query[:400], "rows": rows},
        )
        return r.json()["message"]["items"] if r is not None and r.ok else []

    def _openalex_candidate_dois(self, title, rows=3):
        r = self.get(
            "https://api.openalex.org/works",
            params={"search": title[:300], "per-page": rows, "mailto": self.email},
        )
        if r is None or not r.ok:
            return []
        return [
            (w.get("title") or "", w["doi"].split("doi.org/")[-1], w.get("publication_year"))
            for w in r.json().get("results", [])
            if w.get("doi")
        ]

    def _s2_get(self, url, **kw):
        """Semantic Scholar without an API key shares a rate-limited pool: back off on 429."""
        headers = {"x-api-key": self.s2_key} if self.s2_key else {}
        for attempt in range(4):
            r = self.get(url, headers=headers, **kw)  # get() already retries short 429s
            if r is not None or "api.semanticscholar.org" in self.blocked:
                return r
            time.sleep(5 * (attempt + 1))
        return None

    def _s2_match(self, title):
        r = self._s2_get(
            "https://api.semanticscholar.org/graph/v1/paper/search/match",
            params={
                "query": title[:300],
                "fields": "title,year,authors,externalIds,openAccessPdf,venue,"
                          "citationCount,abstract,publicationTypes",
            },
        )
        if r is None or not r.ok:
            return None
        data = r.json().get("data") or []
        return s2_to_item(data[0]) if data else None

    def by_text(self, raw):
        """Match a reference string to a record. Returns (item, method) or (None, None).
        Item carries '_sources': the databases that agree on it."""
        query = clean_ref(raw)
        title = guess_title(raw)
        oa = self._openalex_candidate_dois(title) if title else []
        oa_dois = {d.lower() for t, d, _y in oa if match_score(t, raw) >= MATCH_THRESHOLD}

        def accept(item, sources):
            a = assess(item, raw)
            if a["accepted"]:
                item["_sources"] = sources
                item["_flags"] = a["flags"]
                item["_check"] = {k: a[k] for k in ("title_score", "author_overlap", "year_delta")}
            return a["accepted"]

        for item in self._crossref_candidates(query):
            both = item.get("DOI", "").lower() in oa_dois
            if accept(item, ["crossref"] + (["openalex"] if both else [])):
                return item, "text:crossref"
        for t, doi, _year in oa:
            if match_score(t, raw) < MATCH_THRESHOLD:
                continue
            item = self.by_doi(doi)
            if item and accept(item, ["openalex"]):
                return item, "text:openalex"
        if title:
            item = self._s2_match(title)
            if item and accept(item, ["semanticscholar"]):
                return item, "text:semanticscholar"
        return None, None

    def resolve(self, raw, forced_doi=None):
        """DOI -> Crossref/doi.org, else text search, else a link-only web record."""
        doi = forced_doi or find_doi(raw)
        if doi:
            item = self.by_doi(doi)
            if item:
                item["_sources"] = ["doi"]
                return item, "doi"
            if forced_doi:
                return None, None
        item, method = self.by_text(raw)
        if item:
            return item, method
        url = find_url(raw)
        if url:
            return web_item(raw, url), "link"
        return None, None

    def pdf_url(self, doi):
        """Best open-access PDF URL via Unpaywall, falling back to OpenAlex."""
        r = self.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": self.email})
        if r is not None and r.ok:
            loc = r.json().get("best_oa_location") or {}
            if loc.get("url_for_pdf"):
                return loc["url_for_pdf"]
        r = self.get(f"https://api.openalex.org/works/doi:{doi}", params={"mailto": self.email})
        if r is not None and r.ok:
            loc = r.json().get("best_oa_location") or {}
            return loc.get("pdf_url")

    def download(self, url, dest):
        r = self.get(url, stream=True)
        if r is None or not r.ok:
            return False
        chunks = r.iter_content(65536)
        first = next(chunks, b"")
        if not first.startswith(b"%PDF"):  # got an HTML landing page, not a PDF
            return False
        with open(dest, "wb") as f:
            f.write(first)
            for chunk in chunks:
                f.write(chunk)
        return True


# ---------- 3. output ----------

def normalize(item):
    authors = [
        f'{a.get("family", "")}, {a.get("given", "")}'.strip(", ")
        for a in item.get("author", [])
    ]
    year = None
    for k in ("issued", "published-print", "published-online"):
        parts = (item.get(k) or {}).get("date-parts", [[None]])[0]
        if parts and parts[0]:
            year = parts[0]
            break
    return {
        "doi": item.get("DOI", "").lower(),
        "title": unescape((item.get("title") or [""])[0]),
        "authors": authors,
        "year": year,
        "venue": (item.get("container-title") or [""])[0],
        "volume": item.get("volume"),
        "issue": item.get("issue"),
        "pages": item.get("page"),
        "publisher": item.get("publisher"),
        "type": item.get("type"),
        "url": item.get("URL"),
        "sources": item.get("_sources", []),
        "flags": item.get("_flags", []),
        "check": item.get("_check"),
        "s2": item.get("_s2"),
    }


def bib_key(m):
    """Citation key like 'Babarit2012Numerical' (ASCII, no punctuation)."""
    def ascii_(s):  # strip accents but keep the capitals
        s = unicodedata.normalize("NFKD", s)
        return re.sub(r"[^A-Za-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)))

    first = m["authors"][0].split(",")[0] if m["authors"] else "anon"
    word = m["title"].split()[0] if m["title"] else ""
    return ascii_(first) + str(m["year"] or "") + ascii_(word)[:12]


def unique_keys(metas):
    """One key per record, made unique with a/b/c suffixes; shared by BibTeX and CSL JSON."""
    seen, keys = {}, []
    for m in metas:
        base = bib_key(m)
        n = seen.get(base, 0)
        seen[base] = n + 1
        keys.append(base if n == 0 else base + chr(ord("a") + n - 1 if n <= 26 else ord("z")))
    return keys


CSL_TYPES = {"journal-article": "article-journal", "proceedings-article": "paper-conference",
             "webpage": "webpage", "dataset": "dataset", "book": "book",
             "book-chapter": "chapter", "posted-content": "article", "report": "report",
             "dissertation": "thesis"}


def to_csl(m, key):
    """CSL JSON item (Zotero's lossless import format)."""
    names = []
    for a in m["authors"]:
        family, _, given = a.partition(",")
        names.append({"family": family.strip(), "given": given.strip()} if given.strip()
                     else {"literal": family.strip()})
    item = {
        "id": key,
        "type": CSL_TYPES.get(m["type"], "document"),
        "title": m["title"],
        "author": names,
        "issued": {"date-parts": [[m["year"]]]} if m["year"] else None,
        "container-title": m["venue"],
        "volume": m["volume"],
        "issue": m["issue"],
        "page": m["pages"],
        "publisher": m["publisher"],
        "DOI": m["doi"],
        "URL": m.get("url") or (f'https://doi.org/{m["doi"]}' if m["doi"] else None),
    }
    return {k: v for k, v in item.items() if v}


def to_bibtex(m, key=None):
    key = key or bib_key(m)
    kind = {"journal-article": "article", "proceedings-article": "inproceedings"}.get(m["type"], "misc")
    fields = {
        "title": "{" + m["title"] + "}",
        "author": " and ".join(m["authors"]),
        "year": m["year"],
        {"article": "journal", "inproceedings": "booktitle"}.get(kind, "howpublished"): m["venue"],
        "volume": m["volume"],
        "number": m["issue"],
        "pages": (m["pages"] or "").replace("-", "--") or None,
        "publisher": m["publisher"],
        "doi": m["doi"],
        "url": m.get("url") if not m["doi"] else None,
    }
    body = ",\n".join(f"  {k} = {{{v}}}" if k != "title" else f"  title = {v}"
                      for k, v in fields.items() if v)
    return f"@{kind}{{{key},\n{body}\n}}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--email", required=True, help="contact email for API polite pools / Unpaywall")
    ap.add_argument("--out", default="output")
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    (out / "pdfs").mkdir(parents=True, exist_ok=True)

    refs = split_references(pdf_text(args.pdf))
    print(f"Found {len(refs)} reference entries")
    res = Resolver(args.email)

    records, unresolved, seen = [], [], set()
    for i, raw in enumerate(refs, 1):
        item, method = res.resolve(raw)
        if item is None:
            unresolved.append(raw)
            print(f"[{i}/{len(refs)}] unresolved")
            continue
        meta = normalize(item)
        if meta["doi"] and meta["doi"] in seen:
            continue
        seen.add(meta["doi"])
        meta["raw"] = raw
        meta["method"] = method
        meta["pdf"] = None
        if not args.no_download and meta["doi"]:
            url = res.pdf_url(meta["doi"])
            if url:
                dest = out / "pdfs" / (meta["doi"].replace("/", "_") + ".pdf")
                if dest.exists() or res.download(url, dest):
                    meta["pdf"] = str(dest)
        records.append(meta)
        print(f"[{i}/{len(refs)}] {meta['title'][:70]} | pdf: {'yes' if meta['pdf'] else 'no'}")

    keys = unique_keys(records)
    (out / "references.bib").write_text(
        "\n\n".join(to_bibtex(m, k) for m, k in zip(records, keys)), encoding="utf-8")
    (out / "references.csl.json").write_text(
        json.dumps([to_csl(m, k) for m, k in zip(records, keys)], indent=2, ensure_ascii=False),
        encoding="utf-8")
    (out / "references.json").write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "unresolved.txt").write_text("\n\n".join(unresolved), encoding="utf-8")
    got = sum(1 for r in records if r["pdf"])
    print(f"\nResolved {len(records)}, unresolved {len(unresolved)}, PDFs downloaded {got}")


if __name__ == "__main__":
    sys.exit(main())
