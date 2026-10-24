"""D-48 additive persistence, project boundaries, and transaction proof."""

import hashlib

import pytest

from refcollector.domain.models import CitationFile
from refcollector.errors import InvalidState, NotFound
from refcollector.storage.repos import SqliteStore
from tests.unit.storage.test_repos import mk_occ, mk_project, mk_source


def seed(db, settings):
    store = SqliteStore(db, settings)
    mk_project(store)
    mk_source(store)
    row = mk_occ("occ_1", raw_text="Citation doi:10.1234/one")
    store.occurrences.add_many([row])
    return store, row


def file_for(row):
    return CitationFile(
        citation_file_id="cfl_1",
        project_id=row.project_id,
        occurrence_id=row.occurrence_id,
        target_doi="10.1234/one",
        citation_revision=row.revision,
        citation_text_sha256=hashlib.sha256(row.text.encode()).hexdigest(),
        job_id="job_1",
        sha256="a" * 64,
        bytes=4,
        source_url="https://example.org/pdf",
        final_url="https://example.org/pdf",
        state="verified",
        export_name="paper.pdf",
        verification_evidence={"readable": True},
    )


def test_req2_req7_additive_migration_persists_without_accepted_work(db, settings):
    store, row = seed(db, settings)
    file = store.citation_files.add(file_for(row))
    assert db.migrate() == []
    assert "0002_citation_files.sql" in {r[0] for r in db.query("SELECT name FROM schema_migrations")}
    assert SqliteStore(db, settings).citation_files.get(row.project_id, file.citation_file_id) == file
    assert store.works._select("project_id = ?", (row.project_id,)) == []


def test_req6_composite_foreign_key_rejects_cross_project_occurrence(db, settings):
    store, row = seed(db, settings)
    mk_project(store, "prj_other")
    file = file_for(row).model_copy(update={"project_id": "prj_other"})
    with pytest.raises(InvalidState):
        store.citation_files.add(file)
    assert store.citation_files.list_for_occurrence("prj_other", row.occurrence_id) == []


def test_req6_project_reads_and_updates_are_scoped(db, settings):
    store, row = seed(db, settings)
    file = store.citation_files.add(file_for(row))
    with pytest.raises(NotFound):
        store.citation_files.get("prj_other", file.citation_file_id)
    with pytest.raises(NotFound):
        store.citation_files.update(file.model_copy(update={"project_id": "prj_other"}))
    assert store.citation_files.get(row.project_id, file.citation_file_id) == file


def test_req6_citation_record_rolls_back_with_transaction(db, settings):
    store, row = seed(db, settings)
    with pytest.raises(RuntimeError), store.transaction(immediate=True):
        store.citation_files.add(file_for(row))
        raise RuntimeError("rollback")
    assert store.citation_files.list_for_occurrence(row.project_id, row.occurrence_id) == []
