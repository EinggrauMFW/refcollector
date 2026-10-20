"""Snapshot-bound providers; clients never cross event loops."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from refcollector.access.discovery import DefaultAccessDiscoverer
from refcollector.config import ELIGIBILITY_POLICY_VERSION, IDENTITY_POLICY_VERSION
from refcollector.errors import IntegrationUnavailable
from refcollector.identity.resolver import DefaultIdentityResolver
from refcollector.providers.crossref import CrossrefProvider
from refcollector.providers.datacite import DataCiteProvider
from refcollector.providers.doiorg import DoiOrgProvider
from refcollector.providers.http import ProviderHttpClient
from refcollector.providers.openalex import OpenAlexProvider
from refcollector.providers.openlibrary import OpenLibraryProvider
from refcollector.providers.semanticscholar import SemanticScholarProvider
from refcollector.providers.unpaywall import UnpaywallProvider


class ProviderFactory:
    def __init__(self, db, limits, http_factory=None):
        self.db, self.limits = db, limits
        self.http_factory = http_factory or ProviderHttpClient
        self.jobs = {}

    @asynccontextmanager
    async def job(self, job):
        loop = asyncio.get_running_loop()
        if job.job_id not in self.jobs:
            manager = self.session(job.snapshot)
            session = await manager.__aenter__()
            self.jobs[job.job_id] = (manager, session, loop)
        if self.jobs[job.job_id][2] is not loop:
            raise IntegrationUnavailable("Job client belongs to another event loop")
        yield self.jobs[job.job_id][1]

    async def close_job(self, job_id):
        entry = self.jobs.pop(job_id, None)
        if entry:
            manager, _session, _loop = entry
            task = asyncio.create_task(manager.__aexit__(None, None, None))
            try:
                await asyncio.shield(task)
            except BaseException:
                await asyncio.gather(task, return_exceptions=True)
                raise

    async def close(self):
        for job_id in list(self.jobs):
            await self.close_job(job_id)

    @asynccontextmanager
    async def session(self, snapshot):
        if (
            snapshot.identity_policy_version != IDENTITY_POLICY_VERSION
            or snapshot.eligibility_policy_version != ELIGIBILITY_POLICY_VERSION
        ):
            raise IntegrationUnavailable("Snapshotted policy version is unavailable")
        http = self.http_factory(snapshot, self.db, self.limits)
        try:
            providers = [
                CrossrefProvider(http, self.limits),
                DataCiteProvider(http),
                DoiOrgProvider(http),
                OpenAlexProvider(http, self.limits),
                SemanticScholarProvider(http, self.limits),
                OpenLibraryProvider(http),
                UnpaywallProvider(http, snapshot.contact_email, self.limits),
            ]
            providers = [p for p in providers if p.name in snapshot.provider_allowlist]
            yield (
                http,
                DefaultIdentityResolver([p for p in providers if hasattr(p, "search")]),
                DefaultAccessDiscoverer(
                    [p for p in providers if hasattr(p, "locations")],
                    enabled_providers=snapshot.provider_allowlist,
                ),
            )
        finally:
            await http.aclose()

    def identity(self, snapshot):
        factory = self

        class Resolver(DefaultIdentityResolver):
            async def resolve(self, *args, **kwargs):
                async with factory.session(snapshot) as (_, resolver, _access):
                    return await resolver.resolve(*args, **kwargs)

        return Resolver([])
