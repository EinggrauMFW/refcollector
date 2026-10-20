# ruff: noqa: E501
"""resolve_citation, attach_file, artifacts (W-22, W-23, W-28, AT-26)."""

from __future__ import annotations

import pytest

from refcollector.contracts.api import (
    AttachFileIn,
    ListArtifactsIn,
    ReadArtifactIn,
    ResolveCitationIn,
)
from refcollector.domain.enums import (
    AccessClassification,
    AuditEventType,
    IdentityState,
    LocalCopyCheck,
    LocalCopyState,
    ReasonCode,
    RetrievalState,
    Scope,
)
from refcollector.domain.models import Artifact
from refcollector.errors import (
    InvalidInput,
    NotAuthorized,
    NotFound,
    PathNotAllowed,
    PrerequisiteNotMet,
)
from refcollector.ids import new_id
from tests.fixtures.pdfs import paper_pdf, write
from tests.unit.services.conftest import key, resolution, scored


def attach(env, pid, occ, path, **kw):
    req = AttachFileIn(
        project_id=pid, occurrence_id=occ.occurrence_id, path=str(path), idempotency_key=key(), **kw
    )
    return env.svc.attach_file(env.ctx, req)


def accepted_occ(env, pid, sid, doi="10.1/abc"):
    occ = env.occ(pid, sid, classification=AccessClassification.ELIGIBLE_FOUND)
    cand = env.candidate(pid, occ.occurrence_id, doi=doi)
    from refcollector.contracts.api import UpdateReferenceIn

    env.svc.update_reference(
        env.ctx,
        UpdateReferenceIn(
            project_id=pid,
            change={
                "type": "select_candidate",
                "occurrence_id": occ.occurrence_id,
                "candidate_id": cand.candidate_id,
                "expected_revision": occ.revision,
            },
            idempotency_key=key(),
        ),
    )
    return env.store.occurrences.get(pid, occ.occurrence_id)


# ---------- resolve_citation ----------


def test_resolve_citation_hints_and_stores_nothing(env):
    pid = env.project()
    before = env.store.projects.get(pid).revision
    cases = [
        (resolution(IdentityState.ACCEPTED_AUTO, scored(), accepted_index=0), "strong"),
        (resolution(IdentityState.NEEDS_REVIEW, scored(reasons=[ReasonCode.GENERIC_TITLE])), "needs_review"),
        (resolution(IdentityState.UNRESOLVED), "unresolved"),
    ]
    for result, hint in cases:
        env.identity.result = result
        out = env.svc.resolve_citation(env.ctx, ResolveCitationIn(citation="Doe 2020 Title"))
        assert out.decision_hint == hint
    assert out.candidates == [] and out.meta.project_revision is None
    env.identity.result = cases[1][0]
    out = env.svc.resolve_citation(
        env.ctx, ResolveCitationIn(fields={"title": "T", "authors": ["Doe"], "year": 2020})
    )
    assert out.candidates[0].rank == 1 and out.candidates[0].reasons == ["GENERIC_TITLE"]
    assert env.identity.calls[-1]["fields"].title == "T"
    assert env.store.projects.get(pid).revision == before


def test_resolve_citation_validation_and_scope(env):
    with pytest.raises(InvalidInput):
        env.svc.resolve_citation(env.ctx, ResolveCitationIn())
    with pytest.raises(NotAuthorized):
        env.svc.resolve_citation(env.ctx_for(Scope.PROJECTS_READ), ResolveCitationIn(citation="x y"))
    env.settings.email = None
    from refcollector.errors import ConfigMissingEmail

    with pytest.raises(ConfigMissingEmail):
        env.svc.resolve_citation(env.ctx, ResolveCitationIn(citation="x y"))


# ---------- attach_file ----------


def test_attach_valid_copy_satisfies_retrieval(env):  # W-22, AT-26
    pid = env.project()
    sid = env.text_source(pid)
    occ = accepted_occ(env, pid, sid)
    path = write(env.allowed, "paper.pdf", paper_pdf("A title", ["Doe"], "10.1/abc"))
    out = attach(env, pid, occ, path, version_hint="published")
    assert out.local_copy == LocalCopyState.USABLE
    assert set(out.checks) == set(LocalCopyCheck)
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.local_copy == LocalCopyState.USABLE
    assert got.retrieval == RetrievalState.SATISFIED_BY_LOCAL_COPY
    assert got.classification == AccessClassification.ELIGIBLE_FOUND  # unchanged
    assert env.local.calls[-1]["hint"].value == "published"
    assert env.local.calls[-1]["path"] == path.resolve()
    stored = env.store.local_copies.list_for_work(pid, got.work_id)
    assert [c.local_copy_id for c in stored] == [out.local_copy_id]
    assert stored[0].occurrence_id == occ.occurrence_id
    events = [e.type for e in env.store.audit.list(pid, subject_id=occ.occurrence_id)]
    assert AuditEventType.LOCAL_COPY_EVALUATED in events


def test_attach_wrong_paper_is_unusable(env):  # W-23
    pid = env.project()
    sid = env.text_source(pid)
    occ = accepted_occ(env, pid, sid)
    env.local.fail = LocalCopyCheck.IDENTITY
    path = write(env.allowed, "other.pdf", paper_pdf("Other", ["Roe"], None))
    out = attach(env, pid, occ, path)
    assert out.local_copy == LocalCopyState.UNUSABLE
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.local_copy == LocalCopyState.UNUSABLE and got.retrieval == RetrievalState.NOT_REQUESTED


def test_attach_rules(env, tmp_path):
    pid = env.project()
    sid = env.text_source(pid)
    path = write(env.allowed, "p.pdf", paper_pdf("A title", ["Doe"], None))
    raw = env.occ(pid, sid)
    with pytest.raises(PrerequisiteNotMet):
        attach(env, pid, raw, path)
    occ = accepted_occ(env, pid, sid)
    outside = write(tmp_path, "out.pdf", paper_pdf("A title", ["Doe"], None))
    with pytest.raises(PathNotAllowed):
        attach(env, pid, occ, outside)
    with pytest.raises(PathNotAllowed):
        attach(env, pid, occ, env.allowed / ".." / "out.pdf")
    with pytest.raises(NotFound):
        attach(env, pid, occ, env.allowed / "gone.pdf")
    with pytest.raises(NotAuthorized):
        env.svc.attach_file(
            env.ctx_for(Scope.REFERENCES_READ),
            AttachFileIn(
                project_id=pid, occurrence_id=occ.occurrence_id, path=str(path), idempotency_key=key()
            ),
        )
    with pytest.raises(NotFound):  # occurrence of another project
        attach(env, env.project("o"), occ, path)


def test_attach_keeps_user_approval_valid(env):
    from refcollector.contracts.api import ReviewDecisionIn
    from refcollector.domain.rules import approval_is_stale

    pid = env.project()
    sid = env.text_source(pid)
    occ = env.occ(pid, sid)
    cand = env.candidate(pid, occ.occurrence_id)
    env.svc.review_decision(
        env.tty,
        ReviewDecisionIn(
            project_id=pid,
            occurrence_id=occ.occurrence_id,
            action="accept",
            candidate_id=cand.candidate_id,
            expected_revision=occ.revision,
            idempotency_key=key(),
        ),
    )
    path = write(env.allowed, "p.pdf", paper_pdf("A title", ["Doe"], None))
    attach(env, pid, occ, path)
    got = env.store.occurrences.get(pid, occ.occurrence_id)
    assert got.identity == IdentityState.ACCEPTED_USER and not approval_is_stale(got)


# ---------- artifacts ----------


def add_artifact(env, pid, name, data, media="text/markdown", kind="export", job_id=None):
    tmp = env.store.files.new_tmp_path(".bin")
    tmp.write_bytes(data)
    blob = env.store.files.commit(tmp, media)
    env.store.files.grant(pid, blob.sha256)
    return env.store.artifacts.add(
        Artifact(
            artifact_id=new_id("artifact"),
            project_id=pid,
            kind=kind,
            name=name,
            media_type=media,
            bytes=blob.bytes,
            sha256=blob.sha256,
            project_revision=1,
            job_id=job_id,
        )
    )


def test_list_artifacts_and_descriptor_path(env):
    pid = env.project()
    a = add_artifact(env, pid, "audit.md", b"# report")
    b = add_artifact(env, pid, "refs.bib", b"@article{}", media="application/x-bibtex", kind="bibtex")
    out = env.svc.list_artifacts(env.ctx, ListArtifactsIn(project_id=pid))
    assert [x.artifact_id for x in out.artifacts] == sorted([a.artifact_id, b.artifact_id])
    first = out.artifacts[0]
    assert first.path.startswith("file://") and first.sha256 and first.bytes
    only = env.svc.list_artifacts(env.ctx, ListArtifactsIn(project_id=pid, kind="bibtex"))
    assert [x.artifact_id for x in only.artifacts] == [b.artifact_id]
    paged = env.svc.list_artifacts(env.ctx, ListArtifactsIn(project_id=pid, limit=1))
    assert paged.next_cursor
    assert env.svc.list_artifacts(env.ctx, ListArtifactsIn(project_id=env.project("o"))).artifacts == []


def test_read_artifact_in_bounded_chunks(env):  # W-28
    pid = env.project()
    text = ("Zażółć gęślą jaźń. " * 8000) + "end"
    art = add_artifact(env, pid, "audit.md", text.encode("utf-8"))
    raw = text.encode("utf-8")
    got, offset = b"", 0
    while True:
        out = env.svc.read_artifact(
            env.ctx, ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id, offset=offset)
        )
        chunk = out.text.encode("utf-8")
        assert 0 < len(chunk) <= 65536
        got, offset = got + chunk, offset + len(chunk)
        if out.eof:
            break
    assert got == raw and len(raw) > 65536
    small = env.svc.read_artifact(
        env.ctx, ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id, offset=0, length=10)
    )
    assert len(small.text.encode()) <= 10 and small.eof is False
    past = env.svc.read_artifact(
        env.ctx, ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id, offset=len(raw) + 5)
    )
    assert past.text == "" and past.eof is True


def test_read_binary_artifact_returns_descriptor_only(env):
    pid = env.project()
    art = add_artifact(env, pid, "x.pdf", b"%PDF-1.4 binary", media="application/pdf", kind="draft_eml")
    out = env.svc.read_artifact(env.ctx, ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id))
    assert out.text is None and out.eof is None and out.descriptor.media_type == "application/pdf"


def test_read_artifact_cross_project_and_scope(env):
    pid = env.project()
    art = add_artifact(env, pid, "a.md", b"hello")
    with pytest.raises(NotFound):
        env.svc.read_artifact(
            env.ctx, ReadArtifactIn(project_id=env.project("o"), artifact_id=art.artifact_id)
        )
    with pytest.raises(NotAuthorized):
        env.svc.read_artifact(env.ctx_for(), ReadArtifactIn(project_id=pid, artifact_id=art.artifact_id))


def test_job_results_list_artifact_ids(env):
    from refcollector.contracts.api import CreateExportIn, GetJobIn

    pid = env.project()
    rev = env.store.projects.get(pid).revision
    job = env.svc.create_export(
        env.ctx, CreateExportIn(project_id=pid, project_revision=rev, formats=["json"], idempotency_key=key())
    )
    art = add_artifact(env, pid, "full.json", b"{}", media="application/json", job_id=job.job_id)
    add_artifact(env, pid, "other.json", b"{}", media="application/json")
    got = env.svc.get_job(env.ctx, GetJobIn(project_id=pid, job_id=job.job_id))
    assert got.results.artifact_ids == [art.artifact_id]
