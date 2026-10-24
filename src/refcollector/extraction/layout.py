"""Read PDF text blocks without losing line positions or column order."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace

import pymupdf

from refcollector.extraction.headings import is_heading


def clean(text: str) -> str:
    accents = "\u00b4\u00a8\u02dc\u02c6\u00b8\u02c7\u02d9"
    text = text.translate(str.maketrans("", "", accents))
    return unicodedata.normalize("NFKC", text)


@dataclass(frozen=True)
class LayoutLine:
    text: str
    page_index: int | None = None
    page_label: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    column: int | None = None
    paragraph_start: bool = False
    start: int = 0
    end: int = 0
    ignored: bool = False
    font_size: float = 0.0


@dataclass
class LayoutPage:
    index: int
    lines: list[LayoutLine]
    is_blank: bool = False

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


def _block_text(block: dict) -> str:
    return "\n".join("".join(span["text"] for span in line["spans"]) for line in block["lines"])


def _ordered_blocks(blocks: list[dict], width: float) -> list[tuple[dict, int | None]]:
    mid = width / 2
    candidates = [b for b in blocks if len(_block_text(b).strip()) > 15 and not is_heading(_block_text(b))]
    left = [b for b in candidates if b["bbox"][2] <= mid + 2]
    right = [b for b in candidates if b["bbox"][0] >= mid - 2]
    # Treat separated left/right text zones as columns; order each column from top to bottom.
    columns = bool(
        left and right and min(b["bbox"][0] for b in right) - min(b["bbox"][0] for b in left) > width * 0.2
    )
    if not columns:
        return [(b, None) for b in sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))]
    full = sorted(
        [b for b in blocks if b["bbox"][0] < mid - 2 and b["bbox"][2] > mid + 2], key=lambda b: b["bbox"][1]
    )
    remaining = [b for b in blocks if b not in full]
    ordered = []
    for separator in [*full, None]:
        band = [b for b in remaining if separator is None or b["bbox"][1] < separator["bbox"][1]]
        remaining = [b for b in remaining if b not in band]
        ordered.extend(
            (b, int(b["bbox"][0] >= mid - 2))
            for b in sorted(band, key=lambda b: (b["bbox"][0] >= mid - 2, b["bbox"][1], b["bbox"][0]))
        )
        if separator is not None:
            ordered.append((separator, None))
    return ordered


def _ignore_headers(pages: list[LayoutPage]) -> None:
    tops: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)
    for page in pages:
        for li, line in enumerate(page.lines):
            if line.bbox is not None and line.bbox[1] < 0.08:
                # A repeated citation at the start of a page is still reference data.
                if line.bbox[1] >= 0.05 and re.search(r"\b(?:19|20)\d{2}\b|,\s+[A-Z]\.", line.text):
                    continue
                tops[(line.text.casefold(), round(line.bbox[1] * 100))].append((page.index, li))
    ignored = set()
    for locations in tops.values():
        indexes = {pi for pi, _ in locations}
        for pi, li in locations:
            line = pages[pi].lines[li]
            consecutive = pi - 1 in indexes or pi + 1 in indexes
            first_in_run = pi
            while first_in_run - 1 in indexes:
                first_in_run -= 1
            duplicate = any(
                is_heading(other.text)
                and other.text.casefold() == line.text.casefold()
                and other.bbox is not None
                and other.bbox[1] > line.bbox[1] + 0.015
                for other in pages[pi].lines
            )
            # In the body margin, keep the first title and suppress its repetitions.
            running = consecutive and (line.bbox[1] < 0.05 or pi != first_in_run)
            if running or (duplicate and line.bbox[1] < 0.05):
                ignored.add((pi, li))
    for page in pages:
        page.lines = [
            replace(
                line, ignored=(page.index, li) in ignored or bool(re.fullmatch(r"\d+", line.text.strip()))
            )
            for li, line in enumerate(page.lines)
        ]


def pages_from_pdf(doc: pymupdf.Document) -> list[LayoutPage]:
    pages = []
    for index, page in enumerate(doc):
        blocks = [b for b in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"] if b["type"] == 0]
        label = page.get_label() or str(index + 1)
        width, height = page.rect.width, page.rect.height
        lines = []
        offset = 0
        for block, column in _ordered_blocks(blocks, width):
            for li, line in enumerate(block["lines"]):
                text = clean("".join(span["text"] for span in line["spans"])).strip()
                if not text:
                    continue
                bbox = tuple(
                    max(0.0, min(1.0, value / (width if i % 2 == 0 else height)))
                    for i, value in enumerate(line["bbox"])
                )
                lines.append(
                    LayoutLine(
                        text,
                        index,
                        label,
                        bbox,
                        column,
                        li == 0,
                        offset,
                        offset + len(text),
                        font_size=max(span["size"] for span in line["spans"]),
                    )
                )
                offset += len(text) + 1
        pages.append(
            LayoutPage(
                index,
                lines,
                is_blank=not lines and not page.get_bboxlog() and not page.annot_xrefs(),
            )
        )
    _ignore_headers(pages)
    return pages
