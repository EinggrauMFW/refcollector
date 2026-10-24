"""Blank separators must not cause OCR errors or hide non-text content."""

from __future__ import annotations

import pymupdf
import pytest

from refcollector.domain.enums import SourceType
from refcollector.domain.models import SectionInfo
from refcollector.errors import OcrRequired
from refcollector.extraction import BuiltinExtractionAdapter


def reference_pdf(position: str, content: str = "blank") -> tuple[bytes, list[int]]:
    with pymupdf.open() as doc:
        for _ in range(3):
            doc.new_page()
        second = 2 if position == "between" else 1
        empty = 1 if position == "between" else 2
        doc[0].insert_text((60, 80), "References\n[1] Smith, A. (2020). First study.")
        doc[second].insert_text((60, 80), "[2] Lee, B. (2021). Second study.")
        if content == "image":
            with pymupdf.open() as scan:
                scan.new_page().insert_text((60, 80), "[3] Chen, C. (2022). Scanned study.")
                image = scan[0].get_pixmap().tobytes("png")
            doc[empty].insert_image(doc[empty].rect, stream=image)
        elif content == "vector":
            doc[empty].draw_rect(pymupdf.Rect(60, 60, 200, 100), fill=(0, 0, 0))
        elif content == "annotation":
            doc[empty].add_rect_annot(pymupdf.Rect(60, 60, 400, 110))
        return doc.tobytes(), [0, second]


@pytest.mark.parametrize("position", ["between", "after"])
@pytest.mark.parametrize("manual", [False, True])
def test_blank_separator_preserves_references_and_physical_page_spans(position, manual):
    data, expected_pages = reference_pdf(position)
    sections = [SectionInfo(section_id="blank-page-test", page_start=0, page_end=2)] if manual else None
    result = BuiltinExtractionAdapter().extract(data, SourceType.PDF, sections)
    assert [entry.text for entry in result.entries] == [
        "Smith, A. (2020). First study.",
        "Lee, B. (2021). Second study.",
    ]
    assert [entry.spans[0].page_index for entry in result.entries] == expected_pages


@pytest.mark.parametrize("content", ["image", "vector", "annotation"])
def test_non_text_reference_page_still_requires_review_or_ocr(content):
    data, _ = reference_pdf("between", content)
    with pytest.raises(OcrRequired):
        BuiltinExtractionAdapter().extract(data, SourceType.PDF)
