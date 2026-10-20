"""Frozen export views and atomic artifact descriptors."""

import hashlib

from refcollector.contracts.ports import ExportRow, RenderedFile, Sender, StepResult
from refcollector.domain.enums import ACCEPTED_IDENTITIES, AuditEventType, RetrievalState, StepType
from refcollector.domain.models import Artifact
from refcollector.errors import PrerequisiteNotMet, RevisionNotCurrent
from refcollector.files.retrieval import effective_metadata
from refcollector.ids import new_id
from refcollector.outputs import DefaultDraftRenderer, DefaultExporter, selected, unique_export_file_names
from refcollector.pipeline.state import audit, blocking, capture, check_cancel, validate


def export_rows(data):
    return [
        ExportRow(
            o,
            data["works"].get(o.work_id),
            data["decisions"][o.occurrence_id][-1] if data["decisions"][o.occurrence_id] else None,
            data["access"].get(o.work_id),
            data["files"].get(o.work_id, []),
        )
        for o in data["rows"]
    ]


def artifact_result(ctx, data, files, *, export=False):
    def commit(store):
        if export and store.projects.get(ctx.job.project_id).revision != ctx.job.options["project_revision"]:
            raise RevisionNotCurrent("Project changed while rendering export")
        validate(ctx, store, data)
        artifacts = []
        for file in files:
            path = store.files.new_tmp_path()
            try:
                path.write_bytes(file.data)
                blob = store.files.commit(path, file.media_type)
                store.files.grant(ctx.job.project_id, blob.sha256)
            finally:
                path.unlink(missing_ok=True)
            kind = (
                "manifest"
                if file.name == "manifest.json"
                else ("export" if export else "draft_eml" if file.name.endswith(".eml") else "draft_bundle")
            )
            artifact = Artifact(
                artifact_id=new_id("artifact"),
                project_id=ctx.job.project_id,
                kind=kind,
                name=file.name,
                media_type=file.media_type,
                bytes=blob.bytes,
                sha256=blob.sha256,
                project_revision=data["project"].revision,
                job_id=ctx.job.job_id,
            )
            store.artifacts.add(artifact)
            artifacts.append(artifact.artifact_id)
        audit(ctx, store, AuditEventType.ARTIFACT_CREATED, ctx.job.job_id, artifact_ids=artifacts)
        return artifacts[0] if artifacts else None

    return StepResult(commit)


class ExportHandler:
    step_type = StepType.EXPORT

    async def run(self, ctx):
        data = await blocking(capture, ctx)
        if data["project"].revision != ctx.job.options["project_revision"]:
            raise RevisionNotCurrent("Export revision is no longer current")
        check_cancel(ctx)

        def render():
            exporter = DefaultExporter()
            selection = ctx.job.options["selection"]
            rows = export_rows(data)
            files = [
                exporter.render(fmt, rows, selection=selection, project=data["project"])
                for fmt in ctx.job.options["formats"]
            ]
            pdfs, seen = [], set()
            for row in selected(rows, selection):
                if row.work is None:
                    continue
                for wf in row.files:
                    if wf.state != RetrievalState.VERIFIED or wf.sha256 in seen:
                        continue
                    raw = ctx.store.files.path_for(ctx.job.project_id, wf.sha256).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != wf.sha256:
                        raise PrerequisiteNotMet("Verified file hash changed")
                    seen.add(wf.sha256)
                    pdfs.append((effective_metadata(row.work), raw))
            names = unique_export_file_names([meta for meta, _ in pdfs])
            files.extend(
                RenderedFile(name, "application/pdf", raw) for name, (_, raw) in zip(names, pdfs, strict=True)
            )
            files.append(
                exporter.manifest(
                    data["project"],
                    selection=selection,
                    files=files,
                    counts={"occurrences": len(selected(rows, selection))},
                    policy_versions={
                        "identity": ctx.job.snapshot.identity_policy_version,
                        "eligibility": ctx.job.snapshot.eligibility_policy_version,
                    },
                )
            )
            return files

        files = await blocking(render)
        check_cancel(ctx)
        return artifact_result(ctx, data, files, export=True)


class DraftHandler:
    step_type = StepType.DRAFT

    async def run(self, ctx):
        data = await blocking(capture, ctx)
        rows = export_rows(data)
        if any(
            row.occurrence.extraction == "excluded"
            or row.occurrence.identity not in ACCEPTED_IDENTITIES
            or row.work is None
            or row.occurrence.classification not in ("none_found", "restricted", "unknown")
            for row in rows
        ):
            raise PrerequisiteNotMet("Draft needs accepted identity and scoped access classification")
        check_cancel(ctx)
        files = await blocking(
            DefaultDraftRenderer().render,
            rows,
            sender=Sender(**ctx.job.options["sender"]),
            language=ctx.job.options["language"],
            group_by_author=ctx.job.options.get("group_by_author", False),
        )
        check_cancel(ctx)
        return artifact_result(ctx, data, files)
