"""Occurrence-bound DOI retrieval without accepting bibliographic metadata."""

import hashlib
from contextlib import nullcontext

from refcollector.contracts.ports import PdfRetrievalOutcome, StepResult
from refcollector.domain.enums import AuditEventType, ExtractionState, RetrievalState, StepType, VersionPolicy
from refcollector.domain.models import CitationFile, WorkMetadata
from refcollector.errors import (
    BudgetExceeded,
    Cancelled,
    InvalidInput,
    RcError,
    RevisionConflict,
    TransientNetworkError,
)
from refcollector.identity.identifiers import unique_doi
from refcollector.ids import new_id
from refcollector.pipeline.retrieve import RetrieveHandler
from refcollector.pipeline.state import audit, blocking, capture, check_cancel, validate


class DoiRetrieveHandler(RetrieveHandler):
    step_type = StepType.RETRIEVE_DOI

    async def prepare(self, ctx, staged):
        data = await blocking(capture, ctx)
        occ = data["occ"]
        target = ctx.job.options["doi_targets"][occ.occurrence_id]
        doi = unique_doi(occ.text)
        text_sha = hashlib.sha256(occ.text.encode("utf-8")).hexdigest()
        if (
            occ.extraction == ExtractionState.EXCLUDED
            or not doi
            or target != {"doi": doi, "revision": occ.revision, "text_sha256": text_sha}
        ):
            raise RevisionConflict("Citation DOI changed after download was queued")
        if (
            ctx.job.snapshot.version_policy != VersionPolicy.ANY_ELIGIBLE
            or not ctx.job.snapshot.pdf_fallback_url
        ):
            raise InvalidInput("Citation DOI retrieval requires configured fallback and any_eligible policy")
        check_cancel(ctx)
        for file in reversed(data["files"]):
            if file.is_current(occ) and file.state in (RetrievalState.VERIFIED, RetrievalState.NEEDS_REVIEW):
                if staged.has_grant(ctx.job.project_id, file.sha256):
                    path = staged.path_for(ctx.job.project_id, file.sha256)
                    if path.is_file() and await blocking(self.file_hash, path) == file.sha256:

                        def reuse(store, file=file):
                            validate(ctx, store, data)
                            check_cancel(ctx)
                            staged.cleanup()
                            return file.citation_file_id

                        return StepResult(reuse)

        budget = ctx.job.options.get("budget", {})
        used_files, used_bytes = self.usage(ctx)
        if used_files >= budget.get("max_files", float("inf")):
            raise BudgetExceeded("Job file budget exhausted")
        remaining = budget.get("max_bytes", used_bytes + self.settings.limits.download_bytes) - used_bytes
        if remaining <= 0:
            raise BudgetExceeded("Job byte budget exhausted")
        limits = self.settings.limits.model_copy(
            update={"download_bytes": min(remaining, self.settings.limits.download_bytes)}
        )
        browser_attempted = False
        outcome = PdfRetrievalOutcome(RetrievalState.NOT_REQUESTED, None, [])
        try:
            async with self.providers.job(ctx.job) as (http, _resolver, _access):
                location = await self.fallback_factory(staged, limits).resolve(
                    ctx.job.snapshot.pdf_fallback_url,
                    doi,
                    http=http,
                    is_cancelled=ctx.is_cancelled,
                )
                check_cancel(ctx)
                if location:
                    outcome = await self.retriever_factory(staged, limits).retrieve_pdf(
                        ctx.job.project_id,
                        WorkMetadata(doi=doi),
                        [location],
                        http=http,
                        is_cancelled=ctx.is_cancelled,
                    )
                if ctx.job.snapshot.pdf_browser_python and (location is None or outcome.blob is None):
                    browser_attempted = True
                    browser = self.browser_factory(
                        python=ctx.job.snapshot.pdf_browser_python,
                        binary=ctx.job.snapshot.pdf_browser_binary or "",
                        limits=limits,
                    )
                    location = await browser.resolve(
                        ctx.job.snapshot.pdf_fallback_url, doi, is_cancelled=ctx.is_cancelled
                    )
                    browser_agent = getattr(browser, "user_agent", None)
                    check_cancel(ctx)
                    if location:
                        profile = (
                            http.browser_pdf_profile(location.url, browser_agent)
                            if browser_agent
                            else nullcontext()
                        )
                        with profile:
                            outcome = await self.retriever_factory(staged, limits).retrieve_pdf(
                                ctx.job.project_id,
                                WorkMetadata(doi=doi),
                                [location],
                                http=http,
                                is_cancelled=ctx.is_cancelled,
                            )
                if location is None:
                    outcome.reasons.append("Configured PDF fallback returned no PDF link.")
        except Cancelled:
            raise
        except TimeoutError:
            error = TransientNetworkError("Configured PDF fallback timed out")
            outcome = PdfRetrievalOutcome(RetrievalState.FAILED, None, [], error.to_payload())
        except RcError as error:
            check_cancel(ctx)
            outcome = PdfRetrievalOutcome(RetrievalState.FAILED, None, [], error.to_payload())
        check_cancel(ctx)
        file = None
        if outcome.blob:
            reasons = list(outcome.reasons)
            state = outcome.state
            if outcome.verification_evidence.get("version_conflict"):
                reasons.append("VERSION_CONFLICT")
                state = RetrievalState.NEEDS_REVIEW
            file = CitationFile(
                citation_file_id=new_id("citation_file"),
                project_id=ctx.job.project_id,
                occurrence_id=occ.occurrence_id,
                target_doi=doi,
                citation_revision=occ.revision,
                citation_text_sha256=text_sha,
                job_id=ctx.job.job_id,
                sha256=outcome.blob.sha256,
                bytes=outcome.blob.bytes,
                source_url=outcome.source_url,
                final_url=outcome.final_url,
                state=state,
                reasons=reasons,
                export_name=f"{occ.occurrence_id}_doi.pdf",
                verification_evidence=outcome.verification_evidence,
            )

        def commit(store):
            try:
                validate(ctx, store, data)
                check_cancel(ctx)
                # Concurrent jobs may have published this exact citation while the PDF was fetched.
                # The captured file list is part of validate; retries then take the reuse path.
                if file:
                    current_files, current_bytes = self.usage(ctx)
                    if current_files >= budget.get(
                        "max_files", float("inf")
                    ) or current_bytes + file.bytes > budget.get("max_bytes", float("inf")):
                        raise BudgetExceeded("Job file or byte budget exhausted before publication")
                staged.publish()
                if file:
                    store.citation_files.add(file)
                audit(
                    ctx,
                    store,
                    AuditEventType.FILE_RETRIEVED,
                    occ.occurrence_id,
                    retrieval_basis="citation_doi",
                    target_doi=doi,
                    citation_file_id=file.citation_file_id if file else None,
                    state=file.state.value if file else outcome.state.value,
                    reasons=file.reasons if file else outcome.reasons,
                    error=outcome.error,
                    bytes=file.bytes if file else 0,
                    browser_attempted=browser_attempted,
                )
                return file.citation_file_id if file else occ.occurrence_id
            finally:
                staged.cleanup()

        warnings = list(file.reasons if file else outcome.reasons)
        if outcome.error:
            warnings.append(outcome.error["code"])
        return StepResult(commit, warnings)

    @staticmethod
    def file_hash(path):
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
