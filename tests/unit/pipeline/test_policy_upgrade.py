"""Do not silently execute a saved job under a different identity policy."""

import asyncio

from refcollector.config import IDENTITY_POLICY_VERSION
from refcollector.contracts import api
from tests.unit.pipeline.conftest import context, create, run


def test_previous_identity_policy_snapshot_fails_closed_without_provider_io(app, offline):
    pid, sid = create(app)
    run(app, pid, sid, stages=("extract",))
    queued = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=["resolve"],
            scope=api.SourceScope(source_ids=[sid]),
            idempotency_key="previous-policy-job",
        ),
    )
    job = app.executor.queue.get_job(pid, queued.job_id)
    assert job.snapshot.identity_policy_version == IDENTITY_POLICY_VERSION
    job.snapshot.identity_policy_version = "identity-policy-v1"
    app.executor.queue.put_job(job)
    asyncio.run(app.executor.run_until_idle())
    step = app.executor.queue.steps(job.job_id)[0]
    assert step.state == "failed_permanent"
    assert step.last_error["code"] == "INTEGRATION_UNAVAILABLE"
    assert offline.requests == [] and offline.clients == []
