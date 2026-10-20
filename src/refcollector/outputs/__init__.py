"""Pure export and request-draft renderers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import asdict, is_dataclass
from email.message import EmailMessage
from typing import Any

from bibtexparser import Library
from bibtexparser.model import Entry, Field

from refcollector.contracts.ports import RenderedFile
from refcollector.domain.enums import ACCEPTED_IDENTITIES, ExportFormat, IdentityState, Selection
from refcollector.errors import InvalidInput

CSL_TYPES = {
    "journal-article": "article-journal",
    "proceedings-article": "paper-conference",
    "webpage": "webpage",
    "dataset": "dataset",
    "book": "book",
    "book-chapter": "chapter",
    "posted-content": "article",
    "report": "report",
    "standard": "standard",
    "dissertation": "thesis",
}
CSV_FIELDS = (
    "occurrence_id",
    "source_id",
    "label",
    "text",
    "identity",
    "reasons",
    "title",
    "authors",
    "year",
    "venue",
    "doi",
    "url",
    "discovery",
    "classification",
    "local_copy",
    "retrieval",
)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    return value


def metadata(row):
    if row.work is None:
        return None
    result = row.work.metadata.model_dump(mode="python")
    result.update(row.work.overrides)
    return result


def selected(rows, selection):
    selection = Selection(selection)
    if selection == Selection.COMPLETE_AUDIT:
        return list(rows)
    if selection == Selection.REVIEWED:
        return [
            r
            for r in rows
            if r.occurrence.identity == IdentityState.ACCEPTED_USER
            and r.occurrence.extraction.value != "excluded"
        ]
    return [
        r
        for r in rows
        if r.occurrence.identity in ACCEPTED_IDENTITIES and r.occurrence.extraction.value != "excluded"
    ]


def _ascii(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", str(value or ""))
        if not unicodedata.combining(c) and c.isascii()
    )


def _key(meta):
    author = meta.get("authors") or []
    first = author[0].get("literal") or author[0].get("family") or "anon" if author else "anon"
    title = (meta.get("title") or "").split()
    return (
        re.sub(r"[^A-Za-z0-9]", "", _ascii(first.split(",")[0]))
        + str(meta.get("year") or "")
        + re.sub(r"[^A-Za-z0-9]", "", _ascii(title[0] if title else ""))[:12]
    )


def _records(rows, selection):
    records = []
    for row in selected(rows, selection):
        meta = metadata(row) if row.occurrence.identity in ACCEPTED_IDENTITIES else None
        if meta is None and selection == Selection.COMPLETE_AUDIT:
            meta = {"title": row.occurrence.text, "authors": [], "year": None, "type": "misc"}
        if meta is not None:
            records.append((row, meta, _key(meta)))
    seen, out = {}, []
    for row, meta, base in records:
        n = seen.get(base, 0)
        seen[base] = n + 1
        key = base if n == 0 else base + chr(ord("a") + n - 1)
        out.append((row, meta, key))
    return out


def _people(meta):
    result = []
    for person in meta.get("authors") or []:
        if person.get("literal"):
            result.append({"literal": person["literal"]})
        else:
            result.append({k: person[k] for k in ("family", "given") if person.get(k)})
    return result


def _csl(meta, key):
    item = {
        "id": key,
        "type": CSL_TYPES.get(meta.get("type"), "document"),
        "title": meta.get("title"),
        "author": _people(meta),
        "issued": {"date-parts": [[meta["year"]]]} if meta.get("year") else None,
        "container-title": meta.get("venue"),
        "volume": meta.get("volume"),
        "issue": meta.get("issue"),
        "page": meta.get("pages"),
        "publisher": meta.get("publisher"),
        "DOI": meta.get("doi"),
        "URL": meta.get("url") or (f"https://doi.org/{meta['doi']}" if meta.get("doi") else None),
    }
    return {k: v for k, v in item.items() if v is not None and v != []}


def _bib(meta, key, incomplete=None):
    types = {"journal-article": "article", "proceedings-article": "inproceedings"}
    entry_type = types.get(meta.get("type"), "misc")
    fields = {
        "title": meta.get("title"),
        "year": meta.get("year"),
        "journal"
        if entry_type == "article"
        else "booktitle"
        if entry_type == "inproceedings"
        else "howpublished": meta.get("venue"),
        "volume": meta.get("volume"),
        "number": meta.get("issue"),
        "pages": meta.get("pages"),
        "publisher": meta.get("publisher"),
        "doi": meta.get("doi"),
        "url": meta.get("url"),
    }
    authors = []
    for p in _people(meta):
        authors.append(
            "{" + p["literal"] + "}"
            if "literal" in p
            else (p.get("family", "") + (", " + p["given"] if p.get("given") else ""))
        )
    if authors:
        fields["author"] = " and ".join(authors)
    if incomplete:
        fields["note"] = f"INCOMPLETE: {incomplete}; {meta.get('title', '')}"
    entry = Entry(
        entry_type, key, [Field(k, "{" + str(v) + "}") for k, v in fields.items() if v not in (None, "")]
    )
    lib = Library()
    lib.add(entry)
    from bibtexparser import write_string

    return write_string(lib)


def _rows_json(rows, selection):
    out = []
    for row in selected(rows, selection):
        occ = row.occurrence
        out.append(
            {
                "occurrence": {
                    "occurrence_id": occ.occurrence_id,
                    "source_id": occ.source_id,
                    "label": occ.label,
                    "text": occ.text,
                    "extraction": occ.extraction.value,
                    "identity": occ.identity.value,
                    "reasons": [x.value for x in occ.identity_reasons],
                    "discovery": occ.discovery.value,
                    "classification": occ.classification.value if occ.classification else None,
                    "local_copy": occ.local_copy.value,
                    "retrieval": occ.retrieval.value,
                },
                "metadata": metadata(row),
                "decision": _dump(row.decision),
                "access": _dump(row.access),
                "files": [_dump(f) for f in row.files],
            }
        )
    return out


def _safe_cell(value):
    text = "; ".join(map(str, value)) if isinstance(value, list) else "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def export_file_name(meta):
    author = (meta.get("authors") or [{}])[0]
    first = author.get("literal") or author.get("family") or "anon"
    title = " ".join((meta.get("title") or "").split()[:6])[:60]

    def slug(s):
        return re.sub(r"[^A-Za-z0-9]+", "_", _ascii(s)).strip("_") or "anon"

    return f"{meta.get('year') or 'noyear'}_{slug(first)}_{slug(title)}.pdf"


def unique_export_file_names(metas):
    seen, result = set(), []
    for meta in metas:
        name = export_file_name(meta)
        stem, ext = name[:-4], ".pdf"
        candidate = name
        n = 2
        while candidate.casefold() in seen:
            candidate = f"{stem}_{n}{ext}"
            n += 1
        seen.add(candidate.casefold())
        result.append(candidate)
    return result


class DefaultExporter:
    def render(self, fmt, rows, *, selection, project):
        try:
            fmt = ExportFormat(fmt)
        except ValueError as e:
            raise InvalidInput(f"Unknown export format: {fmt}") from e
        items = _records(rows, selection)
        if fmt == ExportFormat.BIBTEX:
            data = "\n".join(
                _bib(
                    m, k, r.occurrence.identity.value if m.get("type") == "misc" and r.work is None else None
                )
                for r, m, k in items
            ).encode()
            name, media = "references.bib", "application/x-bibtex"
        elif fmt == ExportFormat.CSL_JSON:
            csl = [_csl(m, k) for _, m, k in items]
            for (r, _m, _k), item in zip(items, csl, strict=True):
                if r.work is None:
                    item["note"] = f"INCOMPLETE: {r.occurrence.identity.value}; {r.occurrence.text}"
            data = json.dumps(csl, ensure_ascii=False, sort_keys=True, indent=2).encode()
            name, media = "references.json", "application/vnd.citationstyles.csl+json"
        elif fmt == ExportFormat.CSV:
            buf = io.StringIO(newline="")
            w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\r\n")
            w.writeheader()
            for row in selected(rows, selection):
                m = metadata(row) or {}
                o = row.occurrence
                w.writerow(
                    {
                        "occurrence_id": o.occurrence_id,
                        "source_id": o.source_id,
                        "label": o.label,
                        "text": o.text,
                        "identity": o.identity.value,
                        "reasons": o.identity_reasons,
                        "title": m.get("title"),
                        "authors": [
                            p.get("literal") or ", ".join(x for x in (p.get("family"), p.get("given")) if x)
                            for p in m.get("authors", [])
                        ],
                        "year": m.get("year"),
                        "venue": m.get("venue"),
                        "doi": m.get("doi"),
                        "url": m.get("url"),
                        "discovery": o.discovery.value,
                        "classification": o.classification.value if o.classification else "",
                        "local_copy": o.local_copy.value,
                        "retrieval": o.retrieval.value,
                    }
                )
            data = "\r\n".join(
                ",".join(_safe_cell(x) for x in row) for row in csv.reader(io.StringIO(buf.getvalue()))
            ).encode()
            name, media = "references.csv", "text/csv"
        elif fmt == ExportFormat.JSON:
            data = json.dumps(
                _rows_json(rows, selection), ensure_ascii=False, sort_keys=True, indent=2
            ).encode()
            name, media = "references.json", "application/json"
        elif fmt == ExportFormat.UNRESOLVED:
            lines = [
                f"{r.occurrence.occurrence_id}\t{r.occurrence.label or ''}\t{', '.join(str(x.value) for x in r.occurrence.identity_reasons)}\t{r.occurrence.text}"  # noqa: E501
                for r in selected(rows, selection)
                if r.occurrence.identity in {IdentityState.UNRESOLVED, IdentityState.NEEDS_REVIEW}
                and r.occurrence.extraction.value != "excluded"
            ]
            data = "\n".join(lines).encode()
            name, media = "unresolved.txt", "text/plain"
        else:
            lines = [f"# {project.name}", f"Revision: {project.revision}", f"Selection: {selection}"]
            for r in selected(rows, selection):
                o = r.occurrence
                lines.append(
                    f"- {o.occurrence_id} {o.label or ''} [{o.identity.value}] {', '.join(str(x.value) for x in o.identity_reasons)} {o.text}"  # noqa: E501
                )
                if r.access and r.access.classification.value == "none_found":
                    lines.append(
                        f"  No eligible copy was found in the configured sources ({', '.join(r.access.sources_consulted)}) at {r.access.checked_at.isoformat()}."  # noqa: E501
                    )
            data = "\n".join(lines).encode()
            name, media = "audit_report.md", "text/markdown"
        return RenderedFile(name, media, data)

    def manifest(self, project, *, selection, files, counts, policy_versions):
        payload = {
            "project_id": project.project_id,
            "revision": project.revision,
            "selection": str(selection),
            "policy_versions": dict(policy_versions),
            "counts": dict(counts),
            "files": [
                {
                    "name": f.name,
                    "media_type": f.media_type,
                    "bytes": len(f.data),
                    "sha256": hashlib.sha256(f.data).hexdigest(),
                }
                for f in files
            ],
        }
        return RenderedFile(
            "manifest.json",
            "application/json",
            json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2).encode(),
        )


class DefaultDraftRenderer:
    def render(self, rows, *, sender, language, group_by_author):
        texts = []
        emls = []
        language = str(language)
        eligible = [r for r in rows if r.occurrence.identity in ACCEPTED_IDENTITIES and r.work]
        groups = []
        if group_by_author:
            grouped = {}
            for r in eligible:
                m = metadata(r)
                author = (
                    (m.get("authors") or [{}])[0].get("literal")
                    or (m.get("authors") or [{}])[0].get("family")
                    or "Unknown"
                )
                grouped.setdefault(author, []).append(r)
            groups = list(grouped.values())
        else:
            groups = [[r] for r in eligible]
        for group in groups:
            first = group[0]
            m = metadata(first)
            title = m.get("title") or ""
            greeting = sender.honorific_style or (
                "Dear authors," if language == "en" else "Yth. Bapak/Ibu penulis,"
            )
            availability = (
                "I could not find an eligible copy in the sources I checked."
                if language == "en"
                else "Saya tidak dapat menemukan salinan yang memenuhi syarat di sumber yang saya periksa."
            )
            purpose = sender.purpose or ("my research" if language == "en" else "penelitian saya")
            works = "\n".join(f"- {metadata(r).get('title') or r.occurrence.text}" for r in group)
            if language == "en":
                body = (
                    f"{greeting}\n\nI am {sender.name}"
                    + (f", {sender.affiliation}" if sender.affiliation else "")
                    + f". I am writing about:\n{works}\n\n{availability} I am using this work for {purpose}.\n\nRegards,\n{sender.name}"  # noqa: E501
                )
            else:
                body = (
                    f"{greeting}\n\nNama saya {sender.name}"
                    + (f", {sender.affiliation}" if sender.affiliation else "")
                    + f". Saya menulis tentang:\n{works}\n\n{availability} Saya menggunakan karya ini untuk {purpose}.\n\nHormat saya,\n{sender.name}"  # noqa: E501
                )
            texts.append(body)
            msg = EmailMessage()
            msg["To"] = ""
            msg["Subject"] = f"Request for a copy: {title}"
            msg.set_content(body)
            emls.append(msg.as_bytes())
        return [RenderedFile("request_drafts.txt", "text/plain", "\n\n---\n\n".join(texts).encode())] + [
            RenderedFile(f"request_{i}.eml", "message/rfc822", b) for i, b in enumerate(emls, 1)
        ]
