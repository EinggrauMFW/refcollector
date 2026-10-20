from __future__ import annotations

import re
import unicodedata

HEADINGS = {
    "en": ("References", "Bibliography", "Works Cited", "Literature Cited", "Reference List"),
    "id": ("Daftar Pustaka", "Referensi", "Daftar Rujukan", "Kepustakaan"),
    "de": ("Literaturverzeichnis", "Literatur", "Quellenverzeichnis"),
    "es": ("Referencias", "Bibliografía"),
    "fr": ("Références", "Bibliographie"),
}
_NAMES = tuple(name for names in HEADINGS.values() for name in names)
_PATTERN = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.)]?\s+)?(?:"
    + "|".join(re.escape(x) for x in sorted(_NAMES, key=len, reverse=True))
    + r")\s*$",
    re.I,
)


def is_heading(line: str) -> bool:
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", line)).strip()
    return bool(_PATTERN.fullmatch(normalized))
