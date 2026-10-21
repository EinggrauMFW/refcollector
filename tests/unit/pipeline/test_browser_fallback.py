"""Browser link resolution retains the real PDF retrieval and verification path."""

import pytest

from refcollector.domain.enums import AuditEventType, LocationBasis, StepType
from refcollector.domain.models import AccessLocation
from tests.fixtures.pdfs import paper_pdf
from tests.unit.pipeline.conftest import refs
from tests.unit.pipeline.test_pdf_fallback import TEMPLATE, enqueue, finish, prepare
from tests.unit.pipeline.test_pdf_fallback import fallback as fallback


@pytest.fixture
def browser(app, fallback):
    calls = []

    class Resolver:
        user_agent = "Mozilla/5.0 Browser"

        def __init__(self, **kwargs):
            self.python = kwargs["python"]

        async def resolve(self, template, doi, *, is_cancelled):
            calls.append(self.python)
            return AccessLocation(
                location_id="loc_browser",
                url="https://pdf.example/paper.pdf",
                provider="configured_browser_fallback",
                basis=LocationBasis.CONFIGURED_FALLBACK,
                eligible=True,
                is_pdf=True,
            )

    app.executor.handlers[StepType.RETRIEVE].browser_factory = Resolver
    app.settings.pdf_fallback_url = TEMPLATE
    app.settings.pdf_browser_python = "isolated-python"
    app.settings.pdf_browser_binary = "chromium"
    fallback["html"] = "<h1>Are you a robot?</h1>"
    return calls


def test_browser_after_missing_static_link_uses_normal_identity_verifier(app, fallback, browser):
    pid, row = prepare(app)
    queued = enqueue(app, pid, [row.occurrence_id])
    app.settings.pdf_browser_python = "changed-after-queue"
    finish(app, pid, queued)
    updated = refs(app, pid)[0]
    assert updated.retrieval == "verified" and updated.classification == "none_found"
    assert updated.approval is None
    assert browser == ["isolated-python"]
    assert fallback["calls"] == ["fallback.example", "pdf.example"]
    event = next(e for e in reversed(app.store.audit.list(pid)) if e.type == AuditEventType.FILE_RETRIEVED)
    assert event.payload["browser_attempted"] is True


def test_static_success_does_not_launch_browser(app, fallback, browser):
    fallback["html"] = '<a href="https://pdf.example/storage/hash/paper.pdf">PDF</a>'
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "verified"
    assert browser == []


def test_browser_wrong_pdf_stays_review_and_is_not_fetched_again(app, fallback, browser):
    fallback["pdf"] = paper_pdf("Unrelated medical study", ["Other"], "10.9999/wrong")
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "needs_review"
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "needs_review"
    assert len(browser) == 1


def test_version_policy_blocks_browser(app, fallback, browser):
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id], policy="prefer_published"))
    assert browser == [] and fallback["calls"] == []


def test_failed_static_pdf_tries_browser_before_giving_up(app, fallback, browser):
    fallback["html"] = '<iframe id="pdf" src="https://repo.example/bad.pdf">'
    fallback["primary_html"] = True
    pid, row = prepare(app)
    finish(app, pid, enqueue(app, pid, [row.occurrence_id]))
    assert refs(app, pid)[0].retrieval == "verified"
    assert browser == ["isolated-python"]
    assert fallback["calls"] == ["fallback.example", "repo.example", "pdf.example"]
