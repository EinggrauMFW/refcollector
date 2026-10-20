import asyncio

from refcollector.bootstrap import open_app
from refcollector.config import Settings
from refcollector.contracts import api
from refcollector.domain.enums import Channel, Scope


def context():
    return api.CallContext(
        principal="test", channel=Channel.CLI, scopes=frozenset(Scope), request_id="request"
    )


def test_w05_w06_real_root_extract_and_reopen(tmp_path):
    settings = Settings(rc_home=tmp_path)
    app = open_app(settings)
    ctx = context()
    try:
        pid = app.service.create_project(
            ctx, api.CreateProjectIn(name="test", idempotency_key="project1")
        ).project_id
        sid = app.service.import_source(
            ctx,
            api.ImportSourceIn(
                project_id=pid,
                input=api.TextInput(
                    type="text", text="Smith, J. (2021). Neural wavefields. Geophysics, 86, 12-20."
                ),
                idempotency_key="source01",
            ),
        ).source_id
        job = app.service.start_job(
            ctx,
            api.StartJobIn(
                project_id=pid,
                stages=["extract"],
                scope=api.SourceScope(source_ids=[sid]),
                idempotency_key="extract1",
            ),
        )
        asyncio.run(app.executor.run_until_idle())
        result = app.service.get_job(ctx, api.GetJobIn(project_id=pid, job_id=job.job_id))
        assert result.state == "completed"
        assert result.results.occurrence_count == 1
        refs = app.service.list_references(
            ctx, api.ListReferencesIn(project_id=pid, filter=api.ReferenceFilter(job_id=job.job_id))
        )
        assert len(refs.occurrences) == 1
    finally:
        app.close()
    reopened = open_app(settings)
    try:
        assert (
            reopened.service.get_job(
                ctx, api.GetJobIn(project_id=pid, job_id=job.job_id)
            ).results.occurrence_count
            == 1
        )
    finally:
        reopened.close()
