"""CSL JSON renderer helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from refcollector.contracts.ports import ExportRow
from refcollector.domain.models import WorkMetadata

from .jsonout import bibliographic_records, json_bytes, metadata_dict

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


def to_csl(meta: WorkMetadata | Mapping[str, Any], key: str) -> dict[str, Any]:
    data = metadata_dict(meta)
    names = []
    for person in data.get("authors") or []:
        if person.get("literal"):
            names.append({"literal": person["literal"]})
        else:
            names.append({name: person[name] for name in ("family", "given") if person.get(name)})
    doi = data.get("doi")
    item = {
        "id": key,
        "type": CSL_TYPES.get(data.get("type"), "document"),
        "title": data.get("title"),
        "author": names,
        "issued": {"date-parts": [[data["year"]]]} if data.get("year") else None,
        "container-title": data.get("venue"),
        "volume": data.get("volume"),
        "issue": data.get("issue"),
        "page": data.get("pages"),
        "publisher": data.get("publisher"),
        "DOI": doi,
        "URL": data.get("url") or (f"https://doi.org/{doi}" if doi else None),
    }
    return {key: value for key, value in item.items() if value}


def render_csl(rows: Sequence[ExportRow]) -> bytes:
    result = []
    for row, meta, key, state in bibliographic_records(rows):
        item = to_csl(meta, key)
        if state:
            item["note"] = f"INCOMPLETE: {state}; {row.occurrence.text}"
        result.append(item)
    return json_bytes(result)
