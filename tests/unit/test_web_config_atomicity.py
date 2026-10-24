"""Independent failure-injection checks for configuration persistence."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import portalocker
import pytest

from refcollector import config


def test_failed_config_replace_preserves_original_and_removes_staging(settings, monkeypatch):
    original = settings.config_path.read_bytes()
    attempted = []

    def fail_replace(source, destination):
        source, destination = Path(source), Path(destination)
        assert destination == settings.config_path
        assert source.parent == settings.config_path.parent
        assert source != destination
        attempted.append(source)
        raise OSError("injected replace failure")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    changed = settings.model_copy(update={"email": "updated@example.org"})
    with pytest.raises(OSError, match="injected replace failure"):
        config.save_settings(changed)
    assert attempted, "save_settings must publish a staged file through atomic replacement"
    assert settings.config_path.read_bytes() == original
    assert all(not source.exists() for source in attempted)


def test_config_writes_use_distinct_same_directory_staging_files(settings, monkeypatch):
    original_replace = config.os.replace
    staged = []

    def record_replace(source, destination):
        staged.append(Path(source))
        return original_replace(source, destination)

    monkeypatch.setattr(config.os, "replace", record_replace)
    config.save_settings(settings.model_copy(update={"email": "first@example.org"}))
    config.save_settings(settings.model_copy(update={"email": "second@example.org"}))
    assert len(staged) == 2
    assert staged[0] != staged[1]
    assert all(source.parent == settings.config_path.parent for source in staged)
    assert config.load_settings(settings.rc_home).email == "second@example.org"


def test_nested_different_home_cannot_bypass_another_homes_lock(tmp_path):
    home_a, home_b = tmp_path / "home-a", tmp_path / "home-b"
    locked, release = Event(), Event()

    def hold_other_home():
        with config.config_lock(home_b):
            locked.set()
            assert release.wait(5), "test did not release held settings lock"

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_other_home)
        try:
            assert locked.wait(3), "second home lock was not acquired"
            with config.config_lock(home_a):
                with pytest.raises(portalocker.exceptions.LockException):
                    with config.config_lock(home_b, timeout=0):
                        pass
        finally:
            release.set()
        holder.result(timeout=3)
