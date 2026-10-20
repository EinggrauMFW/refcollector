"""WP-08: FileVerifier implementation. Spec 10.4 steps 2-6.

Read-only PDF verification. Text is evidence, never executable instructions.

PyMuPDF context managers close native handles; is_repaired identifies recovered input:
https://pymupdf.readthedocs.io/en/latest/document.html
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pymupdf

from refcollector.config import Limits
from refcollector.contracts.ports import VerificationResult
from refcollector.domain.enums import CopyVersion, RetrievalState
from refcollector.domain.models import WorkMetadata


def _words(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


def _version(text: str) -> tuple[CopyVersion, bool]:
    lower = text.casefold()
    found = set()
    for version, pattern in (
        (CopyVersion.PUBLISHED, r"version of record|published version"),
        (CopyVersion.ACCEPTED, r"accepted manuscript|author accepted manuscript"),
        (CopyVersion.SUBMITTED, r"preprint|submitted manuscript"),
    ):
        if re.search(r"^\s*(?:" + pattern + r")\s*\.?\s*$", lower, re.MULTILINE):
            found.add(version)
    return (next(iter(found)) if len(found) == 1 else CopyVersion.UNKNOWN, len(found) > 1)


class DefaultFileVerifier:
    def __init__(self, limits: Limits | None = None) -> None:
        self._limits = limits or Limits()

    def verify(
        self, path: Path, work: WorkMetadata, *, content_type: str | None = None
    ) -> VerificationResult:
        try:
            if (
                content_type is not None
                and content_type.split(";", 1)[0].strip().lower() != "application/pdf"
            ):
                raise ValueError("Content-Type is not application/pdf")
            if path.stat().st_size > self._limits.pdf_bytes:
                raise ValueError("PDF exceeds byte limit")
            with path.open("rb") as file:
                if file.read(5) != b"%PDF-":
                    raise ValueError("PDF signature is absent")
            with pymupdf.open(path) as doc:
                if not doc.is_pdf or doc.needs_pass or doc.is_repaired or not doc.page_count:
                    raise ValueError("Encrypted, repaired or empty PDF")
                if doc.page_count > self._limits.pdf_pages:
                    raise ValueError("PDF exceeds page limit")
                texts = []
                title_text = ""
                for index in range(doc.page_count):
                    # Parse every page, including content streams beyond the evidence pages.
                    page = doc.load_page(index)
                    text = page.get_text("text", sort=True)
                    if index == 0:
                        blocks = page.get_text("blocks", sort=True)
                        title_text = next((block[4] for block in blocks if block[6] == 0), "")
                    if index < 2:
                        texts.append(text)
                pages = doc.page_count
        except Exception as exc:
            # Native MuPDF format/syntax exceptions do not inherit RuntimeError.
            # This parse boundary turns both wrapper and native errors into a
            # typed validation result. Cancellation (BaseException) still escapes.
            return VerificationResult(
                RetrievalState.FAILED,
                ["FILE_VALIDATION_FAILED"],
                False,
                None,
                evidence={"validation_error": str(exc)},
            )

        text = "\n".join(texts)
        dois = sorted(
            {
                value.rstrip(".,;:)]}").casefold()
                for value in re.findall(r"\b10\.\d{4,9}/[^\s<>\"]+", text, re.IGNORECASE)
            }
        )
        # Title must be in the opening text, before the abstract or references. A DOI
        # in the bibliography does not establish that this is the requested paper.
        opening = re.split(r"\b(?:abstract|references|bibliography)\b", text, maxsplit=1, flags=re.I)[0]
        primary_dois = sorted(
            {
                value.rstrip(".,;:)]}").casefold()
                for value in re.findall(r"\b10\.\d{4,9}/[^\s<>\"]+", opening, re.IGNORECASE)
            }
        )
        title = _words(work.title or "")
        title_match = bool(title and title == _words(title_text))
        expected_doi = (work.doi or "").casefold().removeprefix("https://doi.org/").removeprefix("doi:")
        reasons = []
        conflict = False
        if expected_doi:
            if not primary_dois:
                reasons.append("DOI_EVIDENCE_MISSING")
            elif primary_dois != [expected_doi]:
                reasons.append("DOI_MISMATCH")
                conflict = True
        if work.title:
            if not title_match:
                reasons.append("TITLE_MISMATCH" if opening.strip() else "TITLE_EVIDENCE_MISSING")
                conflict = bool(opening.strip()) or conflict
        if not expected_doi and not title:
            reasons.append("IDENTITY_EVIDENCE_MISSING")
        if re.search(
            r"^\s*(?:supplement(?:ary|al)?(?:\s+(?:material|information|data))?|supporting information)\b",
            text,
            re.I | re.MULTILINE,
        ):
            reasons.append("SUPPLEMENT")
            conflict = True
        version, version_conflict = _version(opening)
        return VerificationResult(
            RetrievalState.NEEDS_REVIEW if reasons else RetrievalState.VERIFIED,
            reasons,
            True,
            False if conflict else (None if reasons else True),
            version,
            {
                "dois": dois,
                "primary_dois": primary_dois,
                "title_match": title_match,
                "title_text": title_text,
                "opening_text": opening,
                "pages_parsed": pages,
                "evidence_pages": min(pages, 2),
                "version_conflict": version_conflict,
            },
        )
