"""BibTeX renderer helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import bibtexparser
from bibtexparser.model import Entry, Field

from refcollector.contracts.ports import ExportRow
from refcollector.domain.models import WorkMetadata

from .jsonout import bib_key as bib_key
from .jsonout import bibliographic_records, metadata_dict
from .jsonout import unique_keys as unique_keys


def _literal_text(value: Any) -> str:
    """Protect unmatched braces in citation text while retaining balanced TeX groups."""
    text = str(value)
    stack = []
    unmatched = set()
    for index, char in enumerate(text):
        if char not in "{}":
            continue
        slashes = len(text[:index]) - len(text[:index].rstrip("\\"))
        if slashes % 2:
            continue
        if char == "{":
            stack.append(index)
        elif stack:
            stack.pop()
        else:
            unmatched.add(index)
    unmatched.update(stack)
    return "".join(("\\" if index in unmatched else "") + char for index, char in enumerate(text))


def to_bibtex(
    meta: WorkMetadata | Mapping[str, Any], key: str | None = None, incomplete: str | None = None
) -> str:
    data = metadata_dict(meta)
    kind = {"journal-article": "article", "proceedings-article": "inproceedings"}.get(
        data.get("type"), "misc"
    )
    venue_field = {"article": "journal", "inproceedings": "booktitle"}.get(kind, "howpublished")
    fields = {
        "title": data.get("title"),
        "year": data.get("year"),
        venue_field: data.get("venue"),
        "volume": data.get("volume"),
        "number": data.get("issue"),
        "pages": re.sub(r"(?<!-)-(?!-)", "--", data.get("pages") or "") or None,
        "publisher": data.get("publisher"),
        "doi": data.get("doi"),
        "url": data.get("url"),
        "edition": data.get("edition"),
        "isbn": data.get("isbn"),
    }
    authors = []
    for person in data.get("authors") or []:
        if person.get("literal"):
            authors.append("{" + _literal_text(person["literal"]) + "}")
        else:
            family = person.get("family") or ""
            given = person.get("given") or ""
            authors.append(", ".join(part for part in (family, given) if part))
    if authors:
        fields["author"] = " and ".join(authors)
    if incomplete:
        fields["note"] = f"INCOMPLETE: {incomplete}; {data.get('title') or ''}"
    # bibtexparser 2.1 adds the enclosing field braces itself. Literal author braces remain inside.
    entry = Entry(
        kind,
        key or bib_key(data),
        [Field(name, _literal_text(value)) for name, value in fields.items() if value not in (None, "")],
    )
    return bibtexparser.write_string(bibtexparser.Library([entry]))


def render_bibtex(rows: Sequence[ExportRow]) -> bytes:
    return "\n".join(
        to_bibtex(meta, key, state) for _, meta, key, state in bibliographic_records(rows)
    ).encode()
