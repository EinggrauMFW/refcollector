"""WP-01: SqliteStore repositories.

Spec 7.1, 7.2, 7.5, 11.3; AT-18, AT-37, AT-42, AT-43; W-04, W-30, W-31 (store side).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from refcollector.config import Settings
from refcollector.contracts.ports import OccurrenceQuery
from refcollector.domain.enums import (
    AccessClassification,
    ApprovalSurface,
    AuditEventType,
    Channel,
    DecisionAction,
    DiscoveryState,
    ExtractionWarning,
    IdentityState,
    LocalCopyOrigin,
    LocalCopyState,
    Origin,
    ReasonCode,
    RetrievalState,
    SourceType,
)
from refcollector.domain.models import (
    AccessCheck,
    Approval,
    Artifact,
    AuditEvent,
    Candidate,
    Decision,
    IdempotencyRecord,
    LocalCopy,
    Occurrence,
    Preflight,
    Project,
    ProjectWork,
    Provenance,
    ProviderRecord,
    Source,
    WorkFile,
    WorkMetadata,
)
from refcollector.errors import InvalidInput, InvalidState, NotFound, RevisionConflict
from refcollector.storage.db import Database
from refcollector.storage.repos import SqliteStore

T0 = datetime(2026, 1, 1, tzinfo=UTC)
PROV = Provenance(principal="tester", channel=Channel.MCP, origin=Origin.AGENT)


@pytest.fixture
def store(db: Database, settings: Settings) -> SqliteStore:
    return SqliteStore(db, settings)


def mk_project(store: SqliteStore, pid: str = "prj_a") -> Project:
    return store.projects.add(Project(project_id=pid, name=pid))


def mk_source(store: SqliteStore, pid: str = "prj_a", sid: str = "src_1", sha: str = "a" * 64) -> Source:
    return store.sources.add(
        Source(
            source_id=sid,
            project_id=pid,
            type=SourceType.TEXT,
            label=sid,
            sha256=sha,
            origin="pasted",
            preflight=Preflight(pages=1, text_coverage=1.0),
        )
    )


def mk_occ(oid: str, pid: str = "prj_a", sid: str = "src_1", order: float = 1.0, **kw: object) -> Occurrence:
    kw.setdefault("raw_text", f"Text of {oid}")
    return Occurrence(occurrence_id=oid, project_id=pid, source_id=sid, order=order, **kw)  # type: ignore[arg-type]


def mk_work(wid: str, pid: str = "prj_a", doi: str | None = None, **meta: object) -> ProjectWork:
    keys = {"doi": doi} if doi else {}
    return ProjectWork(
        work_id=wid,
        project_id=pid,
        metadata=WorkMetadata(doi=doi, **meta),
        link_keys=keys,  # type: ignore[arg-type]
    )


def mk_check(cid: str, wid: str, at: datetime, pid: str = "prj_a") -> AccessCheck:
    return AccessCheck(
        check_id=cid,
        project_id=pid,
        work_id=wid,
        checked_at=at,
        sources_consulted=["unpaywall"],
        eligibility_policy_version="eligibility-policy-v1",
        discovery=DiscoveryState.COMPLETE,
        classification=AccessClassification.ELIGIBLE_FOUND,
    )


def mk_work_file(fid: str, wid: str, pid: str = "prj_a") -> WorkFile:
    return WorkFile(
        work_file_id=fid,
        project_id=pid,
        work_id=wid,
        sha256="b" * 64,
        bytes=10,
        source_url="https://x.example/a.pdf",
        final_url="https://x.example/a.pdf",
        state=RetrievalState.VERIFIED,
        export_name="2020_smith_x.pdf",
    )


@pytest.fixture
def two_projects(store: SqliteStore) -> SqliteStore:
    for pid in ("prj_a", "prj_b"):
        mk_project(store, pid)
        mk_source(store, pid, sid=f"src_{pid}")
    return store


# ---------------------------------------------------------------- projects


def test_project_crud_and_paging(store: SqliteStore) -> None:
    for i in range(5):
        mk_project(store, f"prj_{i}")
    assert store.projects.get("prj_2").name == "prj_2"
    with pytest.raises(NotFound) as exc:
        store.projects.get("prj_x")
    assert exc.value.details == {"id": "prj_x"}

    page = store.projects.list(limit=2)
    assert [p.project_id for p in page.items] == ["prj_0", "prj_1"]
    assert page.total == 5 and page.next_after == "prj_1"
    page = store.projects.list(after=page.next_after, limit=2)
    assert [p.project_id for p in page.items] == ["prj_2", "prj_3"]
    page = store.projects.list(after=page.next_after, limit=2)
    assert [p.project_id for p in page.items] == ["prj_4"] and page.next_after is None

    p = store.projects.get("prj_1")
    updated = store.projects.update(p.model_copy(update={"name": "renamed", "download_enabled": True}))
    assert updated.name == "renamed"
    assert store.projects.get("prj_1").download_enabled is True
    with pytest.raises(NotFound):
        store.projects.update(Project(project_id="prj_x", name="x"))


def test_bump_revision(store: SqliteStore) -> None:
    mk_project(store)
    assert store.projects.bump_revision("prj_a") == 1
    assert store.projects.bump_revision("prj_a") == 2
    assert store.projects.get("prj_a").revision == 2
    # update keeps the stored revision (only bump_revision moves it)
    stale = store.projects.get("prj_a").model_copy(update={"revision": 0, "name": "n"})
    assert store.projects.update(stale).revision == 2
    with pytest.raises(NotFound):
        store.projects.bump_revision("prj_x")


# ---------------------------------------------------------------- sources


def test_source_crud_hash_revision(two_projects: SqliteStore) -> None:
    s = two_projects
    src = mk_source(s, "prj_a", sid="src_2", sha="c" * 64)
    assert s.sources.get("prj_a", "src_2") == src
    with pytest.raises(NotFound) as exc:
        s.sources.get("prj_b", "src_2")
    assert exc.value.details == {"id": "src_2"}
    assert s.sources.find_by_hash("prj_a", "c" * 64) == src
    assert s.sources.find_by_hash("prj_b", "c" * 64) is None

    up = s.sources.update(src.model_copy(update={"label": "L"}), expected_revision=0)
    assert up.revision == 1 and s.sources.get("prj_a", "src_2").label == "L"
    with pytest.raises(RevisionConflict) as exc2:
        s.sources.update(up, expected_revision=0)
    assert exc2.value.details == {"current_revisions": {"src_2": 1}}
    with pytest.raises(NotFound):
        s.sources.update(src.model_copy(update={"project_id": "prj_b"}))

    page = s.sources.list("prj_a")
    assert [x.source_id for x in page.items] == ["src_2", "src_prj_a"] and page.total == 2

    with pytest.raises(InvalidState):  # UNIQUE (project_id, sha256)
        mk_source(s, "prj_a", sid="src_3", sha="c" * 64)


# ---------------------------------------------------------------- occurrences


def test_occurrence_get_update_conflict(two_projects: SqliteStore) -> None:
    s = two_projects
    s.occurrences.add_many([mk_occ("occ_1", sid="src_prj_a"), mk_occ("occ_2", sid="src_prj_a")])
    occ = s.occurrences.get("prj_a", "occ_1")
    assert occ.revision == 1
    with pytest.raises(NotFound) as exc:
        s.occurrences.get("prj_b", "occ_1")
    assert exc.value.details == {"id": "occ_1"}
    with pytest.raises(NotFound):
        s.occurrences.get_many("prj_a", ["occ_1", "occ_x"])
    assert [o.occurrence_id for o in s.occurrences.get_many("prj_a", ["occ_2", "occ_1"])] == [
        "occ_2",
        "occ_1",
    ]

    up = s.occurrences.update(occ.model_copy(update={"edited_text": "Edited"}), expected_revision=1)
    assert up.revision == 2 and up.updated_at >= occ.updated_at
    assert s.occurrences.get("prj_a", "occ_1").text == "Edited"
    with pytest.raises(RevisionConflict) as exc2:
        s.occurrences.update(occ, expected_revision=1)
    assert exc2.value.details == {"current_revisions": {"occ_1": 2}}
    assert s.occurrences.get("prj_a", "occ_1").text == "Edited"
    assert s.occurrences.update(occ).revision == 3  # no expected revision: always bumps


def test_check_revisions_all_or_nothing(two_projects: SqliteStore) -> None:
    s = two_projects
    s.occurrences.add_many([mk_occ("occ_1", sid="src_prj_a"), mk_occ("occ_2", sid="src_prj_a")])
    s.occurrences.check_revisions("prj_a", {"occ_1": 1, "occ_2": 1})
    s.occurrences.update(s.occurrences.get("prj_a", "occ_2"))
    with pytest.raises(RevisionConflict) as exc:
        with s.transaction():
            s.occurrences.update(s.occurrences.get("prj_a", "occ_1"), expected_revision=1)
            s.occurrences.check_revisions("prj_a", {"occ_1": 2, "occ_2": 1})
    assert exc.value.details == {"current_revisions": {"occ_1": 2, "occ_2": 2}}
    # the merge transaction rolled back: occ_1 untouched
    assert s.occurrences.get("prj_a", "occ_1").revision == 1
    with pytest.raises(NotFound):
        s.occurrences.check_revisions("prj_b", {"occ_1": 1})


def _ids(page: object) -> list[str]:
    return [o.occurrence_id for o in page.items]  # type: ignore[attr-defined]


def test_occurrence_list_filters(two_projects: SqliteStore) -> None:
    s = two_projects
    mk_source(s, "prj_a", sid="src_z", sha="d" * 64)
    s.works.add(mk_work("wrk_1", year=2001))
    s.works.add(mk_work("wrk_2", year=1999))
    s.occurrences.add_many(
        [
            mk_occ(
                "occ_1", sid="src_prj_a", order=2, raw_text="Smith 2020 Neural WAVEFIELDS", work_id="wrk_1"
            ),
            mk_occ(
                "occ_2",
                sid="src_prj_a",
                order=1,
                identity=IdentityState.NEEDS_REVIEW,
                identity_reasons=[ReasonCode.DOI_TITLE_CONFLICT],
                work_id="wrk_2",
            ),
            mk_occ(
                "occ_3",
                sid="src_z",
                order=0.5,
                warnings=[ExtractionWarning.POSSIBLE_MERGED_REFERENCE],
                discovery=DiscoveryState.PARTIAL,
                classification=AccessClassification.RESTRICTED,
                local_copy=LocalCopyState.USABLE,
                retrieval=RetrievalState.VERIFIED,
            ),
            mk_occ("occ_b", pid="prj_b", sid="src_prj_b", raw_text="neural wavefields"),
        ]
    )

    def q(**kw: object) -> list[str]:
        return _ids(s.occurrences.list("prj_a", OccurrenceQuery(**kw)))  # type: ignore[arg-type]

    assert q() == ["occ_2", "occ_1", "occ_3"]  # source, then order
    assert q(source_id="src_z") == ["occ_3"]
    assert q(identity="needs_review") == ["occ_2"]
    assert q(extraction="extracted") == ["occ_2", "occ_1", "occ_3"]
    assert q(discovery="partial") == ["occ_3"]
    assert q(classification="restricted") == ["occ_3"]
    assert q(local_copy="usable") == ["occ_3"]
    assert q(retrieval="verified") == ["occ_3"]
    assert q(work_id="wrk_1") == ["occ_1"]
    assert q(ids=["occ_3", "occ_1", "occ_b"]) == ["occ_1", "occ_3"]
    assert q(reason="DOI_TITLE_CONFLICT") == ["occ_2"]
    assert q(reason="POSSIBLE_MERGED_REFERENCE") == ["occ_3"]
    assert q(text="neural wave") == ["occ_1"]
    assert q(text="100%") == []
    assert q(sort="year") == ["occ_2", "occ_1", "occ_3"]  # 1999, 2001, unknown last
    assert q(identity="needs_review", reason="DOI_TITLE_CONFLICT", source_id="src_z") == []

    s.occurrences.link_to_job("prj_a", "job_1", ["occ_3", "occ_1"])
    s.occurrences.link_to_job("prj_a", "job_1", ["occ_1"])  # idempotent
    assert q(job_id="job_1") == ["occ_1", "occ_3"]
    assert q(job_id="job_2") == []

    # reason rows follow updates
    occ2 = s.occurrences.get("prj_a", "occ_2")
    s.occurrences.update(occ2.model_copy(update={"identity_reasons": [ReasonCode.YEAR_CONFLICT]}))
    assert q(reason="DOI_TITLE_CONFLICT") == []
    assert q(reason="YEAR_CONFLICT") == ["occ_2"]
    # text in force: edited text replaces raw text
    s.occurrences.update(occ2.model_copy(update={"edited_text": "Fresh EDIT"}))
    assert q(text="fresh edit") == ["occ_2"]
    assert q(sort="updated")[-1] == "occ_2"


def test_occurrence_paging(two_projects: SqliteStore) -> None:
    s = two_projects
    s.occurrences.add_many([mk_occ(f"occ_{i}", sid="src_prj_a", order=i) for i in range(5)])
    query = OccurrenceQuery()
    seen: list[str] = []
    after = None
    while True:
        page = s.occurrences.list("prj_a", query, after=after, limit=2)
        assert page.total == 5
        seen += _ids(page)
        after = page.next_after
        if after is None:
            break
    assert seen == [f"occ_{i}" for i in range(5)]
    assert s.occurrences.list("prj_b", query).total == 0


# ---------------------------------------------------------------- candidates and decisions


def test_candidates_and_decisions(two_projects: SqliteStore) -> None:
    s = two_projects
    s.occurrences.add_many([mk_occ("occ_1", sid="src_prj_a")])

    def cand(cid: str, rank: int) -> Candidate:
        return Candidate(
            candidate_id=cid, project_id="prj_a", occurrence_id="occ_1", rank=rank, metadata=WorkMetadata()
        )

    s.candidates.replace_for_occurrence("prj_a", "occ_1", [cand("c2", 2), cand("c1", 1)])
    assert [c.candidate_id for c in s.candidates.list_for_occurrence("prj_a", "occ_1")] == ["c1", "c2"]
    s.candidates.replace_for_occurrence("prj_a", "occ_1", [cand("c3", 1)])
    assert [c.candidate_id for c in s.candidates.list_for_occurrence("prj_a", "occ_1")] == ["c3"]
    assert s.candidates.list_for_occurrence("prj_b", "occ_1") == []
    with pytest.raises(NotFound):
        s.candidates.get("prj_a", "c1")
    with pytest.raises(NotFound):
        s.candidates.get("prj_b", "c3")
    s.candidates.update(cand("c3", 1).model_copy(update={"rejected": True}))
    assert s.candidates.get("prj_a", "c3").rejected is True
    with pytest.raises(NotFound):
        s.candidates.update(cand("c9", 1))

    def dec(did: str, at: datetime) -> Decision:
        return Decision(
            decision_id=did,
            project_id="prj_a",
            occurrence_id="occ_1",
            action=DecisionAction.REJECT,
            candidate_id="c3",
            identity_after=IdentityState.REJECTED,
            provenance=PROV,
            occurrence_revision=1,
            created_at=at,
        )

    s.decisions.add(dec("d2", T0 + timedelta(seconds=1)))
    s.decisions.add(dec("d1", T0))
    assert [d.decision_id for d in s.decisions.list_for_occurrence("prj_a", "occ_1")] == ["d1", "d2"]
    assert s.decisions.list_for_occurrence("prj_b", "occ_1") == []


# ---------------------------------------------------------------- works


def test_works_link_keys_at18_at42_at43(two_projects: SqliteStore) -> None:
    s = two_projects
    doi = "10.1000/xyz"
    wa = s.works.add(mk_work("wrk_a", doi=doi, title="T"))
    wb = s.works.add(mk_work("wrk_b", pid="prj_b", doi=doi, title="T"))
    # AT-18: one ProjectWork per project per DOI, two occurrences link to it
    with pytest.raises(InvalidState):
        s.works.add(mk_work("wrk_a2", doi=doi))
    assert s.works.find_by_link_key("prj_a", "doi", doi) == wa
    s.occurrences.add_many(
        [
            mk_occ("occ_1", sid="src_prj_a", work_id="wrk_a"),
            mk_occ("occ_2", sid="src_prj_a", order=2, work_id="wrk_a"),
        ]
    )
    assert _ids(s.occurrences.list("prj_a", OccurrenceQuery(work_id="wrk_a"))) == ["occ_1", "occ_2"]

    # AT-43: a lock in A leaves B unchanged
    s.works.update(wa.model_copy(update={"locks": {"title": True}, "overrides": {"title": "Locked"}}))
    assert s.works.get("prj_a", "wrk_a").locks == {"title": True}
    assert s.works.get("prj_b", "wrk_b") == wb
    with pytest.raises(NotFound):
        s.works.get("prj_b", "wrk_a")
    with pytest.raises(NotFound):
        s.works.update(wa.model_copy(update={"project_id": "prj_b"}))

    # link key columns follow updates
    s.works.update(s.works.get("prj_a", "wrk_a").model_copy(update={"link_keys": {"openalex_id": "W1"}}))
    assert s.works.find_by_link_key("prj_a", "doi", doi) is None
    assert s.works.find_by_link_key("prj_a", "openalex_id", "W1") is not None
    assert s.works.find_by_link_key("prj_b", "openalex_id", "W1") is None
    with pytest.raises(InvalidInput):
        s.works.find_by_link_key("prj_a", "title", "x")  # type: ignore[arg-type]


def test_rejection_in_a_leaves_b(two_projects: SqliteStore) -> None:
    """AT-42 store side: candidates and decisions are per project."""
    s = two_projects
    s.occurrences.add_many([mk_occ("occ_a", sid="src_prj_a"), mk_occ("occ_b", pid="prj_b", sid="src_prj_b")])
    meta = WorkMetadata(doi="10.1/x")
    for pid, oid in (("prj_a", "occ_a"), ("prj_b", "occ_b")):
        s.candidates.replace_for_occurrence(
            pid,
            oid,
            [Candidate(candidate_id=f"c_{pid}", project_id=pid, occurrence_id=oid, rank=1, metadata=meta)],
        )
    s.candidates.update(s.candidates.get("prj_a", "c_prj_a").model_copy(update={"rejected": True}))
    occ = s.occurrences.get("prj_a", "occ_a")
    s.occurrences.update(occ.model_copy(update={"identity": IdentityState.REJECTED}))
    assert s.candidates.get("prj_b", "c_prj_b").rejected is False
    assert s.occurrences.get("prj_b", "occ_b").identity == IdentityState.UNPROCESSED


# ---------------------------------------------------------------- access checks, copies, files, artifacts


def test_access_check_supersede_at37(two_projects: SqliteStore) -> None:
    s = two_projects
    s.works.add(mk_work("wrk_1"))
    s.works.add(mk_work("wrk_b", pid="prj_b"))
    assert s.access_checks.current("prj_a", "wrk_1") is None
    s.access_checks.add_current(mk_check("acc_1", "wrk_1", T0))
    s.access_checks.add_current(mk_check("acc_b", "wrk_b", T0, pid="prj_b"))
    new = s.access_checks.add_current(mk_check("acc_2", "wrk_1", T0 + timedelta(days=1)).model_copy())
    assert new.current is True
    cur = s.access_checks.current("prj_a", "wrk_1")
    assert cur is not None and cur.check_id == "acc_2"
    hist = s.access_checks.history("prj_a", "wrk_1")
    assert [(c.check_id, c.current) for c in hist] == [("acc_2", True), ("acc_1", False)]
    cur_b = s.access_checks.current("prj_b", "wrk_b")
    assert cur_b is not None and cur_b.check_id == "acc_b"
    assert s.access_checks.history("prj_b", "wrk_1") == []


def test_local_copies_work_files_artifacts(two_projects: SqliteStore) -> None:
    s = two_projects
    s.works.add(mk_work("wrk_1"))
    lc = s.local_copies.add(
        LocalCopy(
            local_copy_id="lcp_1",
            project_id="prj_a",
            work_id="wrk_1",
            origin=LocalCopyOrigin.USER_ATTACHED,
            state=LocalCopyState.CANDIDATE,
        )
    )
    assert s.local_copies.list_for_work("prj_a", "wrk_1") == [lc]
    assert s.local_copies.list_for_work("prj_b", "wrk_1") == []
    wf = s.work_files.add(mk_work_file("wfl_1", "wrk_1"))
    assert s.work_files.list_for_work("prj_a", "wrk_1") == [wf]
    assert s.work_files.list_for_work("prj_b", "wrk_1") == []

    for i, kind in enumerate(["export", "manifest", "export"]):
        s.artifacts.add(
            Artifact(
                artifact_id=f"art_{i}",
                project_id="prj_a",
                kind=kind,
                name=f"f{i}",
                media_type="text/plain",
                bytes=1,
                sha256="e" * 64,
                project_revision=0,
            )
        )
    assert s.artifacts.get("prj_a", "art_1").kind == "manifest"
    with pytest.raises(NotFound):
        s.artifacts.get("prj_b", "art_1")
    page = s.artifacts.list("prj_a", kind="export", limit=1)
    assert [a.artifact_id for a in page.items] == ["art_0"] and page.total == 2
    page = s.artifacts.list("prj_a", kind="export", after=page.next_after, limit=1)
    assert [a.artifact_id for a in page.items] == ["art_2"] and page.next_after is None
    assert s.artifacts.list("prj_b").total == 0


def test_audit_and_provider_records(two_projects: SqliteStore) -> None:
    s = two_projects
    for i, subject in enumerate(["occ_1", None, "occ_1"]):
        s.audit.append(
            AuditEvent(
                event_id=f"evt_{i}",
                project_id="prj_a",
                type=AuditEventType.OCCURRENCE_EDITED,
                subject_id=subject,
                provenance=PROV,
                project_revision=i,
                payload={"i": i},
            )
        )
    assert [e.event_id for e in s.audit.list("prj_a")] == ["evt_0", "evt_1", "evt_2"]
    assert [e.event_id for e in s.audit.list("prj_a", subject_id="occ_1")] == ["evt_0", "evt_2"]
    assert s.audit.list("prj_b") == []

    rec = ProviderRecord(
        provider="crossref", record_key="10.1/x", fetched_at=T0, payload_hash="h1", metadata=WorkMetadata()
    )
    assert s.provider_records.get("crossref", "10.1/x") is None
    s.provider_records.upsert(rec)
    s.provider_records.upsert(rec.model_copy(update={"payload_hash": "h2"}))
    got = s.provider_records.get("crossref", "10.1/x")
    assert got is not None and got.payload_hash == "h2"


def test_idempotency_expiry_and_purge(store: SqliteStore) -> None:
    def rec(key: str, expires: datetime) -> IdempotencyRecord:
        return IdempotencyRecord(
            principal="tester",
            project_id="",
            tool="create_project",
            key=key,
            fingerprint="fp",
            result={"project_id": "prj_a"},
            created_at=T0,
            expires_at=expires,
        )

    store.idempotency.put(rec("k1", T0 + timedelta(days=7)))
    store.idempotency.put(rec("k2", T0 + timedelta(days=1)))
    got = store.idempotency.get("tester", "", "create_project", "k1", now=T0)
    assert got == rec("k1", T0 + timedelta(days=7))
    assert store.idempotency.get("other", "", "create_project", "k1", now=T0) is None
    assert store.idempotency.get("tester", "prj_a", "create_project", "k1", now=T0) is None
    later = T0 + timedelta(days=2)
    assert store.idempotency.get("tester", "", "create_project", "k2", now=later) is None
    store.idempotency.put(rec("k1", T0 + timedelta(days=8)).model_copy(update={"fingerprint": "fp2"}))
    got = store.idempotency.get("tester", "", "create_project", "k1", now=later)
    assert got is not None and got.fingerprint == "fp2"
    assert store.idempotency.purge_expired(later) == 1
    assert store.idempotency.purge_expired(later) == 0
    # non-UTC "now" compares as time, not as text: 06:00+07:00 is 23:00Z, before expiry at 00:00Z
    plus7 = datetime(2026, 1, 9, 6, 0, tzinfo=timezone(timedelta(hours=7)))
    assert store.idempotency.get("tester", "", "create_project", "k1", now=plus7) is not None


# ---------------------------------------------------------------- transactions and persistence


def test_transaction_rollback(store: SqliteStore) -> None:
    with pytest.raises(RuntimeError), store.transaction(immediate=True):
        mk_project(store)
        mk_source(store)
        store.projects.bump_revision("prj_a")
        raise RuntimeError("boom")
    with pytest.raises(NotFound):
        store.projects.get("prj_a")
    assert store.projects.list().total == 0


def test_persistence_across_reopen_w04_w30_w31(db: Database, settings: Settings) -> None:
    s = SqliteStore(db, settings)
    mk_project(s)
    mk_source(s)
    s.projects.bump_revision("prj_a")
    s.occurrences.add_many([mk_occ("occ_1")])
    occ = s.occurrences.get("prj_a", "occ_1")
    approval = Approval(
        approval_id="apr_1",
        action=DecisionAction.ACCEPT,
        candidate_id="c1",
        occurrence_revision=2,
        approved_at=T0,
        surface=ApprovalSurface.CLI_TTY,
    )
    s.occurrences.update(
        occ.model_copy(
            update={"edited_text": "E", "approval": approval, "identity": IdentityState.ACCEPTED_USER}
        )
    )
    s.works.add(mk_work("wrk_1", doi="10.1/x").model_copy(update={"locks": {"title": True}}))
    s.work_files.add(mk_work_file("wfl_1", "wrk_1"))
    db.close()

    db2 = Database(settings.db_path)
    try:
        s2 = SqliteStore(db2, settings)
        assert s2.projects.get("prj_a").revision == 1
        assert [x.source_id for x in s2.sources.list("prj_a").items] == ["src_1"]
        occ2 = s2.occurrences.get("prj_a", "occ_1")
        assert occ2.text == "E" and occ2.approval == approval and occ2.revision == 2
        assert occ2.identity == IdentityState.ACCEPTED_USER
        assert s2.works.get("prj_a", "wrk_1").locks == {"title": True}
        wf = s2.work_files.list_for_work("prj_a", "wrk_1")
        assert [(w.work_file_id, w.state) for w in wf] == [("wfl_1", RetrievalState.VERIFIED)]
    finally:
        db2.close()
