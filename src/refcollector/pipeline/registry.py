"""Stage-time planning with project-scoped, state-sensitive reuse keys."""

from refcollector.contracts.ports import StepSpec
from refcollector.domain.enums import (
    ACCEPTED_IDENTITIES,
    AuditEventType,
    ExtractionState,
    IdentityState,
    StepType,
)
from refcollector.pipeline.state import input_key, scoped, view


class JobPlanner:
    @staticmethod
    def invalidated(store, row):
        events = store.audit.list(row.project_id, subject_id=row.occurrence_id)
        edits = [e.created_at for e in events if e.type == AuditEventType.OCCURRENCE_EDITED]
        resolved = [e.created_at for e in events if e.type == AuditEventType.CANDIDATES_UPDATED]
        return bool(edits and (not resolved or max(edits) > max(resolved)))

    def plan(self, store, job, stage):
        if stage == StepType.EXTRACT:
            subjects = list(dict.fromkeys(job.scope.get("source_ids", [])))
        elif stage in (StepType.EXPORT, StepType.DRAFT):
            subjects = [job.job_id]
        else:
            rows = [o for o in scoped(store, job) if o.extraction != ExtractionState.EXCLUDED]
            cap = job.options.get("max_occurrences")
            rows = rows[:cap] if cap else rows
            store.occurrences.link_to_job(job.project_id, job.job_id, [o.occurrence_id for o in rows])
            if stage == StepType.RESOLVE:
                rows = [
                    o
                    for o in rows
                    if o.identity == IdentityState.UNPROCESSED
                    or self.invalidated(store, o)
                    or job.options.get("refresh")
                ]
            else:
                rows = [o for o in rows if o.identity in ACCEPTED_IDENTITIES and o.work_id]
            cap = job.options.get("max_occurrences")
            if cap:
                rows = rows[:cap]
            store.occurrences.link_to_job(job.project_id, job.job_id, [o.occurrence_id for o in rows])
            subjects = list(
                dict.fromkeys(o.occurrence_id if stage == StepType.RESOLVE else o.work_id for o in rows)
            )
        specs = []
        for subject in subjects:
            data = view(store, job, stage, subject)
            # Artifact descriptors carry job links. Never reuse artifacts from another job.
            specs.append(StepSpec(stage, subject, input_key(job, subject, data)))
        return specs


def handler_registry(settings, providers, extractor):
    from refcollector.pipeline.handlers import DiscoverHandler, ExtractHandler, ResolveHandler
    from refcollector.pipeline.outputs import DraftHandler, ExportHandler
    from refcollector.pipeline.retrieve import RetrieveHandler

    handlers = [
        ExtractHandler(extractor),
        ResolveHandler(providers),
        DiscoverHandler(providers),
        RetrieveHandler(settings, providers),
        ExportHandler(),
        DraftHandler(),
    ]
    return {handler.step_type: handler for handler in handlers}
