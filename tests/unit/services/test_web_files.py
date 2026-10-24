"""Unit tests for binary file reads: read_project_file (sources, work_files, artifacts)."""

from __future__ import annotations

import pytest

from refcollector.contracts.api import (
    CallContext,
    CreateProjectIn,
    ImportSourceIn,
    ReadProjectFileIn,
    TextInput,
)
from refcollector.domain.enums import Channel, CopyVersion, LocalCopyOrigin, LocalCopyState, Scope
from refcollector.domain.models import Artifact, LocalCopy, ProjectWork, WorkFile, WorkMetadata, utcnow
from refcollector.errors import FileValidationFailed, InvalidInput, NotAuthorized, NotFound
from refcollector.ids import new_id
from tests.unit.services.conftest import key


def create_project(env, name="proj-files"):
    return env.svc.create_project(env.ctx, CreateProjectIn(name=name, idempotency_key=key())).project_id


def test_read_project_file_source(env):
    pid = create_project(env, "proj-source-read")
    sample_text = "This is a test source text for binary read."
    src = env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid,
            input=TextInput(type="text", text=sample_text),
            idempotency_key=key(),
        ),
    )

    # Read the full source
    req = ReadProjectFileIn(
        project_id=pid,
        kind="source",
        resource_id=src.source_id,
        offset=0,
        length=65536,
    )
    out = env.svc.read_project_file(env.ctx, req)
    assert out.data == sample_text.encode("utf-8")
    assert out.descriptor.resource_id == src.source_id
    assert out.descriptor.kind == "source"
    assert out.descriptor.bytes == len(sample_text.encode("utf-8"))
    assert out.eof is True
    assert out.next_offset == len(sample_text.encode("utf-8"))

    # Read sliced
    req_sliced = ReadProjectFileIn(
        project_id=pid,
        kind="source",
        resource_id=src.source_id,
        offset=5,
        length=10,
    )
    out_sliced = env.svc.read_project_file(env.ctx, req_sliced)
    assert out_sliced.data == sample_text.encode("utf-8")[5:15]
    assert out_sliced.next_offset == 15
    assert out_sliced.eof is False

    # Read at EOF (offset == size)
    req_eof = ReadProjectFileIn(
        project_id=pid,
        kind="source",
        resource_id=src.source_id,
        offset=len(sample_text.encode("utf-8")),
        length=10,
    )
    out_eof = env.svc.read_project_file(env.ctx, req_eof)
    assert out_eof.data == b""
    assert out_eof.eof is True
    assert out_eof.next_offset == len(sample_text.encode("utf-8"))

    # Read beyond EOF (offset > size) raises InvalidInput
    req_beyond = ReadProjectFileIn(
        project_id=pid,
        kind="source",
        resource_id=src.source_id,
        offset=len(sample_text.encode("utf-8")) + 10,
        length=10,
    )
    with pytest.raises(InvalidInput):
        env.svc.read_project_file(env.ctx, req_beyond)


def test_read_project_file_artifact(env):
    pid = create_project(env, "proj-art-read")
    blob_path = env.store.files.new_tmp_path(".txt")
    data = b"artifact-binary-payload-12345"
    blob_path.write_bytes(data)
    blob = env.store.files.commit(blob_path, "text/plain")
    env.store.files.grant(pid, blob.sha256)

    art = env.store.artifacts.add(
        Artifact(
            artifact_id=new_id("artifact"),
            project_id=pid,
            job_id=new_id("job"),
            kind="export",
            name="export.txt",
            media_type="text/plain",
            bytes=len(data),
            sha256=blob.sha256,
            project_revision=1,
            created_at=utcnow(),
        )
    )

    out = env.svc.read_project_file(
        env.ctx,
        ReadProjectFileIn(
            project_id=pid,
            kind="artifact",
            resource_id=art.artifact_id,
            offset=0,
            length=10,
        ),
    )
    assert out.data == data[:10]
    assert out.descriptor.resource_id == art.artifact_id
    assert out.descriptor.kind == "artifact"
    assert out.descriptor.bytes == len(data)
    assert out.next_offset == 10
    assert out.eof is False


def test_read_project_file_work_file(env):
    pid = create_project(env, "proj-workfile-read")
    blob_path = env.store.files.new_tmp_path(".pdf")
    pdf_bytes = b"%PDF-1.4 dummy pdf bytes"
    blob_path.write_bytes(pdf_bytes)
    blob = env.store.files.commit(blob_path, "application/pdf")
    env.store.files.grant(pid, blob.sha256)

    from refcollector.domain.enums import RetrievalState
    from refcollector.domain.models import ProjectWork, WorkMetadata

    work = env.store.works.add(
        ProjectWork(
            work_id=new_id("work"),
            project_id=pid,
            metadata=WorkMetadata(title="Test Paper", year=2024),
            updated_at=utcnow(),
        )
    )

    wf = env.store.work_files.add(
        WorkFile(
            work_file_id=new_id("work_file"),
            project_id=pid,
            work_id=work.work_id,
            sha256=blob.sha256,
            bytes=len(pdf_bytes),
            source_url="https://example.org/paper.pdf",
            final_url="https://example.org/paper.pdf",
            state=RetrievalState.VERIFIED,
            export_name="paper.pdf",
            retrieved_at=utcnow(),
        )
    )

    out = env.svc.read_project_file(
        env.ctx,
        ReadProjectFileIn(
            project_id=pid,
            kind="work_file",
            resource_id=wf.work_file_id,
            offset=0,
            length=65536,
        ),
    )
    assert out.data == pdf_bytes
    assert out.descriptor.resource_id == wf.work_file_id
    assert out.descriptor.kind == "work_file"
    assert out.descriptor.bytes == len(pdf_bytes)
    assert out.descriptor.media_type == "application/pdf"
    assert out.eof is True
    assert out.eof is True


def test_read_project_file_cross_project_isolation(env):
    pid1 = create_project(env, "proj-iso-1")
    pid2 = create_project(env, "proj-iso-2")

    src = env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid1,
            input=TextInput(type="text", text="secret document"),
            idempotency_key=key(),
        ),
    )

    # Attempt to read resource belonging to project 1 using project 2 ID
    with pytest.raises(NotFound):
        env.svc.read_project_file(
            env.ctx,
            ReadProjectFileIn(
                project_id=pid2,
                kind="source",
                resource_id=src.source_id,
            ),
        )


def test_read_project_file_scopes(env):
    pid = create_project(env, "proj-scopes")
    src = env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid,
            input=TextInput(type="text", text="scope test document"),
            idempotency_key=key(),
        ),
    )

    # Context with no references:read scope fails reading source
    no_ref_ctx = CallContext(
        principal="tester",
        channel=Channel.MCP,
        scopes=frozenset({Scope.PROJECTS_READ}),
        request_id="req_no_ref",
    )
    with pytest.raises(NotAuthorized):
        env.svc.read_project_file(
            no_ref_ctx,
            ReadProjectFileIn(
                project_id=pid,
                kind="source",
                resource_id=src.source_id,
            ),
        )


def test_read_project_file_storage_integrity_corrupted(env):
    pid = create_project(env, "proj-corrupt")
    src = env.svc.import_source(
        env.ctx,
        ImportSourceIn(
            project_id=pid,
            input=TextInput(type="text", text="intact content"),
            idempotency_key=key(),
        ),
    )
    source_obj = env.store.sources.get(pid, src.source_id)
    path = env.store.files.path_for(pid, source_obj.sha256).resolve()
    # Corrupt file content on disk
    path.write_bytes(b"corrupted bytes different hash")

    with pytest.raises(FileValidationFailed):
        env.svc.read_project_file(
            env.ctx,
            ReadProjectFileIn(
                project_id=pid,
                kind="source",
                resource_id=src.source_id,
            ),
        )


def test_read_project_file_local_copy(env):
    pid = create_project(env, "proj-localcopy-read")
    blob_path = env.store.files.new_tmp_path(".pdf")
    pdf_bytes = b"%PDF-1.4 sample local copy binary bytes"
    blob_path.write_bytes(pdf_bytes)
    blob = env.store.files.commit(blob_path, "application/pdf")
    env.store.files.grant(pid, blob.sha256)

    work = env.store.works.add(
        ProjectWork(
            work_id=new_id("work"),
            project_id=pid,
            metadata=WorkMetadata(title="Local Copy Test Paper", year=2024),
            updated_at=utcnow(),
        )
    )

    lc = env.store.local_copies.add(
        LocalCopy(
            local_copy_id=new_id("local_copy"),
            project_id=pid,
            work_id=work.work_id,
            occurrence_id="occ_test_1",
            origin=LocalCopyOrigin.USER_ATTACHED,
            sha256=blob.sha256,
            version=CopyVersion.ACCEPTED,
            state=LocalCopyState.USABLE,
            created_at=utcnow(),
        )
    )

    # 1. Full read of managed local copy
    out = env.svc.read_project_file(
        env.ctx,
        ReadProjectFileIn(
            project_id=pid,
            kind="local_copy",
            resource_id=lc.local_copy_id,
            offset=0,
            length=65536,
        ),
    )
    assert out.data == pdf_bytes
    assert out.descriptor.resource_id == lc.local_copy_id
    assert out.descriptor.kind == "local_copy"
    assert out.descriptor.name == f"{lc.local_copy_id}.pdf"
    assert out.descriptor.media_type == "application/pdf"
    assert out.descriptor.bytes == len(pdf_bytes)
    assert out.descriptor.sha256 == blob.sha256
    assert out.eof is True
    assert out.next_offset == len(pdf_bytes)

    # 2. Sliced read
    out_sliced = env.svc.read_project_file(
        env.ctx,
        ReadProjectFileIn(
            project_id=pid,
            kind="local_copy",
            resource_id=lc.local_copy_id,
            offset=5,
            length=10,
        ),
    )
    assert out_sliced.data == pdf_bytes[5:15]
    assert out_sliced.next_offset == 15
    assert out_sliced.eof is False

    # 3. Read at EOF
    out_eof = env.svc.read_project_file(
        env.ctx,
        ReadProjectFileIn(
            project_id=pid,
            kind="local_copy",
            resource_id=lc.local_copy_id,
            offset=len(pdf_bytes),
            length=10,
        ),
    )
    assert out_eof.data == b""
    assert out_eof.eof is True

    # 4. Unknown local copy ID -> NotFound
    with pytest.raises(NotFound):
        env.svc.read_project_file(
            env.ctx,
            ReadProjectFileIn(
                project_id=pid,
                kind="local_copy",
                resource_id="lcp_nonexistent",
            ),
        )

    # 5. Wrong project ID -> NotFound
    pid_other = create_project(env, "proj-other-lc")
    with pytest.raises(NotFound):
        env.svc.read_project_file(
            env.ctx,
            ReadProjectFileIn(
                project_id=pid_other,
                kind="local_copy",
                resource_id=lc.local_copy_id,
            ),
        )

    # 6. Local copy with missing sha256 -> NotFound
    lc_no_sha = env.store.local_copies.add(
        LocalCopy(
            local_copy_id=new_id("local_copy"),
            project_id=pid,
            work_id=work.work_id,
            occurrence_id="occ_test_2",
            origin=LocalCopyOrigin.USER_ATTACHED,
            sha256=None,
            version=CopyVersion.UNKNOWN,
            state=LocalCopyState.UNUSABLE,
            created_at=utcnow(),
        )
    )
    with pytest.raises(NotFound):
        env.svc.read_project_file(
            env.ctx,
            ReadProjectFileIn(
                project_id=pid,
                kind="local_copy",
                resource_id=lc_no_sha.local_copy_id,
            ),
        )

    # 7. Tampered local copy bytes on disk -> FileValidationFailed
    path = env.store.files.path_for(pid, blob.sha256).resolve()
    path.write_bytes(b"tampered content that invalidates hash")
    with pytest.raises(FileValidationFailed):
        env.svc.read_project_file(
            env.ctx,
            ReadProjectFileIn(
                project_id=pid,
                kind="local_copy",
                resource_id=lc.local_copy_id,
            ),
        )

    # 8. Scope without REFERENCES_READ -> NotAuthorized
    no_ref_ctx = CallContext(
        principal="tester",
        channel=Channel.MCP,
        scopes=frozenset({Scope.PROJECTS_READ}),
        request_id="req_no_ref_lc",
    )
    with pytest.raises(NotAuthorized):
        env.svc.read_project_file(
            no_ref_ctx,
            ReadProjectFileIn(
                project_id=pid,
                kind="local_copy",
                resource_id=lc.local_copy_id,
            ),
        )
