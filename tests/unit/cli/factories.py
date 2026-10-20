"""DTO builders for CLI tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from refcollector.contracts.api import (
    AccessView,
    CandidateView,
    GetJobOut,
    GetReferenceOut,
    IdentityView,
    JobResults,
    ListProjectsOut,
    ListReferencesOut,
    OccurrenceSummary,
    ProjectListItem,
    RevisionRef,
    StageCounts,
    UpdateReferenceOut,
)
from refcollector.domain.enums import (
    DiscoveryState,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyState,
    RetrievalState,
)
from refcollector.domain.models import Person, WorkMetadata

NOW = datetime(2026, 10, 20, tzinfo=UTC)


class FakeApp:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.started = 0
        self.closed = 0

    def start_executor(self) -> bool:
        self.started += 1
        return True

    def close(self) -> None:
        self.closed += 1


def projects(*items: tuple[str, str]) -> ListProjectsOut:
    return ListProjectsOut(
        projects=[
            ProjectListItem(project_id=pid, name=name, revision=3, counts={"occurrences": 2}, updated_at=NOW)
            for pid, name in items
        ],
        next_cursor=None,
    )


def occurrence(
    occ_id: str = "occ_1",
    revision: int = 4,
    raw_text: str = "Smith, J. (2021). Neural wavefields. Geophysics.",
    identity: IdentityState = IdentityState.NEEDS_REVIEW,
    extraction: ExtractionState = ExtractionState.EXTRACTED,
) -> OccurrenceSummary:
    return OccurrenceSummary(
        occurrence_id=occ_id,
        source_id="src_1",
        revision=revision,
        label="[1]",
        raw_text=raw_text,
        edited_text=None,
        spans=[],
        extraction=extraction,
        identity=IdentityView(state=identity, reasons=["DOI_TITLE_CONFLICT"], candidates=2),
        access=AccessView(discovery=DiscoveryState.NOT_CHECKED),
        local_copy=LocalCopyState.NONE,
        retrieval=RetrievalState.NOT_REQUESTED,
    )


def candidate(cid: str, rank: int, title: str = "Neural wavefields", rejected: bool = False) -> CandidateView:
    return CandidateView(
        candidate_id=cid,
        rank=rank,
        metadata=WorkMetadata(
            title=title, authors=[Person(family="Smith", given="J.")], year=2021, doi="10.1/x"
        ),
        features={},
        reasons=[f"Title matches candidate {cid}."],
        providers=["crossref"],
        identifier_linked=True,
        rejected=rejected,
    )


def reference(occ_id: str = "occ_1", revision: int = 4, n_candidates: int = 2, **kw: Any) -> GetReferenceOut:
    cands = [candidate(f"cand_{i}", i, title=f"Title {i}") for i in range(1, n_candidates + 1)]
    return GetReferenceOut(occurrence=occurrence(occ_id, revision, **kw), candidates=cands)


def occ_dict(occ_id: str = "occ_1", revision: int = 4, **kw: Any) -> dict[str, Any]:
    return occurrence(occ_id, revision, **kw).model_dump(mode="json")


def listing(*occs: dict[str, Any]) -> ListReferencesOut:
    return ListReferencesOut(occurrences=list(occs), next_cursor=None, total=len(occs))


def job(state: JobState, succeeded: int = 0, queued: int = 0) -> GetJobOut:
    return GetJobOut(
        job_id="job_1",
        state=state,
        stages=[StageCounts(name="extract", succeeded=succeeded, queued=queued)],
        warnings=[],
        results=JobResults(changed_filter={"job_id": "job_1"}),
        next_poll_after_ms=10,
    )


def updated(occ_id: str = "occ_1", revision: int = 5) -> UpdateReferenceOut:
    return UpdateReferenceOut(changed=[RevisionRef(occurrence_id=occ_id, revision=revision)], created=[])
