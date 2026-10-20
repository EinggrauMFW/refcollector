"""WP-01: content-addressed FileStore under RC_HOME/store with per-project grants. Spec 6.4, 7.1."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
from pathlib import Path

from refcollector.config import Settings
from refcollector.domain.models import FileBlob, utcnow
from refcollector.errors import NotFound
from refcollector.storage.db import Database
from refcollector.storage.idempotency import iso

_SHA = re.compile(r"[0-9a-f]{64}")


class SqliteFileStore:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.tmp = settings.tmp_dir
        self.store = settings.store_dir

    def new_tmp_path(self, suffix: str = "") -> Path:
        self.tmp.mkdir(parents=True, exist_ok=True)
        return self.tmp / f"{secrets.token_hex(12)}{suffix}"

    def commit(self, tmp_path: Path, media_type: str) -> FileBlob:
        with tmp_path.open("rb") as f:
            sha = hashlib.file_digest(f, "sha256").hexdigest()
        dest = self._path(sha)
        if dest.exists():
            tmp_path.unlink()
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_path, dest)  # same volume (both under RC_HOME): atomic
        self._index(sha, dest.stat().st_size, media_type)
        row = self.db.query_one("SELECT * FROM file_blob WHERE sha256 = ?", (sha,))
        return FileBlob(**dict(row))  # type: ignore[arg-type]

    def grant(self, project_id: str, sha256: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO file_grant VALUES (?, ?, ?)", (project_id, sha256, iso(utcnow()))
        )

    def has_grant(self, project_id: str, sha256: str) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM file_grant WHERE project_id = ? AND sha256 = ?", (project_id, sha256)
        )
        return row is not None

    def path_for(self, project_id: str, sha256: str) -> Path:
        if not self.has_grant(project_id, sha256):
            raise NotFound(f"file {sha256} not found", details={"sha256": sha256})
        return self._path(sha256)

    def cleanup_tmp(self) -> int:
        if not self.tmp.exists():
            return 0
        count = 0
        for p in self.tmp.iterdir():
            if p.is_dir():
                count += sum(1 for c in p.rglob("*") if c.is_file())
                shutil.rmtree(p)
            else:
                p.unlink()
                count += 1
        return count

    def index_orphans(self) -> int:
        count = 0
        for p in self.store.glob("*/*"):
            if p.is_file() and _SHA.fullmatch(p.name) and p.parent.name == p.name[:2]:
                with p.open("rb") as f:
                    media = "application/pdf" if f.read(4) == b"%PDF" else "application/octet-stream"
                count += self._index(p.name, p.stat().st_size, media)
        return count

    def _path(self, sha256: str) -> Path:
        return self.store / sha256[:2] / sha256

    def _index(self, sha256: str, size: int, media_type: str) -> int:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO file_blob VALUES (?, ?, ?, ?)", (sha256, size, media_type, iso(utcnow()))
        )
        return cur.rowcount
