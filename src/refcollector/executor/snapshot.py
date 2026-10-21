"""WP-02: config snapshots and output reuse keys. Spec 6.6."""

from __future__ import annotations

from collections.abc import Mapping

from refcollector.config import ELIGIBILITY_POLICY_VERSION, IDENTITY_POLICY_VERSION, Settings
from refcollector.contracts.ports import snapshot_hash
from refcollector.domain.models import ConfigSnapshot, Project, Step

__all__ = ["build_snapshot", "reuse_key", "snapshot_hash"]


def build_snapshot(
    settings: Settings, project: Project, adapter_versions: Mapping[str, str] | None = None
) -> ConfigSnapshot:
    """Freeze the settings a new job runs with. Resumed jobs keep theirs (AT-32)."""
    return ConfigSnapshot(
        policy_profile=project.policy_profile,
        network_mode=project.network_mode,
        provider_allowlist=list(settings.enabled_providers),
        identity_policy_version=IDENTITY_POLICY_VERSION,
        eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
        adapter_versions=dict(adapter_versions or {}),
        version_policy=project.version_policy,
        contact_email=settings.email,
        pdf_fallback_url=settings.pdf_fallback_url,
    )


def reuse_key(step: Step) -> tuple[str, str, str]:
    """(step type, input fingerprint, snapshot hash): equal keys share a succeeded output."""
    return (step.step_type.value, step.input_fingerprint, step.snapshot_hash)
