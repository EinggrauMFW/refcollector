# ruff: noqa: E501
"""list/get/add/update references, review and approval staleness.

AT-03, AT-12b, AT-13, AT-14, AT-18, AT-34, AT-39, AT-42, AT-43, W-06, W-07, W-11..W-19.
"""

from __future__ import annotations

import pytest

from refcollector.contracts.api import (
    AddReferenceIn,
    GetReferenceIn,
    ListReferencesIn,
    ReviewDecisionIn,
    UpdateReferenceIn,
)
from refcollector.domain.enums import (
    AuditEventType,
    DecisionAction,
    ExtractionState,
    IdentityState,
    Origin,
    ReasonCode,
)
from refcollector.domain.models import Span
from refcollector.errors import (
    InvalidInput,
    InvalidState,
    NotAuthorized,
    NotFound,
    RevisionConflict,
)
from tests.unit.services.conftest import key, resolution, scored


def upd(env, pid, change, ctx=None):
    req = UpdateReferenceIn(project_id=pid, change=change, idempotency_key=key())
    return env.svc.update_reference(ctx or env.ctx, req)


def ref(env, pid, oid, *sections):
    return env.svc.get_reference(
        env.ctx, GetReferenceIn(project_id=pid, occurrence_id=oid, sections=list(sections) or None)
    )


def setup(env):
    pid = env.project()
    sid = env.text_source(pid)
    return pid, sid


def select(env, pid, occ, cand, ctx=None):
    return upd(
        env,
        pid,
        {
            "type": "select_candidate",
            "occurrence_id": occ.occurrence_id,
            "candidate_id": cand.candidate_id,
            "expected_revision": env.store.occurrences.get(pid, occ.occurrence_id).revision,
        },
        ctx,
    )


def user_accept(env, pid, occ, cand):
    rev = env.store.occurrences.get(pid, occ.occurrence_id).revision
    req = ReviewDecisionIn(
        project_id=pid,
        occurrence_id=occ.occurrence_id,
        action="accept",
        candidate_id=cand.candidate_id,
        expected_revision=rev,
        idempotency_key=key(),
    )
    return env.svc.review_decision(env.tty, req)


# ---------- list / get ----------


def test_list_references_filters_sort_fields_and_paging(env):  # W-06
    pid, sid = setup(env)
    other_src = env.text_source(pid, "Other, A. (2001). Another. Venue, 1, 2-3.")
    a = env.occ(pid, sid, "Alpha, A. (2001). First title. Venue.", order=1.0)
    b = env.occ(
        pid, sid, "Beta, B. (1999). Second title. Venue.", order=2.0, identity=IdentityState.UNRESOLVED
    )
    c = env.occ(
        pid,
        sid,
        "Gamma, C. (2010). Third title. Venue.",
        order=3.0,
        identity=IdentityState.NEEDS_REVIEW,
        identity_reasons=[ReasonCode.DOI_TITLE_CONFLICT],
    )
    d = env.occ(pid, other_src, "Delta, D. (2005). Fourth. Venue.", order=1.0)

    def ids(**kw):
        out = env.svc.list_references(env.ctx, ListReferencesIn(project_id=pid, **kw))
        return [o["occurrence_id"] for o in out.occurrences]

    assert ids(filter={"source_id": sid}) == [a.occurrence_id, b.occurrence_id, c.occurrence_id]
    assert ids(filter={"identity": "unresolved"}) == [b.occurrence_id]
    assert ids(filter={"reason": "DOI_TITLE_CONFLICT"}) == [c.occurrence_id]
    assert ids(query="second title") == [b.occurrence_id]
    assert set(ids(filter={"source_id": other_src})) == {d.occurrence_id}
    assert ids(filter={"source_id": sid}, sort="year") == [b.occurrence_id, a.occurrence_id, c.occurrence_id]

    out = env.svc.list_references(
        env.ctx, ListReferencesIn(project_id=pid, filter={"source_id": sid}, fields=["raw_text", "identity"])
    )
    assert set(out.occurrences[0]) == {"occurrence_id", "raw_text", "identity"}
    assert out.total == 3
    with pytest.raises(InvalidInput):
        env.svc.list_references(env.ctx, ListReferencesIn(project_id=pid, fields=["nope"]))

    seen, cursor = [], None
    while True:
        page = env.svc.list_references(
            env.ctx, ListReferencesIn(project_id=pid, filter={"source_id": sid}, limit=2, cursor=cursor)
        )
        seen += [o["occurrence_id"] for o in page.occurrences]
        cursor = page.next_cursor
        if not cursor:
            break
    assert seen == [a.occurrence_id, b.occurrence_id, c.occurrence_id]

    # a cursor does not work with a different filter, and not across 24 h
    first = env.svc.list_references(
        env.ctx, ListReferencesIn(project_id=pid, filter={"source_id": sid}, limit=1)
    )
    with pytest.raises(InvalidInput):
        env.svc.list_references(
            env.ctx,
            ListReferencesIn(
                project_id=pid, sort="year", filter={"source_id": sid}, cursor=first.next_cursor
            ),
        )
    env.clock.advance(hours=25)
    with pytest.raises(InvalidInput):
        env.svc.list_references(
            env.ctx, ListReferencesIn(project_id=pid, filter={"source_id": sid}, cursor=first.next_cursor)
        )


def test_list_references_project_scoped(env):
    pid, sid = setup(env)
    env.occ(pid, sid)
    other = env.project("other")
    with pytest.raises(NotFound):
        env.svc.list_references(env.ctx, ListReferencesIn(project_id=other, filter={"source_id": sid}))
    assert env.svc.list_references(env.ctx, ListReferencesIn(project_id=other)).total == 0


def test_get_reference_sections(env):  # W-07
    pid, sid = setup(env)
    occ = env.occ(pid, sid, spans=[Span(page_index=211, page_label="198")])
    cand = env.candidate(pid, occ.occurrence_id)
    select(env, pid, occ, cand)
    plain = ref(env, pid, occ.occurrence_id)
    assert plain.occurrence.spans[0].page_label == "198" and plain.candidates is None
    assert plain.work.doi == "10.1/abc"
    full = ref(
        env, pid, occ.occurrence_id, "candidates", "evidence", "access", "local_copies", "files", "history"
    )
    assert full.candidates[0].candidate_id == cand.candidate_id
    assert full.access == [] and full.files == [] and full.local_copies == [] and full.evidence == []
    kinds = {h["kind"] for h in full.history}
    assert kinds == {"audit", "decision"}
    with pytest.raises(NotFound):
        env.svc.get_reference(
            env.ctx, GetReferenceIn(project_id=env.project("o"), occurrence_id=occ.occurrence_id)
        )


# ---------- add / edit / split / merge / exclude ----------


def test_add_reference_orders_between(env):  # W-15
    pid, sid = setup(env)
    a = env.occ(pid, sid, order=1.0)
    b = env.occ(pid, sid, order=2.0)

    def add(after=None, text="New, N. (2022). Added. Venue."):
        req = AddReferenceIn(
            project_id=pid, source_id=sid, text=text, after_occurrence_id=after, idempotency_key=key()
        )
        return env.svc.add_reference(env.ctx, req)

    mid = add(a.occurrence_id)
    occ = env.store.occurrences.get(pid, mid.occurrence_id)
    assert 1.0 < occ.order < 2.0 and occ.extraction == ExtractionState.CORRECTED and mid.revision == 1
    tail = env.store.occurrences.get(pid, add().occurrence_id)
    assert tail.order > b.order
    types = [e.type for e in env.store.audit.list(pid, subject_id=mid.occurrence_id)]
    assert types == [AuditEventType.OCCURRENCE_ADDED]
    with pytest.raises(NotFound):
        add("occ_missing")
    other_src = env.text_source(pid, "Other, A. (2001). Another. Venue, 1, 2-3.")
    with pytest.raises(NotFound):  # after-occurrence from another source
        env.svc.add_reference(
            env.ctx,
            AddReferenceIn(
                project_id=pid,
                source_id=other_src,
                text="x y",
                after_occurrence_id=a.occurrence_id,
                idempotency_key=key(),
            ),
        )


def test_edit_text_keeps_raw_and_bumps_revision(env):  # W-12
    pid, sid = setup(env)
    occ = env.occ(pid, sid, "Smith J 2020 Titel with typo")
    out = upd(
        env,
        pid,
        {
            "type": "edit_text",
            "occurrence_id": occ.occurrence_id,
            "text": "Smith, J. (2020). Title.",
            "expected_revision": occ.revision,
        },
    )
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert out.changed[0].revision == got.revision == occ.revision + 1
    assert got.raw_text == "Smith J 2020 Titel with typo" and got.edited_text == "Smith, J. (2020). Title."
    assert got.extraction == ExtractionState.CORRECTED
    event = env.store.audit.list(pid, subject_id=occ.occurrence_id)[-1]
    assert event.payload["previous_text"] == "Smith J 2020 Titel with typo"


def test_two_callers_edit_one_occurrence(env):  # AT-12b
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    change = {
        "type": "edit_text",
        "occurrence_id": occ.occurrence_id,
        "text": "First edit",
        "expected_revision": occ.revision,
    }
    upd(env, pid, change)
    with pytest.raises(RevisionConflict) as err:
        upd(env, pid, {**change, "text": "Second edit"})
    assert err.value.details["current_revisions"] == {occ.occurrence_id: occ.revision + 1}
    assert env.store.occurrences.get(pid, occ.occurrence_id).edited_text == "First edit"


def test_update_reference_idempotent_replay(env):  # AT-12a
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    req = UpdateReferenceIn(
        project_id=pid,
        change={
            "type": "edit_text",
            "occurrence_id": occ.occurrence_id,
            "text": "Edited",
            "expected_revision": occ.revision,
        },
        idempotency_key=key(),
    )
    first = env.svc.update_reference(env.ctx, req)
    again = env.svc.update_reference(env.ctx, req)  # would be a conflict if it ran twice
    assert again == first
    assert env.store.occurrences.get(pid, occ.occurrence_id).revision == occ.revision + 1


def test_split_creates_linked_occurrences(env):  # W-13, AT-03
    pid, sid = setup(env)
    occ = env.occ(pid, sid, "One (2001). A. Two (2002). B.", order=1.0, spans=[Span(page_index=3)])
    nxt = env.occ(pid, sid, order=2.0)
    out = upd(
        env,
        pid,
        {
            "type": "split",
            "occurrence_id": occ.occurrence_id,
            "parts": [{"text": "One (2001). A."}, {"text": "Two (2002). B.", "span": {"page_index": 4}}],
            "expected_revision": occ.revision,
        },
    )
    assert len(out.created) == 2
    new = [env.store.occurrences.get(pid, c.occurrence_id) for c in out.created]
    assert all(
        n.predecessor_ids == [occ.occurrence_id] and n.identity == IdentityState.UNPROCESSED for n in new
    )
    assert occ.order < new[0].order < new[1].order < nxt.order
    assert new[0].spans[0].page_index == 3 and new[1].spans[0].page_index == 4
    assert env.store.occurrences.get(pid, occ.occurrence_id).extraction == ExtractionState.EXCLUDED
    assert env.store.occurrences.get(pid, nxt.occurrence_id).revision == nxt.revision  # untouched


def test_merge_checks_all_revisions(env):  # W-14, AT-39
    pid, sid = setup(env)
    a = env.occ(pid, sid, "First half of a reference", order=1.0)
    b = env.occ(pid, sid, "second half.", order=2.0)
    env.store.occurrences.update(b)  # b is now at revision 2
    events = len(env.store.audit.list(pid))
    stale = {
        "type": "merge",
        "occurrence_ids": [a.occurrence_id, b.occurrence_id],
        "expected_revisions": {a.occurrence_id: a.revision, b.occurrence_id: b.revision},
    }
    with pytest.raises(RevisionConflict) as err:
        upd(env, pid, stale)
    assert err.value.details["current_revisions"][b.occurrence_id] == b.revision + 1
    assert len(env.store.audit.list(pid)) == events
    assert env.store.occurrences.get(pid, a.occurrence_id).extraction == ExtractionState.EXTRACTED

    good = {**stale, "expected_revisions": {a.occurrence_id: a.revision, b.occurrence_id: b.revision + 1}}
    out = upd(env, pid, good)
    merged = env.store.occurrences.get(pid, out.created[0].occurrence_id)
    assert merged.raw_text == "First half of a reference second half."
    assert merged.predecessor_ids == [a.occurrence_id, b.occurrence_id]
    assert {c.occurrence_id for c in out.changed} == {a.occurrence_id, b.occurrence_id}
    assert env.store.occurrences.get(pid, b.occurrence_id).extraction == ExtractionState.EXCLUDED
    # revisions must cover exactly the merged occurrences
    with pytest.raises(InvalidInput):
        upd(env, pid, {**good, "expected_revisions": {a.occurrence_id: 1}})


def test_exclude_and_restore(env):  # W-16
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    upd(
        env,
        pid,
        {
            "type": "exclude",
            "occurrence_id": occ.occurrence_id,
            "reason": "not a reference",
            "expected_revision": occ.revision,
        },
    )
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.extraction == ExtractionState.EXCLUDED and got.excluded_reason == "not a reference"
    with pytest.raises(InvalidState):  # excluded occurrences take no other change
        upd(
            env,
            pid,
            {
                "type": "edit_text",
                "occurrence_id": occ.occurrence_id,
                "text": "x y",
                "expected_revision": got.revision,
            },
        )
    upd(env, pid, {"type": "restore", "occurrence_id": occ.occurrence_id, "expected_revision": got.revision})
    back = env.store.occurrences.get(pid, occ.occurrence_id)
    assert back.extraction == ExtractionState.EXTRACTED and back.excluded_reason is None
    with pytest.raises(InvalidState):
        upd(
            env,
            pid,
            {"type": "restore", "occurrence_id": occ.occurrence_id, "expected_revision": back.revision},
        )


# ---------- candidates ----------


def test_select_candidate_is_accepted_agent(env):  # AT-13, W-17, W-11
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    out = select(env, pid, occ, cand)
    assert out.identity_state == IdentityState.ACCEPTED_AGENT
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.identity == IdentityState.ACCEPTED_AGENT and got.approval is None
    assert got.accepted_candidate_id == cand.candidate_id and got.work_id
    decision = env.store.decisions.list_for_occurrence(pid, occ.occurrence_id)[0]
    assert decision.action == DecisionAction.ACCEPT and decision.provenance.origin == Origin.AGENT
    assert decision.provenance.principal == "tester" and decision.approval is None
    audit = env.store.audit.list(pid, subject_id=occ.occurrence_id)[-1]
    assert audit.type == AuditEventType.DECISION_RECORDED and audit.provenance.origin == Origin.AGENT
    assert env.store.works.get(pid, got.work_id).metadata.doi == "10.1/abc"


def test_same_work_in_two_occurrences_is_one_project_work(env):  # AT-18
    pid, sid = setup(env)
    o1, o2 = env.occ(pid, sid), env.occ(pid, sid)
    c1, c2 = env.candidate(pid, o1.occurrence_id), env.candidate(pid, o2.occurrence_id)
    select(env, pid, o1, c1)
    select(env, pid, o2, c2)
    w1 = env.store.occurrences.get(pid, o1.occurrence_id).work_id
    assert w1 == env.store.occurrences.get(pid, o2.occurrence_id).work_id


def test_candidate_must_belong_to_occurrence(env):
    pid, sid = setup(env)
    o1, o2 = env.occ(pid, sid), env.occ(pid, sid)
    foreign = env.candidate(pid, o2.occurrence_id)
    with pytest.raises(NotFound):
        select(env, pid, o1, foreign)
    other = env.project("other")
    with pytest.raises(NotFound):
        select(env, other, o1, foreign)


def test_reject_candidate_never_reaccepted(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid, identity=IdentityState.NEEDS_REVIEW)
    c1 = env.candidate(pid, occ.occurrence_id)
    c2 = env.candidate(pid, occ.occurrence_id, doi="10.1/two", rank=2)
    upd(
        env,
        pid,
        {
            "type": "reject_candidate",
            "occurrence_id": occ.occurrence_id,
            "candidate_id": c1.candidate_id,
            "expected_revision": occ.revision,
        },
    )
    assert env.store.candidates.get(pid, c1.candidate_id).rejected is True
    assert env.store.occurrences.get(pid, occ.occurrence_id).identity == IdentityState.NEEDS_REVIEW
    with pytest.raises(InvalidState):
        select(env, pid, occ, c1)
    occ = env.store.occurrences.get(pid, occ.occurrence_id)
    upd(
        env,
        pid,
        {
            "type": "reject_candidate",
            "occurrence_id": occ.occurrence_id,
            "candidate_id": c2.candidate_id,
            "expected_revision": occ.revision,
        },
    )
    assert env.store.occurrences.get(pid, occ.occurrence_id).identity == IdentityState.REJECTED


def test_reject_accepted_candidate_unaccepts(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    select(env, pid, occ, cand)
    cur = env.store.occurrences.get(pid, occ.occurrence_id)
    upd(
        env,
        pid,
        {
            "type": "reject_candidate",
            "occurrence_id": occ.occurrence_id,
            "candidate_id": cand.candidate_id,
            "expected_revision": cur.revision,
        },
    )
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.work_id is None and got.accepted_candidate_id is None
    assert got.identity == IdentityState.REJECTED


def test_mark_unresolved(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    select(env, pid, occ, cand)
    cur = env.store.occurrences.get(pid, occ.occurrence_id)
    out = upd(
        env,
        pid,
        {"type": "mark_unresolved", "occurrence_id": occ.occurrence_id, "expected_revision": cur.revision},
    )
    assert out.identity_state == IdentityState.UNRESOLVED
    assert env.store.occurrences.get(pid, occ.occurrence_id).work_id is None
    decision = env.store.decisions.list_for_occurrence(pid, occ.occurrence_id)[-1]
    assert decision.action == DecisionAction.MARK_UNRESOLVED


# ---------- set_doi ----------


def test_set_doi_accepts_only_without_conflict(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid, "Doe, J. (2020). Linked title. Venue.")
    env.identity.result = resolution(IdentityState.ACCEPTED_AUTO, scored(), accepted_index=0)
    out = upd(
        env,
        pid,
        {
            "type": "set_doi",
            "occurrence_id": occ.occurrence_id,
            "doi": "https://doi.org/10.1000/FORCED",
            "expected_revision": occ.revision,
        },
    )
    assert out.identity_state == IdentityState.ACCEPTED_AGENT
    assert env.identity.calls[-1]["forced_doi"] == "10.1000/forced"
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert env.store.works.get(pid, got.work_id).metadata.doi == "10.1/forced"
    assert (
        env.store.decisions.list_for_occurrence(pid, occ.occurrence_id)[-1].action == DecisionAction.SET_DOI
    )
    assert len(env.store.candidates.list_for_occurrence(pid, occ.occurrence_id)) == 1
    assert env.snapshots  # identity resolver is built from the job-style snapshot


def test_set_doi_conflict_goes_to_needs_review(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid, "Doe, J. (2020). Something else. Venue.")
    conflict = scored(title="A different paper", reasons=[ReasonCode.DOI_TITLE_CONFLICT])
    env.identity.result = resolution(
        IdentityState.NEEDS_REVIEW, conflict, reasons=[ReasonCode.DOI_TITLE_CONFLICT]
    )
    out = upd(
        env,
        pid,
        {
            "type": "set_doi",
            "occurrence_id": occ.occurrence_id,
            "doi": "10.1000/forced",
            "expected_revision": occ.revision,
        },
    )
    assert out.identity_state == IdentityState.NEEDS_REVIEW
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.work_id is None and got.identity_reasons == [ReasonCode.DOI_TITLE_CONFLICT]
    assert len(env.store.candidates.list_for_occurrence(pid, occ.occurrence_id)) == 1


def test_set_doi_invalid_and_stale(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    with pytest.raises(InvalidInput):
        upd(
            env,
            pid,
            {
                "type": "set_doi",
                "occurrence_id": occ.occurrence_id,
                "doi": "not-a-doi-at-all",
                "expected_revision": occ.revision,
            },
        )
    with pytest.raises(RevisionConflict):
        upd(
            env,
            pid,
            {
                "type": "set_doi",
                "occurrence_id": occ.occurrence_id,
                "doi": "10.1000/forced",
                "expected_revision": 99,
            },
        )


# ---------- fields and locks ----------


def test_set_fields_stores_overrides(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    select(env, pid, occ, cand)
    cur = env.store.occurrences.get(pid, occ.occurrence_id)
    upd(
        env,
        pid,
        {
            "type": "set_fields",
            "occurrence_id": occ.occurrence_id,
            "fields": {"title": "Corrected", "year": 2021},
            "expected_revision": cur.revision,
        },
    )
    work = env.store.works.get(pid, cur.work_id)
    assert work.overrides == {"title": "Corrected", "year": 2021}
    assert work.metadata.title == "A title"  # provider data is kept
    assert ref(env, pid, occ.occurrence_id).work.title == "Corrected"
    cur = env.store.occurrences.get(pid, occ.occurrence_id)
    for bad in ({"openalex_id": "W1"}, {"nonsense": 1}, {}):
        with pytest.raises(InvalidInput):
            upd(
                env,
                pid,
                {
                    "type": "set_fields",
                    "occurrence_id": occ.occurrence_id,
                    "fields": bad,
                    "expected_revision": cur.revision,
                },
            )
    with pytest.raises(InvalidInput):
        upd(
            env,
            pid,
            {
                "type": "set_fields",
                "occurrence_id": occ.occurrence_id,
                "fields": {"year": "not a year"},
                "expected_revision": cur.revision,
            },
        )


def test_set_fields_without_accepted_work_creates_one(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid, identity=IdentityState.UNRESOLVED)
    upd(
        env,
        pid,
        {
            "type": "set_fields",
            "occurrence_id": occ.occurrence_id,
            "fields": {"title": "Report X", "publisher": "WHO"},
            "expected_revision": occ.revision,
        },
    )
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.identity == IdentityState.UNRESOLVED and got.work_id
    assert ref(env, pid, occ.occurrence_id).work.publisher == "WHO"


def test_locks_and_rejections_are_per_project(env):  # AT-42, AT-43
    pa, sa = setup(env)
    pb, sb = setup(env)
    oa, ob = env.occ(pa, sa), env.occ(pb, sb)
    ca, cb = env.candidate(pa, oa.occurrence_id), env.candidate(pb, ob.occurrence_id)
    select(env, pa, oa, ca)
    select(env, pb, ob, cb)
    cur = env.store.occurrences.get(pa, oa.occurrence_id)
    upd(
        env,
        pa,
        {
            "type": "set_field_locks",
            "occurrence_id": oa.occurrence_id,
            "locks": {"title": True},
            "expected_revision": cur.revision,
        },
    )
    assert ref(env, pa, oa.occurrence_id).locks == {"title": True}
    assert ref(env, pb, ob.occurrence_id).locks == {}
    wa = env.store.occurrences.get(pa, oa.occurrence_id).work_id
    wb = env.store.occurrences.get(pb, ob.occurrence_id).work_id
    assert wa != wb and env.store.works.get(pb, wb).locks == {}
    # a rejection in A leaves B alone
    cur = env.store.occurrences.get(pa, oa.occurrence_id)
    upd(
        env,
        pa,
        {
            "type": "reject_candidate",
            "occurrence_id": oa.occurrence_id,
            "candidate_id": ca.candidate_id,
            "expected_revision": cur.revision,
        },
    )
    assert env.store.candidates.get(pb, cb.candidate_id).rejected is False
    assert env.store.occurrences.get(pb, ob.occurrence_id).identity == IdentityState.ACCEPTED_AGENT
    with pytest.raises(NotFound):  # project A cannot see project B's occurrence
        upd(env, pa, {"type": "exclude", "occurrence_id": ob.occurrence_id, "expected_revision": 1})
    # unlock
    cur = env.store.occurrences.get(pb, ob.occurrence_id)
    with pytest.raises(InvalidInput):
        upd(
            env,
            pb,
            {
                "type": "set_field_locks",
                "occurrence_id": ob.occurrence_id,
                "locks": {"openalex_id": True},
                "expected_revision": cur.revision,
            },
        )


# ---------- review and approval staleness ----------


def test_review_decision_needs_tty(env):  # W-11
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    req = ReviewDecisionIn(
        project_id=pid,
        occurrence_id=occ.occurrence_id,
        action="accept",
        candidate_id=cand.candidate_id,
        expected_revision=occ.revision,
        idempotency_key=key(),
    )
    from refcollector.domain.enums import Channel, Scope

    no_tty = env.ctx_for(*Scope, channel=Channel.CLI_TTY_REVIEW, tty=False)
    for ctx in (env.ctx, env.cli, no_tty):
        with pytest.raises(NotAuthorized):
            env.svc.review_decision(ctx, req)
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.identity == IdentityState.UNPROCESSED and got.revision == occ.revision
    # and no agent route makes accepted_user
    select(env, pid, occ, cand)
    assert env.store.occurrences.get(pid, occ.occurrence_id).identity == IdentityState.ACCEPTED_AGENT


def test_tty_accept_records_approval_at_new_revision(env):  # W-18
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    out = user_accept(env, pid, occ, cand)
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert out.identity_state == IdentityState.ACCEPTED_USER == got.identity
    assert got.revision == occ.revision + 1
    assert got.approval.occurrence_revision == got.revision
    assert got.approval.candidate_id == cand.candidate_id and got.approval.surface.value == "cli_tty"
    from refcollector.domain.rules import approval_is_stale

    assert not approval_is_stale(got)
    decision = env.store.decisions.list_for_occurrence(pid, occ.occurrence_id)[-1]
    assert (
        decision.provenance.origin == Origin.INTERACTIVE
        and decision.provenance.channel.value == "cli_tty_review"
    )
    assert decision.approval == got.approval
    types = [e.type for e in env.store.audit.list(pid, subject_id=occ.occurrence_id)]
    assert AuditEventType.APPROVAL_RECORDED in types


def test_edit_after_approval_makes_it_stale(env):  # AT-34, W-19
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    user_accept(env, pid, occ, cand)
    cur = env.store.occurrences.get(pid, occ.occurrence_id)
    upd(
        env,
        pid,
        {
            "type": "edit_text",
            "occurrence_id": occ.occurrence_id,
            "text": "Doe, J. (2019). Changed text. Venue.",
            "expected_revision": cur.revision,
        },
    )
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.identity == IdentityState.NEEDS_REVIEW and ReasonCode.APPROVAL_STALE in got.identity_reasons
    assert got.approval is None
    types = [e.type for e in env.store.audit.list(pid, subject_id=occ.occurrence_id)]
    assert AuditEventType.APPROVAL_STALE in types and AuditEventType.OCCURRENCE_EDITED in types
    assert AuditEventType.APPROVAL_RECORDED in types  # the old approval stays in the trail
    assert ref(env, pid, occ.occurrence_id).occurrence.identity.reasons == ["APPROVAL_STALE"]


def test_candidate_and_field_changes_make_approval_stale(env):
    pid, sid = setup(env)
    for change in (
        lambda o, c2, r: {
            "type": "set_fields",
            "occurrence_id": o.occurrence_id,
            "fields": {"title": "Fixed"},
            "expected_revision": r,
        },
        lambda o, c2, r: {
            "type": "reject_candidate",
            "occurrence_id": o.occurrence_id,
            "candidate_id": c2.candidate_id,
            "expected_revision": r,
        },
    ):
        occ = env.occ(pid, sid)
        cand = env.candidate(pid, occ.occurrence_id)
        c2 = env.candidate(pid, occ.occurrence_id, doi="10.1/other", rank=2)
        user_accept(env, pid, occ, cand)
        cur = env.store.occurrences.get(pid, occ.occurrence_id)
        upd(env, pid, change(occ, c2, cur.revision))
        got = env.store.occurrences.get(pid, occ.occurrence_id)
        assert got.identity == IdentityState.NEEDS_REVIEW, change


def test_field_change_stales_other_approved_occurrences_of_same_work(env):
    pid, sid = setup(env)
    o1, o2 = env.occ(pid, sid), env.occ(pid, sid)
    c1, c2 = env.candidate(pid, o1.occurrence_id), env.candidate(pid, o2.occurrence_id)
    user_accept(env, pid, o1, c1)
    user_accept(env, pid, o2, c2)
    cur = env.store.occurrences.get(pid, o1.occurrence_id)
    out = upd(
        env,
        pid,
        {
            "type": "set_fields",
            "occurrence_id": o1.occurrence_id,
            "fields": {"title": "Fixed"},
            "expected_revision": cur.revision,
        },
    )
    assert {c.occurrence_id for c in out.changed} == {o1.occurrence_id, o2.occurrence_id}
    assert env.store.occurrences.get(pid, o2.occurrence_id).identity == IdentityState.NEEDS_REVIEW


def test_neutral_changes_keep_approval_valid(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    user_accept(env, pid, occ, cand)
    cur = env.store.occurrences.get(pid, occ.occurrence_id)
    upd(
        env,
        pid,
        {
            "type": "set_field_locks",
            "occurrence_id": occ.occurrence_id,
            "locks": {"title": True},
            "expected_revision": cur.revision,
        },
    )
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    from refcollector.domain.rules import approval_is_stale

    assert got.identity == IdentityState.ACCEPTED_USER and not approval_is_stale(got)


def test_review_reject(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    req = ReviewDecisionIn(
        project_id=pid,
        occurrence_id=occ.occurrence_id,
        action="reject",
        candidate_id=cand.candidate_id,
        expected_revision=occ.revision,
        idempotency_key=key(),
    )
    env.svc.review_decision(env.tty, req)
    assert env.store.candidates.get(pid, cand.candidate_id).rejected is True
    decision = env.store.decisions.list_for_occurrence(pid, occ.occurrence_id)[-1]
    assert decision.action == DecisionAction.REJECT and decision.provenance.origin == Origin.INTERACTIVE
    with pytest.raises(RevisionConflict):  # stale revision
        env.svc.review_decision(env.tty, req.model_copy(update={"idempotency_key": key()}))


@pytest.mark.parametrize("action", ["select", "review"])
def test_accept_shared_metadata_stales_other_approval(env, action):
    pid, sid = setup(env)
    first, second = env.occ(pid, sid), env.occ(pid, sid)
    original = env.candidate(pid, first.occurrence_id, title="Approved title")
    user_accept(env, pid, first, original)
    changed = env.candidate(pid, second.occurrence_id, title="Changed title")
    out = (select if action == "select" else user_accept)(env, pid, second, changed)
    assert {r.occurrence_id for r in out.changed} == {first.occurrence_id, second.occurrence_id}
    got = env.store.occurrences.get(pid, first.occurrence_id)
    assert got.identity == IdentityState.NEEDS_REVIEW
    assert ReasonCode.APPROVAL_STALE in got.identity_reasons
    assert got.approval is None
    assert AuditEventType.APPROVAL_STALE in [
        e.type for e in env.store.audit.list(pid, subject_id=first.occurrence_id)
    ]


def test_set_doi_after_approval_records_staleness(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    user_accept(env, pid, occ, cand)
    current = env.store.occurrences.get(pid, occ.occurrence_id)
    env.identity.result = resolution(IdentityState.ACCEPTED_AUTO, scored(), accepted_index=0)
    upd(
        env,
        pid,
        {
            "type": "set_doi",
            "occurrence_id": occ.occurrence_id,
            "doi": "10.1234/test",
            "expected_revision": current.revision,
        },
    )
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.identity == IdentityState.NEEDS_REVIEW
    assert ReasonCode.APPROVAL_STALE in got.identity_reasons
    assert got.approval is None
    assert AuditEventType.APPROVAL_STALE in [
        e.type for e in env.store.audit.list(pid, subject_id=occ.occurrence_id)
    ]


def test_reject_approved_candidate_records_staleness(env):
    pid, sid = setup(env)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    user_accept(env, pid, occ, cand)
    current = env.store.occurrences.get(pid, occ.occurrence_id)
    upd(
        env,
        pid,
        {
            "type": "reject_candidate",
            "occurrence_id": occ.occurrence_id,
            "candidate_id": cand.candidate_id,
            "expected_revision": current.revision,
        },
    )
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.identity == IdentityState.NEEDS_REVIEW
    assert ReasonCode.APPROVAL_STALE in got.identity_reasons
    assert AuditEventType.APPROVAL_STALE in [
        e.type for e in env.store.audit.list(pid, subject_id=occ.occurrence_id)
    ]


def test_accept_same_shared_metadata_preserves_other_approval(env):
    pid, sid = setup(env)
    first, second = env.occ(pid, sid), env.occ(pid, sid)
    original = env.candidate(pid, first.occurrence_id)
    user_accept(env, pid, first, original)
    before = env.store.occurrences.get(pid, first.occurrence_id)
    same = env.candidate(pid, second.occurrence_id)
    out = select(env, pid, second, same)
    assert [r.occurrence_id for r in out.changed] == [second.occurrence_id]
    assert env.store.occurrences.get(pid, first.occurrence_id) == before
