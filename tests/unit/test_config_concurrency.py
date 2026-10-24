"""Tests for configuration locking, concurrency, and atomic replacement (Wave 3).

Verifies shared sidecar lock (config.lock), unique temporary file usage, absence of shared config.tmp,
re-entrant deadlock avoidance, and concurrent writer consistency.
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any

import pytest

from refcollector.config import Settings, config_lock, load_settings, save_settings


def test_config_lock_survives_config_json_replacement(tmp_path: Path) -> None:
    rc_home = tmp_path / "rc_home"
    rc_home.mkdir()

    s = Settings(rc_home=rc_home, email="initial@example.com")
    save_settings(s)
    assert s.config_path.exists()

    # Lock file is a sidecar file next to config.json
    lock_file = rc_home / "config.lock"
    assert lock_file.exists()

    # Hold the lock while replacing config.json
    with config_lock(rc_home):
        s2 = load_settings(rc_home)
        s2.email = "updated@example.com"
        save_settings(s2)

    # After release, verify content and that lock file still exists
    fresh = load_settings(rc_home)
    assert fresh.email == "updated@example.com"
    assert lock_file.exists()


def test_save_settings_uses_unique_temp_file_never_shared_config_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc_home = tmp_path / "rc_home"
    rc_home.mkdir()

    created_tmps: list[Path] = []
    real_mkstemp = __import__("tempfile").mkstemp

    def track_mkstemp(*args: Any, **kwargs: Any):
        fd, path_str = real_mkstemp(*args, **kwargs)
        created_tmps.append(Path(path_str))
        return fd, path_str

    monkeypatch.setattr("tempfile.mkstemp", track_mkstemp)

    s = Settings(rc_home=rc_home, email="writer@example.com")
    save_settings(s)

    # Must have used mkstemp
    assert len(created_tmps) == 1
    tmp_file = created_tmps[0]

    # Verify temp file was in rc_home, starts with .config., ends with .tmp
    assert tmp_file.parent == rc_home.resolve()
    assert tmp_file.name.startswith(".config.")
    assert tmp_file.name.endswith(".tmp")
    assert tmp_file.name != "config.tmp"  # Never shared config.tmp

    # Shared config.tmp must not exist
    assert not (rc_home / "config.tmp").exists()


def test_config_lock_reentrant_deadlock_avoidance(tmp_path: Path) -> None:
    rc_home = tmp_path / "rc_home"
    rc_home.mkdir()

    s = Settings(rc_home=rc_home, email="outer@example.com")

    # Nested acquisitions within the same thread must not deadlock
    with config_lock(rc_home):
        with config_lock(rc_home):
            with config_lock(rc_home):
                s.email = "inner@example.com"
                save_settings(s)  # save_settings internally also uses config_lock

    assert load_settings(rc_home).email == "inner@example.com"


def test_concurrent_settings_writers(tmp_path: Path) -> None:
    rc_home = tmp_path / "rc_home"
    rc_home.mkdir()

    initial = Settings(rc_home=rc_home, email="start@example.com", allow_folders=[])
    save_settings(initial)

    num_threads = 8
    folders_per_thread = 5

    def worker(worker_id: int) -> None:
        for i in range(folders_per_thread):
            folder = Path(f"/path/from/worker_{worker_id}/folder_{i}")
            with config_lock(rc_home):
                s = load_settings(rc_home)
                if folder not in s.allow_folders:
                    s.allow_folders.append(folder)
                    save_settings(s)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, wid) for wid in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    final = load_settings(rc_home)
    assert len(final.allow_folders) == num_threads * folders_per_thread
