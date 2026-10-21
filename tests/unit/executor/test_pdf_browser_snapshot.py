"""Browser paths are optional and frozen for each job."""

from pathlib import Path

from refcollector.config import Settings
from refcollector.contracts.ports import snapshot_hash
from refcollector.domain.models import ConfigSnapshot, Project
from refcollector.executor.snapshot import build_snapshot


def test_old_settings_and_snapshots_default_to_disabled(tmp_path: Path) -> None:
    settings = Settings.model_validate({"rc_home": tmp_path})
    assert settings.pdf_browser_python is None
    assert settings.pdf_browser_binary is None
    snapshot = build_snapshot(settings, Project(project_id="p", name="P"))
    data = snapshot.model_dump(mode="json")
    data.pop("pdf_browser_python")
    data.pop("pdf_browser_binary")
    restored = ConfigSnapshot.model_validate(data)
    assert restored.pdf_browser_python is None
    assert restored.pdf_browser_binary is None


def test_paths_change_hash_and_remain_frozen(tmp_path: Path) -> None:
    settings = Settings(rc_home=tmp_path)
    project = Project(project_id="p", name="P")
    disabled = build_snapshot(settings, project)
    settings.pdf_browser_python = "python-one"
    python_only = build_snapshot(settings, project)
    settings.pdf_browser_binary = "chrome-one"
    enabled = build_snapshot(settings, project)
    original_hash = snapshot_hash(enabled)
    settings.pdf_browser_python = "python-two"
    settings.pdf_browser_binary = "chrome-two"
    assert enabled.pdf_browser_python == "python-one"
    assert enabled.pdf_browser_binary == "chrome-one"
    assert snapshot_hash(enabled) == original_hash
    assert snapshot_hash(disabled) != snapshot_hash(python_only)
    assert snapshot_hash(python_only) != original_hash
    restored = ConfigSnapshot.model_validate_json(enabled.model_dump_json())
    assert snapshot_hash(restored) == original_hash
