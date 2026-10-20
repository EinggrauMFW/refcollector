"""Offline providers at WP-10 composition seams; all services/storage/adapters stay real."""

import asyncio
from urllib.parse import unquote

import httpx
import pytest

from refcollector.bootstrap import open_app
from refcollector.config import Settings
from refcollector.contracts import api
from refcollector.domain.enums import Channel, Scope, StepType
from refcollector.files.netguard import DefaultNetGuard
from refcollector.files.retrieval import DefaultRetriever
from refcollector.providers.http import ProviderHttpClient
from tests.fixtures.pdfs import paper_pdf

TITLE = "Distinctive ocean wave power absorption by flexible arrays"
DOI = "10.1234/waves"
TEXT = f"Smith, A. (2020). {TITLE}. Ocean Journal 12(3):45-67. doi:{DOI}"


def context():
    return api.CallContext(
        principal="test", channel=Channel.CLI, scopes=frozenset(Scope), request_id="request"
    )


class Offline:
    def __init__(self):
        self.download_wait = None
        self.clients = []
        self.requests = []
        self.downloads = []
        self.snapshots = []
        self.publisher = None
        self.doi = DOI
        self.location = False
        self.failure = False
        self.body = paper_pdf(TITLE, ["Smith, A."], DOI)

    def response(self, request):
        self.requests.append(str(request.url))
        if request.url.host == "repo.example":
            self.downloads.append(str(request.url))
            return httpx.Response(200, content=self.body, headers={"Content-Type": "application/pdf"})
        if request.url.host == "api.unpaywall.org":
            if self.failure:
                return httpx.Response(503)
            locations = (
                [
                    {
                        "url_for_pdf": "https://repo.example/paper",
                        "version": "publishedVersion",
                        "host_type": "repository",
                    }
                ]
                if self.location
                else []
            )
            return httpx.Response(200, json={"oa_locations": locations})
        if request.url.host == "api.crossref.org":
            doi = (
                self.doi
                if request.url.path == "/works"
                else unquote(request.url.path.removeprefix("/works/"))
            )
            item = {
                "title": [TITLE],
                "DOI": doi,
                "author": [{"family": "Smith", "given": "A"}],
                "issued": {"date-parts": [[2020]]},
                "publisher": self.publisher,
                "type": "journal-article",
                "container-title": ["Ocean Journal"],
            }
            return httpx.Response(
                200, json={"message": {"items": [item]} if request.url.path == "/works" else item}
            )
        raise AssertionError(f"Unexpected HTTP {request.url}")

    async def download_response(self, request):
        if self.download_wait:
            await self.download_wait()
        return self.response(request)

    def http(self, snapshot, db, limits):
        self.snapshots.append(snapshot.model_copy(deep=True))
        client = ProviderHttpClient(
            snapshot,
            db,
            limits,
            transport=httpx.MockTransport(self.response),
            download_transport=httpx.MockTransport(self.download_response),
            secrets={},
        )
        self.clients.append(client)
        return client


@pytest.fixture
def offline():
    return Offline()


@pytest.fixture
def app(tmp_path, monkeypatch, offline):
    monkeypatch.setattr("refcollector.pipeline.clients.ProviderHttpClient", offline.http)
    settings = Settings(
        rc_home=tmp_path / "home",
        email="test@example.org",
        allow_folders=[tmp_path],
        enabled_providers=["crossref", "unpaywall"],
    )
    handle = open_app(settings)
    handler = handle.executor.handlers[StepType.RETRIEVE]

    async def resolve(host, port):
        return ["8.8.8.8"]

    handler.retriever_factory = lambda files, limits: DefaultRetriever(
        files, limits=limits, guard=DefaultNetGuard(resolve=resolve)
    )
    yield handle
    handle.close()
    assert all(c._client.is_closed for c in offline.clients)


def create(app, text=TEXT):
    ctx = context()
    pid = app.service.create_project(
        ctx, api.CreateProjectIn(name="test", idempotency_key="project1")
    ).project_id
    sid = source(app, pid, text)
    return pid, sid


def source(app, pid, text=TEXT):
    return app.service.import_source(
        context(),
        api.ImportSourceIn(
            project_id=pid,
            input=api.TextInput(type="text", text=text),
            idempotency_key=f"source-{len(app.store.sources.list(pid).items):04}",
        ),
    ).source_id


def run(
    app, pid, sid=None, stages=("extract", "resolve", "discover_access"), ids=None, refresh=False, cap=None
):
    key = f"start-{len(app.executor.queue.list_jobs(pid, limit=1000)):04}"
    scope = api.OccurrenceScope(occurrence_ids=ids) if ids else api.SourceScope(source_ids=[sid])
    out = app.service.start_job(
        context(),
        api.StartJobIn(
            project_id=pid,
            stages=list(stages),
            scope=scope,
            options=api.JobOptions(refresh=refresh, max_occurrences=cap),
            idempotency_key=key,
        ),
    )
    asyncio.run(app.executor.run_until_idle())
    return app.service.get_job(context(), api.GetJobIn(project_id=pid, job_id=out.job_id))


def refs(app, pid):
    return app.service.all_occurrences(
        pid, __import__("refcollector.contracts.ports", fromlist=["OccurrenceQuery"]).OccurrenceQuery()
    )


def change(app, pid, value):
    return app.service.update_reference(
        context(),
        api.UpdateReferenceIn(
            project_id=pid, change=value, idempotency_key=f"change-{app.store.projects.get(pid).revision:08}"
        ),
    )


@pytest.fixture(autouse=True)
def no_live_sockets(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("Live socket or DNS call forbidden in WP-10 tests")

    import sys

    original_connect = socket.socket.connect

    def connect(sock, address):
        if sys._getframe(1).f_code.co_name == "_fallback_socketpair":
            return original_connect(sock, address)
        return denied(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
