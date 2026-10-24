"""Collection folder synchronization service.

Stores and synchronizes PDF files into user-designated collection folders.
Ensures path validation, atomic record updates, and non-overwriting collision resolution.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from uuid import uuid4

import portalocker

from refcollector.errors import InvalidInput, NotFound, PathNotAllowed


@dataclass
class FolderRecord:
    """Folder record containing folder path and mapped files."""

    folder: Path
    files: dict[str, str] = field(default_factory=dict)


def _compute_sha256(path: Path) -> str:
    """Compute sha256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class LibraryFolders:
    """Manages collection folder records and file synchronization."""

    def __init__(
        self,
        rc_home: Path,
        allowed_folders: Callable[[], list[Path]],
    ) -> None:
        self.rc_home = Path(rc_home).resolve()
        self.allowed_folders = allowed_folders

    def _record_path(self, project_id: str) -> Path:
        return self.rc_home / "library" / f"{project_id}.json"

    @contextmanager
    def _record_lock(self, project_id: str) -> Iterator[None]:
        # Lock a stable sidecar: the JSON record itself is replaced on each save.
        lock_path = self._record_path(project_id).with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(lock_path, mode="a", timeout=30):
            yield

    def _save_record(self, project_id: str, record: FolderRecord) -> None:
        lib_dir = self.rc_home / "library"
        lib_dir.mkdir(parents=True, exist_ok=True)
        target_file = self._record_path(project_id)
        tmp_file = lib_dir / f".{project_id}.tmp.{uuid4().hex}"

        folder_str = str(record.folder.resolve()).replace("\\", "/")
        payload = {
            "folder": folder_str,
            "files": record.files,
        }

        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        os.replace(tmp_file, target_file)

    def get(self, project_id: str) -> FolderRecord | None:
        """Get the folder record for a project if it exists."""
        rec_path = self._record_path(project_id)
        if not rec_path.is_file():
            return None

        try:
            with open(rec_path, encoding="utf-8") as f:
                data = json.load(f)
            folder = Path(data["folder"]).resolve()
            files = dict(data.get("files", {}))
            return FolderRecord(folder=folder, files=files)
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def create(self, project_id: str, folder: Path) -> FolderRecord:
        """Create a new folder record for a project."""
        resolved = folder.expanduser().resolve()
        with self._record_lock(project_id):
            existing = self.get(project_id)
            if existing is not None:
                if existing.folder != resolved:
                    raise InvalidInput(f"Collection folder already configured for project '{project_id}'")
                return existing
            record = FolderRecord(folder=resolved, files={})
            self._save_record(project_id, record)
            return record

    def delete(self, project_id: str) -> None:
        """Remove the sidecar folder record for a project. Does NOT delete disk files."""
        with self._record_lock(project_id):
            rec_path = self._record_path(project_id)
            if rec_path.is_file():
                with suppress(OSError):
                    rec_path.unlink()
        lock_path = self._record_path(project_id).with_suffix(".lock")
        if lock_path.is_file():
            with suppress(OSError):
                lock_path.unlink()

    @staticmethod
    def _destination(folder: Path, name: str) -> Path:
        clean_name = name.strip()
        windows_name = PureWindowsPath(clean_name)
        if (
            not clean_name
            or windows_name.drive
            or windows_name.anchor
            or ":" in clean_name
            or "/" in clean_name
            or "\\" in clean_name
            or ".." in clean_name
            or clean_name in (".", "..")
        ):
            raise InvalidInput(f"Invalid file name: '{name}'")
        if any(ord(c) < 32 or ord(c) == 127 for c in clean_name):
            raise InvalidInput(f"Control characters in file name: '{name}'")
        destination = folder / clean_name
        if not destination.resolve().is_relative_to(folder):
            raise PathNotAllowed(f"File '{name}' is not inside the collection folder")
        return destination

    def sync(
        self,
        project_id: str,
        files: Iterable[tuple[str, str, Path]],
    ) -> FolderRecord:
        """Synchronize a collection of files into the collection folder.

        files: Iterable of (sha256, preferred_name, source_path)
        """
        with self._record_lock(project_id):
            return self._sync(project_id, files)

    def _sync(self, project_id: str, files: Iterable[tuple[str, str, Path]]) -> FolderRecord:
        record = self.get(project_id)
        if record is None:
            raise NotFound(f"Collection folder not configured for project '{project_id}'")

        target_folder = record.folder.resolve()

        # Check against allowed roots
        allowed_roots = [p.resolve() for p in self.allowed_folders()]
        is_allowed = False
        for root in allowed_roots:
            try:
                target_folder.relative_to(root)
                is_allowed = True
                break
            except ValueError:
                continue

        if not is_allowed:
            raise PathNotAllowed(f"Collection folder '{target_folder}' is not inside allowed folders")

        # Folder directory created only when inside allowed folders
        target_folder.mkdir(parents=True, exist_ok=True)

        for sha256, preferred_name, source_path in files:
            # Reject names with path separators or ..
            clean_name = self._destination(target_folder, preferred_name).name

            # Skip a sha already recorded and present on disk
            if sha256 in record.files:
                existing_filename = record.files[sha256]
                existing_file_path = self._destination(target_folder, existing_filename)
                if existing_file_path.is_file():
                    continue

            name_obj = Path(clean_name)
            stem = name_obj.stem
            suffix = name_obj.suffix

            candidate_idx = 1
            while True:
                if candidate_idx == 1:
                    cand_name = clean_name
                else:
                    cand_name = f"{stem} ({candidate_idx}){suffix}"

                cand_path = self._destination(target_folder, cand_name)
                if cand_path.exists():
                    if cand_path.is_file() and _compute_sha256(cand_path) == sha256:
                        record.files[sha256] = cand_name
                        break
                    candidate_idx += 1
                    continue

                if cand_name in record.files.values():
                    candidate_idx += 1
                    continue

                # Available candidate name found. Copy atomically using temp file.
                tmp_file = target_folder / f".tmp.{uuid4().hex}"
                try:
                    shutil.copy2(source_path, tmp_file)
                    self._destination(target_folder, cand_name)
                    try:
                        if os.name == "nt":
                            # Windows rename fails if the destination exists; POSIX rename overwrites.
                            os.rename(tmp_file, cand_path)
                        else:
                            # A same-folder hard link publishes complete bytes without replacing a name.
                            os.link(tmp_file, cand_path)
                    except FileExistsError:
                        # Recheck the raced-in file's hash before choosing the next collision suffix.
                        continue
                finally:
                    if tmp_file.exists():
                        try:
                            tmp_file.unlink()
                        except OSError:
                            pass

                record.files[sha256] = cand_name
                break

        self._save_record(project_id, record)
        return record
