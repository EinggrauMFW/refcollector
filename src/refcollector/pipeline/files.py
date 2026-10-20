"""Stage adapter file writes without publishing rows or grants before step commit."""

import hashlib

import portalocker

from refcollector.domain.models import FileBlob
from refcollector.ids import new_id
from refcollector.storage.files import SqliteFileStore


class SessionFileStore(SqliteFileStore):
    """A live client leases its temp namespace, including imports outside executor work."""

    def __init__(self, db, settings):
        super().__init__(db, settings)
        self.root = settings.tmp_dir.resolve()
        self.tmp = self.root / f"pipeline-{new_id('executor')}"
        self.lease_path = self.root / f"{self.tmp.name}.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lease = self.lease_path.open("a+b")
        try:
            portalocker.lock(self.lease, portalocker.LOCK_EX | portalocker.LOCK_NB)
            self.tmp.mkdir()
        except BaseException:
            self.lease.close()
            raise

    def close_lease(self):
        if not self.lease.closed:
            portalocker.unlock(self.lease)
            self.lease.close()

    def _clear(self, directory):
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root):
            return
        for path in directory.iterdir():
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)

    def cleanup_abandoned(self):
        for directory in self.root.glob("pipeline-*"):
            if not directory.is_dir() or directory.is_symlink() or directory == self.tmp:
                continue
            lease_path = self.root / f"{directory.name}.lock"
            if not lease_path.is_file() or lease_path.is_symlink():
                continue
            with lease_path.open("a+b") as lease:
                try:
                    portalocker.lock(lease, portalocker.LOCK_EX | portalocker.LOCK_NB)
                except portalocker.exceptions.LockException:
                    continue
                try:
                    self._clear(directory)
                finally:
                    portalocker.unlock(lease)
            if not any(directory.iterdir()):
                directory.rmdir()
                lease_path.unlink(missing_ok=True)

    def close(self):
        if self.tmp.exists():
            self._clear(self.tmp)
        self.close_lease()
        if self.tmp.exists() and not any(self.tmp.iterdir()):
            self.tmp.rmdir()
        self.lease_path.unlink(missing_ok=True)


class StagedFiles:
    def __init__(self, files):
        self.files = files
        self.pending = []
        self.grants = []
        self.paths = []

    def new_tmp_path(self, suffix=""):
        path = self.files.new_tmp_path(suffix)
        self.paths.append(path)
        return path

    def commit(self, path, media_type):
        data = path.read_bytes()
        blob = FileBlob(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), media_type=media_type)
        staged = self.new_tmp_path(".stage")
        staged.write_bytes(data)
        self.pending.append((staged, blob))
        return blob

    def grant(self, pid, sha):
        self.grants.append((pid, sha))

    def publish(self):
        for path, blob in self.pending:
            actual = self.files.commit(path, blob.media_type)
            if actual.sha256 != blob.sha256:
                raise ValueError("Staged file hash changed")
        for pid, sha in self.grants:
            self.files.grant(pid, sha)

    def cleanup(self):
        for path in self.paths:
            path.unlink(missing_ok=True)

    def has_grant(self, pid, sha):
        return self.files.has_grant(pid, sha)

    def path_for(self, pid, sha):
        return self.files.path_for(pid, sha)
