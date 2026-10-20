"""WP-01: content-addressed FileStore (spec 6.4, 7.1)."""

from __future__ import annotations

import hashlib

import pytest

from refcollector.config import Settings
from refcollector.domain.models import Project
from refcollector.errors import NotFound
from refcollector.storage.db import Database
from refcollector.storage.files import SqliteFileStore
from refcollector.storage.repos import SqliteStore


@pytest.fixture
def store(db: Database, settings: Settings) -> SqliteStore:
    s = SqliteStore(db, settings)
    s.projects.add(Project(project_id="prj_a", name="a"))
    s.projects.add(Project(project_id="prj_b", name="b"))
    return s


def _write_tmp(store: SqliteStore, data: bytes) -> tuple[str, object]:
    tmp = store.files.new_tmp_path(".pdf")
    tmp.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), tmp


def test_commit_dedup_grant_path_for(store: SqliteStore, settings: Settings) -> None:
    files = store.files
    assert isinstance(files, SqliteFileStore)
    sha, tmp = _write_tmp(store, b"%PDF-1.4 hello")
    assert tmp.parent == settings.tmp_dir and tmp.suffix == ".pdf"  # type: ignore[attr-defined]
    blob = files.commit(tmp, "application/pdf")  # type: ignore[arg-type]
    dest = settings.store_dir / sha[:2] / sha
    assert blob.sha256 == sha and blob.bytes == 14 and dest.read_bytes() == b"%PDF-1.4 hello"
    assert not tmp.exists()  # type: ignore[attr-defined]

    # same bytes again: idempotent, temp removed, same blob row
    sha2, tmp2 = _write_tmp(store, b"%PDF-1.4 hello")
    assert files.commit(tmp2, "text/plain") == blob  # type: ignore[arg-type]
    assert not tmp2.exists()  # type: ignore[attr-defined]
    assert sha2 == sha

    assert not files.has_grant("prj_a", sha)
    with pytest.raises(NotFound):
        files.path_for("prj_a", sha)
    files.grant("prj_a", sha)
    files.grant("prj_a", sha)  # idempotent
    assert files.has_grant("prj_a", sha)
    assert files.path_for("prj_a", sha) == dest
    # project B has no grant: not visible (7.1)
    assert not files.has_grant("prj_b", sha)
    with pytest.raises(NotFound):
        files.path_for("prj_b", sha)


def test_cleanup_tmp_and_index_orphans(store: SqliteStore, settings: Settings, db: Database) -> None:
    files = store.files
    for data in (b"a", b"b"):
        files.new_tmp_path().write_bytes(data)
    (settings.tmp_dir / "sub").mkdir()
    (settings.tmp_dir / "sub" / "x").write_bytes(b"x")
    assert files.cleanup_tmp() == 3
    assert list(settings.tmp_dir.iterdir()) == []
    assert files.cleanup_tmp() == 0

    sha, tmp = _write_tmp(store, b"%PDF committed")
    files.commit(tmp, "application/pdf")  # type: ignore[arg-type]
    orphan = b"%PDF orphan"
    osha = hashlib.sha256(orphan).hexdigest()
    (settings.store_dir / osha[:2]).mkdir(parents=True, exist_ok=True)
    (settings.store_dir / osha[:2] / osha).write_bytes(orphan)
    (settings.store_dir / "zz").mkdir()
    (settings.store_dir / "zz" / "not-a-hash").write_bytes(b"junk")
    assert files.index_orphans() == 1
    assert files.index_orphans() == 0
    row = db.query_one("SELECT bytes, media_type FROM file_blob WHERE sha256 = ?", (osha,))
    assert row is not None and tuple(row) == (len(orphan), "application/pdf")
    files.grant("prj_b", osha)
    assert files.path_for("prj_b", osha).read_bytes() == orphan


def test_grants_survive_reopen(store: SqliteStore, settings: Settings, db: Database) -> None:
    sha, tmp = _write_tmp(store, b"%PDF keep")
    store.files.commit(tmp, "application/pdf")  # type: ignore[arg-type]
    store.files.grant("prj_a", sha)
    db.close()
    db2 = Database(settings.db_path)
    try:
        s2 = SqliteStore(db2, settings)
        assert s2.files.path_for("prj_a", sha).read_bytes() == b"%PDF keep"
    finally:
        db2.close()
