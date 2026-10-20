"""Manifest helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from refcollector.contracts.ports import RenderedFile
from refcollector.domain.models import Project, WorkMetadata

from .jsonout import ascii_text, json_bytes, metadata_dict


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_manifest(
    project: Project,
    *,
    selection: str,
    files: Sequence[RenderedFile],
    counts: Mapping[str, int],
    policy_versions: Mapping[str, str],
) -> RenderedFile:
    payload = {
        "project_id": project.project_id,
        "revision": project.revision,
        "selection": str(selection),
        "counts": dict(counts),
        "policy_versions": dict(policy_versions),
        "files": [
            {
                "name": file.name,
                "media_type": file.media_type,
                "bytes": len(file.data),
                "sha256": sha256(file.data),
            }
            for file in files
        ],
    }
    return RenderedFile("manifest.json", "application/json", json_bytes(payload))


def _slug(text: str, limit: int) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_text(text)).strip("_")[:limit].rstrip("_") or "anon"


def export_file_name(meta: WorkMetadata | Mapping[str, Any]) -> str:
    data = metadata_dict(meta)
    first = (data.get("authors") or [{}])[0]
    author = first.get("literal") or first.get("family") or "anon"
    title = " ".join(re.findall(r"[A-Za-z0-9]+", ascii_text(data.get("title") or ""))[:6])
    year = str(data.get("year") or "")
    if not year.isascii() or not year.isdigit():
        year = "noyear"
    return f"{year}_{_slug(author, 60)}_{_slug(title, 60)}.pdf"


def unique_export_file_names(metas: Sequence[WorkMetadata | Mapping[str, Any]]) -> list[str]:
    names = []
    used: set[str] = set()
    for meta in metas:
        base = export_file_name(meta)
        name = base
        number = 2
        while name.casefold() in used:
            name = f"{base[:-4]}_{number}.pdf"
            number += 1
        used.add(name.casefold())
        names.append(name)
    return names
