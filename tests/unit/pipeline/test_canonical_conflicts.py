"""Canonical matches must agree on every supplied key before metadata merges."""

import pytest

from refcollector.contracts.ports import ResolutionResult, ScoredCandidate
from refcollector.domain.enums import IdentityState, ReasonCode
from refcollector.domain.models import WorkMetadata
from refcollector.identity.resolver import DefaultIdentityResolver
from tests.unit.pipeline.conftest import TEXT, TITLE, create, refs, run, source


@pytest.mark.parametrize("second_work", [False, True])
def test_r07_conflicting_canonical_keys_require_review_without_mutating_shared_work(
    app, monkeypatch, second_work
):
    current = {"doi": "10.1234/first", "openalex_id": "W1"}

    async def resolve(self, *args, **kwargs):
        candidate = ScoredCandidate(WorkMetadata(title=TITLE, **current), {}, [], [], True, 1.0)
        return ResolutionResult(IdentityState.ACCEPTED_AUTO, [], [candidate], 0)

    monkeypatch.setattr(DefaultIdentityResolver, "resolve", resolve)
    pid, sid = create(app)
    run(app, pid, sid, stages=("extract", "resolve"))
    first = refs(app, pid)[0]
    before = [app.store.works.get(pid, first.work_id)]
    if second_work:
        current.update(doi="10.1234/second", openalex_id="W2")
        other_sid = source(app, pid, TEXT + " second")
        run(app, pid, other_sid, stages=("extract", "resolve"))
        other = next(row for row in refs(app, pid) if row.source_id == other_sid)
        before.append(app.store.works.get(pid, other.work_id))
    current.update(doi="10.1234/first", openalex_id="W2")
    run(app, pid, ids=[first.occurrence_id], stages=("resolve",), refresh=True)
    row = app.store.occurrences.get(pid, first.occurrence_id)
    assert row.identity == IdentityState.NEEDS_REVIEW
    assert ReasonCode.PROVIDER_DISAGREEMENT in row.identity_reasons
    assert row.work_id is None
    assert [app.store.works.get(pid, work.work_id) for work in before] == before
    candidates = app.store.candidates.list_for_occurrence(pid, first.occurrence_id)
    assert ReasonCode.PROVIDER_DISAGREEMENT in candidates[0].reasons
