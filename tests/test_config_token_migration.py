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


def test_load_config_migrates_plaintext_token_to_keyring(monkeypatch, tmp_path: Path):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: fake)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "api_base": "https://example.com",
                "agent_password": "plain-token",
                "watch_parent": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    cfg = config.load_config(cfg_path)
    assert cfg.agent_password == "plain-token"

    rewritten = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert rewritten["agent_password"] == secret_store.KEYRING_SENTINEL
