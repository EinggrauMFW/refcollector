"""Split entry starts, retaining wrapped lines and their source spans."""

from __future__ import annotations

import re
from collections.abc import Iterable

from refcollector.contracts.ports import ExtractedEntry
from refcollector.domain.enums import ExtractionWarning
from refcollector.domain.models import Span
from refcollector.extraction.headings import is_heading
from refcollector.extraction.layout import LayoutLine, clean

_NUM = re.compile(r"^\s*(\[\d+\]|\d{1,3}\.(?=\s))\s*(?=\S)")
_AUTHOR = re.compile(
    r"^(?:[^\W\d_][^\s,()]*(?:\s[^\s,()]+){0,3},\s+[A-Z]"
    r"|\u2014\s*\(|[^\n()]{1,100}\.?\s+\((?:(?:19|20)\d{2}|n\.d\.)[a-z]?\))"
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_DOI = re.compile(r"\b10\s*\.\s*\d{4,9}\s*/")
_YEAR_TITLE = re.compile(r"(?:\((?:19|20)\d{2}[a-z]?\)\.?|\b(?:19|20)\d{2}[a-z]?\.)\s+\w")


def starts_entry(text: str) -> bool:
    return bool(
        _NUM.match(text) or (text[:1].isupper() and _AUTHOR.match(text)) or text.startswith("\u2014 (")
    )


def score_text(text: str) -> float:
    """Reference density, gated by an author/label start to reject body citations."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    starts = sum(starts_entry(line) for line in lines)
    if not starts or not _YEAR.search(text):
        return 0.0
    evidence = len(_YEAR.findall(text)) + 2 * len(_DOI.findall(text)) + 2 * starts
    return evidence / max(1, len(text.split()))


def _normalise(lines: list[LayoutLine]) -> tuple[str, str | None]:
    text = "\n".join(line.text for line in lines)
    text = re.sub(r"(?<=\w)[-\u00ad]\n(?=\w)", "", clean(text))
    text = re.sub(r"\s+", " ", text).strip()
    match = _NUM.match(text)
    label = match.group(1) if match else None
    if match:
        text = text[match.end() :]
    text = re.sub(r"\b10\s*\.\s*(\d{4,9})\s*/\s*", r"10.\1/", text)
    text = re.sub(r"(\b10\.\d{4,9}/\S*[./])\s+(?=[A-Za-z0-9])", r"\1", text)
    return text, label


def _entry(lines: list[LayoutLine], section_id: str | None) -> ExtractedEntry:
    text, label = _normalise(lines)
    warnings = []
    if len(text) > 1400 or len(_YEAR_TITLE.findall(text)) >= 2 or len(_DOI.findall(text)) >= 2:
        warnings.append(ExtractionWarning.POSSIBLE_MERGED_REFERENCE)
    if len(text) < 35 or not _YEAR.search(text):
        warnings.append(ExtractionWarning.POSSIBLE_TRUNCATED_REFERENCE)
    spans = []
    last_key = None
    last_columns: dict[int, int] = {}
    suspect = False
    for line in lines:
        if line.page_index is None:
            continue
        if line.column is not None:
            previous = last_columns.get(line.page_index, line.column)
            suspect |= previous > line.column
            last_columns[line.page_index] = line.column
        key = (line.page_index, line.column)
        if key == last_key and line.bbox is not None and spans[-1].bbox is not None:
            old = spans[-1].bbox
            spans[-1].bbox = (
                min(old[0], line.bbox[0]),
                min(old[1], line.bbox[1]),
                max(old[2], line.bbox[2]),
                max(old[3], line.bbox[3]),
            )
        else:
            spans.append(Span(page_index=line.page_index, page_label=line.page_label, bbox=line.bbox))
        last_key = key
    if suspect:
        warnings.append(ExtractionWarning.COLUMN_ORDER_SUSPECT)
    return ExtractedEntry(text=text, section_id=section_id, label=label, spans=spans, warnings=warnings)


def entries_from_lines(lines: Iterable[LayoutLine], section_id: str | None) -> list[ExtractedEntry]:
    entries = []
    current: list[LayoutLine] = []
    for line in lines:
        if line.ignored or is_heading(line.text):
            continue
        if not line.text.strip():
            if current:
                entries.append(_entry(current, section_id))
                current = []
            continue
        # Preserve paragraph boundaries as entry starts; split joined numbered entries too.
        paragraph = (
            current
            and line.paragraph_start
            and current[-1].page_index == line.page_index
            and current[-1].column == line.column
            and current[-1].text.endswith((".", ")"))
            and line.text[:1].isupper()
        )
        if current and (starts_entry(line.text) or paragraph):
            entries.append(_entry(current, section_id))
            current = []
        current.append(line)
    if current:
        entries.append(_entry(current, section_id))
    return entries


def split_entries(text: str, detail: bool = False) -> list:
    entries = entries_from_lines([LayoutLine(clean(line).strip()) for line in text.splitlines()], None)
    if detail:
        return [(entry.text, entry.label, entry.warnings) for entry in entries]
    return [entry.text for entry in entries]
