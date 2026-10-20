"""WP-04: response cache by request fingerprint with TTLs. Spec 6.6.

Rows live in `response_cache`. SQLite access is synchronous and short (D-08), so the async
client calls these functions directly.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from refcollector.contracts.ports import HttpResponse
from refcollector.storage.db import Database


def fingerprint(method: str, url: str, params: Mapping[str, Any] | None, accept: str | None) -> str:
    """sha256 of method, URL, sorted params and the Accept header. Secrets are never part of it."""
    key = json.dumps(
        [method.upper(), url, sorted((str(k), str(v)) for k, v in (params or {}).items()), accept or ""]
    )
    return hashlib.sha256(key.encode()).hexdigest()


def lookup(db: Database, fp: str, now: datetime | None = None) -> HttpResponse | None:
    now = now or datetime.now(UTC)
    row = db.query_one(
        "SELECT status, headers, body, url FROM response_cache WHERE fingerprint = ? AND expires_at > ?",
        (fp, now.isoformat()),
    )
    if row is None:
        return None
    return HttpResponse(row["status"], json.loads(row["headers"]), row["body"], row["url"], from_cache=True)


def store(db: Database, fp: str, provider: str, resp: HttpResponse, ttl_s: int) -> None:
    now = datetime.now(UTC)
    db.execute(
        "INSERT OR REPLACE INTO response_cache VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fp,
            provider,
            resp.url,
            resp.status,
            json.dumps(resp.headers),
            resp.body,
            now.isoformat(),
            (now + timedelta(seconds=ttl_s)).isoformat(),
        ),
    )
