"""Unit tests for LibraryFolders and citation_status (Wave 1, B1)."""

from __future__ import annotations

import hashlib
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from refcollector.domain.enums import (
    AccessClassification,
    DiscoveryState,
    ExtractionState,
    IdentityState,
    RetrievalState,
)
from refcollector.domain.models import Occurrence
from refcollector.errors import InvalidInput, PathNotAllowed
from refcollector.services.library_folder import LibraryFolders
from refcollector.web.library_api import citation_status


def _make_occ(**kwargs) -> Occurrence:
    defaults = {
        "occurrence_id": "occ_1",
        "project_id": "prj_1",
        "source_id": "src_1",
        "order": 1.0,
        "raw_text": "Sample Citation",
        "extraction": ExtractionState.EXTRACTED,
        "identity": IdentityState.UNPROCESSED,
        "discovery": DiscoveryState.NOT_CHECKED,
        "retrieval": RetrievalState.NOT_REQUESTED,
    }
    defaults.update(kwargs)
    return Occurrence(**defaults)


# ============================================================================
# Status mapping tests (Contract Section 3, rows 1-9)
# ============================================================================


def test_status_mapping_row_1_failed() -> None:
    # Rule 1: extraction == failed or retrieval == failed -> failed
    occ_ext_failed = _make_occ(extraction=ExtractionState.FAILED)
    assert citation_status(occ_ext_failed) == "failed"

    occ_ret_failed = _make_occ(retrieval=RetrievalState.FAILED)
    assert citation_status(occ_ret_failed) == "failed"


def test_status_mapping_row_2_excluded() -> None:
    # Rule 2: extraction == excluded -> None
    occ_excluded = _make_occ(extraction=ExtractionState.EXCLUDED)
    assert citation_status(occ_excluded) is None


def test_status_mapping_row_3_downloaded() -> None:
    # Rule 3: retrieval in {verified, satisfied_by_local_copy} or has_verified_file -> downloaded
    occ_verified = _make_occ(retrieval=RetrievalState.VERIFIED)
    assert citation_status(occ_verified) == "downloaded"

    occ_satisfied = _make_occ(retrieval=RetrievalState.SATISFIED_BY_LOCAL_COPY)
    assert citation_status(occ_satisfied) == "downloaded"

    occ_with_file = _make_occ(
        identity=IdentityState.ACCEPTED_AUTO,
        retrieval=RetrievalState.NOT_REQUESTED,
    )
    assert citation_status(occ_with_file, has_verified_file=True) == "downloaded"


def test_status_mapping_row_4_check() -> None:
    # Rule 4: identity == needs_review or retrieval == needs_review or extraction == needs_review -> check
    occ_ident_nr = _make_occ(identity=IdentityState.NEEDS_REVIEW)
    assert citation_status(occ_ident_nr) == "check"

    occ_ret_nr = _make_occ(retrieval=RetrievalState.NEEDS_REVIEW)
    assert citation_status(occ_ret_nr) == "check"

    occ_ext_nr = _make_occ(extraction=ExtractionState.NEEDS_REVIEW)
    assert citation_status(occ_ext_nr) == "check"


def test_status_mapping_row_5_no_match() -> None:
    # Rule 5: identity in {unresolved, rejected} -> no_match
    occ_unresolved = _make_occ(identity=IdentityState.UNRESOLVED)
    assert citation_status(occ_unresolved) == "no_match"

    occ_rejected = _make_occ(identity=IdentityState.REJECTED)
    assert citation_status(occ_rejected) == "no_match"


def test_status_mapping_row_6_extracted() -> None:
    # Rule 6: identity == unprocessed -> extracted
    occ_unprocessed = _make_occ(identity=IdentityState.UNPROCESSED)
    assert citation_status(occ_unprocessed) == "extracted"


def test_status_mapping_row_7_matched() -> None:
    # Rule 7: accepted identity and discovery == not_checked -> matched
    occ_matched = _make_occ(
        identity=IdentityState.ACCEPTED_AUTO,
        discovery=DiscoveryState.NOT_CHECKED,
    )
    assert citation_status(occ_matched) == "matched"

    occ_matched_agent = _make_occ(
        identity=IdentityState.ACCEPTED_AGENT,
        discovery=DiscoveryState.NOT_CHECKED,
    )
    assert citation_status(occ_matched_agent) == "matched"


def test_status_mapping_row_8_pdf_available() -> None:
    # Rule 8: accepted identity and classification == eligible_found -> pdf_available
    occ_pdf = _make_occ(
        identity=IdentityState.ACCEPTED_USER,
        discovery=DiscoveryState.COMPLETE,
        classification=AccessClassification.ELIGIBLE_FOUND,
    )
    assert citation_status(occ_pdf) == "pdf_available"


def test_status_mapping_row_9_no_pdf() -> None:
    # Rule 9: accepted identity, discovery checked, other classification -> no_pdf
    occ_no_pdf = _make_occ(
        identity=IdentityState.ACCEPTED_AUTO,
        discovery=DiscoveryState.COMPLETE,
        classification=AccessClassification.NONE_FOUND,
    )
    assert citation_status(occ_no_pdf) == "no_pdf"

    occ_restricted = _make_occ(
        identity=IdentityState.ACCEPTED_AUTO,
        discovery=DiscoveryState.COMPLETE,
        classification=AccessClassification.RESTRICTED,
    )
    assert citation_status(occ_restricted) == "no_pdf"


# ============================================================================
# LibraryFolders sync tests
# ============================================================================


def test_folder_sync_idempotency(tmp_path: Path) -> None:
    rc_home = tmp_path / "rc_home"
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target_folder = allowed_root / "collection_a"

    folders = LibraryFolders(rc_home, lambda: [allowed_root])
    folders.create("prj_1", target_folder)

    source_file = tmp_path / "test.pdf"
    content = b"PDF dummy content"
    source_file.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()

    # First sync
    rec1 = folders.sync("prj_1", [(sha, "paper.pdf", source_file)])
    assert rec1.files[sha] == "paper.pdf"
    assert (target_folder / "paper.pdf").read_bytes() == content

    # Second sync: idempotent, doesn't copy again
    rec2 = folders.sync("prj_1", [(sha, "paper.pdf", source_file)])
    assert rec2.files[sha] == "paper.pdf"


def test_no_overwrite_of_user_file_and_collision_suffix(tmp_path: Path) -> None:
    rc_home = tmp_path / "rc_home"
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target_folder = allowed_root / "collection_b"
    target_folder.mkdir()

    folders = LibraryFolders(rc_home, lambda: [allowed_root])
    folders.create("prj_2", target_folder)

    # Existing user file with different content
    user_file = target_folder / "paper.pdf"
    user_content = b"User file exists"
    user_file.write_bytes(user_content)

    # Now sync new content with preferred name paper.pdf
    new_source = tmp_path / "new_paper.pdf"
    new_content = b"New refcollector paper"
    new_source.write_bytes(new_content)
    sha_new = hashlib.sha256(new_content).hexdigest()

    rec = folders.sync("prj_2", [(sha_new, "paper.pdf", new_source)])

    # Must not overwrite user file! Must use "paper (2).pdf"
    assert (target_folder / "paper.pdf").read_bytes() == user_content
    assert rec.files[sha_new] == "paper (2).pdf"
    assert (target_folder / "paper (2).pdf").read_bytes() == new_content

    # Sync a third file with collision
    third_source = tmp_path / "third_paper.pdf"
    third_content = b"Third paper content"
    third_source.write_bytes(third_content)
    sha_third = hashlib.sha256(third_content).hexdigest()

    rec3 = folders.sync("prj_2", [(sha_third, "paper.pdf", third_source)])
    assert rec3.files[sha_third] == "paper (3).pdf"
    assert (target_folder / "paper (3).pdf").read_bytes() == third_content


def test_path_escape_rejected(tmp_path: Path) -> None:
    rc_home = tmp_path / "rc_home"
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target_folder = allowed_root / "collection_c"

    folders = LibraryFolders(rc_home, lambda: [allowed_root])
    folders.create("prj_3", target_folder)

    dummy_src = tmp_path / "dummy.pdf"
    dummy_src.write_bytes(b"dummy")

    with pytest.raises(InvalidInput):
        folders.sync("prj_3", [("sha1", "../escape.pdf", dummy_src)])

    with pytest.raises(InvalidInput):
        folders.sync("prj_3", [("sha2", "sub/folder.pdf", dummy_src)])

    with pytest.raises(InvalidInput):
        folders.sync("prj_3", [("sha3", "sub\\folder.pdf", dummy_src)])


def test_folder_outside_allowed_folders_rejected(tmp_path: Path) -> None:
    rc_home = tmp_path / "rc_home"
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()

    outside_folder = tmp_path / "forbidden_folder"

    folders = LibraryFolders(rc_home, lambda: [allowed_root])
    folders.create("prj_4", outside_folder)

    dummy_src = tmp_path / "dummy.pdf"
    dummy_src.write_bytes(b"dummy")

    with pytest.raises(PathNotAllowed):
        folders.sync("prj_4", [("sha", "paper.pdf", dummy_src)])


def test_record_survives_reload(tmp_path: Path) -> None:
    rc_home = tmp_path / "rc_home"
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target_folder = allowed_root / "collection_e"

    folders1 = LibraryFolders(rc_home, lambda: [allowed_root])
    folders1.create("prj_5", target_folder)

    dummy_src = tmp_path / "test.pdf"
    content = b"Content to test reload"
    dummy_src.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()

    folders1.sync("prj_5", [(sha, "report.pdf", dummy_src)])

    # Reload fresh instance
    folders2 = LibraryFolders(rc_home, lambda: [allowed_root])
    rec = folders2.get("prj_5")

    assert rec is not None
    assert rec.folder == target_folder.resolve()
    assert rec.files[sha] == "report.pdf"
    assert (rec.folder / "report.pdf").read_bytes() == content


@pytest.fixture
def collection(tmp_path: Path) -> tuple[LibraryFolders, Path, Path]:
    root = tmp_path / "allowed"
    root.mkdir()
    folders = LibraryFolders(tmp_path / "home", lambda: [root])
    target = root / "collection"
    folders.create("prj_race", target)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"library PDF")
    return folders, target, source


@pytest.mark.parametrize("collision_index", [1, 2])
def test_external_destination_created_during_copy_is_not_overwritten(
    collection: tuple[LibraryFolders, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    collision_index: int,
) -> None:
    folders, target, source = collection
    real_copy = shutil.copy2
    if collision_index == 2:
        target.mkdir()
        (target / "paper.pdf").write_bytes(b"existing user PDF")
    raced_name = "paper.pdf" if collision_index == 1 else "paper (2).pdf"

    def copy_then_external_write(src, dst):
        result = real_copy(src, dst)
        (target / raced_name).write_bytes(b"concurrent user PDF")
        return result

    monkeypatch.setattr(shutil, "copy2", copy_then_external_write)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    record = folders.sync("prj_race", [(sha, "paper.pdf", source)])

    assert (target / raced_name).read_bytes() == b"concurrent user PDF"
    expected = f"paper ({collision_index + 1}).pdf"
    assert record.files == {sha: expected}
    assert (target / expected).read_bytes() == b"library PDF"
    assert not list(target.glob(".tmp.*"))


def test_same_hash_destination_created_during_copy_is_reused(
    collection: tuple[LibraryFolders, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    folders, target, source = collection
    real_copy = shutil.copy2

    def copy_then_external_write(src, dst):
        result = real_copy(src, dst)
        (target / "paper.pdf").write_bytes(b"library PDF")
        return result

    monkeypatch.setattr(shutil, "copy2", copy_then_external_write)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    record = folders.sync("prj_race", [(sha, "paper.pdf", source)])
    assert record.files == {sha: "paper.pdf"}
    assert [p.name for p in target.iterdir()] == ["paper.pdf"]


@pytest.mark.parametrize("second_operation", ["sync", "create"])
def test_concurrent_record_writers_preserve_committed_mappings(
    collection: tuple[LibraryFolders, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_operation: str,
) -> None:
    folders, target, source = collection
    second = LibraryFolders(folders.rc_home, folders.allowed_folders)
    other_source = tmp_path / "other.pdf"
    other_source.write_bytes(b"second PDF")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    other_sha = hashlib.sha256(other_source.read_bytes()).hexdigest()
    first_at_save, release_first, second_started, second_finished = (Event() for _ in range(4))
    real_save = folders._save_record

    def paused_save(project_id, record):
        first_at_save.set()
        assert release_first.wait(5), "First writer was not released"
        real_save(project_id, record)

    def run_second():
        second_started.set()
        try:
            if second_operation == "create":
                return second.create("prj_race", target)
            return second.sync("prj_race", [(other_sha, "other.pdf", other_source)])
        finally:
            second_finished.set()

    monkeypatch.setattr(folders, "_save_record", paused_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(folders.sync, "prj_race", [(sha, "paper.pdf", source)])
        try:
            assert first_at_save.wait(5)
            concurrent = pool.submit(run_second)
            assert second_started.wait(5)
            # An unlocked writer can finish against the old record while the first is paused.
            # A serialized writer resumes only after release_first.
            second_finished.wait(0.5)
        finally:
            release_first.set()
        first.result(timeout=5)
        returned = concurrent.result(timeout=5)

    expected = {sha: "paper.pdf"}
    if second_operation == "sync":
        expected[other_sha] = "other.pdf"
    assert returned.files == expected
    saved = folders.get("prj_race")
    assert saved is not None and saved.files == expected
    assert (target / "paper.pdf").read_bytes() == b"library PDF"
    if second_operation == "sync":
        assert (target / "other.pdf").read_bytes() == b"second PDF"


def test_create_replay_preserves_existing_mapping(
    collection: tuple[LibraryFolders, Path, Path],
) -> None:
    folders, target, source = collection
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    folders.sync("prj_race", [(sha, "paper.pdf", source)])
    before = (folders.rc_home / "library/prj_race.json").read_bytes()
    replay = folders.create("prj_race", target / ".")
    assert replay.folder == target.resolve()
    assert replay.files == {sha: "paper.pdf"}
    assert (folders.rc_home / "library/prj_race.json").read_bytes() == before


def test_create_replay_rejects_changed_folder_without_losing_mapping(
    collection: tuple[LibraryFolders, Path, Path],
) -> None:
    folders, target, source = collection
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    folders.sync("prj_race", [(sha, "paper.pdf", source)])
    before = (folders.rc_home / "library/prj_race.json").read_bytes()
    with pytest.raises(InvalidInput):
        folders.create("prj_race", target.with_name("changed"))
    assert (folders.rc_home / "library/prj_race.json").read_bytes() == before
    assert (target / "paper.pdf").read_bytes() == b"library PDF"


@pytest.mark.parametrize(
    "name",
    [
        "C:escape.pdf",
        "C:\\escape.pdf",
        "C:/escape.pdf",
        "\\rooted.pdf",
        "/rooted.pdf",
        "\\\\server\\share\\paper.pdf",
        "paper.pdf:stream",
        "paper.pdf::$DATA",
        "sub/paper.pdf",
        "sub\\paper.pdf",
        "../paper.pdf",
    ],
)
def test_windows_filename_boundaries_rejected_before_copy(
    collection: tuple[LibraryFolders, Path, Path], monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    folders, target, source = collection

    def unexpected_copy(*args, **kwargs):
        pytest.fail("Unsafe filename reached the file copy")

    monkeypatch.setattr(shutil, "copy2", unexpected_copy)
    with pytest.raises(InvalidInput):
        folders.sync("prj_race", [("sha", name, source)])
    assert not list(target.glob("*.pdf"))


@pytest.mark.parametrize("name", ["paper.pdf", "C-paper.pdf", "論文 2026.pdf"])
def test_normal_filenames_still_sync(collection: tuple[LibraryFolders, Path, Path], name: str) -> None:
    folders, target, source = collection
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    record = folders.sync("prj_race", [(sha, name, source)])
    assert record.files == {sha: name}
    assert (target / name).read_bytes() == b"library PDF"


@pytest.mark.parametrize("during_copy", [False, True])
def test_resolved_destination_outside_collection_is_rejected(
    collection: tuple[LibraryFolders, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    during_copy: bool,
) -> None:
    folders, target, source = collection
    real_resolve = Path.resolve
    real_copy = shutil.copy2
    outside = tmp_path / "outside.pdf"
    redirected = not during_copy

    def copy_then_redirect(src, dst):
        nonlocal redirected
        result = real_copy(src, dst)
        redirected = True
        return result

    def resolve(path, *args, **kwargs):
        if redirected and path == target / "paper.pdf":
            return outside
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(shutil, "copy2", copy_then_redirect)
    with pytest.raises(PathNotAllowed):
        folders.sync("prj_race", [("sha", "paper.pdf", source)])
    assert not outside.exists()
    assert not (target / "paper.pdf").exists()
    assert not list(target.glob(".tmp.*"))


def test_publication_failure_cleans_temp_and_preserves_record(
    collection: tuple[LibraryFolders, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    folders, target, source = collection
    before = (folders.rc_home / "library/prj_race.json").read_bytes()

    def fail_publication(*args, **kwargs):
        raise OSError("publication unavailable")

    monkeypatch.setattr(os, "rename", fail_publication)
    monkeypatch.setattr(os, "link", fail_publication)
    with pytest.raises(OSError, match="publication unavailable"):
        folders.sync("prj_race", [("sha", "paper.pdf", source)])
    assert not list(target.iterdir())
    assert (folders.rc_home / "library/prj_race.json").read_bytes() == before


def test_copy_failure_cleans_temp_and_preserves_record(
    collection: tuple[LibraryFolders, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    folders, target, source = collection
    before = (folders.rc_home / "library/prj_race.json").read_bytes()

    def fail_copy(src, dst):
        Path(dst).write_bytes(b"partial PDF")
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="copy failed"):
        folders.sync("prj_race", [("sha", "paper.pdf", source)])
    assert not list(target.iterdir())
    assert (folders.rc_home / "library/prj_race.json").read_bytes() == before
