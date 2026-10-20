# ruff: noqa: E501
"""Service wiring against the real local-copy checker, and failure semantics."""

from __future__ import annotations

import pytest

from refcollector.contracts.api import AttachFileIn, UpdateReferenceIn
from refcollector.domain.enums import LocalCopyCheck, LocalCopyState, RetrievalState
from refcollector.errors import RevisionConflict
from refcollector.files import DefaultLocalCopyChecker
from tests.fixtures.pdfs import paper_pdf, write
from tests.unit.services.conftest import key
from tests.unit.services.test_files_artifacts import accepted_occ


def attach(env, pid, occ, path):
    req = AttachFileIn(project_id=pid, occurrence_id=occ.occurrence_id, path=str(path), idempotency_key=key())
    return env.svc.attach_file(env.ctx, req)


def test_attach_with_real_checker(env):
    env.svc.local_copies = DefaultLocalCopyChecker(env.store.files, env.store.work_files)
    pid = env.project()
    sid = env.text_source(pid)
    occ = accepted_occ(env, pid, sid, doi="10.1000/abc")
    good = write(env.allowed, "good.pdf", paper_pdf("A title", ["Version of record"], "10.1000/abc"))
    out = attach(env, pid, occ, good)
    assert out.local_copy == LocalCopyState.USABLE
    assert (
        env.store.occurrences.get(pid, occ.occurrence_id).retrieval == RetrievalState.SATISFIED_BY_LOCAL_COPY
    )
    other = accepted_occ(env, pid, sid, doi="10.1000/abc")
    wrong = write(env.allowed, "wrong.pdf", paper_pdf("A completely different paper", ["Roe"], "10.9/zzz"))
    out = attach(env, pid, other, wrong)
    assert out.local_copy == LocalCopyState.UNUSABLE
    assert out.checks[LocalCopyCheck.IDENTITY].outcome.value == "fail"


def test_failed_mutation_does_not_burn_the_key(env):
    pid = env.project()
    sid = env.text_source(pid)
    occ = env.occ(pid, sid)
    k = key()
    base = {"type": "edit_text", "occurrence_id": occ.occurrence_id, "text": "Edited text"}
    with pytest.raises(RevisionConflict):
        env.svc.update_reference(
            env.ctx,
            UpdateReferenceIn(project_id=pid, change={**base, "expected_revision": 9}, idempotency_key=k),
        )
    assert env.store.occurrences.get(pid, occ.occurrence_id).edited_text is None
    env.svc.update_reference(
        env.ctx, UpdateReferenceIn(project_id=pid, change={**base, "expected_revision": 1}, idempotency_key=k)
    )
    assert env.store.occurrences.get(pid, occ.occurrence_id).edited_text == "Edited text"
