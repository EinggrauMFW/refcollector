"""Local-copy-first retrieval with transactional publication and bounded budgets."""

from refcollector.access.versions import select_versions
from refcollector.contracts.ports import StepResult
from refcollector.domain.enums import (
    ACCEPTED_IDENTITIES,
    AuditEventType,
    CheckOutcome,
    ExtractionState,
    LocalCopyCheck,
    LocalCopyState,
    RetrievalState,
    StepType,
)
from refcollector.errors import BudgetExceeded
from refcollector.files.local_copies import DefaultLocalCopyChecker
from refcollector.files.retrieval import DefaultRetriever
from refcollector.files.verify import DefaultFileVerifier
from refcollector.pipeline.files import StagedFiles
from refcollector.pipeline.native import NativeVerifier
from refcollector.pipeline.state import audit, blocking, capture, check_cancel, validate


class RetrieveHandler:
    step_type = StepType.RETRIEVE

    def __init__(self, settings, providers):
        self.settings, self.providers = settings, providers
        self.pending = {}
        self.retriever_factory = lambda files, limits: DefaultRetriever(
            files, limits=limits, verifier=NativeVerifier(DefaultFileVerifier(limits))
        )

    async def run(self, ctx):
        staged = StagedFiles(ctx.store.files)
        self.pending[ctx.step.step_id] = staged
        try:
            return await self.prepare(ctx, staged)
        except BaseException:
            self.cleanup(ctx.step.step_id)
            raise

    async def prepare(self, ctx, staged):
        data = await blocking(capture, ctx)
        work = data["work"]
        check_cancel(ctx)
        checker = DefaultLocalCopyChecker(
            staged,
            ctx.store.work_files,
            limits=self.settings.limits,
            verifier=NativeVerifier(DefaultFileVerifier(self.settings.limits)),
        )
        copies = await blocking(
            checker.evaluate, ctx.job.project_id, work, version_policy=ctx.job.snapshot.version_policy
        )
        for attached in data["copies"]:
            if attached.sha256 and ctx.store.files.has_grant(ctx.job.project_id, attached.sha256):
                copies.extend(
                    await blocking(
                        checker.evaluate,
                        ctx.job.project_id,
                        work,
                        version_policy=ctx.job.snapshot.version_policy,
                        attached_path=ctx.store.files.path_for(ctx.job.project_id, attached.sha256),
                        version_hint=attached.version,
                    )
                )
        check_cancel(ctx)
        usable = any(
            c.state == LocalCopyState.USABLE
            and set(c.checks) == set(LocalCopyCheck)
            and all(v.outcome == CheckOutcome.PASS for v in c.checks.values())
            for c in copies
        )
        outcome = None
        warnings = []
        locations = select_versions(
            data["access"].locations if data["access"] else [], ctx.job.snapshot.version_policy
        )
        warnings.extend(str(r) for r in locations.reasons)
        skipped = not usable and not locations.locations
        if skipped:
            warnings.append(
                "Retrieval skipped: no eligible location matches version policy "
                f"'{ctx.job.snapshot.version_policy.value}'; no download was attempted."
            )
        if not usable and not skipped:
            budget = ctx.job.options.get("budget", {})
            used_files, used_bytes = self.usage(ctx)
            if used_files >= budget.get("max_files", float("inf")):
                raise BudgetExceeded("Job file budget exhausted")
            remaining = (
                budget["max_bytes"] - used_bytes
                if "max_bytes" in budget
                else self.settings.limits.download_bytes
            )
            if remaining <= 0:
                raise BudgetExceeded("Job byte budget exhausted")
            limits = self.settings.limits.model_copy(
                update={"download_bytes": min(remaining, self.settings.limits.download_bytes)}
            )
            try:
                async with self.providers.job(ctx.job) as (http, _resolver, _access):
                    outcome = await self.retriever_factory(staged, limits).retrieve(
                        ctx.job.project_id,
                        work,
                        locations.locations,
                        http=http,
                        is_cancelled=ctx.is_cancelled,
                    )
                check_cancel(ctx)
            except BaseException:
                staged.cleanup()
                raise
        state = (
            RetrievalState.SATISFIED_BY_LOCAL_COPY
            if usable
            else outcome.state
            if outcome
            else RetrievalState.NOT_REQUESTED
        )

        def commit(store):
            try:
                validate(ctx, store, data)
                staged.publish()
                for copy in copies:
                    store.local_copies.add(copy)
                if outcome and outcome.work_file:
                    store.work_files.add(outcome.work_file)
                for occ in data["rows"]:
                    if occ.identity not in ACCEPTED_IDENTITIES or occ.extraction == ExtractionState.EXCLUDED:
                        continue
                    occ.local_copy = LocalCopyState.USABLE if usable else LocalCopyState.UNUSABLE
                    if not skipped:
                        occ.retrieval = state
                    if occ.approval:
                        occ.approval.occurrence_revision = occ.revision + 1
                    store.occurrences.update(occ, expected_revision=occ.revision)
                audit(
                    ctx,
                    store,
                    AuditEventType.FILE_RETRIEVED,
                    work.work_id,
                    state=state.value,
                    reasons=outcome.reasons if outcome else warnings if skipped else [],
                    error=outcome.error if outcome else None,
                    bytes=outcome.work_file.bytes if outcome and outcome.work_file else 0,
                )
                return outcome.work_file.work_file_id if outcome and outcome.work_file else work.work_id
            finally:
                staged.cleanup()

        if outcome and outcome.error:
            warnings.append(outcome.error["code"])
        return StepResult(commit, warnings)

    def cleanup(self, step_id):
        staged = self.pending.pop(step_id, None)
        if staged:
            staged.cleanup()

    @staticmethod
    def usage(ctx):
        events = [
            e
            for e in ctx.store.audit.list(ctx.job.project_id)
            if e.type == AuditEventType.FILE_RETRIEVED
            and e.payload.get("job_id") == ctx.job.job_id
            and e.payload.get("bytes", 0) > 0
        ]
        return len(events), sum(e.payload["bytes"] for e in events)
