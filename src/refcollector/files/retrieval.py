"""WP-08: Retriever implementation. Spec 10.4.

Retrieval orchestration over frozen HTTP and storage ports.

Default retrieval accepts the concrete ProviderHttpClient, whose download path pins
checked addresses. Unknown clients and subclasses that can override download fail
closed. A trusted composition root can still authorize another implementation with
an explicit connection_policy; test fixtures authorize their socket-free fake only.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from typing import Any

from refcollector.config import Limits
from refcollector.contracts.ports import FileStore, FileVerifier, HttpClient, NetGuard, RetrievalOutcome
from refcollector.domain.enums import RetrievalState
from refcollector.domain.models import AccessLocation, ProjectWork, WorkFile, WorkMetadata
from refcollector.errors import (
    Cancelled,
    FileValidationFailed,
    IntegrationUnavailable,
    LimitExceeded,
    NotAuthorized,
    PrerequisiteNotMet,
    RcError,
    TransientNetworkError,
)
from refcollector.ids import new_id
from refcollector.outputs.manifest import export_file_name

from .netguard import DefaultNetGuard
from .verify import DefaultFileVerifier


def effective_metadata(work: ProjectWork) -> WorkMetadata:
    """Read the accepted project view without changing its metadata or locks."""
    return WorkMetadata.model_validate({**work.metadata.model_dump(), **work.overrides})


def _require_connection_policy(http: HttpClient) -> None:
    # Local import avoids a cycle: the provider transport uses the file URL validator.
    from refcollector.providers.http import ProviderHttpClient

    if type(http) is ProviderHttpClient:
        return
    raise IntegrationUnavailable(
        "Retrieval client has no known connection-boundary DNS protection",
        recovery_hint="Use the default ProviderHttpClient or explicitly authorize a secured implementation.",
    )


def _cancel(is_cancelled: Callable[[], bool]) -> None:
    if is_cancelled():
        raise Cancelled("Retrieval cancelled")


async def _in_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking native parsing or hashing off the event loop; timeout and cancel stay live.

    A thread cannot be killed: on timeout or cancel, wait for it to finish (bounded by the byte
    and page limits) so no file handle outlives the call and the temp file can be removed.
    """
    task = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except BaseException:
        await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # mark retrieved
        raise


class DefaultRetriever:
    def __init__(
        self,
        files: FileStore,
        *,
        verifier: FileVerifier | None = None,
        guard: NetGuard | None = None,
        limits: Limits | None = None,
        connection_policy: Callable[[HttpClient], None] = _require_connection_policy,
    ) -> None:
        self._files = files
        self._limits = limits or Limits()
        self._verifier = verifier or DefaultFileVerifier(self._limits)
        self._guard = guard or DefaultNetGuard()
        self._connection_policy = connection_policy

    async def retrieve(
        self,
        project_id: str,
        work: ProjectWork,
        locations: Sequence[AccessLocation],
        *,
        http: HttpClient,
        is_cancelled: Callable[[], bool],
    ) -> RetrievalOutcome:
        _cancel(is_cancelled)
        if project_id != work.project_id:
            error = NotAuthorized("Work belongs to another project")
            return RetrievalOutcome(RetrievalState.FAILED, None, [], error.to_payload())
        eligible = [location for location in locations if location.eligible]
        if not eligible:
            error = PrerequisiteNotMet("No eligible retrieval locations")
            return RetrievalOutcome(RetrievalState.FAILED, None, [], error.to_payload())
        try:
            self._connection_policy(http)
        except RcError as exc:
            return RetrievalOutcome(RetrievalState.FAILED, None, [], exc.to_payload())
        metadata = effective_metadata(work)
        failures = []
        last_error: RcError = FileValidationFailed("No valid PDF was retrieved")
        for location in eligible:
            _cancel(is_cancelled)
            tmp = self._files.new_tmp_path(".pdf")
            try:
                deadline = time.monotonic() + self._limits.download_timeout_s
                async with asyncio.timeout(self._limits.download_timeout_s):
                    result = await http.download(
                        location.url,
                        dest=tmp,
                        max_bytes=self._limits.download_bytes,
                        timeout_s=self._limits.download_timeout_s,
                        max_redirects=self._limits.max_redirects,
                        guard=self._guard,
                        is_cancelled=is_cancelled,
                    )
                    _cancel(is_cancelled)
                    if result.path != tmp or not 200 <= result.status < 300:
                        raise FileValidationFailed("Download did not return a successful PDF response")
                    size = tmp.stat().st_size
                    if size > self._limits.download_bytes:
                        raise LimitExceeded("Downloaded PDF exceeds byte limit")
                    if size != result.bytes:
                        raise FileValidationFailed("Download byte count does not match temporary file")
                    # A missing HTTP media type is not an assertion that this is PDF.
                    if result.content_type is None:
                        raise FileValidationFailed("Download has no Content-Type")
                    verification = await _in_thread(
                        self._verifier.verify, tmp, metadata, content_type=result.content_type
                    )
                    _cancel(is_cancelled)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Verification exceeded retrieval deadline")
                    if verification.state not in {RetrievalState.VERIFIED, RetrievalState.NEEDS_REVIEW}:
                        raise FileValidationFailed(
                            "Downloaded file is not a readable PDF", details=verification.evidence
                        )
                    blob = await _in_thread(self._files.commit, tmp, "application/pdf")
                    self._files.grant(project_id, blob.sha256)
                    wf = WorkFile(
                        work_file_id=new_id("work_file"),
                        project_id=project_id,
                        work_id=work.work_id,
                        sha256=blob.sha256,
                        bytes=blob.bytes,
                        source_url=location.url,
                        final_url=result.final_url,
                        location_id=location.location_id,
                        state=verification.state,
                        reasons=verification.reasons,
                        export_name=export_file_name(metadata),
                    )
                    return RetrievalOutcome(verification.state, wf, verification.reasons)
            except Cancelled:
                raise
            except (TimeoutError, OSError) as exc:
                last_error = TransientNetworkError(
                    f"Retrieval failed: {type(exc).__name__}",
                    recovery_hint="Check storage availability and retry the eligible location.",
                )
            except RcError as exc:
                last_error = exc
            finally:
                tmp.unlink(missing_ok=True)  # noqa: ASYNC240 - cleanup must survive cancellation
            failures.append(f"{location.location_id}: {last_error.code.value}")
        return RetrievalOutcome(RetrievalState.FAILED, None, failures, last_error.to_payload())
