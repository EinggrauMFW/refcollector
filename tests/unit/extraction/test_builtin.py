from __future__ import annotations

from refcollector.domain.enums import ExtractionWarning, SourceType
from refcollector.errors import EncryptedDocument, OcrRequired, UnsupportedSource
from refcollector.extraction import BuiltinExtractionAdapter
from tests.fixtures.pdfs import encrypted_pdf, image_only_pdf, no_heading_pdf, per_chapter_pdf, thesis_pdf


def test_thesis_toc_and_spans():
    adapter = BuiltinExtractionAdapter()
    data = thesis_pdf(toc=True)
    preflight = adapter.preflight(data, SourceType.PDF)
    result = adapter.extract(data, SourceType.PDF)
    assert preflight.pages == 6 and preflight.text_coverage == 1.0
    assert len(result.sections) == 1
    assert len(result.entries) == 8
    # Builder labels the three body pages 1-3, then the bibliography page 4.
    assert all(entry.spans and entry.spans[0].page_label == "4" for entry in result.entries)


def test_chapter_sections_and_columns():
    result = BuiltinExtractionAdapter().extract(per_chapter_pdf(), SourceType.PDF)
    assert len(result.sections) == 3
    assert len(result.entries) == 8
    two_col = BuiltinExtractionAdapter().extract(thesis_pdf(two_column=True), SourceType.PDF)
    assert len(two_col.entries) == 8


def test_inference_and_errors():
    adapter = BuiltinExtractionAdapter()
    assert adapter.extract(no_heading_pdf(), SourceType.PDF).sections[0].inferred
    try:
        adapter.extract(b"not pdf", SourceType.PDF)
    except UnsupportedSource:
        pass
    else:
        raise AssertionError("invalid PDF accepted")
    try:
        adapter.extract(encrypted_pdf(), SourceType.PDF)
    except EncryptedDocument:
        pass
    else:
        raise AssertionError("encrypted PDF accepted")
    try:
        adapter.extract(image_only_pdf(), SourceType.PDF)
    except OcrRequired:
        pass
    else:
        raise AssertionError("image-only PDF accepted")


def test_text_and_warnings():
    result = BuiltinExtractionAdapter().extract(
        "Smith, A. (2020). Good title. Journal.\n\nFragment", SourceType.TEXT
    )
    assert len(result.entries) == 2
    assert result.entries[1].warnings == [ExtractionWarning.POSSIBLE_TRUNCATED_REFERENCE]
