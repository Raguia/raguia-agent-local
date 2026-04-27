from pathlib import Path

import pytest
import yaml

from raguia_local_agent import config, secret_store


def test_secure_storage_refuses_plaintext_when_keyring_available(monkeypatch, tmp_path: Path):
    class _FakeKeyring:
        def set_password(self, service, username, password):
            return None

        def get_password(self, service, username):
            return "tok"

    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: _FakeKeyring())
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "api_base": "https://example.com",
                "agent_token": "plain-token",
                "watch_parent": str(tmp_path),
                "secure_token_storage": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        config.load_config(cfg_path)


def test_secure_storage_does_not_block_without_keyring(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: None)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "api_base": "https://example.com",
                "agent_token": "plain-token",
                "watch_parent": str(tmp_path),
                "secure_token_storage": True,
            }
        ),
        encoding="utf-8",
    )
    cfg = config.load_config(cfg_path)
    assert cfg.agent_token == "plain-token"

