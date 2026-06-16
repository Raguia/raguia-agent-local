"""Tests pour _pid_is_running : critique sur Windows.

Sur Windows, ``os.kill(pid, 0)`` envoie en réalité un signal CTRL_C/SIGTERM
qui peut tuer un processus innocent dont le PID a été recyclé.
``_pid_is_running`` doit utiliser OpenProcess via ctypes sur Windows.
"""

from __future__ import annotations

import os
import sys

from raguia_local_agent.__main__ import _pid_is_running


def test_pid_is_running_returns_true_for_self():
    assert _pid_is_running(os.getpid()) is True


def test_pid_is_running_returns_false_for_zero():
    assert _pid_is_running(0) is False


def test_pid_is_running_returns_false_for_negative():
    assert _pid_is_running(-1) is False


def test_pid_is_running_returns_false_for_clearly_invalid_pid():
    # PID volontairement très grand : pratiquement jamais utilisé.
    # Doit renvoyer False sans planter.
    assert _pid_is_running(99_999_999) is False


def test_pid_is_running_does_not_signal_random_process():
    """Régression Windows : ``os.kill(pid, 0)`` peut envoyer un signal réel.

    Ce test vérifie qu'aucune exception n'est levée et qu'on n'écrase pas
    de processus système existant. Sur Windows, l'implémentation utilise
    OpenProcess (lecture seule) — pas os.kill.
    """
    if sys.platform == "win32":
        # PID 4 = System sur Windows, doit être actif et lisible.
        assert _pid_is_running(4) is True
    else:
        # PID 1 = init/launchd (POSIX), toujours actif.
        assert _pid_is_running(1) is True
