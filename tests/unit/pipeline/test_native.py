"""Deterministic native-owner tests. No concurrent PyMuPDF probe or native monkeypatch."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from refcollector.bootstrap import open_app
from refcollector.config import Settings
from refcollector.contracts import api
from refcollector.contracts.ports import ExtractedEntry, ExtractionResult, VerificationResult
from refcollector.domain.enums import Channel, CopyVersion, RetrievalState, SourceType, StepType
from refcollector.domain.models import Occurrence, Preflight, ProjectWork, WorkFile, WorkMetadata
from tests.unit.pipeline.conftest import context


class Probe:
    def __init__(self):
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.events = []
        self.active = 0
        self.maximum = 0

    def call(self, label, result):
        owner = threading.get_ident()
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.events.append((label, "open", owner))
        self.entered.set()
        try:
            assert self.release.wait(5), "native probe release timed out"
            self.events.append((label, "read", threading.get_ident()))
            if isinstance(result, BaseException):
                raise result
            return result
        finally:
            with self.lock:
                self.events.append((label, "close", threading.get_ident()))
                self.active -= 1

    def reset(self):
        self.events.clear()
        self.maximum = 0
        self.entered.clear()
        self.release.clear()


class Extractor:
    name = "probe"
    version = "test-v1"

    def __init__(self, probe):
        self.probe = probe
        self.calls = []
        self.preflight_result = Preflight(pages=0, text_coverage=0)
        self.extract_result = ExtractionResult([], [ExtractedEntry("Extracted citation", None)])

    def preflight(self, data, kind):
        assert kind == SourceType.TEXT and isinstance(data, str)
        self.calls.append((data, kind))
        return self.probe.call("preflight", self.preflight_result)

    def extract(self, data, kind, sections=None):
        assert kind == SourceType.TEXT and isinstance(data, str)
        self.calls.append((data, kind, sections))
        return self.probe.call("extract", self.extract_result)


class Verifier:
    def __init__(self, probe):
        self.probe = probe
        self.calls = []
        self.result = VerificationResult(RetrievalState.VERIFIED, [], True, True, CopyVersion.PUBLISHED)

    def verify(self, path, work, *, content_type=None):
        assert path.is_file()
        assert isinstance(work, WorkMetadata)
        assert content_type in (None, "application/pdf")
        self.calls.append((path, work, content_type))
        return self.probe.call("verify", self.result)


def count_submissions(monkeypatch, worker, count):
    submitted = threading.Event()
    lock = threading.Lock()
    original = worker.submit
    seen = 0

    def submit(*args, **kwargs):
        nonlocal seen
        future = original(*args, **kwargs)
        with lock:
            seen += 1
            if seen >= count:
                submitted.set()
        return future

    monkeypatch.setattr(worker, "submit", submit)
    return submitted


def test_complete_extractor_and_verifier_calls_use_one_owner_without_overlap(tmp_path, monkeypatch):
    from refcollector.pipeline import native

    probe = Probe()
    extractor = Extractor(probe)
    verifier = Verifier(probe)
    first = native.NativeExtractor(extractor)
    second_delegate = Extractor(probe)
    second = native.NativeExtractor(second_delegate)
    checked = native.NativeVerifier(verifier)
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"fake delegate input")
    assert (first.name, first.version) == (extractor.name, extractor.version)
    probe.reset()
    submitted = count_submissions(monkeypatch, native._WORKER, 3)
    caller_ids = set()
    sections = []
    work = WorkMetadata()

    def caller(method, *args, **kwargs):
        with probe.lock:
            caller_ids.add(threading.get_ident())
        return method(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=3) as callers:
        futures = [
            callers.submit(caller, first.preflight, "one", SourceType.TEXT),
            callers.submit(caller, second.extract, "two", SourceType.TEXT, sections),
            callers.submit(caller, checked.verify, path, work, content_type="application/pdf"),
        ]
        try:
            assert submitted.wait(5), "all callers must submit while the first delegate remains blocked"
            assert probe.entered.wait(5)
            assert len(probe.events) == probe.active == probe.maximum == 1
        finally:
            probe.release.set()
        results = [future.result(timeout=5) for future in futures]
    assert results[0] is extractor.preflight_result
    assert results[1] is second_delegate.extract_result
    assert results[2] is verifier.result
    assert extractor.calls == [("one", SourceType.TEXT)]
    assert second_delegate.calls[0][2] is sections
    assert verifier.calls == [(path, work, "application/pdf")]
    assert len(caller_ids) == 3
    owners = {event[2] for event in probe.events}
    assert len(owners) == 1 and owners.isdisjoint(caller_ids)
    assert probe.maximum == 1 and probe.active == 0
    assert [phase for _, phase, _ in probe.events] == ["open", "read", "close"] * 3


@pytest.mark.parametrize("operation", ["preflight", "extract", "verify"])
def test_native_wrapper_preserves_exception_and_worker_remains_usable(operation, tmp_path):
    from refcollector.errors import UnsupportedSource
    from refcollector.pipeline.native import NativeExtractor, NativeVerifier

    probe = Probe()
    failure = UnsupportedSource("delegate failure")
    extractor, verifier = Extractor(probe), Verifier(probe)
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"input")
    if operation == "verify":
        verifier.result = failure
        method, args = NativeVerifier(verifier).verify, (path, WorkMetadata())
    else:
        setattr(extractor, f"{operation}_result", failure)
        method, args = getattr(NativeExtractor(extractor), operation), ("text", SourceType.TEXT)
    with pytest.raises(UnsupportedSource) as raised:
        method(*args)
    assert raised.value is failure
    replacement = Extractor(probe)
    assert NativeExtractor(replacement).extract("next", SourceType.TEXT) is replacement.extract_result
    assert probe.active == 0 and len({event[2] for event in probe.events}) == 1


@pytest.mark.parametrize("stage", [StepType.EXTRACT, StepType.RETRIEVE])
def test_cross_handle_mcp_service_and_pipeline_share_native_owner(stage, tmp_path, monkeypatch):
    import refcollector.bootstrap as root
    import refcollector.pipeline.retrieve as retrieval
    from refcollector.mcp.tools import call_tool
    from refcollector.pipeline import native

    probe = Probe()
    monkeypatch.setattr(root, "BuiltinExtractionAdapter", lambda limits: Extractor(probe))
    monkeypatch.setattr(root, "DefaultFileVerifier", lambda limits: Verifier(probe), raising=False)
    monkeypatch.setattr(retrieval, "DefaultFileVerifier", lambda limits: Verifier(probe), raising=False)
    settings = Settings(rc_home=tmp_path / "home", allow_folders=[tmp_path], email="test@example.org")
    first, second = open_app(settings), open_app(settings)
    ctx = context()

    def mcp_context():
        return replace(context(), channel=Channel.MCP)

    path = tmp_path / "attachment.pdf"
    path.write_bytes(b"fake PDF consumed only by fake verifier")
    try:
        pid = first.service.create_project(
            ctx, api.CreateProjectIn(name="native", idempotency_key="native-project")
        ).project_id
        sources = [
            first.service.import_source(
                ctx,
                api.ImportSourceIn(
                    project_id=pid,
                    input=api.TextInput(type="text", text=f"source {number}"),
                    idempotency_key=f"native-source-{number}",
                ),
            ).source_id
            for number in range(2)
        ]
        first.service.update_project_settings(
            ctx,
            api.UpdateProjectSettingsIn(
                project_id=pid,
                network_mode="retrieval",
                download_enabled=True,
                idempotency_key="native-permission",
            ),
        )
        works, occurrences = [], []
        with first.store.transaction(immediate=True):
            for number, sid in enumerate(sources):
                work = ProjectWork(
                    work_id=f"wrk_native_{number}", project_id=pid, metadata=WorkMetadata(title="Known work")
                )
                first.store.works.add(work)
                occurrence = Occurrence(
                    occurrence_id=f"occ_native_{number}",
                    project_id=pid,
                    source_id=sid,
                    order=1,
                    raw_text="Known work",
                    identity="accepted_auto",
                    work_id=work.work_id,
                    discovery="complete",
                    classification="none_found",
                )
                first.store.occurrences.add_many([occurrence])
                works.append(work)
                occurrences.append(occurrence)
            temporary = first.store.files.new_tmp_path()
            temporary.write_bytes(path.read_bytes())
            blob = first.store.files.commit(temporary, "application/pdf")
            first.store.files.grant(pid, blob.sha256)
            first.store.work_files.add(
                WorkFile(
                    work_file_id="wfl_native",
                    project_id=pid,
                    work_id=works[1].work_id,
                    sha256=blob.sha256,
                    bytes=blob.bytes,
                    source_url="https://repo.example/paper",
                    final_url="https://repo.example/paper",
                    state="verified",
                    export_name="paper.pdf",
                )
            )
        if stage == StepType.EXTRACT:
            job = second.service.start_job(
                ctx,
                api.StartJobIn(
                    project_id=pid,
                    stages=["extract"],
                    scope=api.SourceScope(source_ids=[sources[1]]),
                    idempotency_key="native-extract",
                ),
            )
        else:
            job = second.service.download_files(
                ctx,
                api.DownloadFilesIn(
                    project_id=pid,
                    scope=api.OccurrenceScope(occurrence_ids=[occurrences[1].occurrence_id]),
                    idempotency_key="native-retrieve",
                ),
            )
        probe.reset()
        submitted = count_submissions(monkeypatch, native._WORKER, 3)

        async def exercise():
            tasks = [
                asyncio.create_task(
                    call_tool(
                        first.service,
                        mcp_context,
                        "import_source",
                        {
                            "project_id": pid,
                            "input": {"type": "text", "text": "concurrent new source"},
                            "idempotency_key": "native-mcp-import",
                        },
                    )
                ),
                asyncio.create_task(
                    call_tool(
                        first.service,
                        mcp_context,
                        "attach_file",
                        {
                            "project_id": pid,
                            "occurrence_id": occurrences[0].occurrence_id,
                            "path": str(path),
                            "idempotency_key": "native-mcp-attach",
                        },
                    )
                ),
                asyncio.create_task(second.executor.run_until_idle()),
            ]
            try:
                assert await asyncio.to_thread(submitted.wait, 5)
                assert await asyncio.to_thread(probe.entered.wait, 5)
                assert probe.maximum == probe.active == len(probe.events) == 1
            finally:
                probe.release.set()
                results = await asyncio.gather(*tasks)
            assert all(not result.is_error for result in results[:2])

        asyncio.run(exercise())
        assert second.executor.queue.get_job(pid, job.job_id).state == "completed"
        assert {label for label, _, _ in probe.events} >= {"preflight", "verify"}
        if stage == StepType.EXTRACT:
            assert "extract" in {label for label, _, _ in probe.events}
        owner = {event[2] for event in probe.events}
        assert len(owner) == 1 and probe.maximum == 1 and probe.active == 0
        first.close()
        # Closing one handle must not close the process-lifetime native worker.
        assert second.service.extractor.preflight("after close", SourceType.TEXT).pages == 0
        default = second.executor.handlers[StepType.RETRIEVE].retriever_factory(
            second.store.files, settings.limits
        )
        assert default._verifier.verify(path, works[1].metadata).state == "verified"
        assert {event[2] for event in probe.events} == owner
    finally:
        probe.release.set()
        first.close()
        second.close()
