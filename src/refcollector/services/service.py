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
import dataclasses
import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, TypeVar
from urllib.parse import urlparse

import pymupdf

from refcollector import __version__
from refcollector.config import (
    ELIGIBILITY_POLICY_VERSION,
    IDENTITY_POLICY_VERSION,
    P0_PROVIDERS,
    Settings,
    config_lock,
    get_secret,
    keyed_secret_digest,
    keys_fingerprints,
    load_settings,
    normalize_pdf_fallback,
    save_settings,
    secrets_present,
    set_secret,
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
    ApprovalSurface,
    AuditEventType,
    Channel,
    DiscoveryState,
    ExportFormat,
    ExtractionState,
    IdentityState,
    JobState,
    LocalCopyOrigin,
    LocalCopyState,
    NetworkMode,
    Origin,
    PolicyProfile,
    ReasonCode,
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
    LocalCopy,
    Occurrence,
    Preflight,
    Project,
    ProjectWork,
    Provenance,
    SectionInfo,
    Source,
    WorkMetadata,
    utcnow,
)
from refcollector.errors import (
    ConfigMissingEmail,
    EncryptedDocument,
    FileValidationFailed,
    IdempotencyConflict,
    IntegrationUnavailable,
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
from refcollector.identity.identifiers import unique_doi
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
from refcollector.services.web_auth import IntentRegistry
from refcollector.services.web_settings import (
    build_get_settings_out,
    compute_settings_revision,
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
        executor: Any = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.queue = queue
        self.extractor = extractor
        self.identity_factory = identity_factory
        self.local_copies = local_copies
        self.clock = clock
        self.executor = executor
        self._intent_registry = IntentRegistry()
        self._config_path = settings.config_path.resolve()
        self._config_mtime_ns: int | None = (
            self._config_path.stat().st_mtime_ns if self._config_path.exists() else None
        )

    # ================================================================ plumbing

    def _maybe_refresh_settings(self) -> Settings:
        try:
            if not self._config_path.exists():
                return self.settings
            current_mtime = self._config_path.stat().st_mtime_ns
            if self._config_mtime_ns is None or current_mtime != self._config_mtime_ns:
                self.settings = load_settings(self._config_path.parent)
                self._config_mtime_ns = current_mtime
        except OSError:
            pass
        return self.settings

    def provenance(self, ctx: CallContext) -> Provenance:
        return Provenance(principal=ctx.principal, channel=ctx.channel, origin=Origin(ctx.origin))

    def snapshot(self, project: Project) -> ConfigSnapshot:
        current = self._maybe_refresh_settings()
        return build_snapshot(current, project, {self.extractor.name: self.extractor.version})

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

    def _replay(
        self,
        ctx: CallContext,
        pid: str,
        tool: str,
        req: Any,
        out_type: type[O],
        *,
        fingerprint_override: str | None = None,
    ) -> O | None:
        rec = self.store.idempotency.get(ctx.principal, pid, tool, req.idempotency_key, now=self.clock())
        if rec is None:
            return None
        expected_fp = fingerprint_override or fingerprint(req)
        if rec.fingerprint != expected_fp:
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
        fingerprint_override: str | None = None,
    ) -> O:
        """One immediate transaction (D-26): idempotency (11.3), the change, audit, revision +1."""
        pid = project_id or ""
        if project_id:
            self.store.projects.get(project_id)
        if (
            hit := self._replay(ctx, pid, tool, req, out_type, fingerprint_override=fingerprint_override)
        ) is not None:
            return hit
        prepared = prepare() if prepare else None  # slow reads (PDF parse, providers) outside the lock
        with self.store.transaction(immediate=True):
            if (
                hit := self._replay(ctx, pid, tool, req, out_type, fingerprint_override=fingerprint_override)
            ) is not None:
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
                    fingerprint=fingerprint_override or fingerprint(req),
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
        self._maybe_refresh_settings()
        if not self.settings.email:
            raise ConfigMissingEmail()

    # ================================================================ views

    def _summary(self, occ: Occurrence) -> api.OccurrenceSummary:
        n = len(self.store.candidates.list_for_occurrence(occ.project_id, occ.occurrence_id))
        citation_files = self._current_citation_files(occ)
        latest = citation_files[-1] if citation_files else None
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
            order=occ.order,
            work_id=occ.work_id,
            citation_doi=unique_doi(occ.text) if occ.extraction != ExtractionState.EXCLUDED else None,
            citation_file_id=latest.citation_file_id if latest else None,
            citation_file_state=latest.state if latest else None,
        )

    def _current_citation_files(self, occ: Occurrence):
        return [
            file
            for file in self.store.citation_files.list_for_occurrence(occ.project_id, occ.occurrence_id)
            if file.is_current(occ)
        ]

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
        with self.store.transaction():
            occs = self.all_occurrences(pid, OccurrenceQuery())
            arts, after = [], None
            while True:  # ids sort by time: the newest artifacts are last
                page = self.store.artifacts.list(pid, after=after, limit=CHUNK)
                arts += page.items
                if page.next_after is None:
                    break
                after = page.next_after
            sources_count = self.store.sources.list(pid, limit=1).total or 0
            recent_jobs = self._job_summaries(pid)
        by_extraction = {e.value: 0 for e in ExtractionState}
        by_identity = {i.value: 0 for i in IdentityState}
        by_discovery = {d.value: 0 for d in DiscoveryState}
        by_classification = {c.value: 0 for c in AccessClassification}
        by_classification["unclassified"] = 0
        by_local_copy = {lc.value: 0 for lc in LocalCopyState}
        by_retrieval = {r.value: 0 for r in RetrievalState}

        for o in occs:
            by_extraction[o.extraction.value] += 1
            by_identity[o.identity.value] += 1
            by_discovery[o.discovery.value] += 1
            by_classification[o.classification.value if o.classification else "unclassified"] += 1
            by_local_copy[o.local_copy.value] += 1
            by_retrieval[o.retrieval.value] += 1

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
                "by_extraction": by_extraction,
                "by_identity": by_identity,
                "by_discovery": by_discovery,
                "by_classification": by_classification,
                "by_local_copy": by_local_copy,
                "by_retrieval": by_retrieval,
                "by_access": dict(
                    Counter(o.classification.value if o.classification else "not_checked" for o in occs)
                ),
            },
            download_enabled=project.download_enabled,
            sources_count=sources_count,
            recent_jobs=recent_jobs,
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

    def rename_project(self, ctx: CallContext, req: api.RenameProjectIn) -> api.RenameProjectOut:
        self._need(ctx, Scope.PROJECTS_WRITE)
        new_name = req.name.strip()
        if not new_name:
            raise InvalidInput("Project name cannot be empty")
        if not req.idempotency_key:
            req = req.model_copy(update={"idempotency_key": new_id("request")})

        def fn(tx: Tx, _: Any) -> api.RenameProjectOut:
            project = tx.project
            assert project is not None
            updated = self.store.projects.update(project.model_copy(update={"name": new_name}))
            tx.attach(updated)
            tx.audit(AuditEventType.FIELDS_SET, project.project_id, name=new_name)
            return api.RenameProjectOut(project_id=project.project_id, name=new_name, revision=tx.revision)

        return self._mutate(ctx, "rename_project", req.project_id, req, api.RenameProjectOut, fn)

    def get_project_consequences(
        self, ctx: CallContext, req: api.GetProjectConsequencesIn
    ) -> api.GetProjectConsequencesOut:
        project = self._read(ctx, Scope.PROJECTS_READ, req.project_id)
        paper_count, citation_count = self.store.projects.get_counts(req.project_id)
        active_states = [s for s in JobState if s not in TERMINAL_JOB_STATES]
        active = self.queue.list_all_jobs(project_id=req.project_id, states=active_states, limit=1).items
        return self._meta(
            ctx,
            api.GetProjectConsequencesOut(
                project_id=project.project_id,
                name=project.name,
                paper_count=paper_count,
                citation_count=citation_count,
                has_active_jobs=bool(active),
            ),
            project.revision,
        )

    def delete_project(self, ctx: CallContext, req: api.DeleteProjectIn) -> api.DeleteProjectOut:
        self._need(ctx, Scope.PROJECTS_WRITE)
        project = self._read(ctx, Scope.PROJECTS_WRITE, req.project_id)
        active_states = [s for s in JobState if s not in TERMINAL_JOB_STATES]
        active = self.queue.list_all_jobs(project_id=req.project_id, states=active_states, limit=1).items
        if active:
            job = active[0]
            raise InvalidState(
                f"Cannot delete project '{req.project_id}': active job {job.job_id} "
                f"is in state {job.state.value}",
                details={"project_id": req.project_id, "job_id": job.job_id, "state": job.state.value},
            )
        self.store.projects.delete(req.project_id)
        return self._meta(ctx, api.DeleteProjectOut(project_id=req.project_id), project.revision)

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

    def _persist_source(
        self,
        tx: Tx,
        project_id: str,
        *,
        sha: str,
        kind: SourceType,
        label: str,
        origin: str,
        preflight: Preflight,
        tmp_path: Path | None = None,
        raw_bytes: bytes | None = None,
    ) -> api.ImportSourceOut:
        known = self.store.sources.find_by_hash(project_id, sha)
        if known is not None:
            return api.ImportSourceOut(source_id=known.source_id, reused=True, preflight=known.preflight)
        media_type = "application/pdf" if kind == SourceType.PDF else "text/plain"
        if tmp_path is None:
            suffix = ".pdf" if kind == SourceType.PDF else ".txt"
            tmp_path = self.store.files.new_tmp_path(suffix)
            assert raw_bytes is not None
            tmp_path.write_bytes(raw_bytes)
        blob = self.store.files.commit(tmp_path, media_type)
        self.store.files.grant(project_id, blob.sha256)
        source = self.store.sources.add(
            Source(
                source_id=new_id("source"),
                project_id=project_id,
                type=kind,
                label=label,
                sha256=sha,
                origin=origin,
                preflight=preflight,
                sections=[s.model_copy() for s in preflight.sections],
                imported_at=tx.now,
            )
        )
        tx.audit(
            AuditEventType.SOURCE_IMPORTED,
            source.source_id,
            sha256=sha,
            type=kind.value,
            label=label,
        )
        return api.ImportSourceOut(source_id=source.source_id, reused=False, preflight=preflight)

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
            return self._persist_source(
                tx,
                req.project_id,
                sha=p["sha"],
                kind=p["kind"],
                label=p["label"],
                origin=p["origin"],
                preflight=p["preflight"],
                raw_bytes=p["raw"],
            )

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
            sha256=src.sha256,
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
            link = []
            if isinstance(scope, api.OccurrenceScope):
                selected = [
                    o
                    for o in self._scope_occurrences(req.project_id, scope)
                    if o.extraction != ExtractionState.EXCLUDED
                ]
                if cap is not None:
                    selected = selected[:cap]
                link = [o.occurrence_id for o in selected]
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
                    retry_wait=c.get("retry_wait", 0),
                    cancelled=c.get("cancelled", 0),
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
            classification_is_null=f.classification_is_null,
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
        if "citation_files" in want:
            out.citation_files = [f.model_dump(mode="json") for f in self._current_citation_files(occ)]
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
        """TTY approval surface (7.4) or validated web review (Wave 3)."""
        if ctx.channel == Channel.CLI_TTY_REVIEW and ctx.tty:
            pass
        elif ctx.channel == Channel.WEB_UI and ctx.review_grant is not None:
            grant = ctx.review_grant
            intent = self._intent_registry.get(grant.token)
            if intent is None or intent.purpose != "review":
                raise NotAuthorized("invalid or forged review grant")
            if intent.status == "pending" and self.clock() > intent.expires_at:
                raise NotAuthorized("review grant expired")
            if (
                intent.web_session_id != grant.web_session_id
                or intent.principal != ctx.principal
                or grant.project_id != req.project_id
                or grant.occurrence_id != req.occurrence_id
                or grant.candidate_id != req.candidate_id
                or grant.action != req.action
                or grant.expected_revision != req.expected_revision
            ):
                raise NotAuthorized("review grant does not match request")
        else:
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

    def _apply_attachment(
        self,
        tx: Tx,
        project_id: str,
        occurrence_id: str,
        *,
        attached: LocalCopy,
        any_usable: bool,
        checked_work: ProjectWork,
        checked_policy: VersionPolicy,
    ) -> api.AttachFileOut:
        occ = self.store.occurrences.get(project_id, occurrence_id)
        if occ.identity not in ACCEPTED_IDENTITIES or not occ.work_id:
            raise PrerequisiteNotMet("attach_file needs an accepted identity")
        assert tx.project is not None
        current_work = self.store.works.get(project_id, occ.work_id)
        if (
            occ.work_id != checked_work.work_id
            or current_work.metadata != checked_work.metadata
            or current_work.overrides != checked_work.overrides
            or tx.project.version_policy != checked_policy
        ):
            raise RevisionConflict("attachment identity or version policy changed; evaluate again")
        copy = self.store.local_copies.add(attached.model_copy(update={"occurrence_id": occ.occurrence_id}))
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
        return api.AttachFileOut(local_copy_id=copy.local_copy_id, checks=copy.checks, local_copy=copy.state)

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
            return self._apply_attachment(
                tx,
                req.project_id,
                req.occurrence_id,
                attached=attached,
                any_usable=any_usable,
                checked_work=checked_work,
                checked_policy=checked_policy,
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
            if req.retrieval_basis == "citation_doi":
                if not self.settings.pdf_fallback_url:
                    raise InvalidInput("Citation DOI retrieval requires a configured trusted fallback")
                if req.version_policy != VersionPolicy.ANY_ELIGIBLE:
                    raise InvalidInput("Citation DOI retrieval requires explicit any_eligible version policy")
            eligible = self._doi_downloadable if req.retrieval_basis == "citation_doi" else self._downloadable
            scope = req.scope
            if isinstance(scope, api.OccurrenceScope):
                occs = self.store.occurrences.get_many(req.project_id, scope.occurrence_ids)
                bad = [o.occurrence_id for o in occs if not eligible(o)]
                if bad:
                    raise PrerequisiteNotMet(
                        "citation DOI must be unique and active"
                        if req.retrieval_basis == "citation_doi"
                        else "identity must be accepted and discovery run",
                        details={"occurrence_ids": bad},
                    )
            else:
                occs = [
                    o
                    for o in self.all_occurrences(req.project_id, self._query(req.project_id, scope.filter))
                    if eligible(o)
                ]
                if not occs:
                    raise PrerequisiteNotMet(
                        "no eligible occurrence in scope for the requested retrieval basis"
                    )
            return [o.occurrence_id for o in occs]

        def fn(tx: Tx, _: Any) -> api.JobOut:
            ids = validate()
            budget = req.budget.model_dump(exclude_none=True) if req.budget else {}
            options: dict[str, Any] = {"budget": budget}
            stage = StepType.RETRIEVE
            if req.retrieval_basis == "citation_doi":
                stage = StepType.RETRIEVE_DOI
                options["doi_targets"] = {
                    oid: {
                        "doi": unique_doi(occ.text),
                        "revision": occ.revision,
                        "text_sha256": hashlib.sha256(occ.text.encode("utf-8")).hexdigest(),
                    }
                    for oid in ids
                    for occ in [self.store.occurrences.get(req.project_id, oid)]
                }
            if req.version_policy:
                options["version_policy"] = req.version_policy.value
            job = self._enqueue(
                tx,
                [stage],
                {"occurrence_ids": ids},
                options,
                link=ids,
                version_policy=req.version_policy,
            )
            return api.JobOut(job_id=job.job_id)

        return self._mutate(ctx, "download_files", req.project_id, req, api.JobOut, fn, bump=False)

    @staticmethod
    def _doi_downloadable(o: Occurrence) -> bool:
        return o.extraction != ExtractionState.EXCLUDED and unique_doi(o.text) is not None

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
            out.next_offset = req.offset + consumed
            out.eof = req.offset + consumed >= size
        return self._meta(ctx, out, project.revision)

    # ================================================================ Section 10 additions

    def list_jobs(self, ctx: CallContext, req: api.ListJobsIn) -> api.ListJobsOut:
        self._need(ctx, Scope.PROJECTS_READ)
        if req.project_id:
            self.store.projects.get(req.project_id)
        after, sk = self._after(
            req.cursor,
            "list_jobs",
            req.project_id,
            [s.value for s in req.states] if req.states else None,
        )
        page = self.queue.list_all_jobs(
            project_id=req.project_id,
            states=req.states,
            after=after,
            limit=self._limit(req.limit),
        )
        project_names: dict[str, str] = {}
        items: list[api.JobListItem] = []
        for j in page.items:
            if j.project_id not in project_names:
                try:
                    p = self.store.projects.get(j.project_id)
                    project_names[j.project_id] = p.name
                except NotFound:
                    project_names[j.project_id] = "unknown"
            items.append(
                api.JobListItem(
                    project_id=j.project_id,
                    project_name=project_names[j.project_id],
                    job_id=j.job_id,
                    state=j.state,
                    stages=[s.value for s in j.stages],
                    created_at=j.created_at,
                    updated_at=j.updated_at,
                )
            )
        out = api.ListJobsOut(
            jobs=items,
            next_cursor=encode_cursor(page.next_after, sk, self.clock()),
        )
        return self._meta(ctx, out)

    def list_global_artifacts(
        self, ctx: CallContext, req: api.ListGlobalArtifactsIn
    ) -> api.ListGlobalArtifactsOut:
        self._need(ctx, Scope.PROJECTS_READ)
        after, sk = self._after(req.cursor, "list_global_artifacts", req.kind)
        page = self.store.artifacts.list_global(kind=req.kind, after=after, limit=self._limit(req.limit))
        project_names: dict[str, str] = {}
        items: list[api.GlobalArtifactItem] = []
        for a in page.items:
            if a.project_id not in project_names:
                try:
                    p = self.store.projects.get(a.project_id)
                    project_names[a.project_id] = p.name
                except NotFound:
                    project_names[a.project_id] = "unknown"
            items.append(
                api.GlobalArtifactItem(
                    project_id=a.project_id,
                    project_name=project_names[a.project_id],
                    descriptor=self._descriptor(a.project_id, a),
                )
            )
        out = api.ListGlobalArtifactsOut(
            artifacts=items,
            next_cursor=encode_cursor(page.next_after, sk, self.clock()),
        )
        return self._meta(ctx, out)

    def get_executor_status(self, ctx: CallContext, req: api.GetExecutorStatusIn) -> api.GetExecutorStatusOut:
        self._need(ctx, Scope.PROJECTS_READ)
        now = self.clock()
        executor = getattr(self, "executor", None)
        if executor is not None and getattr(executor, "is_owner", False):
            out = api.GetExecutorStatusOut(
                state="owned",
                owner_id=executor.executor_id,
                owner_pid=os.getpid(),
                heartbeat_at=None,
                observed_at=now,
                is_local_owner=True,
            )
            return self._meta(ctx, out)

        db = getattr(self.store, "db", None) or getattr(self.queue, "db", None)
        if db is None:
            out = api.GetExecutorStatusOut(
                state="unowned",
                owner_id=None,
                owner_pid=None,
                heartbeat_at=None,
                observed_at=now,
                is_local_owner=False,
            )
            return self._meta(ctx, out)

        row = db.query_one(
            "SELECT id, pid, started_at, stopped_at FROM executor ORDER BY started_at DESC LIMIT 1"
        )
        if row is None or row["stopped_at"] is not None:
            out = api.GetExecutorStatusOut(
                state="unowned",
                owner_id=None,
                owner_pid=None,
                heartbeat_at=None,
                observed_at=now,
                is_local_owner=False,
            )
            return self._meta(ctx, out)

        pid = int(row["pid"])
        # An active row cannot prove ownership without a lease heartbeat. Never
        # signal a PID to inspect it: os.kill(pid, 0) is unsafe on Windows.
        out = api.GetExecutorStatusOut(
            state="unknown",
            owner_id=str(row["id"]),
            owner_pid=pid,
            heartbeat_at=None,
            observed_at=now,
            is_local_owner=False,
        )
        return self._meta(ctx, out)

    def project_file_path(self, ctx: CallContext, project_id: str, kind: str, resource_id: str) -> Path:
        if kind in ("source", "work_file", "local_copy", "citation_file"):
            self._need(ctx, Scope.REFERENCES_READ)
        elif kind == "artifact":
            self._need(ctx, Scope.PROJECTS_READ)
        else:
            raise InvalidInput(f"unsupported kind {kind}")

        self.store.projects.get(project_id)
        if kind == "source":
            source = self.store.sources.get(project_id, resource_id)
            sha = source.sha256
        elif kind == "work_file":
            wf = self.store.work_files.get(project_id, resource_id)
            sha = wf.sha256
        elif kind == "citation_file":
            cf = self.store.citation_files.get(project_id, resource_id)
            occ = self.store.occurrences.get(project_id, cf.occurrence_id)
            if not cf.is_current(occ):
                raise NotFound("Citation file no longer matches the current citation")
            sha = cf.sha256
        elif kind == "local_copy":
            lc = self.store.local_copies.get(project_id, resource_id)
            sha = lc.sha256
            if not sha:
                raise NotFound("file content is missing", details={"resource_id": resource_id})
        elif kind == "artifact":
            art = self.store.artifacts.get(project_id, resource_id)
            sha = art.sha256
        else:
            raise InvalidInput(f"unsupported kind {kind}")

        try:
            path = self.store.files.path_for(project_id, sha).resolve()
        except NotFound:
            raise NotFound("file content is missing", details={"resource_id": resource_id}) from None

        rc_home = self.settings.rc_home.resolve()
        if not path.is_relative_to(rc_home):
            raise PathNotAllowed("file is outside RC_HOME", details={"resource_id": resource_id})

        if not path.is_file():
            raise NotFound("file not found on disk", details={"resource_id": resource_id})

        return path

    def read_project_file(self, ctx: CallContext, req: api.ReadProjectFileIn) -> api.ReadProjectFileOut:
        if req.kind in ("source", "work_file", "local_copy", "citation_file"):
            self._need(ctx, Scope.REFERENCES_READ)
        elif req.kind == "artifact":
            self._need(ctx, Scope.PROJECTS_READ)
        else:
            raise InvalidInput(f"unsupported kind {req.kind}")

        project = self.store.projects.get(req.project_id)
        if req.kind == "source":
            source = self.store.sources.get(req.project_id, req.resource_id)
            sha = source.sha256
            name = source.label
            media_type = "application/pdf" if source.type == SourceType.PDF else "text/plain"
        elif req.kind == "work_file":
            wf = self.store.work_files.get(req.project_id, req.resource_id)
            sha = wf.sha256
            name = wf.export_name or f"{wf.work_file_id}.pdf"
            media_type = "application/pdf"
        elif req.kind == "citation_file":
            cf = self.store.citation_files.get(req.project_id, req.resource_id)
            sha = cf.sha256
            name = cf.export_name
            media_type = "application/pdf"
        elif req.kind == "local_copy":
            lc = self.store.local_copies.get(req.project_id, req.resource_id)
            sha = lc.sha256
            if not sha:
                raise NotFound("file content is missing", details={"resource_id": req.resource_id})
            name = f"{lc.local_copy_id}.pdf"
            media_type = "application/pdf"
        elif req.kind == "artifact":
            art = self.store.artifacts.get(req.project_id, req.resource_id)
            sha = art.sha256
            name = art.name
            media_type = art.media_type
        else:
            raise InvalidInput(f"unsupported kind {req.kind}")

        path = self.project_file_path(ctx, req.project_id, req.kind, req.resource_id)

        with path.open("rb") as f:
            actual_sha = hashlib.file_digest(f, "sha256").hexdigest()
        if actual_sha != sha:
            raise FileValidationFailed(
                f"storage integrity violation: file hash {actual_sha} does not match expected {sha}",
                details={"expected_sha256": sha, "actual_sha256": actual_sha},
            )

        total_bytes = path.stat().st_size
        if req.offset > total_bytes:
            raise InvalidInput(
                f"offset {req.offset} exceeds file size {total_bytes}",
                details={"offset": req.offset, "total_bytes": total_bytes},
            )

        if req.offset == total_bytes:
            data = b""
            next_offset = req.offset
            eof = True
        else:
            with path.open("rb") as f:
                f.seek(req.offset)
                data = f.read(req.length)
            next_offset = req.offset + len(data)
            eof = next_offset >= total_bytes

        descriptor = api.ProjectFileDescriptor(
            resource_id=req.resource_id,
            kind=req.kind,
            name=name,
            media_type=media_type,
            bytes=total_bytes,
            sha256=sha,
        )
        out = api.ReadProjectFileOut(
            descriptor=descriptor,
            data=data,
            next_offset=next_offset,
            eof=eof,
        )
        return self._meta(ctx, out, project.revision)

    def upload_pdf(self, ctx: CallContext, req: api.UploadPdfIn, body: BinaryIO) -> api.UploadPdfOut:
        """Upload a PDF for source import or local-copy attachment (spec 10.3, D-39)."""
        if req.purpose == "source":
            self._need(ctx, Scope.SOURCES_IMPORT)
        elif req.purpose == "local_copy":
            self._need(ctx, Scope.FILES_ATTACH)
            if not req.occurrence_id:
                raise InvalidInput("occurrence_id is required for local_copy upload")
        else:
            raise InvalidInput(f"unknown purpose: {req.purpose}")

        # Validate display name as a safe leaf name
        if not req.name or not req.name.strip():
            raise InvalidInput("name cannot be empty or blank")
        if "/" in req.name or "\\" in req.name:
            raise InvalidInput("name must not contain path separators")
        if ".." in req.name or req.name in (".", ".."):
            raise InvalidInput("name must not contain path traversal")
        if any(ord(c) < 32 or ord(c) == 127 for c in req.name):
            raise InvalidInput("name must not contain control characters")

        # Bounds on declared size
        if req.declared_bytes < 0:
            raise InvalidInput("declared_bytes cannot be negative")
        if req.declared_bytes > self.settings.limits.pdf_bytes:
            raise LimitExceeded(
                "declared_bytes exceeds PDF limit",
                details={"limit": self.settings.limits.pdf_bytes, "declared": req.declared_bytes},
            )

        # Validate project
        project = self.store.projects.get(req.project_id)

        # Early validation for local_copy
        work = None
        if req.purpose == "local_copy":
            occ = self.store.occurrences.get(req.project_id, req.occurrence_id)
            if occ.identity not in ACCEPTED_IDENTITIES or not occ.work_id:
                raise PrerequisiteNotMet("attach_file needs an accepted identity")
            work = self.store.works.get(req.project_id, occ.work_id)

        tmp = self.store.files.new_tmp_path(".pdf")
        try:
            hasher = hashlib.sha256()
            actual_bytes = 0
            chunk_size = 65536
            first_chunk = True

            with tmp.open("wb") as f:
                while True:
                    chunk = body.read(chunk_size)
                    if not chunk:
                        break
                    if first_chunk:
                        first_chunk = False
                        if not chunk.startswith(b"%PDF"):
                            raise UnsupportedSource("uploaded file is not a PDF")
                    actual_bytes += len(chunk)
                    if actual_bytes > self.settings.limits.pdf_bytes:
                        raise LimitExceeded(
                            "file exceeds PDF limit",
                            details={"limit": self.settings.limits.pdf_bytes, "actual": actual_bytes},
                        )
                    if actual_bytes > req.declared_bytes:
                        raise LimitExceeded(
                            "file exceeds declared_bytes",
                            details={"declared": req.declared_bytes, "actual": actual_bytes},
                        )
                    hasher.update(chunk)
                    f.write(chunk)

            if first_chunk or actual_bytes == 0:
                raise UnsupportedSource("uploaded file is empty or not a PDF")

            if actual_bytes != req.declared_bytes:
                raise InvalidInput(
                    "actual bytes does not match declared_bytes",
                    details={"declared": req.declared_bytes, "actual": actual_bytes},
                )

            actual_sha = hasher.hexdigest()
            fp = hashlib.sha256((fingerprint(req) + ":" + actual_sha).encode("utf-8")).hexdigest()

            # Check idempotency replay before mutation
            rec = self.store.idempotency.get(
                ctx.principal, req.project_id, "upload_pdf", req.idempotency_key, now=self.clock()
            )
            if rec is not None:
                if rec.fingerprint != fp:
                    raise IdempotencyConflict(
                        f"key {req.idempotency_key} was used with a different request or payload"
                    )
                return api.UploadPdfOut.model_validate(rec.result)

            if req.purpose == "source":
                known = self.store.sources.find_by_hash(req.project_id, actual_sha)
                if known is not None:
                    preflight = known.preflight
                else:
                    data = tmp.read_bytes()
                    preflight = self.extractor.preflight(data, SourceType.PDF)

                def fn_source(tx: Tx, _prep: Any) -> api.UploadPdfOut:
                    src_out = self._persist_source(
                        tx,
                        req.project_id,
                        sha=actual_sha,
                        kind=SourceType.PDF,
                        label=req.name,
                        origin="uploaded",
                        preflight=preflight,
                        tmp_path=tmp,
                    )
                    return api.UploadPdfOut(source=src_out, attachment=None)

                return self._mutate(
                    ctx,
                    "upload_pdf",
                    req.project_id,
                    req,
                    api.UploadPdfOut,
                    fn_source,
                    fingerprint_override=fp,
                )

            else:  # req.purpose == "local_copy"
                assert work is not None
                rows = self.local_copies.evaluate(
                    req.project_id,
                    work,
                    version_policy=project.version_policy,
                    attached_path=tmp,
                    version_hint=req.version_hint,
                )
                attached = next((r for r in rows if r.origin == LocalCopyOrigin.USER_ATTACHED), None)
                if attached is None:
                    raise InvalidState("the attachment was not evaluated")
                any_usable = any(r.state == LocalCopyState.USABLE for r in rows)
                checked_work = work
                checked_policy = project.version_policy

                def fn_attach(tx: Tx, _prep: Any) -> api.UploadPdfOut:
                    attach_out = self._apply_attachment(
                        tx,
                        req.project_id,
                        req.occurrence_id,
                        attached=attached,
                        any_usable=any_usable,
                        checked_work=checked_work,
                        checked_policy=checked_policy,
                    )
                    return api.UploadPdfOut(source=None, attachment=attach_out)

                return self._mutate(
                    ctx,
                    "upload_pdf",
                    req.project_id,
                    req,
                    api.UploadPdfOut,
                    fn_attach,
                    fingerprint_override=fp,
                )
        finally:
            tmp.unlink(missing_ok=True)

    def render_source_page(self, ctx: CallContext, req: api.RenderSourcePageIn) -> api.RenderSourcePageOut:
        """Render a source PDF page as a raster PNG.

        Coordinates and overlay limitation:
        PyMuPDF user coordinates for page.rect account for page rotation
        (page.rotation in {0, 90, 180, 270}). The extraction adapter
        (extraction/layout.py) normalizes unrotated text bboxes relative to
        page.rect dimensions. On unrotated pages (rotation == 0), normalized
        bounding boxes [x0, y0, x1, y1] map directly to raster pixel coordinates:
            px0 = x0 * width
            py0 = y0 * height
            pw  = (x1 - x0) * width
            ph  = (y1 - y0) * height
        On rotated pages, direct visual overlay is not supported;
        `span_overlay_supported` is set to False to report this limitation.
        """
        self._need(ctx, Scope.REFERENCES_READ)

        if not math.isfinite(req.scale) or not (0.5 <= req.scale <= 2.0):
            raise InvalidInput("scale must be a finite float between 0.5 and 2.0")

        project = self.store.projects.get(req.project_id)
        source = self.store.sources.get(req.project_id, req.source_id)

        if source.type != SourceType.PDF:
            raise UnsupportedSource(
                "source is not a PDF",
                details={"type": source.type.value if hasattr(source.type, "value") else str(source.type)},
            )

        if req.page_index < 0 or req.page_index >= source.preflight.pages:
            raise InvalidInput(
                f"page index {req.page_index} out of bounds (total pages: {source.preflight.pages})",
                details={"page_index": req.page_index, "pages": source.preflight.pages},
            )

        try:
            path = self.store.files.path_for(req.project_id, source.sha256).resolve()
        except NotFound:
            raise NotFound("source content is missing", details={"source_id": req.source_id}) from None

        rc_home = self.settings.rc_home.resolve()
        if not path.is_relative_to(rc_home):
            raise PathNotAllowed("file is outside RC_HOME", details={"source_id": req.source_id})

        if not path.is_file():
            raise NotFound("file not found on disk", details={"source_id": req.source_id})

        with path.open("rb") as f:
            actual_sha = hashlib.file_digest(f, "sha256").hexdigest()
        if actual_sha != source.sha256:
            raise FileValidationFailed(
                f"storage integrity violation: hash {actual_sha} does not match {source.sha256}",
                details={"expected_sha256": source.sha256, "actual_sha256": actual_sha},
            )

        try:
            with pymupdf.open(path) as doc:
                if not doc.is_pdf:
                    raise UnsupportedSource("stored file is not a PDF")
                if doc.is_encrypted:
                    raise EncryptedDocument()
                if req.page_index >= len(doc):
                    raise InvalidInput(
                        f"page index {req.page_index} out of bounds (total pages: {len(doc)})",
                        details={"page_index": req.page_index, "pages": len(doc)},
                    )
                page = doc.load_page(req.page_index)
                matrix = pymupdf.Matrix(req.scale, req.scale)
                target_rect = (page.rect * matrix).round()
                target_width = target_rect.width
                target_height = target_rect.height
                if target_width <= 0 or target_height <= 0:
                    raise InvalidInput("invalid rendered page dimensions")
                pixels = target_width * target_height
                if pixels > 8_000_000:
                    raise LimitExceeded(
                        "raster dimensions exceed 8 million pixels",
                        details={
                            "width": target_width,
                            "height": target_height,
                            "pixels": pixels,
                            "limit": 8_000_000,
                        },
                    )
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                png_data = pix.tobytes("png")
                span_overlay_supported = page.rotation == 0
                out = api.RenderSourcePageOut(
                    data=png_data,
                    media_type="image/png",
                    page_index=req.page_index,
                    width=pix.width,
                    height=pix.height,
                    span_overlay_supported=span_overlay_supported,
                )
                return self._meta(ctx, out, project.revision)
        except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
            raise UnsupportedSource("could not read PDF data") from exc

    # ================================================================ Wave 3: trusted review and web settings

    def _fingerprint_web_settings(self, rc_home: Path, req: api.UpdateWebSettingsIn) -> str:
        c = req.change
        if isinstance(c, api.SetProviderKeyChange):
            secret_digest = keyed_secret_digest(rc_home, c.value.get_secret_value())
            payload = {
                "expected_settings_revision": req.expected_settings_revision,
                "change": {
                    "type": c.type,
                    "provider": c.provider,
                    "secret_digest": secret_digest,
                },
            }
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return fingerprint(req)

    def prepare_web_review(self, ctx: CallContext, req: api.ReviewDecisionIn) -> api.WebIntentOut:
        if ctx.channel != Channel.WEB_UI or not ctx.web_session_id:
            raise NotAuthorized("web review intent requires Channel.WEB_UI and an active web session")
        self._need(ctx, Scope.REFERENCES_WRITE)

        project = self.store.projects.get(req.project_id)
        occ = self.store.occurrences.get(req.project_id, req.occurrence_id)
        if occ.revision != req.expected_revision:
            raise RevisionConflict(
                "occurrence revision has changed",
                details={"current_revisions": {req.occurrence_id: occ.revision}},
            )
        if occ.extraction == ExtractionState.EXCLUDED:
            raise InvalidState(f"{req.occurrence_id} is excluded", recovery_hint="Restore it first.")
        cand = self.store.candidates.get(req.project_id, req.candidate_id)
        if cand.occurrence_id != req.occurrence_id:
            raise NotFound(f"candidate {req.candidate_id} not found on occurrence {req.occurrence_id}")
        if req.action == "accept" and cand.rejected:
            raise InvalidState("candidate was rejected and cannot be accepted")

        summary = {
            "project_id": req.project_id,
            "occurrence_id": req.occurrence_id,
            "action": req.action,
            "candidate_id": req.candidate_id,
            "expected_revision": req.expected_revision,
            "citation_text": occ.text,
            "candidate": {
                "candidate_id": cand.candidate_id,
                "title": cand.metadata.title,
                "authors": [
                    a.model_dump(mode="json") if hasattr(a, "model_dump") else str(a)
                    for a in cand.metadata.authors
                ],
                "year": cand.metadata.year,
                "doi": cand.metadata.doi,
                "reasons": list(cand.reasons),
            },
            "note": req.note,
        }
        fp = fingerprint(req)
        now = self.clock()
        record = self._intent_registry.register(
            purpose="review",
            principal=ctx.principal,
            web_session_id=ctx.web_session_id,
            project_id=req.project_id,
            target_id=req.occurrence_id,
            candidate_id=req.candidate_id,
            action=req.action,
            expected_revision=req.expected_revision,
            idempotency_key=req.idempotency_key,
            fingerprint=fp,
            summary=summary,
            now=now,
        )
        out = api.WebIntentOut(
            confirmation_token=record.token,
            expires_at=record.expires_at,
            purpose="review",
            summary=summary,
        )
        return self._meta(ctx, out, project.revision)

    def confirm_web_review(
        self, ctx: CallContext, req: api.ReviewDecisionIn, confirmation_token: str
    ) -> api.UpdateReferenceOut:
        if ctx.channel != Channel.WEB_UI or not ctx.web_session_id:
            raise NotAuthorized("web review confirmation requires Channel.WEB_UI and an active web session")
        self._need(ctx, Scope.REFERENCES_WRITE)

        if not confirmation_token or not confirmation_token.strip():
            raise NotAuthorized("confirmation token required")

        intent = self._intent_registry.get(confirmation_token)
        if intent is None:
            raise NotAuthorized("invalid or unknown confirmation token")

        if intent.purpose != "review":
            raise NotAuthorized("token purpose mismatch")
        if intent.web_session_id != ctx.web_session_id:
            raise NotAuthorized("token belongs to another session")
        if intent.principal != ctx.principal:
            raise NotAuthorized("token belongs to another principal")

        fp = fingerprint(req)
        if (
            intent.project_id != req.project_id
            or intent.target_id != req.occurrence_id
            or intent.candidate_id != req.candidate_id
            or intent.action != req.action
            or intent.expected_revision != req.expected_revision
            or intent.idempotency_key != req.idempotency_key
            or intent.fingerprint != fp
        ):
            raise NotAuthorized("request does not match confirmed intent")

        if intent.status == "pending" and self.clock() > intent.expires_at:
            raise NotAuthorized("confirmation token expired")

        grant = api.ReviewGrant(
            token=confirmation_token,
            web_session_id=ctx.web_session_id,
            project_id=req.project_id,
            occurrence_id=req.occurrence_id,
            candidate_id=req.candidate_id,
            action=req.action,
            expected_revision=req.expected_revision,
            expires_at=intent.expires_at,
            fingerprint=fp,
        )
        authed_ctx = dataclasses.replace(ctx, review_grant=grant)
        out = self.review_decision(authed_ctx, req)
        self._intent_registry.mark_completed(confirmation_token)
        return out

    def _file_review_target(self, occ: Occurrence, req: api.ReviewFileDecisionIn):
        if req.file_kind == "citation_file":
            file = self.store.citation_files.get(req.project_id, req.work_file_id)
            if (
                not file.is_current(occ)
                or file.state != RetrievalState.NEEDS_REVIEW
                or file.verification_evidence.get("readable") is not True
            ):
                raise InvalidInput("Citation file is not a current readable PDF pending review")
            return file
        if occ.identity not in ACCEPTED_IDENTITIES or not occ.work_id:
            raise InvalidInput("occurrence has no accepted work")
        wf = self.store.work_files.get(req.project_id, req.work_file_id)
        if wf.work_id != occ.work_id or wf.state != RetrievalState.NEEDS_REVIEW:
            raise InvalidInput(
                f"work file {req.work_file_id} does not belong to occurrence "
                f"{req.occurrence_id} accepted work or is not in needs_review state"
            )
        return wf

    def prepare_web_file_review(self, ctx: CallContext, req: api.ReviewFileDecisionIn) -> api.WebIntentOut:
        if ctx.channel != Channel.WEB_UI or not ctx.web_session_id:
            raise NotAuthorized("web file review intent requires Channel.WEB_UI and an active web session")
        self._need(ctx, Scope.REFERENCES_WRITE)

        project = self.store.projects.get(req.project_id)
        occ = self.store.occurrences.get(req.project_id, req.occurrence_id)
        if occ.revision != req.expected_revision:
            raise RevisionConflict(
                "occurrence revision has changed",
                details={"current_revisions": {req.occurrence_id: occ.revision}},
            )
        if occ.extraction == ExtractionState.EXCLUDED:
            raise InvalidState(f"{req.occurrence_id} is excluded", recovery_hint="Restore it first.")
        wf = self._file_review_target(occ, req)
        work = self.store.works.get(req.project_id, occ.work_id) if req.file_kind == "work_file" else None
        host = urlparse(wf.source_url or wf.final_url or "").netloc
        work_title = (
            work.metadata.title
            if work and work.metadata
            else f"DOI {wf.target_doi}"
            if req.file_kind == "citation_file"
            else ""
        )

        summary = {
            "project_id": req.project_id,
            "occurrence_id": req.occurrence_id,
            "work_file_id": req.work_file_id,
            "file_kind": req.file_kind,
            "action": req.action,
            "expected_revision": req.expected_revision,
            "file": {
                "size": wf.bytes,
                "host": host,
                "source_url": wf.source_url,
                "final_url": wf.final_url,
                "failed_reasons": list(wf.reasons),
            },
            "work_title": work_title,
            "note": req.note,
        }
        fp = fingerprint(req)
        now = self.clock()
        record = self._intent_registry.register(
            purpose="file_review",
            principal=ctx.principal,
            web_session_id=ctx.web_session_id,
            project_id=req.project_id,
            target_id=req.occurrence_id,
            candidate_id=req.work_file_id,
            action=req.action,
            expected_revision=req.expected_revision,
            idempotency_key=req.idempotency_key,
            fingerprint=fp,
            summary=summary,
            now=now,
        )
        out = api.WebIntentOut(
            confirmation_token=record.token,
            expires_at=record.expires_at,
            purpose="file_review",
            summary=summary,
        )
        return self._meta(ctx, out, project.revision)

    def confirm_web_file_review(
        self, ctx: CallContext, req: api.ReviewFileDecisionIn, confirmation_token: str
    ) -> api.ReviewFileDecisionOut:
        if ctx.channel != Channel.WEB_UI or not ctx.web_session_id:
            raise NotAuthorized(
                "web file review confirmation requires Channel.WEB_UI and an active web session"
            )
        self._need(ctx, Scope.REFERENCES_WRITE)

        if not confirmation_token or not confirmation_token.strip():
            raise NotAuthorized("confirmation token required")

        intent = self._intent_registry.get(confirmation_token)
        if intent is None:
            raise NotAuthorized("invalid or unknown confirmation token")

        if intent.purpose != "file_review":
            raise NotAuthorized("token purpose mismatch")
        if intent.web_session_id != ctx.web_session_id:
            raise NotAuthorized("token belongs to another session")
        if intent.principal != ctx.principal:
            raise NotAuthorized("token belongs to another principal")

        fp = fingerprint(req)
        if (
            intent.project_id != req.project_id
            or intent.target_id != req.occurrence_id
            or intent.candidate_id != req.work_file_id
            or intent.action != req.action
            or intent.expected_revision != req.expected_revision
            or intent.idempotency_key != req.idempotency_key
            or intent.fingerprint != fp
        ):
            raise NotAuthorized("request does not match confirmed intent")

        if intent.status == "pending" and self.clock() > intent.expires_at:
            raise NotAuthorized("confirmation token expired")

        def fn(tx: Tx, _: Any) -> api.ReviewFileDecisionOut:
            occ = self.store.occurrences.get(req.project_id, req.occurrence_id)
            if occ.revision != req.expected_revision:
                raise RevisionConflict(
                    "occurrence revision has changed",
                    details={"current_revisions": {req.occurrence_id: occ.revision}},
                )
            wf = self._file_review_target(occ, req)
            if req.file_kind == "citation_file":
                state = RetrievalState.VERIFIED if req.action == "approve" else RetrievalState.FAILED
                reasons = list(wf.reasons)
                if req.action == "reject" and ReasonCode.FILE_REJECTED_BY_USER not in reasons:
                    reasons.append(ReasonCode.FILE_REJECTED_BY_USER.value)
                self.store.citation_files.update(wf.model_copy(update={"state": state, "reasons": reasons}))
                if occ.approval:
                    occ.approval.occurrence_revision = occ.revision + 1
                new_occ = self.store.occurrences.update(occ, expected_revision=req.expected_revision)
                tx.audit(
                    AuditEventType.FILE_APPROVED if req.action == "approve" else AuditEventType.FILE_REJECTED,
                    occ.occurrence_id,
                    surface=ApprovalSurface.WEB_UI,
                    principal=ctx.principal,
                    sha256=wf.sha256,
                    work_file_id=wf.citation_file_id,
                    file_kind="citation_file",
                    target_doi=wf.target_doi,
                    overridden_reasons=list(wf.reasons),
                    note=req.note,
                )
                return api.ReviewFileDecisionOut(
                    occurrence_id=occ.occurrence_id,
                    work_file_id=wf.citation_file_id,
                    retrieval=state,
                    revision=new_occ.revision,
                )

            if req.action == "approve":
                updated_wf = wf.model_copy(update={"state": RetrievalState.VERIFIED})
                if hasattr(self.store.work_files, "update"):
                    self.store.work_files.update(updated_wf)
                elif hasattr(self.store.work_files, "_write"):
                    self.store.work_files._write(updated_wf)
                else:
                    self.store.work_files.add(updated_wf)

                occ.retrieval = RetrievalState.VERIFIED
                if occ.approval:
                    occ.approval.occurrence_revision = occ.revision + 1
                new_occ = self.store.occurrences.update(occ, expected_revision=req.expected_revision)

                tx.audit(
                    AuditEventType.FILE_APPROVED,
                    occ.occurrence_id,
                    surface=ApprovalSurface.WEB_UI,
                    principal=ctx.principal,
                    sha256=wf.sha256,
                    work_file_id=wf.work_file_id,
                    overridden_reasons=list(wf.reasons),
                    reasons=list(wf.reasons),
                )
                return api.ReviewFileDecisionOut(
                    occurrence_id=occ.occurrence_id,
                    work_file_id=wf.work_file_id,
                    retrieval=new_occ.retrieval,
                    revision=new_occ.revision,
                )
            else:
                occ.retrieval = RetrievalState.FAILED
                if ReasonCode.FILE_REJECTED_BY_USER not in occ.identity_reasons:
                    occ.identity_reasons.append(ReasonCode.FILE_REJECTED_BY_USER)
                if occ.approval:
                    occ.approval.occurrence_revision = occ.revision + 1
                new_occ = self.store.occurrences.update(occ, expected_revision=req.expected_revision)

                tx.audit(
                    AuditEventType.FILE_REJECTED,
                    occ.occurrence_id,
                    surface=ApprovalSurface.WEB_UI,
                    principal=ctx.principal,
                    sha256=wf.sha256,
                    work_file_id=wf.work_file_id,
                    reason=ReasonCode.FILE_REJECTED_BY_USER,
                )
                return api.ReviewFileDecisionOut(
                    occurrence_id=occ.occurrence_id,
                    work_file_id=wf.work_file_id,
                    retrieval=new_occ.retrieval,
                    revision=new_occ.revision,
                )

        out = self._mutate(ctx, "confirm_web_file_review", req.project_id, req, api.ReviewFileDecisionOut, fn)
        self._intent_registry.mark_completed(confirmation_token)
        return out

    def get_settings(self, ctx: CallContext, req: api.GetSettingsIn) -> api.GetSettingsOut:
        if ctx.channel != Channel.WEB_UI or not ctx.web_session_id:
            raise NotAuthorized("get_settings requires Channel.WEB_UI and an active web session")
        self._need(ctx, Scope.PROJECTS_READ)

        home = self.settings.rc_home.resolve()
        with config_lock(home):
            fresh_settings = load_settings(home)
            try:
                keys_presence = secrets_present()
                keys_fp = keys_fingerprints(home)
                warnings: list[str] = []
            except Exception:
                keys_presence = {name: False for name in ("openalex_key", "s2_key")}
                keys_fp = {name: None for name in ("openalex_key", "s2_key")}
                warnings = ["keyring unavailable"]

            for folder in fresh_settings.allow_folders:
                if not folder.is_dir():
                    warnings.append(f"allowed folder does not exist: {folder}")

            project = self.store.projects.get(req.project_id) if req.project_id else None
            revision = compute_settings_revision(fresh_settings, keys_fp, project)
            out = build_get_settings_out(
                fresh_settings,
                revision=revision,
                keys_presence=keys_presence,
                project=project,
                warnings=warnings,
            )
            return self._meta(ctx, out, project.revision if project else None)

    def prepare_web_settings(self, ctx: CallContext, req: api.UpdateWebSettingsIn) -> api.WebIntentOut:
        if ctx.channel != Channel.WEB_UI or not ctx.web_session_id:
            raise NotAuthorized("prepare_web_settings requires Channel.WEB_UI and an active web session")
        self._need(ctx, Scope.PROJECTS_WRITE)

        home = self.settings.rc_home.resolve()
        with config_lock(home):
            fresh = load_settings(home)
            try:
                keys_presence = secrets_present()
                keys_fp = keys_fingerprints(home)
            except Exception:
                keys_presence = {name: False for name in ("openalex_key", "s2_key")}
                keys_fp = {name: None for name in ("openalex_key", "s2_key")}

            c = req.change
            project = None
            summary: dict[str, Any] = {}
            target_id = None
            if isinstance(c, api.SetProjectNetworkChange):
                project = self.store.projects.get(c.project_id)
                target_id = c.project_id
                summary = {
                    "change": "set_project_network",
                    "project_id": c.project_id,
                    "before": project.network_mode.value,
                    "after": c.mode.value,
                }
            elif isinstance(c, api.SetProjectDownloadsChange):
                project = self.store.projects.get(c.project_id)
                target_id = c.project_id
                summary = {
                    "change": "set_project_downloads",
                    "project_id": c.project_id,
                    "before": project.download_enabled,
                    "after": c.enabled,
                }
            elif isinstance(c, api.SetEmailChange):
                if not c.value or not c.value.strip():
                    raise InvalidInput("email cannot be empty")
                summary = {
                    "change": "set_email",
                    "before": fresh.email,
                    "after": c.value.strip(),
                }
            elif isinstance(c, api.AddAllowedFolderChange):
                p = Path(c.path).expanduser().resolve()
                summary = {
                    "change": "add_allowed_folder",
                    "folder": str(p),
                }
            elif isinstance(c, api.SetPdfFallbackChange):
                normalized = normalize_pdf_fallback(c.value)
                summary = {
                    "change": "set_pdf_fallback",
                    "before": fresh.pdf_fallback_url,
                    "after": normalized,
                }
            elif isinstance(c, api.SetProviderKeyChange):
                if c.provider not in ("openalex_key", "s2_key"):
                    raise InvalidInput(f"unknown provider key {c.provider}")
                try:
                    get_secret(c.provider)
                except Exception:
                    raise IntegrationUnavailable("OS keyring is unavailable") from None
                summary = {
                    "change": "set_provider_key",
                    "provider": c.provider,
                    "before_configured": keys_presence.get(c.provider, False),
                    "after_configured": True,
                }
            else:
                raise InvalidInput(f"unsupported change type: {getattr(c, 'type', None)}")

            curr_rev = compute_settings_revision(fresh, keys_fp, project)
            if curr_rev != req.expected_settings_revision:
                raise RevisionConflict(
                    "settings revision conflict: settings have changed",
                    details={"expected": req.expected_settings_revision, "current": curr_rev},
                )

            fp = self._fingerprint_web_settings(home, req)
            now = self.clock()
            record = self._intent_registry.register(
                purpose="settings",
                principal=ctx.principal,
                web_session_id=ctx.web_session_id,
                project_id=project.project_id if project else None,
                target_id=target_id,
                candidate_id=None,
                action=c.type,
                expected_revision=req.expected_settings_revision,
                idempotency_key=req.idempotency_key,
                fingerprint=fp,
                summary=summary,
                now=now,
            )
            out = api.WebIntentOut(
                confirmation_token=record.token,
                expires_at=record.expires_at,
                purpose="settings",
                summary=summary,
            )
            return self._meta(ctx, out, project.revision if project else None)

    def confirm_web_settings(
        self, ctx: CallContext, req: api.UpdateWebSettingsIn, confirmation_token: str
    ) -> api.GetSettingsOut:
        if ctx.channel != Channel.WEB_UI or not ctx.web_session_id:
            raise NotAuthorized("confirm_web_settings requires Channel.WEB_UI and an active web session")
        self._need(ctx, Scope.PROJECTS_WRITE)

        if not confirmation_token or not confirmation_token.strip():
            raise NotAuthorized("confirmation token required")

        intent = self._intent_registry.get(confirmation_token)
        if intent is None:
            raise NotAuthorized("invalid or unknown confirmation token")

        if intent.purpose != "settings":
            raise NotAuthorized("token purpose mismatch")
        if intent.web_session_id != ctx.web_session_id:
            raise NotAuthorized("token belongs to another session")
        if intent.principal != ctx.principal:
            raise NotAuthorized("token belongs to another principal")

        home = self.settings.rc_home.resolve()
        fp = self._fingerprint_web_settings(home, req)
        if (
            intent.action != req.change.type
            or intent.expected_revision != req.expected_settings_revision
            or intent.idempotency_key != req.idempotency_key
            or intent.fingerprint != fp
        ):
            raise NotAuthorized("request does not match confirmed intent")

        if intent.status == "pending" and self.clock() > intent.expires_at:
            raise NotAuthorized("confirmation token expired")

        grant = api.SettingsGrant(
            token=confirmation_token,
            web_session_id=ctx.web_session_id,
            expected_settings_revision=req.expected_settings_revision,
            expires_at=intent.expires_at,
            fingerprint=fp,
        )
        authed_ctx = dataclasses.replace(ctx, settings_grant=grant)
        out = self.update_web_settings(authed_ctx, req)
        self._intent_registry.mark_completed(confirmation_token)
        return out

    def update_web_settings(self, ctx: CallContext, req: api.UpdateWebSettingsIn) -> api.GetSettingsOut:
        if ctx.channel != Channel.WEB_UI or not ctx.web_session_id:
            raise NotAuthorized("update_web_settings requires Channel.WEB_UI and an active web session")
        if ctx.settings_grant is None:
            raise NotAuthorized("settings write requires service-validated settings grant")
        self._need(ctx, Scope.PROJECTS_WRITE)

        home = self.settings.rc_home.resolve()
        fp = self._fingerprint_web_settings(home, req)
        grant = ctx.settings_grant
        intent = self._intent_registry.get(grant.token)
        if intent is None or intent.purpose != "settings":
            raise NotAuthorized("invalid or forged settings grant")
        if intent.status == "pending" and self.clock() > intent.expires_at:
            raise NotAuthorized("settings grant expired")
        if (
            intent.web_session_id != grant.web_session_id
            or intent.principal != ctx.principal
            or grant.expected_settings_revision != req.expected_settings_revision
            or grant.fingerprint != fp
        ):
            raise NotAuthorized("settings grant does not match request")

        c = req.change
        # Branch 1: Project-scoped settings
        if isinstance(c, (api.SetProjectNetworkChange, api.SetProjectDownloadsChange)):
            pid = c.project_id
            with config_lock(home):
                now = self.clock()
                rec = self.store.idempotency.get(
                    ctx.principal, pid, "update_web_settings", req.idempotency_key, now=now
                )
                if rec is not None:
                    if rec.fingerprint != fp:
                        raise IdempotencyConflict(
                            f"key {req.idempotency_key} was used with a different request"
                        )
                    return api.GetSettingsOut.model_validate(rec.result)

                with self.store.transaction(immediate=True):
                    now = self.clock()
                    rec = self.store.idempotency.get(
                        ctx.principal, pid, "update_web_settings", req.idempotency_key, now=now
                    )
                    if rec is not None:
                        if rec.fingerprint != fp:
                            raise IdempotencyConflict(
                                f"key {req.idempotency_key} was used with a different request"
                            )
                        return api.GetSettingsOut.model_validate(rec.result)

                    fresh = load_settings(home)
                    try:
                        keys_presence = secrets_present()
                        keys_fp = keys_fingerprints(home)
                    except Exception:
                        keys_presence = {name: False for name in ("openalex_key", "s2_key")}
                        keys_fp = {name: None for name in ("openalex_key", "s2_key")}

                    p = self.store.projects.get(pid)
                    curr_rev = compute_settings_revision(fresh, keys_fp, p)
                    if curr_rev != req.expected_settings_revision:
                        raise RevisionConflict(
                            "settings revision conflict",
                            details={"expected": req.expected_settings_revision, "current": curr_rev},
                        )

                    diff: dict[str, Any] = {}
                    if isinstance(c, api.SetProjectNetworkChange):
                        if p.network_mode != c.mode:
                            diff["network_mode"] = [p.network_mode.value, c.mode.value]
                            p = self.store.projects.update(p.model_copy(update={"network_mode": c.mode}))
                    elif isinstance(c, api.SetProjectDownloadsChange):
                        if p.download_enabled != c.enabled:
                            diff["download_enabled"] = [str(p.download_enabled), str(c.enabled)]
                            p = self.store.projects.update(
                                p.model_copy(update={"download_enabled": c.enabled})
                            )

                    if diff:
                        new_proj_rev = self.store.projects.bump_revision(pid)
                        p = self.store.projects.get(pid)
                        now = self.clock()
                        tx = Tx(self, ctx, p, new_proj_rev, now)
                        tx.audit(AuditEventType.PROJECT_SETTINGS_CHANGED, p.project_id, changed=diff)

                    new_settings_rev = compute_settings_revision(fresh, keys_fp, p)
                    out = build_get_settings_out(
                        fresh, revision=new_settings_rev, keys_presence=keys_presence, project=p
                    )
                    self._meta(ctx, out, p.revision)
                    now = self.clock()
                    self.store.idempotency.put(
                        IdempotencyRecord(
                            principal=ctx.principal,
                            project_id=pid,
                            tool="update_web_settings",
                            key=req.idempotency_key,
                            fingerprint=fp,
                            result=out.model_dump(mode="json"),
                            created_at=now,
                            expires_at=expiry(now, self.settings.limits.idempotency_ttl_s),
                        )
                    )
                    return out

        # Branch 2: Global settings (cross-store recovery)
        with config_lock(home):
            now = self.clock()
            rec = self.store.idempotency.get(
                ctx.principal, "", "update_web_settings", req.idempotency_key, now=now
            )
            if rec is not None:
                if rec.fingerprint != fp:
                    raise IdempotencyConflict(f"key {req.idempotency_key} was used with a different request")
                res = rec.result
                if res.get("status") == "completed":
                    return api.GetSettingsOut.model_validate(res["response"])

            fresh = load_settings(home)
            keyring_error = None
            try:
                keys_presence = secrets_present()
                keys_fp = keys_fingerprints(home)
            except Exception as exc:
                keys_presence = {name: False for name in ("openalex_key", "s2_key")}
                keys_fp = {name: None for name in ("openalex_key", "s2_key")}
                keyring_error = exc

            curr_rev = compute_settings_revision(fresh, keys_fp)

            if rec is not None:
                res = rec.result
                if res.get("status") == "pending":
                    if keyring_error is not None:
                        raise IntegrationUnavailable("OS keyring is unavailable") from None
                    if curr_rev == res["target_revision"]:
                        now = self.clock()
                        completed_payload = {**res, "status": "completed"}
                        self.store.idempotency.put(
                            IdempotencyRecord(
                                principal=ctx.principal,
                                project_id="",
                                tool="update_web_settings",
                                key=req.idempotency_key,
                                fingerprint=fp,
                                result=completed_payload,
                                created_at=now,
                                expires_at=expiry(now, self.settings.limits.idempotency_ttl_s),
                            )
                        )
                        self.settings = fresh
                        if self._config_path.exists():
                            self._config_mtime_ns = self._config_path.stat().st_mtime_ns
                        return api.GetSettingsOut.model_validate(res["response"])
                    elif curr_rev == res["before_revision"]:
                        pass
                    else:
                        raise RevisionConflict(
                            "settings state changed during recovery",
                            details={"current": curr_rev, "expected": res["before_revision"]},
                        )

            if curr_rev != req.expected_settings_revision:
                raise RevisionConflict(
                    "settings revision conflict",
                    details={"expected": req.expected_settings_revision, "current": curr_rev},
                )

            target_settings = fresh.model_copy(deep=True)
            target_keys_fp = dict(keys_fp)
            target_keys_presence = dict(keys_presence)

            if isinstance(c, api.SetEmailChange):
                if not c.value or not c.value.strip():
                    raise InvalidInput("email cannot be empty")
                target_settings.email = c.value.strip()
            elif isinstance(c, api.AddAllowedFolderChange):
                folder = Path(c.path).expanduser().resolve()
                if folder not in target_settings.allow_folders:
                    target_settings.allow_folders.append(folder)
            elif isinstance(c, api.SetPdfFallbackChange):
                normalized = normalize_pdf_fallback(c.value)
                target_settings.pdf_fallback_url = normalized
            elif isinstance(c, api.SetProviderKeyChange):
                try:
                    target_keys_presence[c.provider] = True
                    target_keys_fp[c.provider] = keyed_secret_digest(home, c.value.get_secret_value())
                except Exception:
                    raise IntegrationUnavailable("OS keyring is unavailable") from None

            target_rev = compute_settings_revision(target_settings, target_keys_fp)
            safe_out = build_get_settings_out(
                target_settings, revision=target_rev, keys_presence=target_keys_presence
            )
            self._meta(ctx, safe_out)

            now = self.clock()
            pending_payload = {
                "status": "pending",
                "before_revision": curr_rev,
                "target_revision": target_rev,
                "response": safe_out.model_dump(mode="json"),
            }
            self.store.idempotency.put(
                IdempotencyRecord(
                    principal=ctx.principal,
                    project_id="",
                    tool="update_web_settings",
                    key=req.idempotency_key,
                    fingerprint=fp,
                    result=pending_payload,
                    created_at=now,
                    expires_at=expiry(now, self.settings.limits.idempotency_ttl_s),
                )
            )

            if isinstance(c, api.SetProviderKeyChange):
                try:
                    set_secret(c.provider, c.value.get_secret_value())
                except Exception:
                    raise IntegrationUnavailable("OS keyring is unavailable") from None
            else:
                save_settings(target_settings)

            self.settings = load_settings(home)
            if self._config_path.exists():
                self._config_mtime_ns = self._config_path.stat().st_mtime_ns

            completed_payload = {
                "status": "completed",
                "before_revision": curr_rev,
                "target_revision": target_rev,
                "response": safe_out.model_dump(mode="json"),
            }
            self.store.idempotency.put(
                IdempotencyRecord(
                    principal=ctx.principal,
                    project_id="",
                    tool="update_web_settings",
                    key=req.idempotency_key,
                    fingerprint=fp,
                    result=completed_payload,
                    created_at=now,
                    expires_at=expiry(now, self.settings.limits.idempotency_ttl_s),
                )
            )

            return safe_out
