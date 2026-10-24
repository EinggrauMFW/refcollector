"""Composition root: build Store, executor, adapters and service for one RC_HOME.

WP-10 implements `open_app`. MCP (WP-11) and CLI (WP-12) call it and use only the
returned `AppHandle` (contracts.api).
"""

from __future__ import annotations

import asyncio
import threading

from refcollector.config import Settings
from refcollector.domain.enums import TERMINAL_JOB_STATES, TERMINAL_STEP_STATES
from refcollector.executor.loop import Executor
from refcollector.executor.queue import SqliteJobQueue
from refcollector.extraction.builtin import BuiltinExtractionAdapter
from refcollector.files.local_copies import DefaultLocalCopyChecker
from refcollector.files.verify import DefaultFileVerifier
from refcollector.pipeline.clients import ProviderFactory
from refcollector.pipeline.files import SessionFileStore
from refcollector.pipeline.native import NativeExtractor, NativeVerifier
from refcollector.pipeline.registry import JobPlanner, handler_registry
from refcollector.pipeline.state import input_key, view
from refcollector.services.service import RefCollectorServiceImpl
from refcollector.storage.db import Database
from refcollector.storage.repos import SqliteStore


class PipelineExecutor(Executor):
    """Drain workers before SQLite closes; clean only this step's staged paths."""

    def _reuse(self, step):
        job = self.queue.get_job(step.project_id, step.job_id)
        data = view(self.store, job, step.step_type, step.subject_id)
        step.input_fingerprint = input_key(job, step.subject_id, data)
        reused = super()._reuse(step)
        if reused and step.step_type == "extract":
            rows = [o for o in data["rows"] if o.extraction != "excluded"]
            cap = job.options.get("max_occurrences")
            remaining = max(0, cap - data["used_cap"]) if cap else len(rows)
            self.store.occurrences.link_to_job(
                job.project_id, job.job_id, [o.occurrence_id for o in rows[:remaining]]
            )
        return reused

    def _advance(self, job):
        changed = super()._advance(job)
        if job.state in TERMINAL_JOB_STATES and job.job_id in self.providers.jobs:
            task = asyncio.create_task(self.providers.close_job(job.job_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return changed

    async def _run(self, step):
        try:
            await super()._run(step)
        finally:
            handler = self.handlers.get(step.step_type)
            if hasattr(handler, "cleanup"):
                handler.cleanup(step.step_id)
            job = self.queue.get_job(step.project_id, step.job_id)
            if job.current_stage == len(job.stages) - 1 and all(
                s.state in TERMINAL_STEP_STATES for s in self.queue.steps(step.job_id)
            ):
                await self.providers.close_job(step.job_id)

    async def run_until_idle(self):
        try:
            await super().run_until_idle()
        finally:
            await self.providers.close()

    async def _main(self):
        try:
            await super()._main()
        finally:
            await self.providers.close()

    def try_acquire(self):
        owned = self.is_owner
        acquired = super().try_acquire()
        if acquired and not owned:
            # Clients can import while we own execution. Global tmp cleanup is unsafe.
            self.store.files.index_orphans()
            self.store.files.cleanup_abandoned()
        return acquired

    def stop(self, timeout=None):
        super().stop(timeout=timeout)


class AppHandle:
    def __init__(self, settings, db, store, service, executor):
        self.settings, self.db, self.store = settings, db, store
        self.service, self.executor = service, executor
        self._closed = False
        self._mutex = threading.Lock()

    def start_executor(self):
        with self._mutex:
            if self._closed:
                return False
            try:
                owned = self.executor.try_acquire()
                self.executor.start()
                return owned
            except BaseException:
                self.executor.stop()
                self.store.files.close()
                self.db.close()
                self._closed = True
                raise

    def close(self):
        with self._mutex:
            if self._closed:
                return
            self.executor.stop()
            self.store.files.close()
            self.db.close()
            self._closed = True


def open_app(settings: Settings) -> AppHandle:
    db = Database(settings.db_path)
    files = None
    try:
        for path in (settings.rc_home, settings.tmp_dir, settings.store_dir, settings.artifacts_dir):
            path.mkdir(parents=True, exist_ok=True)
        db.migrate()
        store = SqliteStore(db, settings)
        files = SessionFileStore(db, settings)
        store.files = files
        queue = SqliteJobQueue(db)
        extractor = NativeExtractor(BuiltinExtractionAdapter(settings.limits))
        providers = ProviderFactory(db, settings.limits)
        service = RefCollectorServiceImpl(
            settings,
            store,
            queue,
            extractor=extractor,
            identity_factory=providers.identity,
            local_copies=DefaultLocalCopyChecker(
                store.files,
                store.work_files,
                limits=settings.limits,
                verifier=NativeVerifier(DefaultFileVerifier(settings.limits)),
            ),
        )
        executor = PipelineExecutor(
            settings,
            db,
            store,
            handler_registry(settings, providers, extractor),
            JobPlanner(),
            max_concurrency=1,
        )
        executor.providers = providers
        service.executor = executor
        return AppHandle(settings, db, store, service, executor)
    except BaseException:
        if files:
            files.close()
        db.close()
        raise
