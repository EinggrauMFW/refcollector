"""PDF fallback settings are frozen in each job snapshot."""

import json
from pathlib import Path

from refcollector.config import Settings
from refcollector.contracts.ports import snapshot_hash
from refcollector.domain.enums import LocationBasis
from refcollector.domain.models import ConfigSnapshot, Project
from refcollector.executor.snapshot import build_snapshot


def test_old_snapshot_json_defaults_to_disabled(tmp_path: Path) -> None:
    snapshot = build_snapshot(Settings(rc_home=tmp_path), Project(project_id="p", name="P"))
    data = snapshot.model_dump(mode="json")
    data.pop("pdf_fallback_url", None)
    restored = ConfigSnapshot.model_validate_json(json.dumps(data))
    assert restored.pdf_fallback_url is None


def test_configured_snapshot_preserves_url_and_changes_hash(tmp_path: Path) -> None:
    settings = Settings(rc_home=tmp_path)
    project = Project(project_id="p", name="P")
    disabled = build_snapshot(settings, project)
    settings.pdf_fallback_url = "https://resolver.example/{doi}"
    enabled = build_snapshot(settings, project)
    original_hash = snapshot_hash(enabled)
    settings.pdf_fallback_url = None
    assert enabled.pdf_fallback_url == "https://resolver.example/{doi}"
    assert snapshot_hash(enabled) == original_hash
    assert snapshot_hash(disabled) != original_hash
    assert LocationBasis.CONFIGURED_FALLBACK.value == "configured_fallback"
