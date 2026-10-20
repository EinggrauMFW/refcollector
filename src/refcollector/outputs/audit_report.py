"""Markdown audit report helpers."""

from __future__ import annotations

from collections.abc import Sequence

from refcollector.config import ELIGIBILITY_POLICY_VERSION, IDENTITY_POLICY_VERSION
from refcollector.contracts.ports import ExportRow
from refcollector.domain.enums import (
    AccessClassification,
    DiscoveryState,
    ExtractionState,
    IdentityState,
    LocalCopyState,
    RetrievalState,
)
from refcollector.domain.models import Project

IDENTITY_LABELS = {
    IdentityState.ACCEPTED_AUTO: "Strong evidence",
    IdentityState.ACCEPTED_AGENT: "Accepted by agent",
    IdentityState.ACCEPTED_USER: "Accepted with user approval",
    IdentityState.NEEDS_REVIEW: "Needs review",
    IdentityState.UNRESOLVED: "Unresolved",
    IdentityState.UNPROCESSED: "Unprocessed",
    IdentityState.REJECTED: "Rejected",
}
DIMENSIONS = {
    "extraction": ExtractionState,
    "identity": IdentityState,
    "discovery": DiscoveryState,
    "classification": AccessClassification,
    "local_copy": LocalCopyState,
    "retrieval": RetrievalState,
}


def _state(row: ExportRow, dimension: str):
    if row.access is not None and dimension in {"discovery", "classification"}:
        return getattr(row.access, dimension)
    return getattr(row.occurrence, dimension)


def dimension_counts(rows: Sequence[ExportRow]) -> dict[str, dict[str, int]]:
    counts = {dimension: {state.value: 0 for state in states} for dimension, states in DIMENSIONS.items()}
    for row in rows:
        for dimension, values in counts.items():
            state = _state(row, dimension)
            key = state.value if state is not None else "not_classified"
            values[key] = values.get(key, 0) + 1
    return counts


def access_wording(row: ExportRow) -> str:
    check = row.access
    if check is None:
        if row.occurrence.discovery == DiscoveryState.NOT_CHECKED:
            return "Access has not been checked."
        return "Access check details are unavailable."
    if check.classification == AccessClassification.NONE_FOUND:
        sources = ", ".join(check.sources_consulted)
        return (
            f"No eligible copy was found in the configured sources ({sources}) "
            f"at {check.checked_at.isoformat()}."
        )
    return {
        AccessClassification.ELIGIBLE_FOUND: "An eligible copy was found in the configured sources.",
        AccessClassification.RESTRICTED: (
            "Explicit restriction evidence was recorded; no eligible copy was found."
        ),
        AccessClassification.UNKNOWN: "Copy availability is unknown.",
    }[check.classification]


def render_audit(rows: Sequence[ExportRow], *, selection: str, project: Project) -> bytes:
    lines = [
        f"# {project.name}",
        "",
        f"Project ID: {project.project_id}",
        f"Revision: {project.revision}",
        f"Selection: {selection}",
        "",
        "## Policy versions",
        f"- Identity: {IDENTITY_POLICY_VERSION}",
        f"- Eligibility: {ELIGIBILITY_POLICY_VERSION}",
        "",
        f"## Counts (selected occurrences: {len(rows)})",
    ]
    for dimension, values in dimension_counts(rows).items():
        lines.extend(["", f"### {dimension}", "", "| State | Count |", "|---|---:|"])
        lines.extend(f"| {state} | {count} |" for state, count in values.items())
    lines.extend(["", "## Occurrences"])
    for row in rows:
        occurrence = row.occurrence
        lines.extend(
            [
                "",
                f"### {occurrence.occurrence_id} {occurrence.label or ''}".rstrip(),
                f"Source: {occurrence.source_id}; order: {occurrence.order}; revision: {occurrence.revision}",
                f"Identity: {IDENTITY_LABELS[occurrence.identity]} ({occurrence.identity.value})",
                f"Extraction: {occurrence.extraction.value}",
                f"Text: {occurrence.text}",
                f"Reasons: {', '.join(occurrence.identity_reasons) or 'none'}",
            ]
        )
        if occurrence.extraction == ExtractionState.EXCLUDED:
            lines.append(f"Excluded reason: {occurrence.excluded_reason or 'not recorded'}")
        if row.decision:
            provenance = row.decision.provenance
            lines.append(
                f"Decision: {row.decision.action.value}; principal={provenance.principal}; "
                f"channel={provenance.channel.value}; origin={provenance.origin.value}"
            )
            lines.append(f"Decision reasons: {', '.join(row.decision.reasons) or 'none'}")
        else:
            lines.append("Decision origin: not recorded")
        approval = (row.decision.approval if row.decision else None) or occurrence.approval
        if approval:
            lines.append(
                f"Approval: {approval.approval_id}; action={approval.action.value}; "
                f"surface={approval.surface.value}; occurrence revision={approval.occurrence_revision}; "
                f"approved at={approval.approved_at.isoformat()}"
            )
        lines.append(f"Access: {access_wording(row)}")
        discovery = _state(row, "discovery")
        classification = _state(row, "classification")
        lines.append(
            f"Discovery: {discovery.value}; "
            f"classification: {classification.value if classification else 'not_classified'}; "
            f"local copy: {occurrence.local_copy.value}; retrieval: {occurrence.retrieval.value}"
        )
        if row.access:
            lines.extend(
                [
                    f"Sources consulted: {', '.join(row.access.sources_consulted) or 'none'}",
                    f"Checked at: {row.access.checked_at.isoformat()}",
                    f"Eligibility policy: {row.access.eligibility_policy_version}",
                ]
            )
            for failure in row.access.sources_failed:
                lines.append(f"Source failed: {failure.source}: {failure.error}")
    return ("\n".join(lines) + "\n").encode("utf-8")
