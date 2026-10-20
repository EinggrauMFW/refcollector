"""Persist metadata and its field ranks together through the existing JSON repository."""

import pytest

from refcollector.domain.models import Project, ProjectWork, WorkMetadata
from refcollector.identity import DefaultIdentityResolver
from refcollector.storage.db import Database
from refcollector.storage.repos import SqliteStore


def test_metadata_ranks_survive_sequential_updates_reopen_and_rollback(db, settings):
    store = SqliteStore(db, settings)
    for pid in ("prj_a", "prj_b"):
        store.projects.add(Project(project_id=pid, name=pid))
        store.works.add(ProjectWork(work_id=f"wrk_{pid}", project_id=pid, metadata=WorkMetadata()))
    resolver = DefaultIdentityResolver([])
    for rank, title in ((3, "Agent"), (2, "Parsed"), (1, "Corroborated"), (0, "Identifier")):
        # A new database object ensures ranks come from disk, not a resolver cache.
        with_db = Database(settings.db_path)
        try:
            reopened = SqliteStore(with_db, settings)
            current = reopened.works.get("prj_a", "wrk_prj_a")
            updated = resolver.merge_work_metadata(current, WorkMetadata(title=title), source_rank=rank)
            with reopened.transaction(immediate=True):
                reopened.works.update(updated)
        finally:
            with_db.close()
        saved = store.works.get("prj_a", "wrk_prj_a")
        assert saved.metadata.title == title and saved.metadata_source_ranks == {"title": rank}
    current = store.works.get("prj_a", "wrk_prj_a")
    ignored = resolver.merge_work_metadata(current, WorkMetadata(title="Agent again"), source_rank=3)
    store.works.update(ignored)
    assert store.works.get("prj_a", "wrk_prj_a").metadata.title == "Identifier"
    with pytest.raises(RuntimeError), store.transaction(immediate=True):
        changed = resolver.merge_work_metadata(
            current, WorkMetadata(title="Rolled back", venue="New"), source_rank=0
        )
        store.works.update(changed)
        assert store.works.get("prj_a", "wrk_prj_a").metadata_source_ranks == {"title": 0, "venue": 0}
        raise RuntimeError("Rollback metadata and ranks together")
    saved = store.works.get("prj_a", "wrk_prj_a")
    assert saved.metadata.title == "Identifier" and saved.metadata.venue is None
    assert saved.metadata_source_ranks == {"title": 0}
    other = store.works.get("prj_b", "wrk_prj_b")
    assert other.metadata.title is None and other.metadata_source_ranks == {}
