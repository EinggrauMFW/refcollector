"""All five local-copy checks; pipeline transitions remain outside this package."""

import pytest

from refcollector.domain.enums import (
    CheckOutcome,
    CopyVersion,
    LocalCopyCheck,
    LocalCopyOrigin,
    LocalCopyState,
    RetrievalState,
    VersionPolicy,
)
from refcollector.domain.models import WorkFile
from refcollector.files import DefaultLocalCopyChecker
from tests.fixtures.pdfs import paper_pdf
from tests.unit.files.test_retrieval import DOI, TITLE, work


def attached(tmp_path, text=TITLE, doi=DOI, version="Version of record"):
    path = tmp_path / "attached.pdf"
    path.write_bytes(paper_pdf(text, [version], doi))
    return path


def managed(store, data, *, project="prj_a", work_id="wrk_a", state=RetrievalState.VERIFIED, grant=True):
    path = store.files.new_tmp_path(".pdf")
    path.write_bytes(data)
    blob = store.files.commit(path, "application/pdf")
    if grant:
        store.files.grant(project, blob.sha256)
    wf = WorkFile(
        work_file_id="wfl_" + str(len(store.work_files.rows)),
        project_id=project,
        work_id=work_id,
        sha256=blob.sha256,
        bytes=blob.bytes,
        source_url="https://repo.example/a",
        final_url="https://repo.example/a",
        state=state,
        export_name="a.pdf",
    )
    store.work_files.add(wf)
    return wf


def checker(store, **kwargs):
    return DefaultLocalCopyChecker(store.files, store.work_files, **kwargs)


def test_at26_w22_valid_attachment_usable_copied_unsaved_and_unchanged(fake_store, tmp_path):
    accepted = work()
    accepted.metadata.title = "Old title"
    accepted.overrides = {"title": TITLE}
    accepted.locks = {"title": True}
    before = accepted.model_dump()
    path = attached(tmp_path)
    copies = checker(fake_store).evaluate(
        "prj_a", accepted, version_policy=VersionPolicy.PREFER_PUBLISHED, attached_path=path
    )
    copy = next(c for c in copies if c.origin == LocalCopyOrigin.USER_ATTACHED)
    assert copy.state == LocalCopyState.USABLE and copy.failed_check is None
    assert set(copy.checks) == set(LocalCopyCheck)
    assert all(check.outcome == CheckOutcome.PASS for check in copy.checks.values())
    assert fake_store.files.path_for("prj_a", copy.sha256).read_bytes() == path.read_bytes()
    assert fake_store.local_copies.list_for_work("prj_a", "wrk_a") == []
    assert fake_store.work_files.list_for_work("prj_a", "wrk_a") == []
    assert accepted.model_dump() == before and list(fake_store.files.tmp.iterdir()) == []


@pytest.mark.parametrize(
    "kind,failed",
    [
        ("unreadable", LocalCopyCheck.READABLE),
        ("wrong_work", LocalCopyCheck.IDENTITY),
        ("preprint", LocalCopyCheck.VERSION),
        ("unknown", LocalCopyCheck.VERSION),
        ("missing", LocalCopyCheck.ATTACHMENT),
    ],
)
def test_at23_24_25_w23_unusable_attachments_retain_failing_check(fake_store, tmp_path, kind, failed):
    path = attached(
        tmp_path,
        text="Different paper" if kind == "wrong_work" else TITLE,
        doi="10.4321/other" if kind == "wrong_work" else DOI,
        version="Preprint" if kind == "preprint" else "",
    )
    if kind == "unreadable":
        path.write_bytes(b"not pdf")
    if kind == "missing":
        path.unlink()
    result = checker(fake_store, cited_version=lambda meta: CopyVersion.PUBLISHED).evaluate(
        "prj_a", work(), version_policy=VersionPolicy.CITED_VERSION_ONLY, attached_path=path
    )[-1]
    assert result.state == LocalCopyState.UNUSABLE and result.failed_check == failed
    assert len(result.checks) == 5 and result.checks[failed].reason
    assert not fake_store.files.blobs


def test_checks_independent_even_unreadable_version_hint(fake_store, tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"bad")
    copy = checker(fake_store).evaluate(
        "prj_a",
        work(),
        version_policy=VersionPolicy.PREFER_PUBLISHED,
        attached_path=path,
        version_hint=CopyVersion.ACCEPTED,
    )[-1]
    assert copy.checks[LocalCopyCheck.RECORD].outcome == CheckOutcome.PASS
    assert copy.checks[LocalCopyCheck.ATTACHMENT].outcome == CheckOutcome.PASS
    assert copy.checks[LocalCopyCheck.READABLE].outcome == CheckOutcome.FAIL
    assert copy.checks[LocalCopyCheck.IDENTITY].outcome == CheckOutcome.FAIL
    assert copy.checks[LocalCopyCheck.VERSION].outcome == CheckOutcome.PASS
    assert copy.failed_check == LocalCopyCheck.READABLE


@pytest.mark.parametrize(
    "policy,version,cited,usable",
    [
        (VersionPolicy.PREFER_PUBLISHED, CopyVersion.PUBLISHED, CopyVersion.UNKNOWN, True),
        (VersionPolicy.PREFER_PUBLISHED, CopyVersion.ACCEPTED, CopyVersion.UNKNOWN, True),
        (VersionPolicy.PREFER_PUBLISHED, CopyVersion.SUBMITTED, CopyVersion.UNKNOWN, False),
        (VersionPolicy.ANY_ELIGIBLE, CopyVersion.SUBMITTED, CopyVersion.UNKNOWN, True),
        (VersionPolicy.ANY_ELIGIBLE, CopyVersion.UNKNOWN, CopyVersion.UNKNOWN, False),
        (VersionPolicy.CITED_VERSION_ONLY, CopyVersion.SUBMITTED, CopyVersion.PUBLISHED, False),
        (VersionPolicy.CITED_VERSION_ONLY, CopyVersion.SUBMITTED, CopyVersion.SUBMITTED, True),
        (VersionPolicy.CITED_VERSION_ONLY, CopyVersion.PUBLISHED, CopyVersion.UNKNOWN, False),
    ],
)
def test_all_version_policies_no_fabricated_cited_version(
    fake_store, tmp_path, policy, version, cited, usable
):
    path = attached(tmp_path, version="")
    copy = checker(fake_store, cited_version=lambda meta: cited).evaluate(
        "prj_a", work(), version_policy=policy, attached_path=path, version_hint=version
    )[-1]
    assert (copy.state == LocalCopyState.USABLE) == usable
    if not usable:
        assert copy.failed_check == LocalCopyCheck.VERSION


def test_detected_version_conflicts_with_hint_fail_conservatively(fake_store, tmp_path):
    copy = checker(fake_store).evaluate(
        "prj_a",
        work(),
        version_policy=VersionPolicy.ANY_ELIGIBLE,
        attached_path=attached(tmp_path, version="Preprint"),
        version_hint=CopyVersion.PUBLISHED,
    )[-1]
    assert copy.state == LocalCopyState.UNUSABLE and copy.failed_check == LocalCopyCheck.VERSION
    assert copy.version == CopyVersion.UNKNOWN


def test_hint_cannot_erase_conflicting_version_declarations(fake_store, tmp_path):
    path = attached(tmp_path, version="Preprint\nVersion of record")
    copy = checker(fake_store).evaluate(
        "prj_a",
        work(),
        version_policy=VersionPolicy.ANY_ELIGIBLE,
        attached_path=path,
        version_hint=CopyVersion.PUBLISHED,
    )[-1]
    assert copy.version == CopyVersion.UNKNOWN
    assert copy.state == LocalCopyState.UNUSABLE and copy.failed_check == LocalCopyCheck.VERSION


def test_at19_managed_reuse_requires_verified_work_project_and_grant(fake_store):
    data = paper_pdf(TITLE, ["Version of record"], DOI)
    good = managed(fake_store, data)
    copies = checker(fake_store).evaluate("prj_a", work(), version_policy=VersionPolicy.PREFER_PUBLISHED)
    assert len(copies) == 1 and copies[0].state == LocalCopyState.USABLE
    assert copies[0].origin == LocalCopyOrigin.MANAGED_RETRIEVED and copies[0].sha256 == good.sha256
    assert len(fake_store.work_files.list_for_work("prj_a", "wrk_a")) == 1


@pytest.mark.parametrize(
    "case",
    [
        "no_grant",
        "other_project",
        "other_work",
        "needs_review",
        "missing_bytes",
        "corrupt_bytes",
        "unknown_version",
    ],
)
def test_managed_reuse_cannot_bypass_scope_state_or_validation(fake_store, case):
    wf = managed(
        fake_store,
        paper_pdf(TITLE, ["" if case == "unknown_version" else "Version of record"], DOI),
        project="prj_b" if case == "other_project" else "prj_a",
        work_id="wrk_other" if case == "other_work" else "wrk_a",
        state=RetrievalState.NEEDS_REVIEW if case == "needs_review" else RetrievalState.VERIFIED,
        grant=case != "no_grant",
    )
    if case in ("missing_bytes", "corrupt_bytes"):
        path = fake_store.files.path_for("prj_a", wf.sha256)
        if case == "missing_bytes":
            path.unlink()
        else:
            path.write_bytes(b"bad")
    copies = checker(fake_store).evaluate("prj_a", work(), version_policy=VersionPolicy.ANY_ELIGIBLE)
    assert not any(c.state == LocalCopyState.USABLE for c in copies)
    assert all(len(c.checks) == 5 for c in copies)


def test_missing_record_reports_record_failure(fake_store):
    copy = checker(fake_store).evaluate("prj_a", work(), version_policy=VersionPolicy.ANY_ELIGIBLE)[0]
    assert copy.failed_check == LocalCopyCheck.RECORD and copy.state == LocalCopyState.UNUSABLE


def test_prefer_published_orders_usable_managed_copies(fake_store):
    managed(fake_store, paper_pdf(TITLE, ["Accepted manuscript"], DOI))
    managed(fake_store, paper_pdf(TITLE, ["Version of record"], DOI))
    copies = checker(fake_store).evaluate("prj_a", work(), version_policy=VersionPolicy.PREFER_PUBLISHED)
    assert [c.version for c in copies] == [CopyVersion.PUBLISHED, CopyVersion.ACCEPTED]


def test_cross_project_attachment_evaluation_is_denied(fake_store, tmp_path):
    from refcollector.errors import NotAuthorized

    with pytest.raises(NotAuthorized):
        checker(fake_store).evaluate(
            "prj_b", work(), version_policy=VersionPolicy.ANY_ELIGIBLE, attached_path=attached(tmp_path)
        )
    assert not fake_store.files.blobs


def test_attachment_copy_bound_applies_before_parser(fake_store, tmp_path):
    from refcollector.config import Limits
    from refcollector.files import DefaultFileVerifier

    limits = Limits(pdf_bytes=32)
    sizes = []

    class RecordingVerifier(DefaultFileVerifier):
        def verify(self, path, metadata, **kwargs):
            sizes.append(path.stat().st_size)
            return super().verify(path, metadata, **kwargs)

    copy = checker(fake_store, limits=limits, verifier=RecordingVerifier(limits)).evaluate(
        "prj_a",
        work(),
        version_policy=VersionPolicy.PREFER_PUBLISHED,
        attached_path=attached(tmp_path),
        version_hint=CopyVersion.PUBLISHED,
    )[-1]
    assert copy.failed_check == LocalCopyCheck.READABLE and not fake_store.files.blobs
    assert not sizes or max(sizes) <= 33
    assert list(fake_store.files.tmp.iterdir()) == []


def test_copy_read_error_cannot_commit_even_a_parseable_prefix(fake_store, tmp_path, monkeypatch):
    import builtins
    import io
    from pathlib import Path

    path = attached(tmp_path)
    data = path.read_bytes()

    class ReadThenFail(io.BytesIO):
        def __init__(self):
            super().__init__(data)
            self.reads = 0

        def read(self, size=-1):
            self.reads += 1
            if self.reads > 1:
                raise OSError("source interrupted after a parseable PDF")
            return super().read(size)

        def readinto(self, buffer):
            chunk = self.read(len(buffer))
            buffer[: len(chunk)] = chunk
            return len(chunk)

    original_builtin, original_io = builtins.open, io.open

    def intercept(original):
        def opened(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, Path)) and Path(file) == path and mode == "rb":
                return ReadThenFail()
            return original(file, mode, *args, **kwargs)

        return opened

    monkeypatch.setattr(builtins, "open", intercept(original_builtin))
    monkeypatch.setattr(io, "open", intercept(original_io))
    copy = checker(fake_store).evaluate(
        "prj_a",
        work(),
        version_policy=VersionPolicy.PREFER_PUBLISHED,
        attached_path=path,
        version_hint=CopyVersion.PUBLISHED,
    )[-1]
    assert copy.state == LocalCopyState.UNUSABLE and copy.failed_check == LocalCopyCheck.READABLE
    assert not fake_store.files.blobs and list(fake_store.files.tmp.iterdir()) == []
