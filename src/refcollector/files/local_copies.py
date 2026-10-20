"""WP-08: LocalCopyChecker implementation. Spec 10.3.

Project-scoped local copies; no access-classification or pipeline mutations."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from refcollector.config import Limits
from refcollector.contracts.ports import FileStore, FileVerifier, WorkFileRepo
from refcollector.domain.enums import (
    CheckOutcome,
    CopyVersion,
    LocalCopyCheck,
    LocalCopyOrigin,
    LocalCopyState,
    RetrievalState,
    VersionPolicy,
)
from refcollector.domain.models import CheckResult, LocalCopy, ProjectWork, WorkMetadata
from refcollector.errors import NotAuthorized, RcError
from refcollector.ids import new_id

from .retrieval import effective_metadata
from .verify import DefaultFileVerifier


def _version_allowed(version: CopyVersion, policy: VersionPolicy, cited: CopyVersion) -> bool:
    if version == CopyVersion.UNKNOWN:
        return False
    if policy == VersionPolicy.CITED_VERSION_ONLY:
        return cited != CopyVersion.UNKNOWN and version == cited
    if policy == VersionPolicy.PREFER_PUBLISHED:
        return version in {CopyVersion.PUBLISHED, CopyVersion.ACCEPTED}
    return policy == VersionPolicy.ANY_ELIGIBLE


def _check(passed: bool, reason: str) -> CheckResult:
    return CheckResult(
        outcome=CheckOutcome.PASS if passed else CheckOutcome.FAIL, reason=None if passed else reason
    )


class DefaultLocalCopyChecker:
    """Return unsaved rows. Caller supplies cited-version evidence if it has any.

    WorkMetadata has no cited-version field in contracts v1. Without an injected
    cited_version lookup, cited_version_only cannot pass. version_hint belongs to
    the explicit user attachment only; managed files are rechecked from their bytes.
    Attachment allow-list authorization belongs to the calling service (spec 13).
    """

    def __init__(
        self,
        files: FileStore,
        work_files: WorkFileRepo,
        *,
        verifier: FileVerifier | None = None,
        limits: Limits | None = None,
        cited_version: Callable[[WorkMetadata], CopyVersion] | None = None,
    ) -> None:
        self._files = files
        self._work_files = work_files
        self._limits = limits or Limits()
        self._verifier = verifier or DefaultFileVerifier(self._limits)
        self._cited_version = cited_version

    def evaluate(
        self,
        project_id: str,
        work: ProjectWork,
        *,
        version_policy: VersionPolicy,
        attached_path: Path | None = None,
        version_hint: CopyVersion | None = None,
    ) -> list[LocalCopy]:
        if project_id != work.project_id:
            raise NotAuthorized("Work belongs to another project")
        metadata = effective_metadata(work)
        cited = self._cited_version(metadata) if self._cited_version else CopyVersion.UNKNOWN
        rows = []
        for wf in self._work_files.list_for_work(project_id, work.work_id):
            if (
                wf.project_id != project_id
                or wf.work_id != work.work_id
                or wf.state != RetrievalState.VERIFIED
            ):
                continue
            granted = self._files.has_grant(project_id, wf.sha256)
            path = None
            if granted:
                try:
                    path = self._files.path_for(project_id, wf.sha256)
                except RcError:
                    pass
            rows.append(
                self._evaluate(
                    project_id,
                    work.work_id,
                    metadata,
                    path,
                    LocalCopyOrigin.MANAGED_RETRIEVED,
                    granted,
                    version_policy,
                    cited,
                    expected_sha=wf.sha256,
                )
            )
        if attached_path is not None:
            # Verify the bytes that will be committed, not a mutable source path. Copy
            # first to prevent a source replacement between verification and storage.
            tmp = self._files.new_tmp_path(".pdf")
            try:
                present = attached_path.is_file()
                copy_error = None
                try:
                    if present:
                        size = 0
                        with attached_path.open("rb") as source, tmp.open("xb") as target:
                            while chunk := source.read(min(64 * 1024, self._limits.pdf_bytes - size + 1)):
                                size += len(chunk)
                                if size > self._limits.pdf_bytes:
                                    copy_error = "Attachment exceeds PDF byte limit"
                                    break
                                target.write(chunk)
                except OSError as exc:
                    copy_error = f"Attachment copy failed: {type(exc).__name__}"
                row = self._evaluate(
                    project_id,
                    work.work_id,
                    metadata,
                    tmp if tmp.exists() else None,
                    LocalCopyOrigin.USER_ATTACHED,
                    True,
                    version_policy,
                    cited,
                    version_hint=version_hint,
                    attachment_present=present,
                    copy_error=copy_error,
                )
                if row.state == LocalCopyState.USABLE:
                    blob = self._files.commit(tmp, "application/pdf")
                    self._files.grant(project_id, blob.sha256)
                    row.sha256 = blob.sha256
                rows.append(row)
            finally:
                tmp.unlink(missing_ok=True)
        if not rows:
            rows.append(
                self._evaluate(
                    project_id,
                    work.work_id,
                    metadata,
                    None,
                    LocalCopyOrigin.MANAGED_RETRIEVED,
                    False,
                    version_policy,
                    cited,
                )
            )
        if version_policy == VersionPolicy.PREFER_PUBLISHED:
            order = {
                CopyVersion.PUBLISHED: 0,
                CopyVersion.ACCEPTED: 1,
                CopyVersion.SUBMITTED: 2,
                CopyVersion.UNKNOWN: 3,
            }
            rows.sort(key=lambda row: (row.state != LocalCopyState.USABLE, order[row.version]))
        return rows

    def _evaluate(
        self,
        project_id: str,
        work_id: str,
        metadata: WorkMetadata,
        path: Path | None,
        origin: LocalCopyOrigin,
        record_found: bool,
        policy: VersionPolicy,
        cited: CopyVersion,
        *,
        expected_sha: str | None = None,
        version_hint: CopyVersion | None = None,
        attachment_present: bool | None = None,
        copy_error: str | None = None,
    ) -> LocalCopy:
        present = path is not None and path.is_file()
        verification = self._verifier.verify(path, metadata) if present and not copy_error else None
        readable = bool(verification and verification.readable)
        sha = None
        if readable and path is not None:
            try:
                with path.open("rb") as file:
                    sha = hashlib.file_digest(file, "sha256").hexdigest()
                if expected_sha is not None and expected_sha != sha:
                    readable = False
            except OSError:
                readable = False
        identity = bool(verification and verification.identity_match is True)
        detected = verification.version if verification else CopyVersion.UNKNOWN
        version = detected if detected != CopyVersion.UNKNOWN else (version_hint or CopyVersion.UNKNOWN)
        if version_hint not in (None, CopyVersion.UNKNOWN) and detected not in (
            CopyVersion.UNKNOWN,
            version_hint,
        ):
            version = CopyVersion.UNKNOWN
        if verification and verification.evidence.get("version_conflict"):
            version = CopyVersion.UNKNOWN
        checks = {
            LocalCopyCheck.RECORD: _check(record_found, "No verified work file with this project's grant"),
            LocalCopyCheck.ATTACHMENT: _check(
                present if attachment_present is None else attachment_present,
                "Attachment is missing or is not a regular file",
            ),
            LocalCopyCheck.READABLE: _check(
                readable, copy_error or "PDF is unreadable or stored hash does not match"
            ),
            LocalCopyCheck.IDENTITY: _check(
                identity,
                "; ".join(verification.reasons) if verification else "Identity evidence is unavailable",
            ),
            LocalCopyCheck.VERSION: _check(
                _version_allowed(version, policy, cited),
                "VERSION_UNKNOWN_OR_OUTSIDE_POLICY",
            ),
        }
        if cited != CopyVersion.UNKNOWN and version not in (CopyVersion.UNKNOWN, cited):
            checks[LocalCopyCheck.VERSION].reason = "VERSION_DIFFERENCE"
        failed = next((key for key, result in checks.items() if result.outcome == CheckOutcome.FAIL), None)
        return LocalCopy(
            local_copy_id=new_id("local_copy"),
            project_id=project_id,
            work_id=work_id,
            origin=origin,
            sha256=sha if readable else None,
            version=version,
            checks=checks,
            state=LocalCopyState.USABLE if failed is None else LocalCopyState.UNUSABLE,
            failed_check=failed,
        )
