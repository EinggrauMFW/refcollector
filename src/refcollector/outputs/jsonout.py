"""Full JSON, unresolved lists, and common pure row projections."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from refcollector.contracts.ports import ExportRow
from refcollector.domain.enums import ACCEPTED_IDENTITIES, ExtractionState, IdentityState, Selection
from refcollector.domain.models import WorkMetadata
from refcollector.errors import InvalidInput


def metadata_dict(meta: WorkMetadata | Mapping[str, Any]) -> dict[str, Any]:
    return meta.model_dump(mode="json") if isinstance(meta, WorkMetadata) else dict(meta)


def metadata(row: ExportRow) -> dict[str, Any] | None:
    """Apply overrides without modifying either stored metadata or overrides."""
    if row.work is None:
        return None
    data = row.work.metadata.model_dump(mode="python")
    data.update(row.work.overrides)
    return WorkMetadata.model_validate(data).model_dump(mode="json")


def selected(rows: Sequence[ExportRow], selection: str) -> list[ExportRow]:
    try:
        selection = Selection(selection)
    except ValueError as exc:
        raise InvalidInput(f"Unknown export selection: {selection}") from exc
    if selection == Selection.COMPLETE_AUDIT:
        included = list(rows)
    else:
        identities = {IdentityState.ACCEPTED_USER} if selection == Selection.REVIEWED else ACCEPTED_IDENTITIES
        included = [
            row
            for row in rows
            if row.occurrence.identity in identities and row.occurrence.extraction != ExtractionState.EXCLUDED
        ]
    # Stable sorting retains the caller's order for occurrences with equal reading order.
    return sorted(included, key=lambda row: row.occurrence.order)


def ascii_text(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def bib_key(meta: WorkMetadata | Mapping[str, Any]) -> str:
    """Legacy author/year/first-title-word key, with ASCII characters only."""
    data = metadata_dict(meta)
    authors = data.get("authors") or []
    first = (authors[0].get("literal") or authors[0].get("family") or "anon") if authors else "anon"
    words = (data.get("title") or "").split()
    author = re.sub(r"[^A-Za-z0-9]", "", ascii_text(first.split(",")[0])) or "anon"
    word = re.sub(r"[^A-Za-z0-9]", "", ascii_text(words[0] if words else ""))[:12]
    return author + str(data.get("year") or "") + word


def _suffix(number: int) -> str:
    result = ""
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(ord("a") + digit) + result
    return result


def unique_keys(metas: Sequence[WorkMetadata | Mapping[str, Any]]) -> list[str]:
    """Use base, basea, baseb, ..., basez, baseaa, without cross-base collisions."""
    bases = [bib_key(meta) for meta in metas]
    reserved = set(bases)
    used: set[str] = set()
    counts: dict[str, int] = {}
    result = []
    for base in bases:
        number = counts.get(base, 0)
        key = base if number == 0 else base + _suffix(number)
        while key in used or (number > 0 and key in reserved):
            number += 1
            key = base + _suffix(number)
        counts[base] = number + 1
        used.add(key)
        result.append(key)
    return result


def incomplete_state(row: ExportRow) -> str | None:
    if row.occurrence.extraction == ExtractionState.EXCLUDED:
        return "excluded"
    if row.occurrence.identity not in ACCEPTED_IDENTITIES or row.work is None:
        return row.occurrence.identity.value
    return None


def bibliographic_records(
    rows: Sequence[ExportRow],
) -> list[tuple[ExportRow, dict[str, Any], str, str | None]]:
    records = []
    for row in rows:
        state = incomplete_state(row)
        data = metadata(row) if state is None else {"title": row.occurrence.text, "type": "misc"}
        records.append((row, data, state))
    keys = unique_keys([data for _, data, _ in records])
    return [(row, data, key, state) for (row, data, state), key in zip(records, keys, strict=True)]


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def render_json(rows: Sequence[ExportRow]) -> bytes:
    result = []
    for row in rows:
        occurrence = row.occurrence.model_dump(mode="json")
        occurrence["text"] = row.occurrence.text
        result.append(
            {
                "occurrence": occurrence,
                "metadata": metadata(row),
                "decision": row.decision.model_dump(mode="json") if row.decision else None,
                "access": row.access.model_dump(mode="json") if row.access else None,
                "files": [file.model_dump(mode="json") for file in row.files],
            }
        )
    return json_bytes(result)


def render_unresolved(rows: Sequence[ExportRow]) -> bytes:
    lines = []
    for row in rows:
        occurrence = row.occurrence
        if occurrence.identity in {IdentityState.UNRESOLVED, IdentityState.NEEDS_REVIEW}:
            if occurrence.extraction != ExtractionState.EXCLUDED:
                reasons = ", ".join(occurrence.identity_reasons)
                lines.append(
                    f"{occurrence.occurrence_id}\t{occurrence.label or ''}\t{reasons}\t{occurrence.text}"
                )
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
