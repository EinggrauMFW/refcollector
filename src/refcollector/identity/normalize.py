"""WP-06: ported v0.1 normalisation (_fold, _norm, _stem, _tokens, match_score). Spec 9.2."""

import re
import unicodedata
from html import unescape

from refcollector.domain.models import WorkMetadata

from .identifiers import normalize_doi, normalize_isbn


def normalize_metadata(metadata: WorkMetadata) -> WorkMetadata:
    """Copy provider data. Decode display text without changing personal/literal names."""
    data = metadata.model_dump()
    for name, value in data.items():
        if isinstance(value, str):
            data[name] = unescape(unicodedata.normalize("NFKC", value)).strip() or None
    if data["doi"]:
        data["doi"] = normalize_doi(data["doi"])
    if data["isbn"]:
        data["isbn"] = normalize_isbn(data["isbn"])
    return WorkMetadata.model_validate(data)


def _fold(s):
    """Lowercase and strip accents so 'Sepúlveda' and 'Sepulveda' compare equal."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def _norm(s):
    """Comparable form of a title/reference: NFKC, HTML entities, accents, '&' -> 'and'."""
    return _fold(unescape(unicodedata.normalize("NFKC", s))).replace("&", " and ")


def _stem(w):
    """Cheap normalisation so spelling variants compare equal (applied to both sides)."""
    w = re.sub(r"is(ation|ing|ed|e)$", lambda m: "iz" + m.group(1), w)  # optimisation
    w = re.sub(r"yse$", "yze", w)  # analyse
    w = re.sub(r"our(s?)$", r"or\1", w)  # behaviour, harbour
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]  # waves -> wave
    return w


def _tokens(s, join_hyphens=False):
    """Word set. Hyphens split words ('wave-power' -> wave, power) or, with join_hyphens,
    join them ('pre-trained' -> pretrained)."""
    s = re.sub(r"[-‐-―]", "" if join_hyphens else " ", _norm(s))
    return {_stem(w) for w in re.findall(r"[a-z0-9]{3,}", s)}


def match_score(title, raw):
    """Share of the candidate title's words that also appear in the reference string.
    Tries the title with hyphens split and joined, and lets long words match inside a
    run-together form of the reference (so 'pretrained' matches 'pre-trained')."""
    raw_tokens = _tokens(raw)
    squashed = re.sub(r"[^a-z0-9]", "", _norm(raw))
    best = 0.0
    for join in (False, True):
        t = _tokens(title, join)
        if t:
            found = sum(1 for w in t if w in raw_tokens or (len(w) >= 6 and w in squashed))
            best = max(best, found / len(t))
    return best
