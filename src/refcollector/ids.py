"""Opaque, sortable IDs (spec 7.2): a type prefix plus a ULID. A DOI is never a primary key."""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32

PREFIXES = {
    "project": "prj",
    "source": "src",
    "section": "sec",
    "occurrence": "occ",
    "candidate": "cand",
    "decision": "dec",
    "approval": "apr",
    "work": "wrk",
    "access_check": "acc",
    "location": "loc",
    "local_copy": "lcp",
    "work_file": "wfl",
    "citation_file": "cfl",
    "artifact": "art",
    "job": "job",
    "step": "stp",
    "event": "evt",
    "executor": "exe",
    "request": "req",
}


def ulid(timestamp_ms: int | None = None) -> str:
    """26-character ULID: 48-bit millisecond time + 80 random bits."""
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))


def new_id(kind: str) -> str:
    """`new_id("occurrence")` -> 'occ_01J9...'. Raises KeyError for an unknown kind."""
    return f"{PREFIXES[kind]}_{ulid()}"


def kind_of(item_id: str) -> str | None:
    prefix = item_id.split("_", 1)[0]
    return next((k for k, p in PREFIXES.items() if p == prefix), None)
