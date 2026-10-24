"""In-memory Store implementing contracts.ports for unit tests (no SQLite).

Semantics match the Protocol docstrings: NotFound for missing or cross-project IDs,
revision bumps on update, RevisionConflict on a stale expected revision, superseded access
checks. Stored objects are deep copies, so tests cannot mutate them by accident.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from refcollector.contracts.ports import OccurrenceQuery, Page
from refcollector.domain.models import (
    AccessCheck,
    Artifact,
    AuditEvent,
    Candidate,
    CitationFile,
    Decision,
    FileBlob,
    IdempotencyRecord,
    LocalCopy,
    Occurrence,
    Project,
    ProjectWork,
    ProviderRecord,
    Source,
    WorkFile,
    utcnow,
)
from refcollector.errors import NotFound, RevisionConflict

M = TypeVar("M", bound=BaseModel)


def _copy(model: M) -> M:
    return model.model_copy(deep=True)


def _page(items: list[M], key: str, after: str | None, limit: int) -> Page[M]:
    ids = [getattr(i, key) for i in items]
    start = ids.index(after) + 1 if after in ids else 0
    chunk = items[start : start + limit]
    more = start + limit < len(items)
    return Page([_copy(i) for i in chunk], getattr(chunk[-1], key) if more and chunk else None, len(items))


class _Scoped:
    """Dict of project-scoped models keyed by an ID attribute."""

    key = ""

    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    def _get(self, project_id: str, item_id: str) -> Any:
        row = self.rows.get(item_id)
        if row is None or row.project_id != project_id:
            raise NotFound(f"{item_id} not found", details={"id": item_id})
        return row

    def add(self, item: M) -> M:
        self.rows[getattr(item, self.key)] = _copy(item)
        return _copy(item)

    def get(self, project_id: str, item_id: str) -> Any:
        return _copy(self._get(project_id, item_id))


class FakeProjectRepo:
    def __init__(self) -> None:
        self.rows: dict[str, Project] = {}

    def add(self, project: Project) -> Project:
        self.rows[project.project_id] = _copy(project)
        return _copy(project)

    def get(self, project_id: str) -> Project:
        if project_id not in self.rows:
            raise NotFound(f"{project_id} not found", details={"id": project_id})
        return _copy(self.rows[project_id])

    def update(self, project: Project) -> Project:
        self.get(project.project_id)
        project = project.model_copy(update={"updated_at": utcnow()})
        self.rows[project.project_id] = _copy(project)
        return _copy(project)

    def list(self, *, after: str | None = None, limit: int = 25) -> Page[Project]:
        return _page(sorted(self.rows.values(), key=lambda p: p.project_id), "project_id", after, limit)

    def bump_revision(self, project_id: str) -> int:
        project = self.rows[project_id]
        project.revision += 1
        project.updated_at = utcnow()
        return project.revision

    def get_counts(self, project_id: str) -> tuple[int, int]:
        self.get(project_id)
        return 0, 0

    def delete(self, project_id: str) -> None:
        if project_id not in self.rows:
            raise NotFound(f"{project_id} not found", details={"id": project_id})
        del self.rows[project_id]


class FakeSourceRepo(_Scoped):
    key = "source_id"

    def find_by_hash(self, project_id: str, sha256: str) -> Source | None:
        hit = next((s for s in self.rows.values() if s.project_id == project_id and s.sha256 == sha256), None)
        return _copy(hit) if hit else None

    def update(self, source: Source, *, expected_revision: int | None = None) -> Source:
        current = self._get(source.project_id, source.source_id)
        if expected_revision is not None and expected_revision != current.revision:
            raise RevisionConflict(details={"current_revisions": {source.source_id: current.revision}})
        source = source.model_copy(update={"revision": current.revision + 1})
        self.rows[source.source_id] = _copy(source)
        return _copy(source)

    def list(self, project_id: str, *, after: str | None = None, limit: int = 25) -> Page[Source]:
        items = sorted(
            (s for s in self.rows.values() if s.project_id == project_id), key=lambda s: s.source_id
        )
        return _page(items, "source_id", after, limit)


class FakeOccurrenceRepo(_Scoped):
    key = "occurrence_id"

    def __init__(self) -> None:
        super().__init__()
        self.job_links: dict[str, set[str]] = {}

    def add_many(self, occurrences: Sequence[Occurrence]) -> None:
        for occ in occurrences:
            self.add(occ)

    def get_many(self, project_id: str, occurrence_ids: Sequence[str]) -> list[Occurrence]:
        return [self.get(project_id, i) for i in occurrence_ids]

    def update(self, occurrence: Occurrence, *, expected_revision: int | None = None) -> Occurrence:
        current = self._get(occurrence.project_id, occurrence.occurrence_id)
        if expected_revision is not None and expected_revision != current.revision:
            raise RevisionConflict(
                details={"current_revisions": {occurrence.occurrence_id: current.revision}}
            )
        occurrence = occurrence.model_copy(update={"revision": current.revision + 1, "updated_at": utcnow()})
        self.rows[occurrence.occurrence_id] = _copy(occurrence)
        return _copy(occurrence)

    def check_revisions(self, project_id: str, expected: Mapping[str, int]) -> None:
        current = {i: self._get(project_id, i).revision for i in expected}
        if any(current[i] != rev for i, rev in expected.items()):
            raise RevisionConflict(details={"current_revisions": current})

    def link_to_job(self, project_id: str, job_id: str, occurrence_ids: Sequence[str]) -> None:
        self.job_links.setdefault(job_id, set()).update(occurrence_ids)

    def list(
        self, project_id: str, query: OccurrenceQuery, *, after: str | None = None, limit: int = 25
    ) -> Page[Occurrence]:
        def keep(o: Occurrence) -> bool:
            checks = [
                o.project_id == project_id,
                query.source_id in (None, o.source_id),
                query.extraction in (None, o.extraction),
                query.identity in (None, o.identity),
                query.discovery in (None, o.discovery),
                query.classification in (None, o.classification),
                query.local_copy in (None, o.local_copy),
                query.retrieval in (None, o.retrieval),
                query.work_id in (None, o.work_id),
                query.ids is None or o.occurrence_id in query.ids,
                query.job_id is None or o.occurrence_id in self.job_links.get(query.job_id, set()),
                query.reason is None or query.reason in [*o.identity_reasons, *o.warnings],
                query.text is None or query.text.lower() in o.text.lower(),
            ]
            return all(checks)

        sort_key = {
            "order": lambda o: (o.source_id, o.order, o.occurrence_id),
            "year": lambda o: (o.occurrence_id,),
            "updated": lambda o: (o.updated_at, o.occurrence_id),
        }[query.sort]
        items = sorted(filter(keep, self.rows.values()), key=sort_key)
        return _page(items, "occurrence_id", after, limit)


class FakeCandidateRepo(_Scoped):
    key = "candidate_id"

    def replace_for_occurrence(
        self, project_id: str, occurrence_id: str, candidates: Sequence[Candidate]
    ) -> None:
        self.rows = {
            k: c
            for k, c in self.rows.items()
            if not (c.project_id == project_id and c.occurrence_id == occurrence_id)
        }
        for c in candidates:
            self.add(c)

    def list_for_occurrence(self, project_id: str, occurrence_id: str) -> list[Candidate]:
        hits = [
            c for c in self.rows.values() if c.project_id == project_id and c.occurrence_id == occurrence_id
        ]
        return [_copy(c) for c in sorted(hits, key=lambda c: c.rank)]

    def update(self, candidate: Candidate) -> Candidate:
        self._get(candidate.project_id, candidate.candidate_id)
        return self.add(candidate)


class FakeDecisionRepo(_Scoped):
    key = "decision_id"

    def list_for_occurrence(self, project_id: str, occurrence_id: str) -> list[Decision]:
        hits = [
            d for d in self.rows.values() if d.project_id == project_id and d.occurrence_id == occurrence_id
        ]
        return [_copy(d) for d in sorted(hits, key=lambda d: d.created_at)]


class FakeWorkRepo(_Scoped):
    key = "work_id"

    def update(self, work: ProjectWork) -> ProjectWork:
        self._get(work.project_id, work.work_id)
        return self.add(work.model_copy(update={"updated_at": utcnow()}))

    def find_by_link_key(self, project_id: str, key: str, value: str) -> ProjectWork | None:
        hit = next(
            (w for w in self.rows.values() if w.project_id == project_id and w.link_keys.get(key) == value),
            None,
        )
        return _copy(hit) if hit else None


class FakeAccessCheckRepo:
    def __init__(self) -> None:
        self.rows: list[AccessCheck] = []

    def add_current(self, check: AccessCheck) -> AccessCheck:
        for c in self.rows:
            if c.project_id == check.project_id and c.work_id == check.work_id:
                c.current = False
        check = check.model_copy(update={"current": True})
        self.rows.append(_copy(check))
        return _copy(check)

    def current(self, project_id: str, work_id: str) -> AccessCheck | None:
        hit = next((c for c in self.history(project_id, work_id) if c.current), None)
        return hit

    def history(self, project_id: str, work_id: str) -> list[AccessCheck]:
        hits = [c for c in self.rows if c.project_id == project_id and c.work_id == work_id]
        return [_copy(c) for c in sorted(hits, key=lambda c: c.checked_at, reverse=True)]


class _ByWork(_Scoped):
    def list_for_work(self, project_id: str, work_id: str) -> list[Any]:
        return [_copy(r) for r in self.rows.values() if r.project_id == project_id and r.work_id == work_id]


class FakeLocalCopyRepo(_ByWork):
    key = "local_copy_id"

    def add(self, copy: LocalCopy) -> LocalCopy:
        return super().add(copy)


class FakeWorkFileRepo(_ByWork):
    key = "work_file_id"

    def add(self, wf: WorkFile) -> WorkFile:
        return super().add(wf)


class FakeArtifactRepo(_Scoped):
    key = "artifact_id"

    def list(
        self, project_id: str, *, kind: str | None = None, after: str | None = None, limit: int = 25
    ) -> Page[Artifact]:
        items = sorted(
            (a for a in self.rows.values() if a.project_id == project_id and kind in (None, a.kind)),
            key=lambda a: a.artifact_id,
        )
        return _page(items, "artifact_id", after, limit)


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> AuditEvent:
        self.rows.append(_copy(event))
        return _copy(event)

    def list(self, project_id: str, *, subject_id: str | None = None) -> list[AuditEvent]:
        return [
            _copy(e) for e in self.rows if e.project_id == project_id and subject_id in (None, e.subject_id)
        ]


class FakeIdempotencyRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], IdempotencyRecord] = {}

    def get(
        self, principal: str, project_id: str, tool: str, key: str, *, now: datetime
    ) -> IdempotencyRecord | None:
        rec = self.rows.get((principal, project_id, tool, key))
        return _copy(rec) if rec and rec.expires_at > now else None

    def put(self, record: IdempotencyRecord) -> None:
        self.rows[(record.principal, record.project_id, record.tool, record.key)] = _copy(record)

    def purge_expired(self, now: datetime) -> int:
        old = [k for k, r in self.rows.items() if r.expires_at <= now]
        for k in old:
            del self.rows[k]
        return len(old)


class FakeProviderRecordRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ProviderRecord] = {}

    def upsert(self, record: ProviderRecord) -> None:
        self.rows[(record.provider, record.record_key)] = _copy(record)

    def get(self, provider: str, record_key: str) -> ProviderRecord | None:
        rec = self.rows.get((provider, record_key))
        return _copy(rec) if rec else None


class FakeFileStore:
    """Real files under `root`, in-memory blob and grant tables."""

    def __init__(self, root: Path) -> None:
        self.tmp = root / "tmp"
        self.store = root / "store"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.store.mkdir(parents=True, exist_ok=True)
        self.blobs: dict[str, FileBlob] = {}
        self.grants: set[tuple[str, str]] = set()

    def new_tmp_path(self, suffix: str = "") -> Path:
        return self.tmp / f"{os.urandom(8).hex()}{suffix}"

    def commit(self, tmp_path: Path, media_type: str) -> FileBlob:
        sha = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
        dest = self.store / sha[:2] / sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            tmp_path.unlink()
        else:
            shutil.move(tmp_path, dest)
        blob = self.blobs.setdefault(
            sha, FileBlob(sha256=sha, bytes=dest.stat().st_size, media_type=media_type)
        )
        return _copy(blob)

    def grant(self, project_id: str, sha256: str) -> None:
        self.grants.add((project_id, sha256))

    def has_grant(self, project_id: str, sha256: str) -> bool:
        return (project_id, sha256) in self.grants

    def path_for(self, project_id: str, sha256: str) -> Path:
        if not self.has_grant(project_id, sha256):
            raise NotFound(f"file {sha256} not found", details={"sha256": sha256})
        return self.store / sha256[:2] / sha256

    def cleanup_tmp(self) -> int:
        stray = list(self.tmp.iterdir())
        for p in stray:
            p.unlink()
        return len(stray)

    def index_orphans(self) -> int:
        count = 0
        for p in self.store.glob("*/*"):
            if p.name not in self.blobs:
                self.blobs[p.name] = FileBlob(
                    sha256=p.name, bytes=p.stat().st_size, media_type="application/pdf"
                )
                count += 1
        return count


class FakeCitationFileRepo(_Scoped):
    key = "citation_file_id"

    def update(self, file: CitationFile) -> CitationFile:
        self.get(file.project_id, file.citation_file_id)
        return self.add(file)

    def list_for_occurrence(self, project_id: str, occurrence_id: str) -> list[CitationFile]:
        return [
            _copy(file)
            for file in self.rows.values()
            if file.project_id == project_id and file.occurrence_id == occurrence_id
        ]


class FakeStore:
    """Implements contracts.ports.Store. `transaction()` is a no-op context manager."""

    def __init__(self, root: Path) -> None:
        self.projects = FakeProjectRepo()
        self.sources = FakeSourceRepo()
        self.occurrences = FakeOccurrenceRepo()
        self.candidates = FakeCandidateRepo()
        self.decisions = FakeDecisionRepo()
        self.works = FakeWorkRepo()
        self.access_checks = FakeAccessCheckRepo()
        self.local_copies = FakeLocalCopyRepo()
        self.work_files = FakeWorkFileRepo()
        self.citation_files = FakeCitationFileRepo()
        self.artifacts = FakeArtifactRepo()
        self.audit = FakeAuditRepo()
        self.idempotency = FakeIdempotencyRepo()
        self.provider_records = FakeProviderRecordRepo()
        self.files = FakeFileStore(root)

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[None]:
        yield None
