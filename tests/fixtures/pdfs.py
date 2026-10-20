"""PDF builders for tests (decision D-07: *.pdf is git-ignored, so PDFs are made at test time).

All builders return bytes. Pages get labels: front matter in roman numerals, body in arabic
numbers starting at 1 (W-07 needs a page label).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

A4 = pymupdf.paper_rect("a4")
MARGIN = 56
FONT = 9.5

SAMPLE_REFS = [
    "Babarit, A. (2012). Numerical benchmarking study of a selection of wave energy converters. "
    "Renewable Energy, 41, 44-63. doi:10.1016/j.renene.2011.10.002",
    "Falcao, A. F. de O. (2010). Wave energy utilization: A review of the technologies. "
    "Renewable and Sustainable Energy Reviews, 14(3), 899-918.",
    "Rothery, P., & Roy, D. B. (2001). Application of generalized additive models to butterfly "
    "transect count data. Journal of Applied Statistics, 28(7), 897-909.",
    "World Health Organization. (2019). Guidelines on physical activity and sedentary behaviour. "
    "Geneva: WHO Press.",
    "Smith, J., & Lee, K. (2021). Neural wavefields. Geophysics, 86(4), A12-A20.",
    "Badan Standardisasi Nasional. (2017). SNI 2847:2019 Persyaratan beton struktural untuk "
    "bangunan gedung. Jakarta: BSN.",
    "Vaswani, A., Shazeer, N., Parmar, N., et al. (2017). Attention is all you need. In Advances "
    "in Neural Information Processing Systems 30, 5998-6008.",
    "Ringwood, J. V., Bacelli, G., & Fusco, F. (2014). Energy-maximizing control of wave-energy "
    "converters: The development of control system technology to optimize their operation. "
    "IEEE Control Systems Magazine, 34(5), 30-55.",
]

BODY_TEXT = (
    "This chapter describes the method. Earlier work (Babarit, 2012; Falcao, 2010) compared "
    "devices. The results are discussed in the next section. "
) * 6


def _textbox(page: pymupdf.Page, rect: pymupdf.Rect, text: str, size: float = FONT) -> None:
    if page.insert_textbox(rect, text, fontsize=size, fontname="helv") < 0:
        raise ValueError("text does not fit; use fewer items per page")


def _ref_pages(
    doc: pymupdf.Document,
    refs: list[str],
    heading: str | None,
    *,
    two_column: bool,
    running_header: str | None,
    per_page: int = 8,
) -> None:
    for start in range(0, len(refs), per_page):
        page = doc.new_page(width=A4.width, height=A4.height)
        top = MARGIN
        if running_header:
            _textbox(page, pymupdf.Rect(MARGIN, 20, A4.width - MARGIN, 40), running_header, 8)
        if heading and start == 0:
            _textbox(page, pymupdf.Rect(MARGIN, top, A4.width - MARGIN, top + 24), heading, 14)
            top += 34
        chunk = refs[start : start + per_page]
        if two_column:
            half = (len(chunk) + 1) // 2
            mid = A4.width / 2
            _textbox(page, pymupdf.Rect(MARGIN, top, mid - 10, A4.height - MARGIN), "\n\n".join(chunk[:half]))
            _textbox(
                page,
                pymupdf.Rect(mid + 10, top, A4.width - MARGIN, A4.height - MARGIN),
                "\n\n".join(chunk[half:]),
            )
        else:
            _textbox(
                page, pymupdf.Rect(MARGIN, top, A4.width - MARGIN, A4.height - MARGIN), "\n\n".join(chunk)
            )


def _body_page(doc: pymupdf.Document, title: str | None = None) -> None:
    page = doc.new_page(width=A4.width, height=A4.height)
    text = (title + "\n\n" if title else "") + BODY_TEXT
    _textbox(page, pymupdf.Rect(MARGIN, MARGIN, A4.width - MARGIN, A4.height - MARGIN), text)


def _labels(doc: pymupdf.Document, front_matter: int) -> None:
    rules = []
    if front_matter:
        rules.append({"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1})
    rules.append({"startpage": front_matter, "prefix": "", "style": "D", "firstpagenum": 1})
    doc.set_page_labels(rules)


def thesis_pdf(
    refs: list[str] | None = None,
    *,
    heading: str | None = "References",
    toc: bool = True,
    body_pages: int = 3,
    two_column: bool = False,
    running_header: str | None = None,
) -> bytes:
    """Title page, optional contents page naming the heading (AT-01), body, reference list."""
    refs = SAMPLE_REFS if refs is None else refs
    doc = pymupdf.open()
    _body_page(doc, "A Thesis About Wave Energy\n\nSubmitted for the degree of Doctor of Philosophy")
    front = 1
    if toc:
        page = doc.new_page(width=A4.width, height=A4.height)
        lines = [
            "Contents",
            "1 Introduction ........ 1",
            "2 Method ........ 2",
            f"{heading or 'Appendix'} ........ 4",
        ]
        _textbox(page, pymupdf.Rect(MARGIN, MARGIN, A4.width - MARGIN, 300), "\n".join(lines))
        front = 2
    for i in range(body_pages):
        _body_page(doc, f"{i + 1} Chapter {i + 1}")
    _ref_pages(doc, refs, heading, two_column=two_column, running_header=running_header)
    _labels(doc, front)
    return doc.tobytes()


def per_chapter_pdf(chapters: list[list[str]] | None = None, heading: str = "Daftar Pustaka") -> bytes:
    """Indonesian thesis with a reference list after every chapter (AT-02)."""
    chapters = chapters or [SAMPLE_REFS[:3], SAMPLE_REFS[3:6], SAMPLE_REFS[6:]]
    doc = pymupdf.open()
    _body_page(doc, "Skripsi\n\nProgram Studi Teknik Sipil")
    for n, refs in enumerate(chapters, 1):
        _body_page(doc, f"BAB {n}\n\nPendahuluan")
        _ref_pages(doc, refs, heading, two_column=False, running_header=None)
    _labels(doc, 1)
    return doc.tobytes()


def no_heading_pdf(refs: list[str] | None = None) -> bytes:
    """Reference list without any heading (W-08: inferred section or REFERENCE_SECTION_NOT_FOUND)."""
    return thesis_pdf(refs, heading=None, toc=False)


def encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    _body_page(doc, "Secret")
    return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")


def image_only_pdf(pages: int = 2) -> bytes:
    """Scanned-like PDF: images, no text layer (OCR_REQUIRED)."""
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), 0)
    pix.clear_with(230)
    for _ in range(pages):
        page = doc.new_page(width=A4.width, height=A4.height)
        page.insert_image(pymupdf.Rect(MARGIN, MARGIN, A4.width - MARGIN, A4.height - MARGIN), pixmap=pix)
    return doc.tobytes()


def paper_pdf(
    title: str, authors: list[str], doi: str | None = None, *, supplement: bool = False, pages: int = 3
) -> bytes:
    """A downloaded article: title, authors and DOI on page 1 (10.4 identity evidence).
    `supplement=True` makes a supplementary-material file for the same work (AT-09b)."""
    doc = pymupdf.open()
    head = ("Supplementary Material for\n" if supplement else "") + title
    lines = [head, ", ".join(authors)]
    if doi:
        lines.append(f"https://doi.org/{doi}")
    page = doc.new_page(width=A4.width, height=A4.height)
    _textbox(page, pymupdf.Rect(MARGIN, MARGIN, A4.width - MARGIN, 260), "\n\n".join(lines), 12)
    _textbox(page, pymupdf.Rect(MARGIN, 280, A4.width - MARGIN, A4.height - MARGIN), "Abstract. " + BODY_TEXT)
    for _ in range(pages - 1):
        _body_page(doc)
    return doc.tobytes()


def write(folder: Path, name: str, data: bytes) -> Path:
    path = folder / name
    path.write_bytes(data)
    return path
