from __future__ import annotations

from pathlib import Path

import yaml

from raguia_local_agent import config, secret_store


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))


def test_env_agent_password_is_loaded(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "api_base": "https://example.com",
                "client_slug": "demo",
                "watch_parent": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RAGUIA_AGENT_PASSWORD", "from-env")

    cfg = config.load_config(cfg_path)

    assert cfg.agent_password == "from-env"


def test_migration_does_not_store_sentinel_as_password(monkeypatch, tmp_path: Path):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: fake)
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

    config.load_config(cfg_path)

    assert all(
        password != secret_store.KEYRING_SENTINEL for password in fake.store.values()
    )


def test_save_agent_token_keeps_agent_password_keyring(monkeypatch, tmp_path: Path):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: fake)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "api_base": "https://example.com",
                "agent_password": "portal-password",
                "client_slug": "demo",
                "watch_parent": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    cfg = config.load_config(cfg_path)
    cfg.save_agent_token("new-jwt")

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert raw["agent_password"] == secret_store.KEYRING_SENTINEL
    assert raw["agent_token"] == "new-jwt"
    assert cfg.agent_password == "portal-password"
