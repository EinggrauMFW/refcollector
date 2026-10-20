"""Locate bibliography sections in page-local character coordinates."""

from __future__ import annotations

import re
from dataclasses import replace
from statistics import median

from refcollector.domain.models import SectionInfo
from refcollector.extraction.headings import is_heading
from refcollector.extraction.layout import LayoutLine, LayoutPage
from refcollector.extraction.split import score_text, starts_entry
from refcollector.ids import new_id

_PREFIX = r"(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.)]?\s+)?"
_BODY = re.compile(
    _PREFIX
    + r"(?:chapter|bab)\b.{0,100}$|"
    + _PREFIX
    + r"(?:introduction|methodology|methods|results|discussion|conclusions?|"
    r"appendix|pendahuluan|metode|hasil|pembahasan|kesimpulan)\b(?:\s+[^.!?]{1,80})?\s*[:.]?$",
    re.I,
)


def _contents_page(page: LayoutPage) -> bool:
    lines = [line.text.strip() for line in page.lines if not line.ignored]
    marked = sum(bool(re.search(r"\.{2,}\s*\w+$|\s\d+\s*$", line)) for line in lines)
    return marked >= 2 and marked > len(lines) / 2


def detect_sections(pages: list[LayoutPage]) -> list[SectionInfo]:
    events = []
    for page in pages:
        if _contents_page(page):
            continue
        sizes = [line.font_size for line in page.lines if not line.ignored and line.font_size]
        typical_size = median(sizes) if sizes else 0.0
        for li, line in enumerate(page.lines):
            if line.ignored:
                continue
            heading = is_heading(line.text)
            # Contents can put the heading and its destination on separate lines.
            if (
                heading
                and li + 1 < len(page.lines)
                and re.fullmatch(r"\d+|[ivxlcdm]+", page.lines[li + 1].text, re.I)
            ):
                continue
            large_title = (
                line.paragraph_start
                and line.font_size > typical_size * 1.15
                and len(line.text) < 100
                and not starts_entry(re.sub(r"^" + _PREFIX, "", line.text, count=1))
            )
            if heading or _BODY.fullmatch(line.text) or large_title:
                events.append((page.index, line, heading))
    sections = []
    for ei, (pi, line, heading) in enumerate(events):
        if not heading:
            continue
        end_page, end_offset = len(pages) - 1, len(pages[-1].text)
        if ei + 1 < len(events):
            next_pi, next_line, _ = events[ei + 1]
            prefix = [line for line in pages[next_pi].lines if line.end <= next_line.start]
            if next_pi > pi and not any(not line.ignored for line in prefix):
                end_page, end_offset = next_pi - 1, len(pages[next_pi - 1].text)
            else:
                end_page, end_offset = next_pi, next_line.start
        sections.append(
            SectionInfo(
                section_id=new_id("section"),
                page_start=pi,
                page_end=end_page,
                offset_start=min(line.end + 1, len(pages[pi].text)),
                offset_end=end_offset,
                heading=line.text,
            )
        )
    return sections


def inferred_sections(pages: list[LayoutPage]) -> list[SectionInfo]:
    scores = []
    for page in pages:
        blocks: list[list[str]] = []
        for line in page.lines:
            if line.ignored:
                continue
            if line.paragraph_start or not blocks:
                blocks.append([])
            blocks[-1].append(line.text)
        scores.append(sum(score_text("\n".join(block)) for block in blocks) / max(1, len(blocks)))
    runs: list[list[int]] = []
    for pi, score in enumerate(scores):
        if score >= 0.025:
            if runs and runs[-1][-1] == pi - 1:
                runs[-1].append(pi)
            else:
                runs.append([pi])
    if not runs:
        return []
    best = max(runs, key=lambda run: sum(scores[pi] for pi in run))
    return [
        SectionInfo(
            section_id=new_id("section"),
            page_start=best[0],
            page_end=best[-1],
            offset_start=0,
            offset_end=len(pages[best[-1]].text),
            inferred=True,
        )
    ]


def section_lines(pages: list[LayoutPage], section: SectionInfo) -> list[LayoutLine]:
    lines = []
    for pi in range(section.page_start, section.page_end + 1):
        start = (section.offset_start or 0) if pi == section.page_start else 0
        end = (
            section.offset_end
            if pi == section.page_end and section.offset_end is not None
            else len(pages[pi].text)
        )
        for line in pages[pi].lines:
            if line.end > start and line.start < end:
                text = line.text[max(0, start - line.start) : end - line.start].strip()
                if text:
                    lines.append(replace(line, text=text))
    return lines
