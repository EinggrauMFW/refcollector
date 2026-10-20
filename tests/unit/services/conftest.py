"""Fixtures for service tests: real SqliteStore + SqliteJobQueue, fake identity / local copies."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from refcollector.config import Settings
from refcollector.contracts.api import CallContext, CreateProjectIn, ImportSourceIn
from refcollector.contracts.ports import ResolutionResult, ScoredCandidate
from refcollector.domain.enums import (
    Channel,
    CheckOutcome,
    ExtractionState,
    IdentityState,
    LocalCopyCheck,
    LocalCopyOrigin,
    LocalCopyState,
    Scope,
)
from refcollector.domain.models import (
    Candidate,
    CheckResult,
    LocalCopy,
    Occurrence,
    ProjectWork,
    WorkMetadata,
)
from refcollector.executor.queue import SqliteJobQueue
from refcollector.extraction.builtin import BuiltinExtractionAdapter
from refcollector.ids import new_id
from refcollector.services.service import RefCollectorServiceImpl
from refcollector.storage.db import Database
from refcollector.storage.repos import SqliteStore

_keys = count(1)


def key() -> str:
    return f"idem-key-{next(_keys):06d}"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


class FakeIdentity:
    """Async resolve returns `result`; merge keeps locked fields."""

    def __init__(self) -> None:
        self.result: ResolutionResult | None = None
        self.calls: list[dict[str, Any]] = []

    async def resolve(self, citation, *, fields=None, forced_doi=None, rejected=()):
        self.calls.append({"citation": citation, "fields": fields, "forced_doi": forced_doi})
        assert self.result is not None, "set FakeIdentity.result first"
        return self.result

    def evaluate(self, citation: str, metadata: WorkMetadata) -> ScoredCandidate:
        raise NotImplementedError

    def merge_metadata(self, work: ProjectWork | None, accepted: WorkMetadata, *, source_rank: int):
        data = work.metadata.model_dump() if work else {}
        for name, value in accepted.model_dump().items():
            if value in (None, "", []):
                continue
            if work and work.locks.get(name):
                continue
            data[name] = value
        return WorkMetadata.model_validate(data)


class FakeLocalCopies:
    """Returns one user_attached row; `fail` names the failing check (None = all pass)."""

    def __init__(self) -> None:
        self.fail: LocalCopyCheck | None = None
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, project_id, work, *, version_policy, attached_path=None, version_hint=None):
        self.calls.append({"project_id": project_id, "path": attached_path, "hint": version_hint})
        checks = {
            c: CheckResult(
                outcome=CheckOutcome.FAIL if c == self.fail else CheckOutcome.PASS,
                reason="forced" if c == self.fail else None,
            )
            for c in LocalCopyCheck
        }
        return [
            LocalCopy(
                local_copy_id=new_id("local_copy"),
                project_id=project_id,
                work_id=work.work_id,
                origin=LocalCopyOrigin.USER_ATTACHED,
                sha256="a" * 64,
                checks=checks,
                state=LocalCopyState.UNUSABLE if self.fail else LocalCopyState.USABLE,
                failed_check=self.fail,
            )
        ]


@dataclass
class Env:
    svc: RefCollectorServiceImpl
    store: SqliteStore
    queue: SqliteJobQueue
    settings: Settings
    clock: Clock
    identity: FakeIdentity
    local: FakeLocalCopies
    allowed: Path
    ctx: CallContext
    snapshots: list[Any] = field(default_factory=list)

    def ctx_for(self, *scopes: Scope, channel: Channel = Channel.MCP, tty: bool = False) -> CallContext:
        return CallContext(
            principal="tester", channel=channel, scopes=frozenset(scopes), request_id="req_x", tty=tty
        )

    @property
    def cli(self) -> CallContext:
        return CallContext(
            principal="tester", channel=Channel.CLI, scopes=frozenset(Scope), request_id="req_cli"
        )

    @property
    def tty(self) -> CallContext:
        return CallContext(
            principal="tester",
            channel=Channel.CLI_TTY_REVIEW,
            scopes=frozenset(Scope),
            request_id="req_tty",
            tty=True,
        )

    # ---- seed helpers ----

    def project(self, name: str = "p") -> str:
        req = CreateProjectIn(name=name, idempotency_key=key())
        return self.svc.create_project(self.ctx, req).project_id

    def text_source(
        self, project_id: str, text: str = "Smith, J. (2020). A title. Journal, 1(2), 3-4."
    ) -> str:
        req = ImportSourceIn(
            project_id=project_id, input={"type": "text", "text": text}, idempotency_key=key()
        )
        return self.svc.import_source(self.ctx, req).source_id

    def occ(
        self, project_id: str, source_id: str, text: str = "Doe, J. (2019). Some title. Venue.", **kw: Any
    ) -> Occurrence:
        occ = Occurrence(
            occurrence_id=new_id("occurrence"),
            project_id=project_id,
            source_id=source_id,
            order=kw.pop("order", float(next(_keys))),
            raw_text=text,
            extraction=kw.pop("extraction", ExtractionState.EXTRACTED),
            **kw,
        )
        self.store.occurrences.add_many([occ])
        return self.store.occurrences.get(project_id, occ.occurrence_id)

    def candidate(
        self,
        project_id: str,
        occurrence_id: str,
        *,
        title: str = "A title",
        doi: str | None = "10.1/abc",
        rank: int = 1,
        identifier_linked: bool = True,
    ) -> Candidate:
        cand = Candidate(
            candidate_id=new_id("candidate"),
            project_id=project_id,
            occurrence_id=occurrence_id,
            rank=rank,
            metadata=WorkMetadata(title=title, doi=doi, year=2020),
            identifier_linked=identifier_linked,
        )
        existing = self.store.candidates.list_for_occurrence(project_id, occurrence_id)
        self.store.candidates.replace_for_occurrence(project_id, occurrence_id, [*existing, cand])
        return cand


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
def env(db: Database, settings: Settings, allowed_dir: Path, identity: FakeIdentity) -> Env:
    store = SqliteStore(db, settings)
    queue = SqliteJobQueue(db)
    clock = Clock()
    local = FakeLocalCopies()
    snapshots: list[Any] = []

    def factory(snapshot: Any) -> FakeIdentity:
        snapshots.append(snapshot)
        return identity

    svc = RefCollectorServiceImpl(
        settings,
        store,
        queue,
        extractor=BuiltinExtractionAdapter(settings.limits),
        identity_factory=factory,
        local_copies=local,
        clock=clock,
    )
    ctx = CallContext(principal="tester", channel=Channel.MCP, scopes=frozenset(Scope), request_id="req_test")
    return Env(svc, store, queue, settings, clock, identity, local, allowed_dir, ctx, snapshots)


def resolution(state: IdentityState, *cands: ScoredCandidate, reasons=(), accepted_index=None):
    return ResolutionResult(
        state=state, reasons=list(reasons), candidates=list(cands), accepted_index=accepted_index
    )


def scored(title: str = "Linked title", doi: str = "10.1/forced", reasons: Sequence = (), linked=True):
    return ScoredCandidate(
        metadata=WorkMetadata(title=title, doi=doi, year=2020),
        features={"title_score": 0.9},
        reasons=list(reasons),
        evidence=[],
        identifier_linked=linked,
        score=0.9,
    )
