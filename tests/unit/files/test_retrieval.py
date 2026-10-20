"""Retrieval behavior over fake HTTP and real content-addressed storage."""

import asyncio
import hashlib

import pytest

from refcollector.config import Limits
from refcollector.contracts.ports import DownloadResult
from refcollector.domain.enums import LocationBasis, RetrievalState
from refcollector.domain.models import AccessLocation, ProjectWork, WorkMetadata
from refcollector.errors import Cancelled, IntegrationUnavailable, LimitExceeded, TransientNetworkError
from refcollector.files import DefaultFileVerifier, DefaultNetGuard, DefaultRetriever
from tests.fixtures.pdfs import paper_pdf
from tests.unit.files.test_guard import resolver

TITLE = "Ocean wave energy converter control"
DOI = "10.1234/waves.1"


def work():
    return ProjectWork(work_id="wrk_a", project_id="prj_a", metadata=WorkMetadata(title=TITLE, doi=DOI))


def location(name="one", eligible=True):
    return AccessLocation(
        location_id=name,
        url=f"https://repo.example/{name}",
        provider="test",
        basis=LocationBasis.REPOSITORY,
        eligible=eligible,
    )


class FakeHttp:
    """No sockets. Responses can fail after writing a partial stream."""

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    async def download(self, url, *, dest, max_bytes, timeout_s, max_redirects, guard, is_cancelled):
        self.calls.append((url, max_bytes, timeout_s, max_redirects))
        await guard.check(url)
        value = next(self.responses)
        dest.write_bytes(b"partial")
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return await value(dest, guard)
        dest.write_bytes(value)
        if len(value) > max_bytes:
            raise LimitExceeded("too large")
        return DownloadResult(url, 200, "application/pdf", dest, len(value))


def offline_connection_policy(http):
    if not isinstance(http, FakeHttp):
        raise IntegrationUnavailable("Only this socket-free test transport is authorized")


def retriever(store, **kw):
    return DefaultRetriever(
        store.files,
        guard=DefaultNetGuard(resolve=resolver("8.8.8.8")),
        connection_policy=offline_connection_policy,
        **kw,
    )


@pytest.mark.anyio
async def test_defaults_fail_closed_without_connection_boundary_authorization(fake_store):
    http = FakeHttp(paper_pdf(TITLE, [], DOI))
    result = await DefaultRetriever(fake_store.files).retrieve(
        "prj_a", work(), [location()], http=http, is_cancelled=lambda: False
    )
    assert result.state == RetrievalState.FAILED and result.work_file is None
    assert result.error["code"] == "INTEGRATION_UNAVAILABLE"
    assert not http.calls and not fake_store.files.blobs


@pytest.mark.anyio
async def test_w24_verified_hash_grant_filename_effective_metadata_and_unsaved(fake_store):
    accepted = work()
    accepted.metadata.title = "Old provider title"
    accepted.overrides = {"title": TITLE, "year": 2024}
    accepted.locks = {"title": True}
    before = accepted.model_dump()
    data = paper_pdf(TITLE, [], DOI)
    result = await retriever(fake_store).retrieve(
        "prj_a", accepted, [location()], http=FakeHttp(data), is_cancelled=lambda: False
    )
    wf = result.work_file
    assert result.state == RetrievalState.VERIFIED and wf is not None
    assert wf.sha256 == hashlib.sha256(data).hexdigest() and wf.bytes == len(data)
    assert wf.export_name == "2024_anon_Ocean_wave_energy_converter_control.pdf"
    assert wf.location_id == "one" and wf.source_url == wf.final_url == location().url
    assert fake_store.files.has_grant("prj_a", wf.sha256)
    assert not fake_store.files.has_grant("prj_b", wf.sha256)
    assert fake_store.work_files.list_for_work("prj_a", "wrk_a") == []
    assert accepted.model_dump() == before and list(fake_store.files.tmp.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "data", [paper_pdf(TITLE, [], DOI, supplement=True), paper_pdf("Different paper", [], "10.4321/other")]
)
async def test_at09bc_w24_review_files_are_stored_with_reasons(fake_store, data):
    result = await retriever(fake_store).retrieve(
        "prj_a", work(), [location()], http=FakeHttp(data), is_cancelled=lambda: False
    )
    assert result.state == RetrievalState.NEEDS_REVIEW
    assert result.work_file.state == RetrievalState.NEEDS_REVIEW and result.work_file.reasons
    assert fake_store.files.path_for("prj_a", result.work_file.sha256).read_bytes() == data


@pytest.mark.anyio
async def test_at09a_invalid_nothing_stored_and_policy_order_fallback(fake_store):
    http = FakeHttp(b"<html>login</html>", TransientNetworkError("broken"), paper_pdf(TITLE, [], DOI))
    result = await retriever(fake_store).retrieve(
        "prj_a",
        work(),
        [location("skip", False), location("bad"), location("broken"), location("good")],
        http=http,
        is_cancelled=lambda: False,
    )
    assert result.state == RetrievalState.VERIFIED
    assert [c[0] for c in http.calls] == [location(n).url for n in ("bad", "broken", "good")]
    assert result.work_file.location_id == "good" and len(fake_store.files.blobs) == 1
    assert list(fake_store.files.tmp.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        TransientNetworkError("interrupted"),
        LimitExceeded("large"),
        OSError("disk failure"),
        Cancelled("cancelled"),
        asyncio.CancelledError(),
    ],
)
async def test_at10a_partial_cleanup_on_errors_and_cancellation(fake_store, failure):
    call = retriever(fake_store).retrieve(
        "prj_a", work(), [location()], http=FakeHttp(failure), is_cancelled=lambda: False
    )
    if isinstance(failure, (Cancelled, asyncio.CancelledError)):
        with pytest.raises(type(failure)):
            await call
    else:
        assert (await call).state == RetrievalState.FAILED
    assert list(fake_store.files.tmp.iterdir()) == [] and not fake_store.files.blobs


@pytest.mark.anyio
async def test_cancellation_before_io_and_after_download(fake_store):
    for seen in (iter([True]), iter([False, False, True])):
        with pytest.raises(Cancelled):
            await retriever(fake_store).retrieve(
                "prj_a",
                work(),
                [location()],
                http=FakeHttp(paper_pdf(TITLE, [], DOI)),
                is_cancelled=lambda seen=seen: next(seen, True),
            )
        assert list(fake_store.files.tmp.iterdir()) == [] and not fake_store.files.blobs


@pytest.mark.anyio
async def test_limits_reach_download_and_oversized_bytes_cannot_commit(fake_store):
    http = FakeHttp(b"x" * 11)
    result = await retriever(
        fake_store, limits=Limits(download_bytes=10, download_timeout_s=3, max_redirects=2)
    ).retrieve("prj_a", work(), [location()], http=http, is_cancelled=lambda: False)
    assert result.error["code"] == "LIMIT_EXCEEDED"
    assert http.calls == [(location().url, 10, 3, 2)]
    assert not fake_store.files.blobs and list(fake_store.files.tmp.iterdir()) == []


@pytest.mark.anyio
async def test_total_timeout_cleans_partial_file(fake_store):
    async def slow(dest, guard):
        await asyncio.sleep(0.1)

    result = await retriever(fake_store, limits=Limits(download_timeout_s=0.01)).retrieve(
        "prj_a", work(), [location()], http=FakeHttp(slow), is_cancelled=lambda: False
    )
    assert result.error["code"] == "PROVIDER_UNAVAILABLE"
    assert list(fake_store.files.tmp.iterdir()) == [] and not fake_store.files.blobs


@pytest.mark.anyio
async def test_no_eligible_locations_or_cross_project_work_has_no_io(fake_store):
    http = FakeHttp()
    for project, locations in (("prj_a", [location(eligible=False)]), ("prj_b", [location()])):
        result = await retriever(fake_store).retrieve(
            project, work(), locations, http=http, is_cancelled=lambda: False
        )
        assert result.state == RetrievalState.FAILED and result.work_file is None
    assert not http.calls


@pytest.mark.anyio
async def test_verifier_exception_cleans_temp(fake_store):
    class BrokenVerifier(DefaultFileVerifier):
        def verify(self, *args, **kwargs):
            raise RuntimeError("parser bug")

    with pytest.raises(RuntimeError, match="parser bug"):
        await retriever(fake_store, verifier=BrokenVerifier()).retrieve(
            "prj_a",
            work(),
            [location()],
            http=FakeHttp(paper_pdf(TITLE, [], DOI)),
            is_cancelled=lambda: False,
        )
    assert list(fake_store.files.tmp.iterdir()) == [] and not fake_store.files.blobs


@pytest.mark.anyio
async def test_synchronous_verification_deadline_checked_before_commit(fake_store):
    import time

    class SlowVerifier(DefaultFileVerifier):
        def verify(self, *args, **kwargs):
            result = super().verify(*args, **kwargs)
            time.sleep(0.03)
            return result

    result = await retriever(
        fake_store, verifier=SlowVerifier(), limits=Limits(download_timeout_s=0.01)
    ).retrieve(
        "prj_a", work(), [location()], http=FakeHttp(paper_pdf(TITLE, [], DOI)), is_cancelled=lambda: False
    )
    assert result.state == RetrievalState.FAILED and not fake_store.files.blobs
    assert list(fake_store.files.tmp.iterdir()) == []


@pytest.mark.anyio
async def test_invalid_only_download_stores_nothing(fake_store):
    result = await retriever(fake_store).retrieve(
        "prj_a", work(), [location()], http=FakeHttp(b"<html>login</html>"), is_cancelled=lambda: False
    )
    assert result.error["code"] == "FILE_VALIDATION_FAILED" and result.work_file is None
    assert not fake_store.files.blobs and list(fake_store.files.tmp.iterdir()) == []


@pytest.mark.anyio
async def test_storage_commit_error_cleans_temp(fake_store, monkeypatch):
    def fail(path, media):
        raise OSError("disk full")

    monkeypatch.setattr(fake_store.files, "commit", fail)
    result = await retriever(fake_store).retrieve(
        "prj_a", work(), [location()], http=FakeHttp(paper_pdf(TITLE, [], DOI)), is_cancelled=lambda: False
    )
    assert result.state == RetrievalState.FAILED and result.work_file is None
    assert not fake_store.files.blobs and list(fake_store.files.tmp.iterdir()) == []
