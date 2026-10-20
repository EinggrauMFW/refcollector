"""WP-06: identity policy v1 (ports v0.1 assess). Spec 9.2."""

import re

from .normalize import _fold, _tokens, match_score

MATCH_THRESHOLD = 0.75
BAD_TYPES = {"component", "peer-review", "grant", "posted-content-figure"}


def close_candidates(best, other):
    """D-10: RapidFuzz participates only in the top-two combined-score gate."""

    def combined(candidate):
        return 0.9 * candidate.score + 0.1 * candidate.features["token_set_ratio"] / 100

    return abs(combined(best) - combined(other)) <= 0.05 + 1e-12


def assess(item, raw):
    """Compare a candidate record with a reference string, field by field.

    Returns {'accepted': bool, 'title_score', 'author_overlap', 'year_delta', 'flags': [...]}.
    'flags' are human-readable reasons to double-check an accepted match."""
    title = (item.get("title") or [""])[0]
    out = {"accepted": False, "title_score": 0.0, "author_overlap": None, "year_delta": None, "flags": []}
    if not title or item.get("type") in BAD_TYPES:
        return out
    score = out["title_score"] = round(match_score(title, raw), 2)
    if score < MATCH_THRESHOLD:
        return out
    ntokens = len(_tokens(title))
    folded = _fold(raw)

    families = [_fold(a.get("family", "")) for a in item.get("author", [])]
    families = [f for f in families if len(f) >= 3]
    found = sum(1 for f in families if f in folded)
    author_ok = found >= 1
    if families:
        # Citations often list only the first few authors, so judge the overlap against
        # at most the first four on record.
        out["author_overlap"] = round(found / min(len(families), 4), 2) if found else 0.0
        out["author_overlap"] = min(out["author_overlap"], 1.0)

    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", raw)]
    year = item_year(item)
    if years and year is not None:
        out["year_delta"] = min(abs(year - y) for y in years)
    year_ok = not years or (out["year_delta"] is not None and out["year_delta"] <= 1)

    strong_title = score >= 0.95
    if not year_ok:
        # Thesis citing the wrong year: tolerate it only for a complete, specific title
        # with a matching author. Anything weaker is treated as a different work.
        if not (author_ok and strong_title and ntokens >= 5):
            return out
        detail = "record has no year" if year is None else f"cited {sorted(set(years))[0]}, record {year}"
        out["flags"].append(f"year differs ({detail})")
    if families:
        # Author lists in theses are often garbled ('F. d. O. Antonio' for Falcão); a full,
        # specific title with the right year is enough evidence on its own.
        if not (author_ok or (strong_title and ntokens >= 6)):
            return out
        if not author_ok:
            out["flags"].append("author names in the citation don't match the record")
        elif len(families) >= 3 and out["author_overlap"] < 0.5:
            # Only one of several authors matches: fine for a distinctive title, a red
            # flag for a generic one.
            if not (strong_title and ntokens >= 5):
                return out
            out["flags"].append(f"only {found} of {min(len(families), 4)} authors found in the citation")
    else:
        # No authors on the candidate (standards, web pages, book parts): demand a full,
        # long title, and skip figure/appendix-style fragments.
        if not (
            strong_title
            and ntokens >= 5
            and not re.match(r"(?:appendix|figure|table|chapter|part|section|preface|index)\b", title, re.I)
        ):
            return out
        out["flags"].append("record lists no authors to compare")
    if score < 0.9:
        out["flags"].append(f"title only {int(score * 100)}% matched")
    out["accepted"] = True
    return out


def item_year(item):
    for k in ("issued", "published-print", "published-online"):
        parts = (item.get(k) or {}).get("date-parts", [[None]])[0]
        if parts and parts[0]:
            return parts[0]
    return None
