"""WP-06: DOI, arXiv, ISBN, PMID reading and conservative normalisation. Spec 9.1 step 1."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import unquote, urlsplit

IdentifierKind = Literal["doi", "isbn", "arxiv", "pmid"]

# Spec 9.1 step 1 / principle 8: spaces inside a DOI are PDF line-break repair, allowed
# only after an explicit marker. A bare DOI must be unspaced, so "Vol 10. 1234/5678" is not one.
_MARKED_DOI = r"(?:\bdoi\b\s*:?\s*|doi\.org/\s*)10\s*\.\s*(\d{4,9})\s*/\s*([^\s\"<>]+)"
_BARE_DOI = r"\b10\.(\d{4,9})/([^\s\"<>]+)"
_DOI_SPAN = rf"{_MARKED_DOI}|{_BARE_DOI}"


def without_identifiers(text: str) -> str:
    """Remove identifier spans before extracting years or bibliographic fields."""
    text = re.sub(_DOI_SPAN, "", text, flags=re.I)
    return re.sub(r"https?://\S+|\b(?:arxiv|pmid|isbn)(?:-1[03])?\s*:\s*[^;]+(?=;|$)", "", text, flags=re.I)


def normalize_doi(value: str | None) -> str | None:
    """Repair structural spaces, not arbitrary words following a DOI.

    Balanced parentheses belong to the suffix. Only unmatched closing punctuation
    and citation terminal punctuation are removed. Percent escapes are URL data.
    """
    if not value:
        return None
    text = unquote(value).replace("\u00ad", "")
    text = re.sub(r"(\S)[ \t]*\r?\n[ \t]*(?=[./_-])", r"\1", text)
    text = re.sub(r"([./_-])[ \t]*\r?\n[ \t]*(?=[a-z0-9])", r"\1", text)
    url = re.search(r"https?://(?:dx\.)?doi\.org/[^\s\"<>]+", text, re.I)
    if url and re.match(r"10\.\d{4,9}/", path := urlsplit(url.group()).path.lstrip("/")):
        text = path
    found = re.search(_MARKED_DOI, text, re.I) or re.search(_BARE_DOI, text, re.I)
    if not found:
        return None
    suffix = found.group(2).rstrip(".,;:!?'\u2019\u201d")
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        while suffix.endswith(closing) and suffix.count(closing) > suffix.count(opening):
            suffix = suffix[:-1].rstrip(".,;")
    return f"10.{found.group(1)}/{suffix}".lower() if suffix else None


def normalize_isbn(value: str) -> str | None:
    """Remove ISBN separators and validate the ISBN-10 or ISBN-13 checksum."""
    digits = re.sub(r"[\s-]", "", value).upper()
    if re.fullmatch(r"\d{9}[\dX]", digits):
        values = [10 if c == "X" else int(c) for c in digits]
        if sum((10 - i) * v for i, v in enumerate(values)) % 11 == 0:
            return digits
    if re.fullmatch(r"97[89]\d{10}", digits):
        if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits)) % 10 == 0:
            return digits
    return None


def read_identifiers(text: str | None) -> dict[IdentifierKind, str]:
    """Read explicit identifiers. Bare years or numeric strings are not PMIDs/ISBNs."""
    text = text or ""
    result: dict[IdentifierKind, str] = {}
    if doi := normalize_doi(text):
        result["doi"] = doi
    arxiv = re.search(
        r"(?:\barxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)"
        r"((?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?)\b",
        text,
        re.I,
    )
    if arxiv:
        result["arxiv"] = arxiv.group(1).lower()
    pmid = re.search(r"(?:\bpmid\s*:\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{1,9})\b", text, re.I)
    if pmid:
        result["pmid"] = pmid.group(1)
    isbn = re.search(
        r"\bisbn(?:-1[03])?\s*:?\s*(97[89](?:[\s-]*\d){10}|\d(?:[\s-]*\d){8}[\s-]*[\dX])\b",
        text,
        re.I,
    )
    if isbn and (value := normalize_isbn(isbn.group(1))):
        result["isbn"] = value
    return result
