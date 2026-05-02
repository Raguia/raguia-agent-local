"""Tests : trousseau OS temporairement indisponible ne doit pas bloquer le démarrage.

Régression : sur Windows, juste après un redémarrage, le service Credential
Locker peut prendre quelques secondes avant d'être disponible. Auparavant
``load_config()`` levait une ValueError empêchant tout retry.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from raguia_local_agent import config, secret_store


def test_load_config_does_not_raise_when_keyring_returns_empty(monkeypatch, tmp_path: Path, caplog):
    """Le YAML référence le sentinel mais le keyring renvoie None : warning, pas raise."""
    class _EmptyKeyring:
        def set_password(self, service, username, password):
            return None

        def get_password(self, service, username):
            return None  # Keyring temporairement indisponible

    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: _EmptyKeyring())

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "api_base": "https://example.com",
                "agent_password": secret_store.KEYRING_SENTINEL,
                "client_slug": "demo",
                "watch_parent": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        cfg = config.load_config(cfg_path)

    assert cfg.agent_password == "", "Token doit être vide pour permettre le démarrage en mode dégradé"
    assert any("Trousseau" in rec.message or "trousseau" in rec.message for rec in caplog.records)


def test_load_config_succeeds_when_keyring_returns_token(monkeypatch, tmp_path: Path):
    """Sanity check : avec un keyring fonctionnel, le token est bien chargé."""
    class _OkKeyring:
        def set_password(self, service, username, password):
            return None

        def get_password(self, service, username):
            return "real-token"

    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: _OkKeyring())

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "api_base": "https://example.com",
                "agent_password": secret_store.KEYRING_SENTINEL,
                "client_slug": "demo",
                "watch_parent": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    cfg = config.load_config(cfg_path)
    assert cfg.agent_password == "real-token"
