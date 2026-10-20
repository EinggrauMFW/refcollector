"""Pure export and request-draft renderers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from refcollector.contracts.ports import ExportRow, RenderedFile
from refcollector.domain.enums import ExportFormat, Selection
from refcollector.domain.models import Project
from refcollector.errors import InvalidInput

from .audit_report import render_audit
from .bibtex import render_bibtex
from .csl import render_csl
from .csvout import _safe_cell as _safe_cell
from .csvout import render_csv
from .drafts import DefaultDraftRenderer as DefaultDraftRenderer
from .jsonout import bib_key, render_json, render_unresolved
from .jsonout import metadata as metadata
from .jsonout import selected as selected
from .manifest import export_file_name as export_file_name
from .manifest import render_manifest
from .manifest import unique_export_file_names as unique_export_file_names

_key = bib_key


class DefaultExporter:
    def render(
        self,
        fmt: str,
        rows: Sequence[ExportRow],
        *,
        selection: str,
        project: Project,
    ) -> RenderedFile:
        try:
            fmt = ExportFormat(fmt)
        except ValueError as exc:
            raise InvalidInput(f"Unknown export format: {fmt}") from exc
        included = selected(rows, selection)
        if fmt in {ExportFormat.BIBTEX, ExportFormat.CSL_JSON} and selection != Selection.COMPLETE_AUDIT:
            included = [row for row in included if row.work is not None]
        if fmt == ExportFormat.BIBTEX:
            return RenderedFile("references.bib", "application/x-bibtex", render_bibtex(included))
        if fmt == ExportFormat.CSL_JSON:
            return RenderedFile(
                "references.csl.json", "application/vnd.citationstyles.csl+json", render_csl(included)
            )
        if fmt == ExportFormat.CSV:
            return RenderedFile("references.csv", "text/csv", render_csv(included))
        if fmt == ExportFormat.JSON:
            return RenderedFile("references.json", "application/json", render_json(included))
        if fmt == ExportFormat.UNRESOLVED:
            return RenderedFile("unresolved.txt", "text/plain", render_unresolved(included))
        return RenderedFile(
            "audit_report.md", "text/markdown", render_audit(included, selection=selection, project=project)
        )

    def manifest(
        self,
        project: Project,
        *,
        selection: str,
        files: Sequence[RenderedFile],
        counts: Mapping[str, int],
        policy_versions: Mapping[str, str],
    ) -> RenderedFile:
        return render_manifest(
            project, selection=selection, files=files, counts=counts, policy_versions=policy_versions
        )
