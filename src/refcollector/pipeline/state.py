"""Consistent inputs and optimistic validation for pipeline commits."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from refcollector.contracts.ports import OccurrenceQuery
from refcollector.domain.enums import AuditEventType
from refcollector.domain.models import AuditEvent
from refcollector.errors import Cancelled, RevisionConflict
from refcollector.ids import new_id


def fingerprint(value: Any) -> str:
    def encode(obj: Any) -> Any:
        return obj.model_dump(mode="json")

    return hashlib.sha256(json.dumps(value, default=encode, sort_keys=True).encode()).hexdigest()


def occurrences(store, pid, query=None):
    rows, after = [], None
    while True:
        page = store.occurrences.list(pid, query or OccurrenceQuery(), after=after, limit=500)
        rows.extend(page.items)
        if page.next_after is None:
            return rows
        after = page.next_after


def scoped(store, job):
    scope, pid = job.scope, job.project_id
    if "occurrence_ids" in scope:
        return store.occurrences.get_many(pid, list(dict.fromkeys(scope["occurrence_ids"])))
    if "source_ids" in scope:
        rows = []
        for sid in dict.fromkeys(scope["source_ids"]):
            store.sources.get(pid, sid)
            rows.extend(occurrences(store, pid, OccurrenceQuery(source_id=sid)))
        return rows
    return occurrences(store, pid, OccurrenceQuery(**scope.get("filter", {})))


def view(store, job, stage, subject):
    """Detached documents, read in one transaction by the caller."""
    pid = job.project_id
    if stage == "extract":
        source = store.sources.get(pid, subject)
        used_cap = (
            store.occurrences.list(pid, OccurrenceQuery(job_id=job.job_id), limit=1).total or 0
            if job.options.get("max_occurrences")
            else 0
        )
        return {
            "source": source,
            "rows": occurrences(store, pid, OccurrenceQuery(source_id=subject)),
            "used_cap": used_cap,
        }
    if stage == "resolve":
        occ = store.occurrences.get(pid, subject)
        return {
            "occ": occ,
            "candidates": store.candidates.list_for_occurrence(pid, subject),
            "decisions": store.decisions.list_for_occurrence(pid, subject),
            "work": store.works.get(pid, occ.work_id) if occ.work_id else None,
        }
    if stage in ("retrieve", "discover_access"):
        return {
            "work": store.works.get(pid, subject),
            "rows": occurrences(store, pid, OccurrenceQuery(work_id=subject)),
            "access": store.access_checks.current(pid, subject),
            "files": store.work_files.list_for_work(pid, subject),
            "copies": store.local_copies.list_for_work(pid, subject),
        }
    rows = scoped(store, job)
    return {
        "project": store.projects.get(pid),
        "rows": rows,
        "works": {o.work_id: store.works.get(pid, o.work_id) for o in rows if o.work_id},
        "decisions": {
            o.occurrence_id: store.decisions.list_for_occurrence(pid, o.occurrence_id) for o in rows
        },
        "access": {o.work_id: store.access_checks.current(pid, o.work_id) for o in rows if o.work_id},
        "files": {o.work_id: store.work_files.list_for_work(pid, o.work_id) for o in rows if o.work_id},
    }


def capture(ctx):
    with ctx.store.transaction():
        data = view(ctx.store, ctx.job, ctx.step.step_type, ctx.step.subject_id)
    ctx.step.input_fingerprint = input_key(ctx.job, ctx.step.subject_id, data)
    return data


def input_key(job, subject, data):
    return fingerprint(
        {
            "project": job.project_id,
            "subject": subject,
            "data": data,
            "options": job.options,
            "scope": job.scope,
            "refresh_job": job.job_id if job.options.get("refresh") else None,
        }
    )


def validate(ctx, store, data):
    current = view(store, ctx.job, ctx.step.step_type, ctx.step.subject_id)
    if fingerprint(current) != fingerprint(data):
        raise RevisionConflict("Pipeline input changed before commit")


def check_cancel(ctx):
    if ctx.is_cancelled():
        raise Cancelled("Job cancelled")


async def blocking(fn, *args, **kwargs):
    """Drain native work before cancellation lets resources close."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except BaseException:
        await asyncio.gather(task, return_exceptions=True)
        raise


def audit(ctx, store, type_: AuditEventType, subject, **payload):
    revision = store.projects.bump_revision(ctx.job.project_id)
    store.audit.append(
        AuditEvent(
            event_id=new_id("event"),
            project_id=ctx.job.project_id,
            type=type_,
            subject_id=subject,
            provenance=ctx.job.provenance,
            project_revision=revision,
            payload={"job_id": ctx.job.job_id, **payload},
        )
    )
