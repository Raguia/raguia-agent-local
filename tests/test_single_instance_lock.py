"""Tests pour le lock atomique d'instance unique."""

from __future__ import annotations

import os
from pathlib import Path


from raguia_local_agent import __main__ as main_module


def _patch_app_data(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(main_module, "APP_DATA_DIR", tmp_path)


def test_acquire_lock_succeeds_when_no_existing_lock(monkeypatch, tmp_path: Path):
    _patch_app_data(monkeypatch, tmp_path)
    ok, path = main_module._acquire_single_instance_lock()
    assert ok is True
    assert path is not None
    assert path.is_file()
    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_lock_succeeds_when_stale_pid_in_file(monkeypatch, tmp_path: Path):
    """PID inexistant dans le fichier → on doit pouvoir prendre le lock."""
    _patch_app_data(monkeypatch, tmp_path)
    stale_lock = tmp_path / "agent.pid"
    stale_lock.write_text("99999999", encoding="utf-8")
    ok, path = main_module._acquire_single_instance_lock()
    assert ok is True
    assert path is not None
    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_lock_fails_when_running_process(monkeypatch, tmp_path: Path):
    """PID actif (le notre) → lock refuse pour un autre processus simulé."""
    _patch_app_data(monkeypatch, tmp_path)

    # Simule une autre instance active : on écrit notre PID puis on patche
    # _pid_is_running pour qu'elle considère ce PID comme actif ET différent
    # de getpid (sinon la branche "current_pid" court-circuite la vérif).
    other_pid = os.getpid() + 1
    (tmp_path / "agent.pid").write_text(str(other_pid), encoding="utf-8")
    monkeypatch.setattr(main_module, "_pid_is_running", lambda pid: pid == other_pid)

    ok, path = main_module._acquire_single_instance_lock()
    assert ok is False
    assert path is None


def test_acquire_lock_handles_corrupt_pid_file(monkeypatch, tmp_path: Path):
    """Fichier corrompu → on doit pouvoir reprendre le lock (PID 0 = invalide)."""
    _patch_app_data(monkeypatch, tmp_path)
    (tmp_path / "agent.pid").write_text("not-a-number", encoding="utf-8")
    ok, path = main_module._acquire_single_instance_lock()
    assert ok is True
    assert path is not None
    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_lock_writes_atomically(monkeypatch, tmp_path: Path):
    """Le PID écrit doit être complet et lisible immédiatement (pas de partial write)."""
    _patch_app_data(monkeypatch, tmp_path)
    ok, path = main_module._acquire_single_instance_lock()
    assert ok and path is not None
    content = path.read_text(encoding="utf-8")
    assert content.strip().isdigit()
    assert int(content.strip()) == os.getpid()
