"""Built-in synchronous, offline PyMuPDF extraction adapter."""

from __future__ import annotations

from collections.abc import Sequence

import pymupdf

from refcollector.config import Limits
from refcollector.contracts.ports import ExtractionResult
from refcollector.domain.enums import SourceType
from refcollector.domain.models import Preflight, SectionInfo
from refcollector.errors import (
    EncryptedDocument,
    InvalidInput,
    LimitExceeded,
    OcrRequired,
    ReferenceSectionNotFound,
    UnsupportedSource,
)
from refcollector.extraction.layout import LayoutLine, LayoutPage, clean, pages_from_pdf
from refcollector.extraction.preflight import detect_sections, inferred_sections, section_lines
from refcollector.extraction.split import entries_from_lines
from refcollector.ids import new_id


class BuiltinExtractionAdapter:
    name = "builtin"
    version = "1.0"

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits if limits is not None else Limits()

    def _limit(self, name: str, actual: int) -> None:
        limit = getattr(self.limits, name)
        if actual > limit:
            raise LimitExceeded(
                f"Input exceeds {name}.", details={"limit": limit, "name": name, "actual": actual}
            )

    def _read(self, data: bytes | str, kind: SourceType) -> str | list[LayoutPage]:
        if kind == SourceType.TEXT and isinstance(data, str):
            self._limit("pasted_text_bytes", len(data.encode("utf-8")))
            return data
        if kind != SourceType.PDF or not isinstance(data, bytes) or not data.startswith(b"%PDF"):
            raise UnsupportedSource("Use PDF bytes or a pasted text string.")
        self._limit("pdf_bytes", len(data))
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if doc.is_encrypted or (doc.metadata or {}).get("encryption"):
                    raise EncryptedDocument()
                self._limit("pdf_pages", len(doc))
                pages = pages_from_pdf(doc)
        except (pymupdf.FileDataError, pymupdf.EmptyFileError, ValueError, RuntimeError) as exc:
            raise UnsupportedSource("Could not read PDF data.") from exc
        if not any(page.has_text for page in pages):
            raise OcrRequired()
        return pages

    @staticmethod
    def _manual(section: SectionInfo, lengths: list[int]) -> SectionInfo:
        if not 0 <= section.page_start <= section.page_end < len(lengths):
            raise InvalidInput("Section pages must be within the source.")
        start = section.offset_start if section.offset_start is not None else 0
        end = section.offset_end if section.offset_end is not None else lengths[section.page_end]
        if (
            not 0 <= start <= lengths[section.page_start]
            or not 0 <= end <= lengths[section.page_end]
            or (section.page_start == section.page_end and start > end)
        ):
            raise InvalidInput("Section offsets must be within the selected pages.")
        return section.model_copy(
            update={"section_id": section.section_id or new_id("section"), "manual": True, "inferred": False}
        )

    def _result(
        self, source: str | list[LayoutPage], sections: Sequence[SectionInfo] | None
    ) -> ExtractionResult:
        if isinstance(source, str):
            if sections is not None and len(sections) > 1:
                raise InvalidInput("Pasted text has one section.")
            if sections is not None and not sections:
                return ExtractionResult([], [])
            section = (
                self._manual(sections[0], [len(source)])
                if sections is not None
                else SectionInfo(
                    section_id=new_id("section"),
                    page_start=0,
                    page_end=0,
                    offset_start=0,
                    offset_end=len(source),
                )
            )
            selected = source[section.offset_start or 0 : section.offset_end]
            entries = entries_from_lines(
                [LayoutLine(clean(line).strip()) for line in selected.splitlines()], section.section_id
            )
            chosen = [section]
        else:
            if sections is not None:
                chosen = [self._manual(section, [len(page.text) for page in source]) for section in sections]
            else:
                chosen = detect_sections(source) or inferred_sections(source)
                if not chosen:
                    raise ReferenceSectionNotFound()
            entries = []
            for section in chosen:
                if any(not source[pi].has_text for pi in range(section.page_start, section.page_end + 1)):
                    raise OcrRequired()
                entries.extend(entries_from_lines(section_lines(source, section), section.section_id))
        self._limit("occurrences_per_source", len(entries))
        warnings = sorted({warning.value for entry in entries for warning in entry.warnings})
        if any(section.inferred for section in chosen):
            warnings.append("REFERENCE_SECTION_INFERRED")
        return ExtractionResult(sections=chosen, entries=entries, warnings=warnings)

    def preflight(self, data: bytes | str, kind: SourceType) -> Preflight:
        source = self._read(data, kind)
        result = self._result(source, None)
        pages = len(source) if isinstance(source, list) else 0
        coverage = sum(page.has_text for page in source) / pages if pages else 0.0
        return Preflight(
            pages=pages,
            text_coverage=coverage,
            sections=result.sections,
            estimated_entries=len(result.entries),
            warnings=result.warnings,
        )

    def extract(
        self, data: bytes | str, kind: SourceType, sections: Sequence[SectionInfo] | None = None
    ) -> ExtractionResult:
        return self._result(self._read(data, kind), sections)
