"""Helpers for web settings calculation, validation and views (Wave 3).

Computes deterministic 64-character SHA-256 settings revisions covering global config,
keyring secrets presence/fingerprints, and target project state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from refcollector.config import Settings
from refcollector.contracts import api
from refcollector.domain.models import Project


def get_browser_status(settings: Settings) -> api.BrowserStatus:
    has_paths = bool(settings.pdf_browser_python and settings.pdf_browser_binary)
    if not has_paths:
        return api.BrowserStatus(enabled=False, configured=False)
    py_ok = Path(settings.pdf_browser_python).is_file()
    bin_ok = Path(settings.pdf_browser_binary).is_file()
    return api.BrowserStatus(enabled=bool(py_ok and bin_ok), configured=True)


def get_project_settings_view(project: Project | None) -> api.ProjectSettingsView | None:
    if project is None:
        return None
    return api.ProjectSettingsView(
        project_id=project.project_id,
        network_mode=project.network_mode,
        download_enabled=project.download_enabled,
        version_policy=project.version_policy,
    )


def compute_settings_revision(
    settings: Settings,
    keys_status: dict[str, str | None],
    project: Project | None = None,
) -> str:
    """Canonical deterministic 64-character SHA-256 digest covering global config, keys, and project."""
    payload = {
        "email": settings.email,
        "allow_folders": [str(p) for p in sorted(settings.allow_folders, key=lambda x: str(x))],
        "download_enabled_global": settings.download_enabled_global,
        "pdf_fallback_url": settings.pdf_fallback_url,
        "pdf_browser_python": settings.pdf_browser_python,
        "pdf_browser_binary": settings.pdf_browser_binary,
        "enabled_providers": sorted(settings.enabled_providers),
        "mcp_scopes": sorted(s.value for s in settings.mcp_scopes),
        "limits": settings.limits.model_dump(),
        "keys": keys_status,
        "project": None
        if project is None
        else {
            "project_id": project.project_id,
            "revision": project.revision,
            "network_mode": project.network_mode.value,
            "download_enabled": project.download_enabled,
            "version_policy": project.version_policy.value,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_get_settings_out(
    settings: Settings,
    *,
    revision: str,
    keys_presence: dict[str, bool],
    project: Project | None = None,
    warnings: list[str] | None = None,
) -> api.GetSettingsOut:
    return api.GetSettingsOut(
        revision=revision,
        rc_home=str(settings.rc_home.resolve()),
        email=settings.email,
        allow_folders=[str(p) for p in settings.allow_folders],
        download_enabled_global=settings.download_enabled_global,
        pdf_fallback_url=settings.pdf_fallback_url,
        browser=get_browser_status(settings),
        enabled_providers=list(settings.enabled_providers),
        mcp_scopes=sorted(s.value for s in settings.mcp_scopes),
        limits=settings.limits.model_dump(),
        keys=keys_presence,
        project=get_project_settings_view(project),
        warnings=list(warnings or []),
    )
