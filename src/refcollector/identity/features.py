"""WP-06: comparison features incl. rapidfuzz token-set ratio. Spec 9.2."""

from __future__ import annotations

import re

from rapidfuzz.fuzz import token_set_ratio

from refcollector.contracts.ports import ScoredCandidate
from refcollector.domain.enums import ReasonCode as R
from refcollector.domain.models import WorkMetadata

from .identifiers import read_identifiers, without_identifiers
from .normalize import _fold, _norm, _tokens, match_score, normalize_metadata
from .policy_v1 import assess


def guess_title(text: str) -> str | None:
    """Use a quoted title or the sentence after a parenthesized year, as in v0.1."""
    quoted = re.search(r'[“"](.{8,}?)[”"]', text)
    if quoted:
        return quoted.group(1).strip(" .,")
    dated = re.search(r"\((?:19|20)\d{2}[a-z]?\)\.?\s*(.{8,}?)(?:\.\s|$)", text)
    return dated.group(1).strip() if dated else None


def citation_type(text: str) -> str | None:
    """Only explicit type words/patterns provide type evidence."""
    for pattern, kind in (
        (r"\b(?:supplement|supplementary)\b", "supplement"),
        (r"\bchapter\b", "book-chapter"),
        (r"\b(?:ISO|IEC|ASTM|IEEE)\s*[-:]?\s*\d{2,}", "standard"),
        (r"\b(?:technical\s+report|report)\b", "report"),
        (r"\b(?:thesis|dissertation)\b", "dissertation"),
        (r"\bpreprint\b|\barxiv\s*:", "posted-content"),
    ):
        if re.search(pattern, text, re.I):
            return kind
    return None


def edition_number(value: str | None) -> str | None:
    if not value:
        return None
    for word, number in (("first", "1"), ("second", "2"), ("third", "3"), ("fourth", "4")):
        if re.search(rf"\b{word}\b", value, re.I):
            return number
    found = re.search(r"\d+", value)
    return found.group() if found else _norm(value).strip()


def _cited_edition(text: str) -> str | None:
    found = re.search(r"\b(\d+(?:st|nd|rd|th)?|first|second|third|fourth)\s*(?:ed\.?|edition)\b", text, re.I)
    return edition_number(found.group(1)) if found else None


def _author_text(text: str, title: str | None) -> str:
    """Do not count a name that occurs only inside the title as an author."""
    if title and (position := text.lower().find(title.lower())) >= 0:
        prefix = text[:position]
    else:
        year = re.search(r"\b(?:19|20)\d{2}\b", text)
        prefix = text[: year.start()] if year else ""
    prefix = re.sub(r"https?://\S+|\bdoi\s*:\s*\S+", "", prefix, flags=re.I)
    return prefix.strip("0123456789 .,;:()[]")


def _present(value: str | None, text: str) -> bool | None:
    if not value:
        return None
    normalized = re.sub(r"\s+", " ", _norm(text)).replace("–", "-").replace("—", "-")
    needle = re.sub(r"\s+", " ", _norm(value)).replace("–", "-").replace("—", "-")
    return bool(re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", normalized)) or None


def _bibliographic_fields(text: str) -> dict[str, str]:
    """Read explicit volume/issue/pages patterns, never bare publication year digits."""
    fields = {}
    bib = re.search(r"\b(\d+)\s*\(\s*(\d+)\s*\)\s*:?\s*(\d+(?:\s*[-–—]\s*\d+)?)?", text)
    if bib:
        fields["volume"], fields["issue"] = bib.group(1), bib.group(2)
        if bib.group(3):
            fields["pages"] = bib.group(3)
    elif found := re.search(r"\b(\d+)\s*:\s*(\d+\s*[-–—]\s*\d+)", text):
        fields["volume"], fields["pages"] = found.group(1), found.group(2)
    for field, pattern in (
        ("volume", r"\bvol(?:ume)?\.?\s*(\d+)"),
        ("issue", r"\b(?:issue|no)\.?\s*(\d+)"),
        ("pages", r"\bpp?\.?\s*(\d+(?:\s*[-–—]\s*\d+)?)"),
    ):
        if found := re.search(pattern, text, re.I):
            fields[field] = found.group(1)
    return fields


def compare(citation: str, metadata: WorkMetadata, *, title: str | None = None) -> ScoredCandidate:
    """Score without I/O. Legacy assess stays unchanged; safety checks are additional gates.

    Ranking uses legacy title/author/year features. RapidFuzz is used only in the
    top-two gate: combined_score = 0.9 * score + 0.1 * token_set_ratio / 100.
    RapidFuzz 3.x has no default preprocessing; both strings use our legacy tokens.
    https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html#rapidfuzz.fuzz.token_set_ratio
    """
    md = normalize_metadata(metadata)
    title = title or guess_title(citation)
    identifiers = read_identifiers(citation)
    # Identifiers can contain year-shaped digits. They are not publication dates.
    text = without_identifiers(citation)
    item = {
        "title": [md.title or ""],
        "type": md.type,
        "author": [{"family": p.family or p.literal or "", "given": p.given or ""} for p in md.authors],
        "issued": {"date-parts": [[md.year]]},
    }
    legacy = assess(item, text)
    title_score = round(match_score(md.title or "", title or text), 2)
    authors = _author_text(text, title)
    names = [_fold(p.family or p.literal or "") for p in md.authors]
    names = [name for name in names if name]
    found = sum(bool(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", _fold(authors))) for name in names)
    overlap = min(round(found / min(len(names), 4), 2), 1.0) if names and authors else None
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", text)]
    delta = min(abs(md.year - y) for y in years) if years and md.year is not None else None
    year_agreement = delta <= 1 if delta is not None else None
    doi_agreement = md.doi == identifiers["doi"] if md.doi and "doi" in identifiers else None
    cited_type = citation_type(text)
    record_type = {"chapter": "book-chapter", "preprint": "posted-content", "technical-report": "report"}.get(
        md.type or "", md.type
    )
    edition = _cited_edition(text)
    record_edition = edition_number(md.edition)
    ntokens = len(_tokens(md.title or ""))
    generic_words = {"review", "introduction", "overview", "study", "report", "annual", "data", "handling"}
    distinctive = ntokens >= 2 and bool(_tokens(md.title or "") - generic_words)
    bibliographic = _bibliographic_fields(text)
    features: dict[str, float | int | bool | None] = {
        "title_score": title_score,
        "title_tokens": ntokens,
        "title_distinctive": distinctive,
        "token_set_ratio": token_set_ratio(
            " ".join(sorted(_tokens(md.title or ""))), " ".join(sorted(_tokens(title or text)))
        ),
        "legacy_accepted": legacy["accepted"],
        "author_overlap": overlap,
        "cited_author_present": bool(authors),
        "record_author_count": len(names),
        "year_delta": delta,
        "cited_year": years[0] if years else None,
        "record_year": md.year,
        "year_agreement": year_agreement,
        "doi_agreement": doi_agreement,
        **{
            f"{kind}_agreement": (
                getattr(md, kind) == identifiers[kind] if getattr(md, kind) and kind in identifiers else None
            )
            for kind in ("isbn", "arxiv", "pmid")
        },
        "venue_agreement": _present(md.venue, text),
        **{
            f"{field}_agreement": (
                re.sub(r"\s+", "", getattr(md, field)).replace("–", "-").replace("—", "-")
                == re.sub(r"\s+", "", value).replace("–", "-").replace("—", "-")
                if (value := bibliographic.get(field)) and getattr(md, field)
                else None
            )
            for field in ("volume", "issue", "pages")
        },
        "type_agreement": cited_type == record_type if cited_type and record_type else None,
        "edition_agreement": edition == record_edition if edition and record_edition else None,
        "cited_edition": int(edition) if edition and edition.isdigit() else None,
        "record_edition": int(record_edition) if record_edition and record_edition.isdigit() else None,
        "provider_count": 0,
    }
    reasons = []
    if any(features[f"{kind}_agreement"] is False for kind in ("doi", "isbn", "pmid")) or any(
        features[f"{field}_agreement"] is False for field in ("volume", "issue", "pages")
    ):
        reasons.append(R.PROVIDER_DISAGREEMENT)
    if overlap == 0 or (overlap is not None and len(names) >= 3 and overlap < 0.5):
        reasons.append(R.AUTHOR_CONFLICT)
    if delta is not None and delta > 1:
        reasons.append(R.YEAR_CONFLICT)
    if not distinctive:
        reasons.append(R.GENERIC_TITLE)
    if edition != record_edition and (edition or record_edition):
        reasons.append(R.EDITION_CONFLICT)
    risky_type = record_type in {"book-chapter", "supplement", "component", "posted-content-figure"}
    supplement = bool(re.search(r"\bsupplement(?:ary)?\b", md.title or "", re.I))
    cited_supplement = bool(re.search(r"\bsupplement(?:ary)?\b", text, re.I))
    if (
        (risky_type and cited_type != record_type)
        or supplement != cited_supplement
        or (cited_type and record_type and cited_type != record_type)
    ):
        reasons.append(R.VERSION_DIFFERENCE)
    arxiv = identifiers.get("arxiv")
    if arxiv and md.arxiv and arxiv.lower() != md.arxiv.lower():
        reasons.append(R.VERSION_DIFFERENCE)
    support = bool(
        (overlap is not None and overlap > 0)
        or year_agreement
        or features["venue_agreement"]
        or features["pages_agreement"]
    )
    features["supporting_fields"] = support
    if not md.title or not support or not legacy["accepted"]:
        reasons.append(R.INSUFFICIENT_FIELDS)
    score = 0.75 * title_score + 0.15 * (overlap or 0) + 0.10 * bool(year_agreement)
    features["combined_score"] = 0.9 * score + 0.1 * features["token_set_ratio"] / 100
    return ScoredCandidate(md, features, list(dict.fromkeys(reasons)), [], False, score)
