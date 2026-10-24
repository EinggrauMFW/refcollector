"""Unit tests for managed PDF upload: upload_pdf (spec 10.3, D-39)."""

from __future__ import annotations

import io

import pytest

from refcollector.contracts.api import UploadPdfIn
from refcollector.domain.enums import (
    CopyVersion,
    LocalCopyCheck,
    LocalCopyState,
    RetrievalState,
    Scope,
    SourceType,
)
from refcollector.errors import (
    EncryptedDocument,
    IdempotencyConflict,
    InvalidInput,
    LimitExceeded,
    NotAuthorized,
    NotFound,
    PrerequisiteNotMet,
    UnsupportedSource,
)
from refcollector.files import DefaultLocalCopyChecker
from tests.fixtures.pdfs import encrypted_pdf, paper_pdf, thesis_pdf
from tests.unit.services.conftest import key
from tests.unit.services.test_files_artifacts import accepted_occ


def test_upload_source_success(env):
    """Successful source PDF upload imports blob, creates Source row, and cleans staging."""
    pid = env.project()
    pdf_bytes = thesis_pdf()
    body = io.BytesIO(pdf_bytes)
    k = key()

    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="thesis.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=k,
    )
    out = env.svc.upload_pdf(env.ctx, req, body)

    assert out.source is not None
    assert out.attachment is None
    assert out.source.reused is False
    assert out.source.preflight.pages >= 3
    assert out.source.preflight.sections

    # Verify source row in database
    src = env.store.sources.get(pid, out.source_id if hasattr(out, "source_id") else out.source.source_id)
    assert src.type == SourceType.PDF
    assert src.label == "thesis.pdf"
    assert env.store.files.has_grant(pid, src.sha256)

    # Verify staging cleanup: no residual tmp files
    tmp_files = list(env.store.files.tmp.glob("*.pdf"))
    assert tmp_files == []


def test_upload_source_deduplication(env):
    """Uploading identical PDF bytes to the same project reuses the existing source."""
    pid = env.project()
    pdf_bytes = thesis_pdf()

    req1 = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="first.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=key(),
    )
    out1 = env.svc.upload_pdf(env.ctx, req1, io.BytesIO(pdf_bytes))
    assert out1.source is not None
    assert out1.source.reused is False
    rev_after_first = env.store.projects.get(pid).revision

    req2 = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="second.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=key(),
    )
    out2 = env.svc.upload_pdf(env.ctx, req2, io.BytesIO(pdf_bytes))
    assert out2.source is not None
    assert out2.source.reused is True
    assert out2.source.source_id == out1.source.source_id
    assert env.store.projects.get(pid).revision == rev_after_first


def test_upload_idempotency_replay_identical(env):
    """Replaying upload with identical key, fields, and bytes returns cached response."""
    pid = env.project()
    pdf_bytes = thesis_pdf()
    k = key()

    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="thesis.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=k,
    )
    out1 = env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))

    # Replay with identical payload and key
    out2 = env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))
    assert out2.source is not None
    assert out2.source.source_id == out1.source.source_id
    assert out2.source.reused == out1.source.reused

    # Verify no duplicate source rows
    sources = env.store.sources.list(pid).items
    assert len(sources) == 1


def test_upload_idempotency_conflict_different_bytes(env):
    """Replaying the same idempotency key with different file contents raises IdempotencyConflict."""
    pid = env.project()
    pdf_bytes_1 = thesis_pdf()
    pdf_bytes_2 = paper_pdf("Different Title", ["Author A"], "10.1234/dif")
    k = key()

    req1 = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="doc.pdf",
        declared_bytes=len(pdf_bytes_1),
        idempotency_key=k,
    )
    env.svc.upload_pdf(env.ctx, req1, io.BytesIO(pdf_bytes_1))

    req2 = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="doc.pdf",
        declared_bytes=len(pdf_bytes_2),
        idempotency_key=k,
    )
    with pytest.raises(IdempotencyConflict):
        env.svc.upload_pdf(env.ctx, req2, io.BytesIO(pdf_bytes_2))


def test_upload_idempotency_conflict_different_fields(env):
    """Replaying the same idempotency key with different input fields raises IdempotencyConflict."""
    pid = env.project()
    pdf_bytes = thesis_pdf()
    k = key()

    req1 = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="name_one.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=k,
    )
    env.svc.upload_pdf(env.ctx, req1, io.BytesIO(pdf_bytes))

    req2 = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="name_two.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=k,
    )
    with pytest.raises(IdempotencyConflict):
        env.svc.upload_pdf(env.ctx, req2, io.BytesIO(pdf_bytes))


def test_upload_attachment_success(env):
    """Uploading a matching local copy evaluates checks and marks retrieval satisfied."""
    env.svc.local_copies = DefaultLocalCopyChecker(env.store.files, env.store.work_files)
    pid = env.project()
    sid = env.text_source(pid)
    occ = accepted_occ(env, pid, sid, doi="10.1000/abc")

    pdf_bytes = paper_pdf("A title", ["Author Doe"], "10.1000/abc")
    req = UploadPdfIn(
        project_id=pid,
        purpose="local_copy",
        name="paper.pdf",
        declared_bytes=len(pdf_bytes),
        occurrence_id=occ.occurrence_id,
        version_hint=CopyVersion.PUBLISHED,
        idempotency_key=key(),
    )
    out = env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))

    assert out.source is None
    assert out.attachment is not None
    assert out.attachment.local_copy == LocalCopyState.USABLE
    assert set(out.attachment.checks) == set(LocalCopyCheck)

    updated_occ = env.store.occurrences.get(pid, occ.occurrence_id)
    assert updated_occ.local_copy == LocalCopyState.USABLE
    assert updated_occ.retrieval == RetrievalState.SATISFIED_BY_LOCAL_COPY

    # Verify staging cleanup
    assert list(env.store.files.tmp.glob("*.pdf")) == []


def test_upload_attachment_unusable_mismatched_paper(env):
    """Uploading a mismatched PDF paper fails identity check and leaves copy unusable."""
    env.svc.local_copies = DefaultLocalCopyChecker(env.store.files, env.store.work_files)
    pid = env.project()
    sid = env.text_source(pid)
    occ = accepted_occ(env, pid, sid, doi="10.1000/abc")

    # Mismatched title and DOI
    pdf_bytes = paper_pdf("Unrelated Work", ["Other Author"], "10.9999/unrelated")
    req = UploadPdfIn(
        project_id=pid,
        purpose="local_copy",
        name="paper.pdf",
        declared_bytes=len(pdf_bytes),
        occurrence_id=occ.occurrence_id,
        idempotency_key=key(),
    )
    out = env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))

    assert out.attachment is not None
    assert out.attachment.local_copy == LocalCopyState.UNUSABLE
    assert out.attachment.checks[LocalCopyCheck.IDENTITY].outcome.value == "fail"

    updated_occ = env.store.occurrences.get(pid, occ.occurrence_id)
    assert updated_occ.local_copy == LocalCopyState.UNUSABLE
    assert updated_occ.retrieval == RetrievalState.NOT_REQUESTED


def test_upload_attachment_prerequisite_not_met(env):
    """Attachment upload requires an occurrence with an accepted identity."""
    pid = env.project()
    sid = env.text_source(pid)
    raw_occ = env.occ(pid, sid)  # unaccepted

    pdf_bytes = paper_pdf("A title", ["Author Doe"], "10.1000/abc")
    req = UploadPdfIn(
        project_id=pid,
        purpose="local_copy",
        name="paper.pdf",
        declared_bytes=len(pdf_bytes),
        occurrence_id=raw_occ.occurrence_id,
        idempotency_key=key(),
    )
    with pytest.raises(PrerequisiteNotMet):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))


def test_upload_attachment_requires_occurrence_id(env):
    """Local copy upload requires occurrence_id."""
    pid = env.project()
    pdf_bytes = thesis_pdf()
    req = UploadPdfIn(
        project_id=pid,
        purpose="local_copy",
        name="paper.pdf",
        declared_bytes=len(pdf_bytes),
        occurrence_id=None,
        idempotency_key=key(),
    )
    with pytest.raises(InvalidInput):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))


def test_upload_invalid_non_pdf(env):
    """Uploading non-PDF bytes is rejected with UnsupportedSource."""
    pid = env.project()
    garbage = b"NOT_A_PDF_FILE_HEADER_GARBAGE"
    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="doc.pdf",
        declared_bytes=len(garbage),
        idempotency_key=key(),
    )
    with pytest.raises(UnsupportedSource):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(garbage))

    # Cleaned up, no rows
    assert env.store.sources.list(pid).items == []
    assert list(env.store.files.tmp.glob("*.pdf")) == []


def test_upload_encrypted_pdf(env):
    """Uploading an encrypted PDF is rejected with EncryptedDocument."""
    pid = env.project()
    enc_bytes = encrypted_pdf()
    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="encrypted.pdf",
        declared_bytes=len(enc_bytes),
        idempotency_key=key(),
    )
    with pytest.raises(EncryptedDocument):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(enc_bytes))

    assert env.store.sources.list(pid).items == []
    assert list(env.store.files.tmp.glob("*.pdf")) == []


def test_upload_oversize_declared_bytes(env):
    """Declaring size exceeding the configured limit is rejected before streaming."""
    pid = env.project()
    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="huge.pdf",
        declared_bytes=env.settings.limits.pdf_bytes + 1024,
        idempotency_key=key(),
    )
    with pytest.raises(LimitExceeded):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(b"%PDF-header"))


def test_upload_stream_exceeds_declared_bytes(env):
    """Streaming more bytes than declared is rejected during streaming."""
    pid = env.project()
    pdf_bytes = thesis_pdf()
    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="doc.pdf",
        declared_bytes=100,  # declared less than actual
        idempotency_key=key(),
    )
    with pytest.raises(LimitExceeded):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))

    assert list(env.store.files.tmp.glob("*.pdf")) == []


def test_upload_truncated_stream(env):
    """Streaming fewer bytes than declared is rejected after stream EOF."""
    pid = env.project()
    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="doc.pdf",
        declared_bytes=50000,
        idempotency_key=key(),
    )
    # Send only valid PDF header of 100 bytes
    truncated = b"%PDF-1.4 " + b"0" * 90
    with pytest.raises(InvalidInput):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(truncated))

    assert list(env.store.files.tmp.glob("*.pdf")) == []


def test_upload_safe_leaf_names(env):
    """Input names containing traversal, path separators, or control chars are rejected."""
    pid = env.project()
    pdf_bytes = thesis_pdf()

    invalid_names = [
        "..",
        ".",
        "../escape.pdf",
        "..\\escape.pdf",
        "dir/name.pdf",
        "dir\\name.pdf",
        "ctrl\x00char.pdf",
        "ctrl\nchar.pdf",
        "   ",
    ]
    for bad_name in invalid_names:
        with pytest.raises((InvalidInput, ValueError)):
            req = UploadPdfIn(
                project_id=pid,
                purpose="source",
                name=bad_name,
                declared_bytes=len(pdf_bytes),
                idempotency_key=key(),
            )
            env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))


def test_upload_scope_enforcement(env):
    """Missing appropriate scopes is rejected with NotAuthorized."""
    pid = env.project()
    pdf_bytes = thesis_pdf()

    # Purpose source requires SOURCES_IMPORT
    req_src = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="doc.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=key(),
    )
    with pytest.raises(NotAuthorized):
        env.svc.upload_pdf(env.ctx_for(Scope.REFERENCES_READ), req_src, io.BytesIO(pdf_bytes))

    # Purpose local_copy requires FILES_ATTACH
    req_att = UploadPdfIn(
        project_id=pid,
        purpose="local_copy",
        name="doc.pdf",
        declared_bytes=len(pdf_bytes),
        occurrence_id="occ_dummy",
        idempotency_key=key(),
    )
    with pytest.raises(NotAuthorized):
        env.svc.upload_pdf(env.ctx_for(Scope.SOURCES_IMPORT), req_att, io.BytesIO(pdf_bytes))


def test_upload_wrong_project(env):
    """Uploading to a missing project raises NotFound."""
    pdf_bytes = thesis_pdf()
    req = UploadPdfIn(
        project_id="nonexistent-project-id",
        purpose="source",
        name="doc.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=key(),
    )
    with pytest.raises(NotFound):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))


def test_upload_cleanup_preserves_other_staging_files(env):
    """Failure cleans only the invocation's own staging file, preserving existing tmp files."""
    pid = env.project()
    # Create an independent existing staging file
    other_tmp = env.store.files.new_tmp_path(".pdf")
    other_tmp.write_bytes(b"%PDF-other-data")

    # Run a failed upload
    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="doc.pdf",
        declared_bytes=10,
        idempotency_key=key(),
    )
    with pytest.raises(UnsupportedSource):
        env.svc.upload_pdf(env.ctx, req, io.BytesIO(b"garbage-not-pdf"))

    # The other staging file remains intact
    assert other_tmp.exists()
    other_tmp.unlink()
