"""Local web UI for refcollector.  Run:  python webapp/app.py   ->  http://127.0.0.1:8000

Each job gets its own folder in the library, named after the paper:

    library/<Paper title>/
        source/<original>.pdf
        pdfs/<year> - <Author> - <Title>.pdf     downloaded open-access papers
        references/references.bib|csv|json       full exports
        references/references.csl.json           CSL JSON (lossless import into Zotero)
        references/unresolved.txt                entries that could not be matched
        paywalled/paywalled.csv                  papers without a free PDF
        paywalled/request_drafts.txt             email drafts to ask the authors
        summary.txt
        .job.json                                app state (do not edit)
"""
import csv
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import refcollector as rc  # noqa: E402

import pymupdf  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
LEGACY_JOBS = DATA / "jobs"  # older layout: data/jobs/<id>/
DEFAULT_LIBRARY = HERE / "library"
CONFIG = DATA / "config.json"
DATA.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="refcollector")
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()

DEFAULT_SETTINGS = {
    "email": "",
    "sender_name": "",
    "affiliation": "",
    "purpose": "my academic research",
    "library_dir": "",
    "openalex_key": "",
    "s2_key": "",
}
SECRET_KEYS = ("openalex_key", "s2_key")


# ---------- settings ----------

def load_settings():
    try:
        return {**DEFAULT_SETTINGS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def library_root(settings=None):
    s = settings or load_settings()
    return Path(s["library_dir"]).expanduser() if s["library_dir"] else DEFAULT_LIBRARY


def valid_email(e):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e)) and not e.lower().endswith(
        ("@example.com", "@example.org", "@test.com")
    )


class Settings(BaseModel):
    email: str = ""
    sender_name: str = ""
    affiliation: str = ""
    purpose: str = "my academic research"
    library_dir: str = ""
    openalex_key: str = ""
    s2_key: str = ""


def make_resolver(email):
    s = load_settings()
    return rc.Resolver(email, openalex_key=s["openalex_key"] or None, s2_key=s["s2_key"] or None)


@app.get("/api/settings")
def get_settings():
    s = load_settings()
    # API keys are never sent back to the page; it only learns whether one is stored.
    out = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    return {**out, "library_effective": str(library_root()),
            **{f"{k}_set": bool(s[k]) for k in SECRET_KEYS}}


@app.post("/api/settings")
def set_settings(s: Settings):
    if s.email and not valid_email(s.email):
        raise HTTPException(400, "Enter a real email address (Unpaywall rejects placeholders).")
    if s.library_dir:
        try:
            Path(s.library_dir).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"Cannot use that library folder: {e}")
    new, old = s.model_dump(), load_settings()
    for k in SECRET_KEYS:  # an empty field means "keep the stored key"
        new[k] = new[k].strip() or old[k]
    CONFIG.write_text(json.dumps(new, indent=2), encoding="utf-8")
    return {k: v for k, v in new.items() if k not in SECRET_KEYS}


# ---------- naming / folders ----------

def safe_name(s, limit=60):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].rstrip(" .") or "Untitled"


def paper_title(pdf_path, fallback):
    """Best guess at a paper's title: PDF metadata, else the largest text on page 1-2."""
    try:
        with pymupdf.open(pdf_path) as doc:
            t = (doc.metadata or {}).get("title", "").strip()
            if len(t) > 8 and not re.search(r"\.(docx?|tex|pdf|indd)$|untitled|microsoft word", t, re.I):
                return t
            for page in list(doc)[:2]:
                spans = [
                    s
                    for b in page.get_text("dict")["blocks"]
                    for l in b.get("lines", [])
                    for s in l["spans"]
                    if s["text"].strip()
                ]
                if not spans:
                    continue
                big = max(s["size"] for s in spans)
                title = " ".join(s["text"].strip() for s in spans if s["size"] >= big * 0.95)
                if len(title) > 8:
                    return title
    except Exception:  # noqa: BLE001
        pass
    return fallback


def unique_folder(root, name):
    p, n = root / name, 2
    while p.exists():
        p = root / f"{name} ({n})"
        n += 1
    return p


def job_dir(job):
    if job.get("folder"):
        return Path(job["folder"])
    return LEGACY_JOBS / job["id"]  # legacy jobs


def pdf_filename(meta, taken):
    first = meta["authors"][0].split(",")[0] if meta["authors"] else "Unknown"
    base = safe_name(f'{meta["year"] or "n.d."} - {first} - {meta["title"]}', 90)
    name = f"{base}.pdf"
    if name in taken:
        name = f'{base} [{safe_name(meta["doi"].split("/")[-1], 20)}].pdf'
    return name


# ---------- job persistence ----------

def save_job(job):
    with LOCK:
        d = job_dir(job)
        d.mkdir(parents=True, exist_ok=True)
        fname = ".job.json" if job.get("folder") else "job.json"  # legacy jobs keep job.json
        (d / fname).write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")


def load_jobs():
    files = list(LEGACY_JOBS.glob("*/job.json")) + list(
        library_root().glob("*/.job.json")
    )
    for f in files:
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if j.get("status") in ("queued", "extracting", "resolving"):
            j["status"] = "interrupted"
        JOBS[j["id"]] = j


# ---------- enrichment ----------

def abstract_from_index(inv):
    if not inv:
        return ""
    words = {}
    for w, positions in inv.items():
        for p in positions:
            words[p] = w
    return " ".join(words[k] for k in sorted(words))


def enrich(res, doi, item):
    """Detailed fields from Crossref item (already fetched) + OpenAlex."""
    d = {
        "url": item.get("URL"),
        "issn": item.get("ISSN") or [],
        "subjects": item.get("subject") or [],
        "crossref_cited_by": item.get("is-referenced-by-count"),
        "license": [l.get("URL") for l in item.get("license", [])][:2],
        "abstract": re.sub(r"<[^>]+>", "", item.get("abstract", "") or ""),
        "keywords": [],
        "topics": [],
        "author_details": [],
        "cited_by_count": None,
        "referenced_works_count": None,
        "oa_status": None,
        "is_oa": None,
        "language": None,
        "openalex_id": None,
        "oa_pdf_urls": [],
    }
    if not doi:  # web-only source, nothing more to look up
        return d
    r = res.get(f"https://api.openalex.org/works/doi:{doi}", params={"mailto": res.email})
    if r is None or not r.ok:
        return d
    w = r.json()
    d["openalex_id"] = w.get("id")
    d["abstract"] = abstract_from_index(w.get("abstract_inverted_index")) or d["abstract"]
    d["keywords"] = [k["display_name"] for k in w.get("keywords", [])]
    d["topics"] = [t["display_name"] for t in w.get("topics", [])[:5]]
    d["cited_by_count"] = w.get("cited_by_count")
    d["referenced_works_count"] = w.get("referenced_works_count")
    d["language"] = w.get("language")
    oa = w.get("open_access") or {}
    d["is_oa"], d["oa_status"] = oa.get("is_oa"), oa.get("oa_status")
    d["author_details"] = [
        {
            "name": a["author"]["display_name"],
            "orcid": a["author"].get("orcid"),
            "institutions": [i["display_name"] for i in a.get("institutions", [])],
        }
        for a in w.get("authorships", [])
    ]
    for loc in w.get("locations", []):
        if loc.get("pdf_url"):
            d["oa_pdf_urls"].append(loc["pdf_url"])
    return d


def find_and_download_pdf(res, doi, oa_urls, dest):
    """Try every known open-access PDF URL until one really is a PDF."""
    urls = []
    r = res.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": res.email}) if doi else None
    if r is not None and r.ok:
        for loc in r.json().get("oa_locations") or []:
            if loc.get("url_for_pdf"):
                urls.append(loc["url_for_pdf"])
    urls += oa_urls
    for u in dict.fromkeys(urls):
        if res.download(u, dest):
            return u
    return None


# ---------- pipeline ----------

def resolve_ref(job, ref, res):
    item, method = res.resolve(ref["raw"], ref.get("forced_doi"))
    if item is None:
        ref.update(status="unresolved", method=None, meta=None, details=None, pdf=None,
                   pdf_status="n/a")
        return
    meta = rc.normalize(item)
    details = enrich(res, meta["doi"], item)
    s2 = meta.get("s2")
    if not meta["doi"] and s2:  # Semantic Scholar record without a DOI
        details.update(cited_by_count=s2["citations"], abstract=s2["abstract"] or "",
                       oa_pdf_urls=[s2["pdf"]] if s2["pdf"] else [])
    if method == "link":
        confidence = "link"
    elif method == "doi":
        confidence = "high"
    elif meta["flags"]:
        confidence = "review"  # matched, but something about it is off: worth a human look
    elif len(meta["sources"]) >= 2:
        confidence = "high"  # two databases agree, no red flags
    else:
        confidence = "medium"  # clean match, but only one database found it
    can_download = bool(meta["doi"] or details["oa_pdf_urls"])
    ref.update(status="resolved", method=method, confidence=confidence, meta=meta,
               details=details, pdf=None, pdf_status="skipped" if can_download else "n/a")
    if job["download"] and can_download:
        pdf_dir = job_dir(job) / "pdfs"
        pdf_dir.mkdir(exist_ok=True)
        taken = {r["pdf"] for r in job["refs"] if r.get("pdf")}
        fname = pdf_filename(meta, taken)
        if find_and_download_pdf(res, meta["doi"], details["oa_pdf_urls"], pdf_dir / fname):  # doi may be ""
            ref.update(pdf=fname, pdf_status="downloaded")
        else:
            ref.update(pdf_status="paywalled")


def run_job(job_id):
    job = JOBS[job_id]
    try:
        job["status"] = "extracting"
        save_job(job)
        source = job_dir(job) / "source" / job["name"]
        refs = rc.split_references(rc.pdf_text(source))
        job["refs"] = [{"i": i, "raw": r, "status": "pending"} for i, r in enumerate(refs)]
        job["status"] = "resolving"
        save_job(job)
        res = make_resolver(job["email"])
        job["warnings"] = res.warnings  # filled in live if a source hits its rate limit
        seen = set()
        for ref in job["refs"]:
            resolve_ref(job, ref, res)
            doi = (ref.get("meta") or {}).get("doi")
            if doi and doi in seen:  # web-only records have no DOI and are never duplicates
                ref["duplicate"] = True
            seen.add(doi)
            save_job(job)
        job["status"] = "done"
    except SystemExit as e:
        job.update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        job.update(status="error", error=f"{type(e).__name__}: {e}")
    save_job(job)
    if job["status"] == "done":
        write_exports(job)


# ---------- exports ----------

def resolved(job):
    return [r for r in job["refs"] if r["status"] == "resolved" and not r.get("duplicate")]


def build_bib(job):
    metas = [r["meta"] for r in resolved(job)]
    return "\n\n".join(rc.to_bibtex(m, k) for m, k in zip(metas, rc.unique_keys(metas)))


def build_csl(job):
    """CSL JSON: imports into Zotero, Mendeley, Citation.js, pandoc without losing fields."""
    metas = [r["meta"] for r in resolved(job)]
    return json.dumps([rc.to_csl(m, k) for m, k in zip(metas, rc.unique_keys(metas))],
                      indent=2, ensure_ascii=False)


CSV_COLS = ["title", "authors", "year", "venue", "volume", "issue", "pages", "publisher",
            "type", "confidence", "doi", "url", "cited_by_count", "oa_status", "keywords", "pdf_status",
            "pdf", "abstract"]


def build_csv(job, only=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for r in resolved(job):
        if only and r["pdf_status"] != only:
            continue
        row = {**r["meta"], **r["details"], "pdf_status": r["pdf_status"], "pdf": r["pdf"] or "",
               "confidence": r.get("confidence", "")}
        w.writerow(["; ".join(row[c]) if isinstance(row.get(c), list) else row.get(c, "") for c in CSV_COLS])
    return "﻿" + buf.getvalue()


def build_drafts(job, s):
    out = []
    for r in resolved(job):
        if r["pdf_status"] != "paywalled":
            continue
        m, d = r["meta"], r["details"]
        first = d["author_details"][0]["name"] if d["author_details"] else (m["authors"][0] if m["authors"] else "Author")
        last = first.split(",")[0] if "," in first else first.split()[-1]
        who = ", ".join(x for x in (s["sender_name"], s["affiliation"]) if x)
        authors = "; ".join(
            a["name"] + (f' ({a["institutions"][0]})' if a["institutions"] else "")
            for a in d["author_details"]
        ) or "; ".join(m["authors"])
        out.append(
            f'=== {m["doi"]} ===\nAuthors: {authors}\nPublisher page: https://doi.org/{m["doi"]}\n'
            f'Subject: Request for a copy of your paper: "{m["title"]}"\n\n'
            f'Dear Dr. {last},\n\n{"My name is " + who + ". " if who else ""}I am reading your paper '
            f'"{m["title"]}" ({m["venue"]}, {m["year"]}; DOI {m["doi"]}) for '
            f'{s["purpose"] or "my academic research"}, but I do not have subscription access to it.\n\n'
            "Would you be able to send me a PDF copy for personal study? I would be very grateful, "
            "and I will of course cite the work appropriately.\n\nThank you for your time.\n\n"
            f'Kind regards,\n{s["sender_name"]}\n'
        )
    return "\n".join(out)


def write_exports(job):
    d = job_dir(job)
    (d / "references").mkdir(exist_ok=True)
    (d / "paywalled").mkdir(exist_ok=True)
    (d / "references" / "references.bib").write_text(build_bib(job), encoding="utf-8")
    (d / "references" / "references.csl.json").write_text(build_csl(job), encoding="utf-8")
    (d / "references" / "references.csv").write_text(build_csv(job), encoding="utf-8")
    (d / "references" / "references.json").write_text(
        json.dumps(job["refs"], indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "references" / "unresolved.txt").write_text(
        "\n\n".join(r["raw"] for r in job["refs"] if r["status"] == "unresolved"), encoding="utf-8")
    (d / "paywalled" / "paywalled.csv").write_text(build_csv(job, "paywalled"), encoding="utf-8")
    (d / "paywalled" / "request_drafts.txt").write_text(
        build_drafts(job, load_settings()), encoding="utf-8")
    refs = job["refs"]
    count = lambda k: sum(r.get("pdf_status") == k for r in refs)  # noqa: E731
    (d / "summary.txt").write_text(
        f'{job.get("title") or job["name"]}\nSource: {job["name"]}\n\n'
        f'References found: {len(refs)}\nResolved: {sum(r["status"] == "resolved" for r in refs)}\n'
        f'Unresolved: {sum(r["status"] == "unresolved" for r in refs)}\n'
        f'PDFs downloaded: {count("downloaded")}\nPaywalled: {count("paywalled")}\n',
        encoding="utf-8")


# ---------- API ----------

@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...), download: bool = Form(True)):
    s = load_settings()
    if not valid_email(s["email"]):
        raise HTTPException(400, "Set your email in Settings first.")
    job_id = uuid.uuid4().hex[:8]
    name = safe_name(Path(file.filename).name, 150)
    tmp = DATA / f"upload_{job_id}.pdf"
    with open(tmp, "wb") as f:
        shutil.copyfileobj(file.file, f)
    title = paper_title(tmp, Path(name).stem)
    root = library_root(s)
    root.mkdir(parents=True, exist_ok=True)
    folder = unique_folder(root, safe_name(title))
    (folder / "source").mkdir(parents=True)
    (folder / "pdfs").mkdir()
    shutil.move(str(tmp), folder / "source" / name)
    job = {
        "id": job_id,
        "name": name,
        "title": title,
        "folder": str(folder),
        "created": time.time(),
        "status": "queued",
        "download": download,
        "email": s["email"],
        "refs": [],
    }
    JOBS[job_id] = job
    save_job(job)
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return {"id": job_id}


@app.get("/api/jobs")
def list_jobs():
    return sorted(
        (
            {
                "id": j["id"],
                "name": j["name"],
                "title": j.get("title") or j["name"],
                "status": j["status"],
                "created": j["created"],
                "total": len(j["refs"]),
                "resolved": sum(r["status"] == "resolved" for r in j["refs"]),
            }
            for j in JOBS.values()
        ),
        key=lambda x: -x["created"],
    )


def get_job(job_id):
    if job_id not in JOBS:
        raise HTTPException(404, "No such job")
    return JOBS[job_id]


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    return get_job(job_id)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = get_job(job_id)
    JOBS.pop(job_id)
    d = job_dir(job)
    if (d / ".job.json").exists() or (d / "job.json").exists():
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/open-folder")
def open_folder(job_id: str):
    d = job_dir(get_job(job_id))
    if not hasattr(os, "startfile"):
        raise HTTPException(400, "Opening folders is only supported on Windows.")
    os.startfile(d)  # noqa: S606 (local app, path is ours)
    return {"folder": str(d)}


class DoiFix(BaseModel):
    doi: str


@app.post("/api/jobs/{job_id}/refs/{i}/doi")
def fix_doi(job_id: str, i: int, body: DoiFix):
    job = get_job(job_id)
    if not 0 <= i < len(job["refs"]):
        raise HTTPException(404, "No such reference")
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", body.doi.strip())
    ref = job["refs"][i]
    ref["forced_doi"] = doi
    resolve_ref(job, ref, make_resolver(job["email"]))
    save_job(job)
    if ref["status"] != "resolved":
        raise HTTPException(404, f"Crossref has no record for DOI {doi}")
    write_exports(job)
    return ref


@app.get("/api/jobs/{job_id}/references.csl.json")
def export_csl(job_id: str):
    return Response(build_csl(get_job(job_id)), media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=references.csl.json"})


@app.get("/api/jobs/{job_id}/references.bib")
def export_bib(job_id: str):
    return Response(build_bib(get_job(job_id)), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=references.bib"})


@app.get("/api/jobs/{job_id}/references.json")
def export_json(job_id: str):
    text = json.dumps(get_job(job_id)["refs"], indent=2, ensure_ascii=False)
    return Response(text, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=references.json"})


@app.get("/api/jobs/{job_id}/references.csv")
def export_csv(job_id: str):
    return Response(build_csv(get_job(job_id)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=references.csv"})


@app.get("/api/jobs/{job_id}/pdfs.zip")
def export_zip(job_id: str):
    d = job_dir(get_job(job_id)) / "pdfs"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for f in d.glob("*.pdf"):
            z.write(f, f.name)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=pdfs.zip"})


@app.get("/api/jobs/{job_id}/pdfs/{name}")
def get_pdf(job_id: str, name: str):
    p = job_dir(get_job(job_id)) / "pdfs" / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


load_jobs()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
