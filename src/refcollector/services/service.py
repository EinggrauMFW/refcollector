"""RefCollectorServiceImpl: the application services behind MCP and CLI (spec 5, 7.4, 11, 13).

Services validate, check scopes, paths and network mode, enforce idempotency and revisions, record
provenance and audit events, and enqueue jobs. They never run pipeline steps (spec 5).

Conventions other work packages rely on:
- Job.scope is {"source_ids": [...]} or {"occurrence_ids": [...]} (export: {"filter": {}} = project).
- Job.options: export {"formats", "selection", "project_revision"}; retrieve {"budget", "version_policy"};
  draft {"sender", "language", "group_by_author"}; extract/resolve/discover {"max_occurrences", "refresh"}.
- Mutations of project data bump the project revision once. Enqueueing or controlling a job does
  not, so a manifest revision equals the `project_revision` that `create_export` was called with.
"""

from __future__ import annotations

import codecs
import hashlib
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from refcollector import __version__
from refcollector.config import (
    ELIGIBILITY_POLICY_VERSION,
    IDENTITY_POLICY_VERSION,
    P0_PROVIDERS,
    Settings,
    secrets_present,
)
from refcollector.contracts import api
from refcollector.contracts.api import (
    STAGE_SCOPES,
    CallContext,
    Meta,
    Out,
)
from refcollector.contracts.ports import (
    CitationQuery,
    ExtractionAdapter,
    IdentityResolver,
    JobQueue,
    LocalCopyChecker,
    OccurrenceQuery,
    Store,
    snapshot_hash,
)
from refcollector.domain.enums import (
    ACCEPTED_IDENTITIES,
    TERMINAL_JOB_STATES,
    AccessClassification,
    AuditEventType,
    Channel,
    DiscoveryState,
    ExportFormat,
    ExtractionState,
    IdentityState,
    LocalCopyOrigin,
    LocalCopyState,
    NetworkMode,
    Origin,
    PolicyProfile,
    RetrievalState,
    Scope,
    SourceType,
    Stage,
    StepType,
    VersionPolicy,
)
from refcollector.domain.models import (
    ConfigSnapshot,
    IdempotencyRecord,
    Job,
    Occurrence,
    Preflight,
    Project,
    Provenance,
    SectionInfo,
    Source,
    WorkMetadata,
    utcnow,
)
from refcollector.errors import (
    ConfigMissingEmail,
    IdempotencyConflict,
    InvalidInput,
    InvalidState,
    LimitExceeded,
    NetworkModeDenied,
    NotAuthorized,
    NotFound,
    PathNotAllowed,
    PrerequisiteNotMet,
    RevisionConflict,
    RevisionNotCurrent,
    UnsupportedSource,
)
from refcollector.executor.snapshot import build_snapshot
from refcollector.ids import new_id
from refcollector.services import changes
from refcollector.services.support import (
    Tx,
    decode_cursor,
    encode_cursor,
    expiry,
    fingerprint,
    run_sync,
    scope_key,
)

O = TypeVar("O", bound=Out)  # noqa: E741

POLL_MS = 1000
RESOLVABLE = frozenset({ExtractionState.EXTRACTED, ExtractionState.NEEDS_REVIEW, ExtractionState.CORRECTED})
DRAFTABLE = frozenset(
    {AccessClassification.NONE_FOUND, AccessClassification.RESTRICTED, AccessClassification.UNKNOWN}
)
TEXT_MEDIA = {"application/json", "application/x-bibtex", "message/rfc822", "application/xml"}
CHUNK = 500
CLI_CHANNELS = frozenset({Channel.CLI, Channel.CLI_TTY_REVIEW})


def _is_text(media_type: str) -> bool:
    return (
        media_type.startswith("text/") or media_type in TEXT_MEDIA or media_type.endswith(("+json", "+xml"))
    )


class RefCollectorServiceImpl:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        queue: JobQueue,
        *,
        extractor: ExtractionAdapter,
        identity_factory: Callable[[ConfigSnapshot], IdentityResolver],
        local_copies: LocalCopyChecker,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self.store = store
        self.queue = queue
        self.extractor = extractor
        self.identity_factory = identity_factory
        self.local_copies = local_copies
        self.clock = clock

    # ================================================================ plumbing

    def provenance(self, ctx: CallContext) -> Provenance:
        return Provenance(principal=ctx.principal, channel=ctx.channel, origin=Origin(ctx.origin))

    def snapshot(self, project: Project) -> ConfigSnapshot:
        return build_snapshot(self.settings, project, {self.extractor.name: self.extractor.version})

    def all_occurrences(self, project_id: str, query: OccurrenceQuery) -> list[Occurrence]:
        out: list[Occurrence] = []
        after = None
        while True:
            page = self.store.occurrences.list(project_id, query, after=after, limit=CHUNK)
            out += page.items
            if page.next_after is None:
                return out
            after = page.next_after

    @staticmethod
    def _need(ctx: CallContext, scope: Scope) -> None:
        if scope not in ctx.scopes:
            raise NotAuthorized(f"scope {scope.value} is not granted", details={"scope": scope.value})

    def _limit(self, requested: int | None) -> int:
        lim = self.settings.limits
        return min(requested or lim.list_default, lim.list_max)

    def _meta(self, ctx: CallContext, out: O, revision: int | None = None) -> O:
        out.meta = Meta(request_id=ctx.request_id, project_revision=revision)
        return out

    def _read(self, ctx: CallContext, scope: Scope, project_id: str) -> Project:
        self._need(ctx, scope)
        return self.store.projects.get(project_id)

    def _after(self, cursor: str | None, *key: Any) -> tuple[str | None, str]:
        sk = scope_key(*key)
        return decode_cursor(cursor, sk, self.clock(), self.settings.limits.cursor_ttl_s), sk

    def _replay(self, ctx: CallContext, pid: str, tool: str, req: Any, out_type: type[O]) -> O | None:
        rec = self.store.idempotency.get(ctx.principal, pid, tool, req.idempotency_key, now=self.clock())
        if rec is None:
            return None
        if rec.fingerprint != fingerprint(req):
            raise IdempotencyConflict(f"key {req.idempotency_key} was used with a different request")
        return out_type.model_validate(rec.result)

    def _mutate(
        self,
        ctx: CallContext,
        tool: str,
        project_id: str | None,
        req: Any,
        out_type: type[O],
        fn: Callable[[Tx, Any], O],
        *,
        prepare: Callable[[], Any] | None = None,
        bump: bool = True,
    ) -> O:
        """One immediate transaction (D-26): idempotency (11.3), the change, audit, revision +1."""
        pid = project_id or ""
        if project_id:
            self.store.projects.get(project_id)
        if (hit := self._replay(ctx, pid, tool, req, out_type)) is not None:
            return hit
        prepared = prepare() if prepare else None  # slow reads (PDF parse, providers) outside the lock
        with self.store.transaction(immediate=True):
            if (hit := self._replay(ctx, pid, tool, req, out_type)) is not None:
                return hit
            now = self.clock()
            project = self.store.projects.get(project_id) if project_id else None
            tx = Tx(self, ctx, project, (project.revision + int(bump)) if project else 0, now)
            out = fn(tx, prepared)
            assert tx.project is not None
            if bump and tx.events:
                revision = self.store.projects.bump_revision(tx.project.project_id)
            else:
                revision = self.store.projects.get(tx.project.project_id).revision
            self._meta(ctx, out, revision)
            self.store.idempotency.put(
                IdempotencyRecord(
                    principal=ctx.principal,
                    project_id=pid,
                    tool=tool,
                    key=req.idempotency_key,
                    fingerprint=fingerprint(req),
                    result=out.model_dump(mode="json"),
                    created_at=now,
                    expires_at=expiry(now, self.settings.limits.idempotency_ttl_s),
                )
            )
            return out

    def _enqueue(
        self,
        tx: Tx,
        stages: list[StepType],
        scope: dict[str, Any],
        options: dict[str, Any],
        *,
        link: list[str] | None = None,
        version_policy: Any = None,
    ) -> Job:
        assert tx.project is not None
        snap = self.snapshot(tx.project)
        if version_policy is not None:
            snap = snap.model_copy(update={"version_policy": version_policy})
        job = self.queue.create_job(
            Job(
                job_id=new_id("job"),
                project_id=tx.project.project_id,
                stages=stages,
                scope=scope,
                options=options,
                snapshot=snap,
                snapshot_hash=snapshot_hash(snap),
                provenance=self.provenance(tx.ctx),
                created_at=tx.now,
                updated_at=tx.now,
            )
        )
        if link:
            self.store.occurrences.link_to_job(job.project_id, job.job_id, link)
        tx.audit(AuditEventType.JOB_CREATED, job.job_id, stages=[s.value for s in stages], scope=scope)
        return job

    def _require_email(self) -> None:
        if not self.settings.email:
            raise ConfigMissingEmail()

    # ================================================================ views

    def _summary(self, occ: Occurrence) -> api.OccurrenceSummary:
        n = len(self.store.candidates.list_for_occurrence(occ.project_id, occ.occurrence_id))
        return api.OccurrenceSummary(
            occurrence_id=occ.occurrence_id,
            source_id=occ.source_id,
            revision=occ.revision,
            label=occ.label,
            raw_text=occ.raw_text,
            edited_text=occ.edited_text,
            spans=occ.spans,
            extraction=occ.extraction,
            identity=api.IdentityView(
                state=occ.identity, reasons=[r.value for r in occ.identity_reasons], candidates=n
            ),
            access=api.AccessView(
                discovery=occ.discovery, classification=occ.classification, checked_at=occ.access_checked_at
            ),
            local_copy=occ.local_copy,
            retrieval=occ.retrieval,
            warnings=[w.value for w in occ.warnings],
        )

    def _job_summaries(self, project_id: str, limit: int = 10) -> list[api.JobSummary]:
        return [
            api.JobSummary(
                job_id=j.job_id, state=j.state, stages=[s.value for s in j.stages], created_at=j.created_at
            )
            for j in self.queue.list_jobs(project_id, limit=limit)
        ]

    def _descriptor(self, project_id: str, art: Any) -> api.ArtifactDescriptor:
        return api.ArtifactDescriptor(
            artifact_id=art.artifact_id,
            kind=art.kind,
            media_type=art.media_type,
            bytes=art.bytes,
            sha256=art.sha256,
            project_revision=art.project_revision,
            name=art.name,
            path=(p.as_uri() if (p := self._artifact_path(project_id, art)) else None),
        )

    def _artifact_path(self, project_id: str, art: Any) -> Path | None:
        try:
            path = self.store.files.path_for(project_id, art.sha256).resolve()
        except NotFound:
            return None
        return path if path.is_relative_to(self.settings.rc_home.resolve()) else None

    def _project_view(self, project: Project, revision: int | None = None) -> api.GetProjectOut:
        pid = project.project_id
        occs = self.all_occurrences(pid, OccurrenceQuery())
        arts, after = [], None
        while True:  # ids sort by time: the newest artifacts are last
            page = self.store.artifacts.list(pid, after=after, limit=CHUNK)
            arts += page.items
            if page.next_after is None:
                break
            after = page.next_after
        return api.GetProjectOut(
            project_id=pid,
            name=project.name,
            revision=project.revision if revision is None else revision,
            policy={
                "profile": project.policy_profile.value,
                "version_policy": project.version_policy.value,
                "identity_policy_version": IDENTITY_POLICY_VERSION,
                "eligibility_policy_version": ELIGIBILITY_POLICY_VERSION,
            },
            network_mode=project.network_mode.value,
            counts={
                "occurrences": len(occs),
                "by_identity": dict(Counter(o.identity.value for o in occs)),
                "by_access": dict(
                    Counter(o.classification.value if o.classification else "not_checked" for o in occs)
                ),
                "by_retrieval": dict(Counter(o.retrieval.value for o in occs)),
            },
            sources_count=self.store.sources.list(pid, limit=1).total or 0,
            recent_jobs=self._job_summaries(pid),
            recent_artifacts=[self._descriptor(pid, a) for a in reversed(arts[-10:])],
        )

    # ================================================================ 1-4 capabilities, projects

    def get_capabilities(self, ctx: CallContext, req: api.GetCapabilitiesIn) -> api.GetCapabilitiesOut:
        try:
            keys = secrets_present()
        except Exception:  # noqa: BLE001 - no keyring backend: report absence, never fail
            keys = {"openalex_key": False, "s2_key": False}
        enabled = set(self.settings.enabled_providers)
        email = bool(self.settings.email)
        out = api.GetCapabilitiesOut(
            server_version=__version__,
            source_types=list(SourceType),
            stages=list(Stage),
            adapters={"builtin": True, "grobid": False, "ocr": False},
            providers=[
                api.ProviderStatus(name=p, enabled=p in enabled, configured=email) for p in P0_PROVIDERS
            ],
            local_copies=["managed", "attached"],
            export_formats=list(ExportFormat),
            approval_surfaces=["cli_tty"],
            limits=self.settings.limits.model_dump(),
            scopes_granted=sorted(ctx.scopes),
            config={"email_set": email, "keys": keys},
        )
        return self._meta(ctx, out)

    def list_projects(self, ctx: CallContext, req: api.ListProjectsIn) -> api.ListProjectsOut:
        self._need(ctx, Scope.PROJECTS_READ)
        after, sk = self._after(req.cursor, "list_projects")
        page = self.store.projects.list(after=after, limit=self._limit(req.limit))
        items = [
            api.ProjectListItem(
                project_id=p.project_id,
                name=p.name,
                revision=p.revision,
                counts={
                    "sources": self.store.sources.list(p.project_id, limit=1).total or 0,
                    "occurrences": self.store.occurrences.list(p.project_id, OccurrenceQuery(), limit=1).total
                    or 0,
                },
                updated_at=p.updated_at,
            )
            for p in page.items
        ]
        return self._meta(
            ctx,
            api.ListProjectsOut(projects=items, next_cursor=encode_cursor(page.next_after, sk, self.clock())),
        )

    def create_project(self, ctx: CallContext, req: api.CreateProjectIn) -> api.CreateProjectOut:
        self._need(ctx, Scope.PROJECTS_WRITE)

        def fn(tx: Tx, _: Any) -> api.CreateProjectOut:
            fields = {}
            if req.policy_profile:
                fields["policy_profile"] = req.policy_profile
            if req.version_policy:
                fields["version_policy"] = req.version_policy
            project = self.store.projects.add(
                Project(
                    project_id=new_id("project"),
                    name=req.name,
                    created_at=tx.now,
                    updated_at=tx.now,
                    **fields,
                )
            )
            tx.attach(project)
            tx.audit(AuditEventType.PROJECT_CREATED, project.project_id, name=project.name)
            return api.CreateProjectOut(project_id=project.project_id, revision=tx.revision)

        return self._mutate(ctx, "create_project", None, req, api.CreateProjectOut, fn)

    def get_project(self, ctx: CallContext, req: api.GetProjectIn) -> api.GetProjectOut:
        project = self._read(ctx, Scope.PROJECTS_READ, req.project_id)
        return self._meta(ctx, self._project_view(project), project.revision)

    def update_project_settings(
        self, ctx: CallContext, req: api.UpdateProjectSettingsIn
    ) -> api.GetProjectOut:
        """CLI only (D-23): agents must not raise their own permissions."""
        if ctx.channel not in CLI_CHANNELS:
            raise NotAuthorized("project settings change only through the CLI")
        self._need(ctx, Scope.PROJECTS_WRITE)

        def fn(tx: Tx, _: Any) -> api.GetProjectOut:
            project = tx.project
            assert project is not None
            changes_: dict[str, Any] = {}
            if req.network_mode is not None:
                changes_["network_mode"] = NetworkMode(req.network_mode)
            if req.download_enabled is not None:
                changes_["download_enabled"] = req.download_enabled
            if req.version_policy is not None:
                changes_["version_policy"] = req.version_policy
            if not changes_:
                raise InvalidInput("nothing to change")
            diff = {
                k: [str(getattr(project, k)), str(v)] for k, v in changes_.items() if getattr(project, k) != v
            }
            project = self.store.projects.update(project.model_copy(update=changes_))
            if diff:
                tx.audit(AuditEventType.PROJECT_SETTINGS_CHANGED, project.project_id, changed=diff)
            return self._project_view(project, tx.revision if diff else project.revision)

        return self._mutate(ctx, "update_project_settings", req.project_id, req, api.GetProjectOut, fn)

    # ================================================================ 5-8 sources

    def _allowed_file(self, raw: str) -> Path:
        """Resolve first, then check the allow-list (13): symlink and `..` escapes land outside."""
        roots = []
        for folder in self.settings.allow_folders:
            try:
                roots.append(Path(folder).resolve())
            except (OSError, RuntimeError):
                continue
        try:
            candidate = Path(raw).expanduser()
            loose = candidate.resolve()
        except (OSError, ValueError, RuntimeError) as exc:
            raise InvalidInput("unusable path") from exc
        if not any(loose.is_relative_to(r) for r in roots):
            raise PathNotAllowed("path is outside the allowed folders", details={"path": raw})
        try:
            real = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise NotFound("file not found", details={"path": raw}) from exc
        except (OSError, RuntimeError) as exc:
            raise InvalidInput("unusable path") from exc
        if not any(real.is_relative_to(r) for r in roots):
            raise PathNotAllowed("path is outside the allowed folders", details={"path": raw})
        if not real.is_file():
            raise UnsupportedSource("not a regular file", details={"path": raw})
        return real

    def _read_bytes(self, path: Path) -> bytes:
        if path.stat().st_size > self.settings.limits.pdf_bytes:
            raise LimitExceeded(
                "file is larger than the PDF limit", details={"limit": self.settings.limits.pdf_bytes}
            )
        return path.read_bytes()

    def import_source(self, ctx: CallContext, req: api.ImportSourceIn) -> api.ImportSourceOut:
        self._need(ctx, Scope.SOURCES_IMPORT)
        spec = req.input

        def prepare() -> dict[str, Any]:
            if isinstance(spec, api.LocalFileInput):
                path = self._allowed_file(spec.path)
                data = self._read_bytes(path)
                label, origin = path.name, str(path)
                if data.startswith(b"%PDF"):
                    kind, payload = SourceType.PDF, data
                else:
                    try:
                        payload = data.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise UnsupportedSource("use a PDF or a UTF-8 text file") from exc
                    kind = SourceType.TEXT
            else:
                payload, kind, label, origin = (
                    spec.text,
                    SourceType.TEXT,
                    spec.label or "pasted text",
                    "pasted",
                )
            raw = payload.encode("utf-8") if isinstance(payload, str) else payload
            if kind == SourceType.TEXT:
                if "\x00" in payload:
                    raise UnsupportedSource("text contains NUL bytes")
                if len(raw) > self.settings.limits.pasted_text_bytes:
                    raise LimitExceeded(
                        "text is larger than 1 MB", details={"limit": self.settings.limits.pasted_text_bytes}
                    )
            sha = hashlib.sha256(raw).hexdigest()
            known = self.store.sources.find_by_hash(req.project_id, sha)
            preflight = (
                known.preflight if known else self.extractor.preflight(payload, kind)
            )  # errors propagate
            return {
                "kind": kind,
                "raw": raw,
                "sha": sha,
                "label": label,
                "origin": origin,
                "preflight": preflight,
            }

        def fn(tx: Tx, p: dict[str, Any]) -> api.ImportSourceOut:
            pid = req.project_id
            known = self.store.sources.find_by_hash(pid, p["sha"])
            if known is not None:  # W-02
                return api.ImportSourceOut(source_id=known.source_id, reused=True, preflight=known.preflight)
            suffix = ".pdf" if p["kind"] == SourceType.PDF else ".txt"
            tmp = self.store.files.new_tmp_path(suffix)
            tmp.write_bytes(p["raw"])
            blob = self.store.files.commit(
                tmp, "application/pdf" if p["kind"] == SourceType.PDF else "text/plain"
            )
            self.store.files.grant(pid, blob.sha256)
            preflight: Preflight = p["preflight"]
            source = self.store.sources.add(
                Source(
                    source_id=new_id("source"),
                    project_id=pid,
                    type=p["kind"],
                    label=p["label"],
                    sha256=p["sha"],
                    origin=p["origin"],
                    preflight=preflight,
                    sections=[s.model_copy() for s in preflight.sections],
                    imported_at=tx.now,
                )
            )
            tx.audit(
                AuditEventType.SOURCE_IMPORTED,
                source.source_id,
                sha256=p["sha"],
                type=p["kind"].value,
                label=p["label"],
            )
            return api.ImportSourceOut(source_id=source.source_id, reused=False, preflight=preflight)

        return self._mutate(
            ctx, "import_source", req.project_id, req, api.ImportSourceOut, fn, prepare=prepare
        )

    def _source_item(self, src: Source) -> api.SourceListItem:
        count = self.store.occurrences.list(
            src.project_id, OccurrenceQuery(source_id=src.source_id), limit=1
        ).total
        return api.SourceListItem(
            source_id=src.source_id,
            label=src.label,
            type=src.type,
            pages=src.preflight.pages,
            sections=len(src.sections),
            extraction_state=src.extraction_state,
            occurrence_count=count or 0,
            imported_at=src.imported_at,
        )

    def list_sources(self, ctx: CallContext, req: api.ListSourcesIn) -> api.ListSourcesOut:
        project = self._read(ctx, Scope.REFERENCES_READ, req.project_id)
        after, sk = self._after(req.cursor, "list_sources", req.project_id)
        page = self.store.sources.list(req.project_id, after=after, limit=self._limit(req.limit))
        out = api.ListSourcesOut(
            sources=[self._source_item(s) for s in page.items],
            next_cursor=encode_cursor(page.next_after, sk, self.clock()),
        )
        return self._meta(ctx, out, project.revision)

    def get_source(self, ctx: CallContext, req: api.GetSourceIn) -> api.GetSourceOut:
        project = self._read(ctx, Scope.REFERENCES_READ, req.project_id)
        src = self.store.sources.get(req.project_id, req.source_id)
        occs = self.all_occurrences(req.project_id, OccurrenceQuery(source_id=src.source_id))
        warnings = Counter(w.value for o in occs for w in o.warnings)
        jobs = [
            j
            for j in self.queue.list_jobs(req.project_id, limit=50)
            if src.source_id in j.scope.get("source_ids", [])
        ]
        out = api.GetSourceOut(
            source=self._source_item(src),
            revision=src.revision,
            preflight=src.preflight,
            sections=src.sections,
            extraction={
                "state": src.extraction_state.value,
                "adapter": src.extraction_adapter,
                "occurrences": len(occs),
                "warnings_by_code": dict(warnings),
            },
            jobs=[
                api.JobSummary(
                    job_id=j.job_id,
                    state=j.state,
                    stages=[s.value for s in j.stages],
                    created_at=j.created_at,
                )
                for j in jobs
            ],
        )
        return self._meta(ctx, out, project.revision)

    def set_source_sections(self, ctx: CallContext, req: api.SetSourceSectionsIn) -> api.SetSourceSectionsOut:
        self._need(ctx, Scope.REFERENCES_WRITE)

        def fn(tx: Tx, _: Any) -> api.SetSourceSectionsOut:
            src = self.store.sources.get(req.project_id, req.source_id)
            pages = src.preflight.pages
            sections = []
            for r in req.sections:
                last = max(pages - 1, 0)
                if r.page_end < r.page_start or r.page_end > last:
                    raise InvalidInput("page range is outside the source", details={"pages": pages})
                if (r.offset_start is None) != (r.offset_end is None) or (
                    r.offset_start is not None
                    and r.offset_end is not None
                    and (r.offset_start < 0 or (r.page_start == r.page_end and r.offset_end < r.offset_start))
                ):
                    raise InvalidInput("offsets must be given together and ordered")
                sections.append(
                    SectionInfo(
                        section_id=new_id("section"),
                        page_start=r.page_start,
                        page_end=r.page_end,
                        offset_start=r.offset_start,
                        offset_end=r.offset_end,
                        manual=True,
                    )
                )
            updated = self.store.sources.update(
                src.model_copy(update={"sections": sections}), expected_revision=req.expected_source_revision
            )
            tx.audit(
                AuditEventType.SECTIONS_SET,
                src.source_id,
                sections=[s.model_dump(mode="json") for s in sections],
            )
            return api.SetSourceSectionsOut(sections=updated.sections)

        return self._mutate(ctx, "set_source_sections", req.project_id, req, api.SetSourceSectionsOut, fn)

    # ================================================================ 9-11 jobs

    def _scope_occurrences(
        self, project_id: str, scope: api.SourceScope | api.OccurrenceScope
    ) -> list[Occurrence]:
        if isinstance(scope, api.OccurrenceScope):
            return self.store.occurrences.get_many(project_id, scope.occurrence_ids)
        return [
            o
            for sid in scope.source_ids
            for o in self.all_occurrences(project_id, OccurrenceQuery(source_id=sid))
        ]

    def start_job(self, ctx: CallContext, req: api.StartJobIn) -> api.StartJobOut:
        for stage in req.stages:
            self._need(ctx, STAGE_SCOPES[stage])
        stages = list(req.stages)
        if stages != sorted(set(stages), key=list(Stage).index):
            raise InvalidInput(
                "stages must be unique and in pipeline order: extract, resolve, discover_access"
            )

        def validate() -> tuple[int, list[str]]:
            project = self.store.projects.get(req.project_id)
            scope = req.scope
            if Stage.EXTRACT in stages and not isinstance(scope, api.SourceScope):
                raise PrerequisiteNotMet("extract needs source_ids")
            if isinstance(scope, api.SourceScope):
                for sid in scope.source_ids:
                    self.store.sources.get(req.project_id, sid)
            else:
                self.store.occurrences.get_many(req.project_id, scope.occurrence_ids)
            if {Stage.RESOLVE, Stage.DISCOVER_ACCESS} & set(stages):
                if project.network_mode == NetworkMode.OFFLINE:
                    raise NetworkModeDenied(
                        "project is offline", details={"stages": [s.value for s in stages]}
                    )
                self._require_email()
            cap = req.options.max_occurrences if req.options else None
            if stages[0] == Stage.EXTRACT:
                assert isinstance(scope, api.SourceScope)
                planned = len(scope.source_ids)
            else:
                occs = [
                    o
                    for o in self._scope_occurrences(req.project_id, scope)
                    if o.extraction != ExtractionState.EXCLUDED
                ]
                resolvable = [o for o in occs if o.extraction in RESOLVABLE]
                accepted = [o for o in occs if o.identity in ACCEPTED_IDENTITIES and o.work_id]
                if stages[0] == Stage.RESOLVE and not resolvable:
                    raise PrerequisiteNotMet("resolve needs extracted occurrences")
                if Stage.DISCOVER_ACCESS in stages and Stage.RESOLVE not in stages and not accepted:
                    raise PrerequisiteNotMet("discover_access needs accepted identities")
                planned = (
                    len(resolvable) if stages[0] == Stage.RESOLVE else len({o.work_id for o in accepted})
                )
            if cap is not None:
                planned = min(planned, cap)
            link = list(scope.occurrence_ids) if isinstance(scope, api.OccurrenceScope) else []
            return planned, link

        def fn(tx: Tx, _: Any) -> api.StartJobOut:
            planned, link = validate()
            scope = req.scope
            scope_doc = (
                {"source_ids": list(scope.source_ids)}
                if isinstance(scope, api.SourceScope)
                else {"occurrence_ids": list(scope.occurrence_ids)}
            )
            options = req.options.model_dump(exclude_none=True) if req.options else {}
            job = self._enqueue(tx, [StepType(s.value) for s in stages], scope_doc, options, link=link)
            return api.StartJobOut(job_id=job.job_id, steps_planned=planned, next_poll_after_ms=POLL_MS)

        return self._mutate(ctx, "start_job", req.project_id, req, api.StartJobOut, fn, bump=False)

    def get_job(self, ctx: CallContext, req: api.GetJobIn) -> api.GetJobOut:
        project = self._read(ctx, Scope.PROJECTS_READ, req.project_id)
        job = self.queue.get_job(req.project_id, req.job_id)
        counts = self.queue.stage_counts(job.job_id)
        names = [s for s in job.stages] + [s for s in counts if s not in job.stages]
        stages = []
        for name in names:
            c = counts.get(name, {})
            stages.append(
                api.StageCounts(
                    name=name.value,
                    queued=c.get("queued", 0) + c.get("retry_wait", 0),
                    running=c.get("running", 0),
                    succeeded=c.get("succeeded", 0),
                    failed_retryable=c.get("failed_retryable", 0),
                    failed_permanent=c.get("failed_permanent", 0),
                    skipped=c.get("skipped", 0),
                )
            )
        artifact_ids, after = [], None
        while True:
            page = self.store.artifacts.list(req.project_id, after=after, limit=CHUNK)
            artifact_ids += [a.artifact_id for a in page.items if a.job_id == job.job_id]
            if page.next_after is None:
                break
            after = page.next_after
        total = self.store.occurrences.list(req.project_id, OccurrenceQuery(job_id=job.job_id), limit=1).total
        out = api.GetJobOut(
            job_id=job.job_id,
            state=job.state,
            stages=stages,
            warnings=list(job.warnings),
            results=api.JobResults(
                source_ids=list(job.scope.get("source_ids", [])),
                occurrence_count=total or 0,
                changed_filter={"job_id": job.job_id},
                artifact_ids=artifact_ids,
            ),
            next_poll_after_ms=None if job.state in TERMINAL_JOB_STATES else POLL_MS,
        )
        return self._meta(ctx, out, project.revision)

    def control_job(self, ctx: CallContext, req: api.ControlJobIn) -> api.ControlJobOut:
        self._need(ctx, Scope.PROJECTS_WRITE)

        def fn(tx: Tx, _: Any) -> api.ControlJobOut:
            job = self.queue.request(
                req.project_id, req.job_id, req.action
            )  # InvalidState on a bad transition
            tx.audit(AuditEventType.JOB_CONTROLLED, job.job_id, action=req.action, state=job.state.value)
            return api.ControlJobOut(job_id=job.job_id, state=job.state)

        return self._mutate(ctx, "control_job", req.project_id, req, api.ControlJobOut, fn, bump=False)

    # ================================================================ 12-15 references

    def _query(
        self, project_id: str, flt: api.ReferenceFilter | None, text: str | None = None, sort: str = "order"
    ) -> OccurrenceQuery:
        f = flt or api.ReferenceFilter()
        if f.source_id:
            self.store.sources.get(project_id, f.source_id)
        if f.job_id:
            self.queue.get_job(project_id, f.job_id)
        return OccurrenceQuery(
            source_id=f.source_id,
            job_id=f.job_id,
            extraction=f.extraction.value if f.extraction else None,
            identity=f.identity.value if f.identity else None,
            discovery=f.discovery.value if f.discovery else None,
            classification=f.classification.value if f.classification else None,
            local_copy=f.local_copy.value if f.local_copy else None,
            retrieval=f.retrieval.value if f.retrieval else None,
            reason=f.reason,
            text=text,
            sort=sort,  # type: ignore[arg-type]
        )

    def list_references(self, ctx: CallContext, req: api.ListReferencesIn) -> api.ListReferencesOut:
        project = self._read(ctx, Scope.REFERENCES_READ, req.project_id)
        valid = set(api.OccurrenceSummary.model_fields)
        if req.fields is not None and (bad := sorted(set(req.fields) - valid)):
            raise InvalidInput("unknown fields", details={"unknown": bad, "valid": sorted(valid)})
        query = self._query(req.project_id, req.filter, req.query, req.sort)
        after, sk = self._after(
            req.cursor,
            "list_references",
            req.project_id,
            req.filter.model_dump(mode="json") if req.filter else None,
            req.query,
            req.sort,
        )
        page = self.store.occurrences.list(req.project_id, query, after=after, limit=self._limit(req.limit))
        rows = []
        for occ in page.items:
            row = self._summary(occ).model_dump(mode="json")
            if req.fields is not None:
                row = {k: v for k, v in row.items() if k in {*req.fields, "occurrence_id"}}
            rows.append(row)
        out = api.ListReferencesOut(
            occurrences=rows,
            next_cursor=encode_cursor(page.next_after, sk, self.clock()),
            total=page.total or 0,
        )
        return self._meta(ctx, out, project.revision)

    def get_reference(self, ctx: CallContext, req: api.GetReferenceIn) -> api.GetReferenceOut:
        project = self._read(ctx, Scope.REFERENCES_READ, req.project_id)
        pid = req.project_id
        occ = self.store.occurrences.get(pid, req.occurrence_id)
        want = set(req.sections or [])
        work = self.store.works.get(pid, occ.work_id) if occ.work_id else None
        cands = self.store.candidates.list_for_occurrence(pid, occ.occurrence_id)
        out = api.GetReferenceOut(
            occurrence=self._summary(occ),
            work=WorkMetadata.model_validate({**work.metadata.model_dump(), **work.overrides})
            if work
            else None,
            locks=dict(work.locks) if work else {},
        )
        if "candidates" in want:
            out.candidates = [
                api.CandidateView(
                    candidate_id=c.candidate_id,
                    rank=c.rank,
                    metadata=c.metadata,
                    features=dict(c.features),
                    reasons=[r.value for r in c.reasons],
                    providers=list(dict.fromkeys(e.provider for e in c.evidence)),
                    identifier_linked=c.identifier_linked,
                    rejected=c.rejected,
                )
                for c in cands
            ]
        if "evidence" in want:
            out.evidence = [
                {"candidate_id": c.candidate_id, **e.model_dump(mode="json")}
                for c in cands
                for e in c.evidence
            ]
        if "access" in want:
            checks = self.store.access_checks.history(pid, work.work_id) if work else []
            out.access = [c.model_dump(mode="json") for c in sorted(checks, key=lambda c: not c.current)]
        if "local_copies" in want:
            copies = self.store.local_copies.list_for_work(pid, work.work_id) if work else []
            out.local_copies = [
                c.model_dump(mode="json") for c in copies if c.occurrence_id in (None, occ.occurrence_id)
            ]
        if "files" in want:
            files = self.store.work_files.list_for_work(pid, work.work_id) if work else []
            out.files = [f.model_dump(mode="json") for f in files]
        if "history" in want:
            events = [
                (
                    e.created_at,
                    {
                        "kind": "audit",
                        "type": e.type.value,
                        "at": e.created_at.isoformat(),
                        "project_revision": e.project_revision,
                        "provenance": e.provenance.model_dump(mode="json"),
                        "payload": e.payload,
                    },
                )
                for e in self.store.audit.list(pid, subject_id=occ.occurrence_id)
            ] + [
                (
                    d.created_at,
                    {"kind": "decision", "at": d.created_at.isoformat(), **d.model_dump(mode="json")},
                )
                for d in self.store.decisions.list_for_occurrence(pid, occ.occurrence_id)
            ]
            out.history = [row for _, row in sorted(events, key=lambda t: t[0])]
        return self._meta(ctx, out, project.revision)

    def add_reference(self, ctx: CallContext, req: api.AddReferenceIn) -> api.AddReferenceOut:
        self._need(ctx, Scope.REFERENCES_WRITE)
        text = req.text.strip()
        if not text:
            raise InvalidInput("text is empty")

        def fn(tx: Tx, _: Any) -> api.AddReferenceOut:
            pid = req.project_id
            self.store.sources.get(pid, req.source_id)
            existing = self.all_occurrences(pid, OccurrenceQuery(source_id=req.source_id, sort="order"))
            section_id: str | None = existing[-1].section_id if existing else None
            if req.after_occurrence_id:
                after = self.store.occurrences.get(pid, req.after_occurrence_id)
                if after.source_id != req.source_id:
                    raise NotFound(
                        f"{req.after_occurrence_id} not found", details={"id": req.after_occurrence_id}
                    )
                later = [o.order for o in existing if o.order > after.order]
                # ponytail: midpoints halve the gap per insert; renumber the source if gaps ever underflow
                order = (after.order + min(later)) / 2 if later else after.order + 1.0
                section_id = after.section_id
            else:
                order = existing[-1].order + 1.0 if existing else 1.0
            occ = Occurrence(
                occurrence_id=new_id("occurrence"),
                project_id=pid,
                source_id=req.source_id,
                section_id=section_id,
                order=order,
                raw_text=text,
                extraction=ExtractionState.CORRECTED,
                created_at=tx.now,
                updated_at=tx.now,
            )
            self.store.occurrences.add_many([occ])
            tx.audit(AuditEventType.OCCURRENCE_ADDED, occ.occurrence_id, source_id=req.source_id, order=order)
            return api.AddReferenceOut(occurrence_id=occ.occurrence_id, revision=occ.revision)

        return self._mutate(ctx, "add_reference", req.project_id, req, api.AddReferenceOut, fn)

    def update_reference(self, ctx: CallContext, req: api.UpdateReferenceIn) -> api.UpdateReferenceOut:
        self._need(ctx, Scope.REFERENCES_WRITE)
        change = req.change
        prepare = (
            (lambda: changes.prepare_set_doi(self, ctx, req.project_id, change))
            if isinstance(change, api.SetDoi)
            else None
        )

        def fn(tx: Tx, prepared: Any) -> api.UpdateReferenceOut:
            changed, created, primary = changes.HANDLERS[change.type](self, tx, change, prepared)
            ref = lambda o: api.RevisionRef(occurrence_id=o.occurrence_id, revision=o.revision)  # noqa: E731
            return api.UpdateReferenceOut(
                changed=[ref(o) for o in changed],
                created=[ref(o) for o in created],
                identity_state=primary.identity,
            )

        return self._mutate(
            ctx, "update_reference", req.project_id, req, api.UpdateReferenceOut, fn, prepare=prepare
        )

    def review_decision(self, ctx: CallContext, req: api.ReviewDecisionIn) -> api.UpdateReferenceOut:
        """TTY approval surface only (7.4): never reachable from MCP or a non-interactive CLI."""
        if ctx.channel != Channel.CLI_TTY_REVIEW or not ctx.tty:
            raise NotAuthorized("accepting with user approval needs the interactive review surface")
        self._need(ctx, Scope.REFERENCES_WRITE)

        def fn(tx: Tx, _: Any) -> api.UpdateReferenceOut:
            changed, _created, primary = changes.review(self, tx, req)
            return api.UpdateReferenceOut(
                changed=[
                    api.RevisionRef(occurrence_id=o.occurrence_id, revision=o.revision) for o in changed
                ],
                created=[],
                identity_state=primary.identity,
            )

        return self._mutate(ctx, "review_decision", req.project_id, req, api.UpdateReferenceOut, fn)

    # ================================================================ 16-18 resolve, files

    def resolve_citation(self, ctx: CallContext, req: api.ResolveCitationIn) -> api.ResolveCitationOut:
        self._need(ctx, Scope.METADATA_RESOLVE)
        if not req.citation and not req.fields:
            raise InvalidInput("give a citation or fields")
        self._require_email()
        snap = ConfigSnapshot(
            policy_profile=PolicyProfile.DEFAULT,
            network_mode=NetworkMode.METADATA,
            provider_allowlist=list(self.settings.enabled_providers),
            identity_policy_version=IDENTITY_POLICY_VERSION,
            eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
            adapter_versions={self.extractor.name: self.extractor.version},
            version_policy=VersionPolicy.PREFER_PUBLISHED,
            contact_email=self.settings.email,
        )
        fields = (
            CitationQuery(title=req.fields.title, authors=list(req.fields.authors), year=req.fields.year)
            if req.fields
            else None
        )
        result = run_sync(self.identity_factory(snap).resolve(req.citation, fields=fields))
        hint = {
            IdentityState.ACCEPTED_AUTO: "strong",
            IdentityState.NEEDS_REVIEW: "needs_review",
        }.get(result.state, "unresolved")
        out = api.ResolveCitationOut(
            candidates=[
                api.RankedCandidate(
                    rank=i + 1,
                    metadata=c.metadata,
                    scores=dict(c.features),
                    reasons=[r.value for r in c.reasons],
                )
                for i, c in enumerate(result.candidates)
            ],
            decision_hint=hint,  # type: ignore[arg-type]
        )
        return self._meta(ctx, out)

    def attach_file(self, ctx: CallContext, req: api.AttachFileIn) -> api.AttachFileOut:
        self._need(ctx, Scope.FILES_ATTACH)

        def prepare() -> Any:
            project = self.store.projects.get(req.project_id)
            occ = self.store.occurrences.get(req.project_id, req.occurrence_id)
            if occ.identity not in ACCEPTED_IDENTITIES or not occ.work_id:
                raise PrerequisiteNotMet("attach_file needs an accepted identity")
            path = self._allowed_file(req.path)
            work = self.store.works.get(req.project_id, occ.work_id)
            rows = self.local_copies.evaluate(
                req.project_id,
                work,
                version_policy=project.version_policy,
                attached_path=path,
                version_hint=req.version_hint,
            )
            attached = next((r for r in rows if r.origin == LocalCopyOrigin.USER_ATTACHED), None)
            if attached is None:
                raise InvalidState("the attachment was not evaluated")
            return attached, any(r.state == LocalCopyState.USABLE for r in rows), work, project.version_policy

        def fn(tx: Tx, prepared: Any) -> api.AttachFileOut:
            attached, any_usable, checked_work, checked_policy = prepared
            occ = self.store.occurrences.get(req.project_id, req.occurrence_id)
            if occ.identity not in ACCEPTED_IDENTITIES or not occ.work_id:
                raise PrerequisiteNotMet("attach_file needs an accepted identity")
            assert tx.project is not None
            current_work = self.store.works.get(req.project_id, occ.work_id)
            if (
                occ.work_id != checked_work.work_id
                or current_work.metadata != checked_work.metadata
                or current_work.overrides != checked_work.overrides
                or tx.project.version_policy != checked_policy
            ):
                raise RevisionConflict("attachment identity or version policy changed; evaluate again")
            copy = self.store.local_copies.add(
                attached.model_copy(update={"occurrence_id": occ.occurrence_id})
            )
            occ.local_copy = LocalCopyState.USABLE if any_usable else copy.state
            if copy.state == LocalCopyState.USABLE:  # all five checks pass: no download (AT-26, W-22)
                occ.retrieval = RetrievalState.SATISFIED_BY_LOCAL_COPY
            changes.rebase(occ)  # a local copy changes neither text, candidates nor fields
            changes.save(self, occ)
            tx.audit(
                AuditEventType.LOCAL_COPY_EVALUATED,
                occ.occurrence_id,
                local_copy_id=copy.local_copy_id,
                state=copy.state.value,
                failed_check=copy.failed_check.value if copy.failed_check else None,
            )
            return api.AttachFileOut(
                local_copy_id=copy.local_copy_id, checks=copy.checks, local_copy=copy.state
            )

        return self._mutate(ctx, "attach_file", req.project_id, req, api.AttachFileOut, fn, prepare=prepare)

    def download_files(self, ctx: CallContext, req: api.DownloadFilesIn) -> api.JobOut:
        self._need(ctx, Scope.FILES_DOWNLOAD)

        def validate() -> list[str]:
            project = self.store.projects.get(req.project_id)
            if not (project.download_enabled or self.settings.download_enabled_global):
                raise NotAuthorized(
                    "downloads are not enabled for this project", recovery_hint="Enable them in the CLI."
                )
            if project.network_mode != NetworkMode.RETRIEVAL:
                raise NetworkModeDenied("project network mode is not retrieval")
            self._require_email()
            scope = req.scope
            if isinstance(scope, api.OccurrenceScope):
                occs = self.store.occurrences.get_many(req.project_id, scope.occurrence_ids)
                bad = [o.occurrence_id for o in occs if not self._downloadable(o)]
                if bad:
                    raise PrerequisiteNotMet(
                        "identity must be accepted and discovery run", details={"occurrence_ids": bad}
                    )
            else:
                occs = [
                    o
                    for o in self.all_occurrences(req.project_id, self._query(req.project_id, scope.filter))
                    if self._downloadable(o)
                ]
                if not occs:
                    raise PrerequisiteNotMet(
                        "no occurrence in scope has an accepted identity and a discovery run"
                    )
            return [o.occurrence_id for o in occs]

        def fn(tx: Tx, _: Any) -> api.JobOut:
            ids = validate()
            budget = req.budget.model_dump(exclude_none=True) if req.budget else {}
            options: dict[str, Any] = {"budget": budget}
            if req.version_policy:
                options["version_policy"] = req.version_policy.value
            job = self._enqueue(
                tx,
                [StepType.RETRIEVE],
                {"occurrence_ids": ids},
                options,
                link=ids,
                version_policy=req.version_policy,
            )
            return api.JobOut(job_id=job.job_id)

        return self._mutate(ctx, "download_files", req.project_id, req, api.JobOut, fn, bump=False)

    @staticmethod
    def _downloadable(o: Occurrence) -> bool:
        return (
            o.identity in ACCEPTED_IDENTITIES
            and o.work_id is not None
            and o.discovery != DiscoveryState.NOT_CHECKED
            and o.extraction != ExtractionState.EXCLUDED
        )

    # ================================================================ 19-22 exports, drafts, artifacts

    def create_export(self, ctx: CallContext, req: api.CreateExportIn) -> api.JobOut:
        self._need(ctx, Scope.EXPORTS_CREATE)

        def fn(tx: Tx, _: Any) -> api.JobOut:
            assert tx.project is not None
            current = tx.project.revision
            if req.project_revision != current:  # AT-40, W-26
                raise RevisionNotCurrent(
                    f"project is at revision {current}",
                    details={"current_revision": current, "requested": req.project_revision},
                )
            options = {
                "formats": [f.value for f in dict.fromkeys(req.formats)],
                "selection": req.selection.value,
                "project_revision": current,
            }
            job = self._enqueue(tx, [StepType.EXPORT], {"filter": {}}, options)
            return api.JobOut(job_id=job.job_id)

        return self._mutate(ctx, "create_export", req.project_id, req, api.JobOut, fn, bump=False)

    def draft_requests(self, ctx: CallContext, req: api.DraftRequestsIn) -> api.JobOut:
        self._need(ctx, Scope.DRAFTS_CREATE)

        def validate() -> list[str]:
            occs = self.store.occurrences.get_many(req.project_id, req.scope.occurrence_ids)
            bad = [o.occurrence_id for o in occs if o.classification not in DRAFTABLE]
            if bad:
                raise PrerequisiteNotMet(
                    "drafts need classification none_found, restricted or unknown",
                    details={"occurrence_ids": bad},
                )
            return [o.occurrence_id for o in occs]

        def fn(tx: Tx, _: Any) -> api.JobOut:
            ids = validate()
            options = {
                "sender": req.sender.model_dump(exclude_none=True),
                "language": req.language.value,
                "group_by_author": req.group_by_author,
            }
            job = self._enqueue(tx, [StepType.DRAFT], {"occurrence_ids": ids}, options, link=ids)
            return api.JobOut(job_id=job.job_id)

        return self._mutate(ctx, "draft_requests", req.project_id, req, api.JobOut, fn, bump=False)

    def list_artifacts(self, ctx: CallContext, req: api.ListArtifactsIn) -> api.ListArtifactsOut:
        project = self._read(ctx, Scope.PROJECTS_READ, req.project_id)
        after, sk = self._after(req.cursor, "list_artifacts", req.project_id, req.kind)
        page = self.store.artifacts.list(
            req.project_id, kind=req.kind, after=after, limit=self._limit(req.limit)
        )
        out = api.ListArtifactsOut(
            artifacts=[self._descriptor(req.project_id, a) for a in page.items],
            next_cursor=encode_cursor(page.next_after, sk, self.clock()),
        )
        return self._meta(ctx, out, project.revision)

    def read_artifact(self, ctx: CallContext, req: api.ReadArtifactIn) -> api.ReadArtifactOut:
        project = self._read(ctx, Scope.PROJECTS_READ, req.project_id)
        art = self.store.artifacts.get(req.project_id, req.artifact_id)
        out = api.ReadArtifactOut(descriptor=self._descriptor(req.project_id, art))
        if _is_text(art.media_type):
            path = self._artifact_path(req.project_id, art)
            if path is None:
                raise NotFound("artifact content is missing", details={"artifact_id": art.artifact_id})
            size = path.stat().st_size
            want = min(req.length or 65536, self.settings.limits.read_artifact_bytes)
            with path.open("rb") as f:
                f.seek(min(req.offset, size))
                chunk = f.read(want)
            end = req.offset + len(chunk)
            # a chunk must not split a multibyte character: hold the incomplete tail for the next read
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            out.text = decoder.decode(chunk, final=end >= size)
            consumed = len(chunk) - len(decoder.getstate()[0])
            if chunk and consumed == 0:
                raise InvalidInput(
                    "length cannot hold one complete UTF-8 character",
                    recovery_hint="Increase length to at least 4 bytes (within the server limit) and retry.",
                )
            out.eof = req.offset + consumed >= size
        return self._meta(ctx, out, project.revision)
