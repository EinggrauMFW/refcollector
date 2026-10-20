"""WP-01: SQLite repositories implementing contracts.ports and Store.

ProjectRepo ... ProviderRecordRepo. Spec 7.1, 7.2, 7.5, 11.3.

Each row keeps the model as JSON in `data` plus indexed columns (D-21); `_cols` keeps the
columns in sync on every write. Project-scoped reads filter by project_id, so a row of
another project is NotFound (AT-42/43). Writes that read-then-write run in one transaction
(BEGIN IMMEDIATE when not already inside one).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Generic, TypeVar

from refcollector.config import Settings
from refcollector.contracts.ports import OccurrenceQuery, Page
from refcollector.domain.models import (
    AccessCheck,
    Artifact,
    Candidate,
    Decision,
    LocalCopy,
    Model,
    Occurrence,
    Project,
    ProjectWork,
    ProviderRecord,
    Source,
    WorkFile,
    utcnow,
)
from refcollector.errors import InvalidInput, InvalidState, NotFound, RevisionConflict
from refcollector.storage.audit import SqliteAuditRepo
from refcollector.storage.db import Database
from refcollector.storage.files import SqliteFileStore
from refcollector.storage.idempotency import SqliteIdempotencyRepo, iso

M = TypeVar("M", bound=Model)

_YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
_IN = "IN (SELECT value FROM json_each(?))"  # one JSON-array parameter, no variable limit


def _not_found(item_id: str) -> NotFound:
    return NotFound(f"{item_id} not found", details={"id": item_id})


class _Table(Generic[M]):
    """JSON document + indexed columns for one table. Subclasses set the class attributes."""

    table = ""
    model: type[M]
    key = ""  # model ID attribute

    def __init__(self, db: Database) -> None:
        self.db = db

    def _cols(self, m: M, d: dict[str, Any]) -> dict[str, Any]:
        """Indexed columns besides id and data. `d` is m.model_dump(mode="json")."""
        return {"project_id": d["project_id"]}

    def _tx(self) -> AbstractContextManager[Any]:
        return self.db.transaction(immediate=not self.db.in_transaction)

    def _row(self, m: M) -> dict[str, Any]:
        return {
            "id": getattr(m, self.key),
            **self._cols(m, m.model_dump(mode="json")),
            "data": m.model_dump_json(),
        }

    def _insert(self, m: M) -> M:
        row = self._row(m)
        sql = f"INSERT INTO {self.table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})"  # noqa: S608
        self._run(sql, list(row.values()), row["id"])
        return m.model_copy(deep=True)

    def _write(self, m: M) -> M:
        row = self._row(m)
        item_id = row.pop("id")
        sets = ", ".join(f"{c} = ?" for c in row)
        self._run(f"UPDATE {self.table} SET {sets} WHERE id = ?", [*row.values(), item_id], item_id)  # noqa: S608
        return m.model_copy(deep=True)

    def _run(self, sql: str, params: list[Any], item_id: str) -> None:
        try:
            self.db.execute(sql, params)
        except sqlite3.IntegrityError as e:  # duplicate ID, UNIQUE index, missing parent row
            raise InvalidState(f"{item_id}: {e}", details={"id": item_id}) from e

    def _get(self, project_id: str, item_id: str) -> M:
        row = self.db.query_one(
            f"SELECT data FROM {self.table} WHERE id = ? AND project_id = ?",  # noqa: S608
            (item_id, project_id),
        )
        if row is None:
            raise _not_found(item_id)
        return self.model.model_validate_json(row[0])

    def _select(self, where: str, params: Sequence[Any], order: str = "id") -> list[M]:
        rows = self.db.query(f"SELECT data FROM {self.table} WHERE {where} ORDER BY {order}", params)  # noqa: S608
        return [self.model.model_validate_json(r[0]) for r in rows]

    def _page(self, id_sql: str, params: Sequence[Any], after: str | None, limit: int) -> Page[M]:
        """`id_sql` selects the ordered IDs of every match; `after` is the last ID of the previous page.

        ponytail: reads all matching IDs per page (fine for one project's rows);
        switch to keyset paging if it gets slow.
        """
        ids = [r[0] for r in self.db.query(id_sql, params)]
        start = ids.index(after) + 1 if after in ids else 0
        chunk = ids[start : start + limit]
        rows = self.db.query(
            f"SELECT id, data FROM {self.table} WHERE id {_IN}",  # noqa: S608
            (json.dumps(chunk),),
        )
        by_id = {r[0]: self.model.model_validate_json(r[1]) for r in rows}
        more = start + limit < len(ids)
        return Page([by_id[i] for i in chunk], chunk[-1] if more and chunk else None, len(ids))


# ---------------------------------------------------------------- projects and sources


class SqliteProjectRepo(_Table[Project]):
    table, model, key = "project", Project, "project_id"

    def _cols(self, m: Project, d: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": m.name,
            "revision": m.revision,
            "created_at": iso(m.created_at),
            "updated_at": iso(m.updated_at),
        }

    def add(self, project: Project) -> Project:
        return self._insert(project)

    def get(self, project_id: str) -> Project:
        row = self.db.query_one("SELECT data FROM project WHERE id = ?", (project_id,))
        if row is None:
            raise _not_found(project_id)
        return Project.model_validate_json(row[0])

    def update(self, project: Project) -> Project:
        """Keeps the stored revision: only bump_revision moves it."""
        with self._tx():
            current = self.get(project.project_id)
            return self._write(
                project.model_copy(update={"revision": current.revision, "updated_at": utcnow()})
            )

    def list(self, *, after: str | None = None, limit: int = 25) -> Page[Project]:
        return self._page("SELECT id FROM project ORDER BY id", (), after, limit)

    def bump_revision(self, project_id: str) -> int:
        now = iso(utcnow())
        rows = self.db.query(
            "UPDATE project SET revision = revision + 1, updated_at = ?,"
            " data = json_set(data, '$.revision', revision + 1, '$.updated_at', ?)"
            " WHERE id = ? RETURNING revision",
            (now, now, project_id),
        )
        if not rows:
            raise _not_found(project_id)
        return int(rows[0][0])


class SqliteSourceRepo(_Table[Source]):
    table, model, key = "source", Source, "source_id"

    def _cols(self, m: Source, d: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": m.project_id,
            "sha256": m.sha256,
            "type": d["type"],
            "extraction": d["extraction_state"],
            "revision": m.revision,
            "imported_at": iso(m.imported_at),
        }

    def add(self, source: Source) -> Source:
        return self._insert(source)

    def get(self, project_id: str, source_id: str) -> Source:
        return self._get(project_id, source_id)

    def find_by_hash(self, project_id: str, sha256: str) -> Source | None:
        hits = self._select("project_id = ? AND sha256 = ?", (project_id, sha256))
        return hits[0] if hits else None

    def update(self, source: Source, *, expected_revision: int | None = None) -> Source:
        with self._tx():
            current = self._get(source.project_id, source.source_id)
            if expected_revision is not None and expected_revision != current.revision:
                raise RevisionConflict(details={"current_revisions": {source.source_id: current.revision}})
            return self._write(source.model_copy(update={"revision": current.revision + 1}))

    def list(self, project_id: str, *, after: str | None = None, limit: int = 25) -> Page[Source]:
        return self._page(
            "SELECT id FROM source WHERE project_id = ? ORDER BY id", (project_id,), after, limit
        )


# ---------------------------------------------------------------- occurrences


_OCC_EQ = (
    "source_id",
    "extraction",
    "identity",
    "discovery",
    "classification",
    "local_copy",
    "retrieval",
    "work_id",
)
_WORK_YEAR = (
    "COALESCE(json_extract(w.data, '$.overrides.year'), json_extract(w.data, '$.metadata.year'), o.year)"
)
_OCC_SORT = {
    "order": "o.source_id, o.ordinal, o.id",
    "year": f"{_WORK_YEAR} IS NULL, {_WORK_YEAR}, o.source_id, o.ordinal, o.id",
    "updated": "o.updated_at, o.id",
}


class SqliteOccurrenceRepo(_Table[Occurrence]):
    table, model, key = "occurrence", Occurrence, "occurrence_id"

    def _cols(self, m: Occurrence, d: dict[str, Any]) -> dict[str, Any]:
        year = _YEAR.search(m.text)
        return {
            "project_id": m.project_id,
            "source_id": m.source_id,
            "ordinal": m.order,
            "revision": m.revision,
            "extraction": d["extraction"],
            "identity": d["identity"],
            "discovery": d["discovery"],
            "classification": d["classification"],
            "local_copy": d["local_copy"],
            "retrieval": d["retrieval"],
            "work_id": m.work_id,
            "year": int(year.group(1)) if year else None,  # parsed from the text; accepted work wins in sort
            "search_text": m.text.lower(),
            "created_at": iso(m.created_at),
            "updated_at": iso(m.updated_at),
        }

    def _save_reasons(self, occ: Occurrence) -> None:
        self.db.execute("DELETE FROM occurrence_reason WHERE occurrence_id = ?", (occ.occurrence_id,))
        codes = {str(c) for c in [*occ.identity_reasons, *occ.warnings]}
        for code in sorted(codes):
            self.db.execute(
                "INSERT INTO occurrence_reason VALUES (?, ?, ?)", (occ.project_id, occ.occurrence_id, code)
            )

    def _revisions(self, project_id: str, ids: Sequence[str]) -> dict[str, int]:
        """Current revisions of `ids`; NotFound for the first missing or cross-project ID."""
        rows = self.db.query(
            f"SELECT id, revision FROM occurrence WHERE project_id = ? AND id {_IN}",  # noqa: S608
            (project_id, json.dumps(list(ids))),
        )
        found = {r[0]: r[1] for r in rows}
        missing = next((i for i in ids if i not in found), None)
        if missing is not None:
            raise _not_found(missing)
        return {i: found[i] for i in ids}

    def add_many(self, occurrences: Sequence[Occurrence]) -> None:
        with self._tx():
            for occ in occurrences:
                self._insert(occ)
                self._save_reasons(occ)

    def get(self, project_id: str, occurrence_id: str) -> Occurrence:
        return self._get(project_id, occurrence_id)

    def get_many(self, project_id: str, occurrence_ids: Sequence[str]) -> list[Occurrence]:
        self._revisions(project_id, occurrence_ids)
        found = {o.occurrence_id: o for o in self._select(f"id {_IN}", (json.dumps(list(occurrence_ids)),))}
        return [found[i].model_copy(deep=True) for i in occurrence_ids]

    def update(self, occurrence: Occurrence, *, expected_revision: int | None = None) -> Occurrence:
        oid = occurrence.occurrence_id
        with self._tx():
            current = self._revisions(occurrence.project_id, [oid])[oid]
            if expected_revision is not None and expected_revision != current:
                raise RevisionConflict(details={"current_revisions": {oid: current}})
            new = self._write(occurrence.model_copy(update={"revision": current + 1, "updated_at": utcnow()}))
            self._save_reasons(new)
            return new

    def check_revisions(self, project_id: str, expected: Mapping[str, int]) -> None:
        current = self._revisions(project_id, list(expected))
        if any(current[i] != rev for i, rev in expected.items()):
            raise RevisionConflict(details={"current_revisions": current})

    def list(
        self, project_id: str, query: OccurrenceQuery, *, after: str | None = None, limit: int = 25
    ) -> Page[Occurrence]:
        where, params = ["o.project_id = ?"], [project_id]
        for col in _OCC_EQ:
            value = getattr(query, col)
            if value is not None:
                where.append(f"o.{col} = ?")
                params.append(str(value))
        if query.ids is not None:
            where.append(f"o.id {_IN}")
            params.append(json.dumps(list(query.ids)))
        if query.job_id is not None:
            where.append(
                "o.id IN (SELECT occurrence_id FROM job_occurrence WHERE job_id = ? AND project_id = ?)"
            )
            params += [query.job_id, project_id]
        if query.reason is not None:
            where.append(
                "o.id IN (SELECT occurrence_id FROM occurrence_reason WHERE project_id = ? AND code = ?)"
            )
            params += [project_id, str(query.reason)]
        if query.text is not None:
            where.append("instr(o.search_text, ?) > 0")
            params.append(query.text.lower())
        sql = (
            "SELECT o.id FROM occurrence o"  # noqa: S608 - fixed fragments, values are parameters
            " LEFT JOIN project_work w ON w.id = o.work_id AND w.project_id = o.project_id"
            f" WHERE {' AND '.join(where)} ORDER BY {_OCC_SORT[query.sort]}"
        )
        return self._page(sql, params, after, limit)

    def link_to_job(self, project_id: str, job_id: str, occurrence_ids: Sequence[str]) -> None:
        with self._tx():
            self._revisions(project_id, occurrence_ids)
            for oid in occurrence_ids:
                self.db.execute(
                    "INSERT OR IGNORE INTO job_occurrence VALUES (?, ?, ?)", (job_id, oid, project_id)
                )


# ---------------------------------------------------------------- candidates, decisions, works


class SqliteCandidateRepo(_Table[Candidate]):
    table, model, key = "candidate", Candidate, "candidate_id"

    def _cols(self, m: Candidate, d: dict[str, Any]) -> dict[str, Any]:
        return {"project_id": m.project_id, "occurrence_id": m.occurrence_id, "rank": m.rank}

    def replace_for_occurrence(
        self, project_id: str, occurrence_id: str, candidates: Sequence[Candidate]
    ) -> None:
        with self._tx():
            self.db.execute(
                "DELETE FROM candidate WHERE project_id = ? AND occurrence_id = ?",
                (project_id, occurrence_id),
            )
            for c in candidates:
                self._insert(c)

    def list_for_occurrence(self, project_id: str, occurrence_id: str) -> list[Candidate]:
        return self._select("project_id = ? AND occurrence_id = ?", (project_id, occurrence_id), "rank, id")

    def get(self, project_id: str, candidate_id: str) -> Candidate:
        return self._get(project_id, candidate_id)

    def update(self, candidate: Candidate) -> Candidate:
        with self._tx():
            self._get(candidate.project_id, candidate.candidate_id)
            return self._write(candidate)


class SqliteDecisionRepo(_Table[Decision]):
    table, model, key = "decision", Decision, "decision_id"

    def _cols(self, m: Decision, d: dict[str, Any]) -> dict[str, Any]:
        return {"project_id": m.project_id, "occurrence_id": m.occurrence_id, "created_at": iso(m.created_at)}

    def add(self, decision: Decision) -> Decision:
        return self._insert(decision)

    def list_for_occurrence(self, project_id: str, occurrence_id: str) -> list[Decision]:
        return self._select(
            "project_id = ? AND occurrence_id = ?", (project_id, occurrence_id), "created_at, id"
        )


class SqliteWorkRepo(_Table[ProjectWork]):
    table, model, key = "project_work", ProjectWork, "work_id"

    def _cols(self, m: ProjectWork, d: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": m.project_id,
            "doi": m.link_keys.get("doi"),
            "openalex_id": m.link_keys.get("openalex_id"),
            "updated_at": iso(m.updated_at),
        }

    def add(self, work: ProjectWork) -> ProjectWork:
        return self._insert(work)

    def get(self, project_id: str, work_id: str) -> ProjectWork:
        return self._get(project_id, work_id)

    def update(self, work: ProjectWork) -> ProjectWork:
        with self._tx():
            self._get(work.project_id, work.work_id)
            return self._write(work.model_copy(update={"updated_at": utcnow()}))

    def find_by_link_key(self, project_id: str, key: str, value: str) -> ProjectWork | None:
        if key not in ("doi", "openalex_id"):
            raise InvalidInput(f"unknown link key {key!r}", details={"key": key})
        hits = self._select(f"project_id = ? AND {key} = ?", (project_id, value))
        return hits[0] if hits else None


# ---------------------------------------------------------------- access, copies, files, artifacts


class SqliteAccessCheckRepo(_Table[AccessCheck]):
    table, model, key = "access_check", AccessCheck, "check_id"

    def _cols(self, m: AccessCheck, d: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": m.project_id,
            "work_id": m.work_id,
            "current": int(m.current),
            "checked_at": iso(m.checked_at),
        }

    def add_current(self, check: AccessCheck) -> AccessCheck:
        with self._tx():
            self.db.execute(
                "UPDATE access_check SET current = 0, data = json_set(data, '$.current', json('false'))"
                " WHERE project_id = ? AND work_id = ? AND current = 1",
                (check.project_id, check.work_id),
            )
            return self._insert(check.model_copy(update={"current": True}))

    def current(self, project_id: str, work_id: str) -> AccessCheck | None:
        hits = self._select("project_id = ? AND work_id = ? AND current = 1", (project_id, work_id))
        return hits[0] if hits else None

    def history(self, project_id: str, work_id: str) -> list[AccessCheck]:
        return self._select(
            "project_id = ? AND work_id = ?", (project_id, work_id), "checked_at DESC, rowid DESC"
        )


class SqliteLocalCopyRepo(_Table[LocalCopy]):
    table, model, key = "local_copy", LocalCopy, "local_copy_id"

    def _cols(self, m: LocalCopy, d: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": m.project_id,
            "work_id": m.work_id,
            "state": d["state"],
            "created_at": iso(m.created_at),
        }

    def add(self, copy: LocalCopy) -> LocalCopy:
        return self._insert(copy)

    def list_for_work(self, project_id: str, work_id: str) -> list[LocalCopy]:
        return self._select("project_id = ? AND work_id = ?", (project_id, work_id), "created_at, id")


class SqliteWorkFileRepo(_Table[WorkFile]):
    table, model, key = "work_file", WorkFile, "work_file_id"

    def _cols(self, m: WorkFile, d: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": m.project_id,
            "work_id": m.work_id,
            "sha256": m.sha256,
            "state": d["state"],
            "retrieved_at": iso(m.retrieved_at),
        }

    def add(self, wf: WorkFile) -> WorkFile:
        return self._insert(wf)

    def list_for_work(self, project_id: str, work_id: str) -> list[WorkFile]:
        return self._select("project_id = ? AND work_id = ?", (project_id, work_id), "retrieved_at, id")


class SqliteArtifactRepo(_Table[Artifact]):
    table, model, key = "artifact", Artifact, "artifact_id"

    def _cols(self, m: Artifact, d: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": m.project_id,
            "kind": m.kind,
            "job_id": m.job_id,
            "created_at": iso(m.created_at),
        }

    def add(self, artifact: Artifact) -> Artifact:
        return self._insert(artifact)

    def get(self, project_id: str, artifact_id: str) -> Artifact:
        return self._get(project_id, artifact_id)

    def list(
        self, project_id: str, *, kind: str | None = None, after: str | None = None, limit: int = 25
    ) -> Page[Artifact]:
        sql = "SELECT id FROM artifact WHERE project_id = ? AND (? IS NULL OR kind = ?) ORDER BY id"
        return self._page(sql, (project_id, kind, kind), after, limit)


# ---------------------------------------------------------------- shared tier


class SqliteProviderRecordRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, record: ProviderRecord) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO provider_record VALUES (?, ?, ?, ?, ?)",
            (
                record.provider,
                record.record_key,
                iso(record.fetched_at),
                record.payload_hash,
                record.model_dump_json(),
            ),
        )

    def get(self, provider: str, record_key: str) -> ProviderRecord | None:
        row = self.db.query_one(
            "SELECT data FROM provider_record WHERE provider = ? AND record_key = ?", (provider, record_key)
        )
        return ProviderRecord.model_validate_json(row[0]) if row else None


# ---------------------------------------------------------------- store


class SqliteStore:
    """Implements contracts.ports.Store over one Database."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.projects = SqliteProjectRepo(db)
        self.sources = SqliteSourceRepo(db)
        self.occurrences = SqliteOccurrenceRepo(db)
        self.candidates = SqliteCandidateRepo(db)
        self.decisions = SqliteDecisionRepo(db)
        self.works = SqliteWorkRepo(db)
        self.access_checks = SqliteAccessCheckRepo(db)
        self.local_copies = SqliteLocalCopyRepo(db)
        self.work_files = SqliteWorkFileRepo(db)
        self.artifacts = SqliteArtifactRepo(db)
        self.audit = SqliteAuditRepo(db)
        self.idempotency = SqliteIdempotencyRepo(db)
        self.provider_records = SqliteProviderRecordRepo(db)
        self.files = SqliteFileStore(db, settings)

    def transaction(self, immediate: bool = False) -> AbstractContextManager[Any]:
        return self.db.transaction(immediate=immediate)
