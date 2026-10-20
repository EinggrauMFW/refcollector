"""CSV renderer helpers."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from refcollector.contracts.ports import ExportRow

from .jsonout import metadata

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


def _safe_cell(value) -> str:
    text = "; ".join(map(str, value)) if isinstance(value, list) else "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def render_csv(rows: Sequence[ExportRow]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        occurrence = row.occurrence
        meta = metadata(row) or {}
        authors = [
            person.get("literal")
            or ", ".join(part for part in (person.get("family"), person.get("given")) if part)
            for person in meta.get("authors") or []
        ]
        values = {
            "occurrence_id": occurrence.occurrence_id,
            "source_id": occurrence.source_id,
            "label": occurrence.label,
            "text": occurrence.text,
            "identity": occurrence.identity,
            "reasons": list(occurrence.identity_reasons),
            "title": meta.get("title"),
            "authors": authors,
            "year": meta.get("year"),
            "venue": meta.get("venue"),
            "doi": meta.get("doi"),
            "url": meta.get("url"),
            "discovery": row.access.discovery if row.access else occurrence.discovery,
            "classification": row.access.classification if row.access else occurrence.classification,
            "local_copy": occurrence.local_copy,
            "retrieval": occurrence.retrieval,
        }
        writer.writerow({key: _safe_cell(value) for key, value in values.items()})
    return buffer.getvalue().encode("utf-8")
