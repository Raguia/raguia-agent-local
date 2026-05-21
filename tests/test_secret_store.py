from pathlib import Path

from raguia_local_agent import secret_store


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))


def test_save_and_load_token_with_keyring(monkeypatch, tmp_path: Path):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: fake)
    cfg = tmp_path / "config.yaml"

    stored = secret_store.save_token(cfg, "jwt-123")
    assert stored == secret_store.KEYRING_SENTINEL
    assert secret_store.load_token(cfg, stored) == "jwt-123"


def test_default_alias_does_not_leak_token_to_other_configs(
    monkeypatch, tmp_path: Path
):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: fake)

    default_cfg = Path.home() / ".raguia" / "config.yaml"
    custom_cfg = tmp_path / "tenant-b.yaml"

    stored = secret_store.save_token(default_cfg, "tenant-a-token")

    assert stored == secret_store.KEYRING_SENTINEL
    assert secret_store.load_token(default_cfg, stored) == "tenant-a-token"
    assert secret_store.load_token(custom_cfg, stored) == ""


def test_save_token_falls_back_without_keyring(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: None)
    cfg = tmp_path / "config.yaml"

    stored = secret_store.save_token(cfg, "jwt-456")
    assert stored == "jwt-456"
    assert secret_store.load_token(cfg, stored) == "jwt-456"


def test_load_token_accepts_legacy_default_alias(monkeypatch, tmp_path: Path):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: fake)
    cfg = tmp_path / "config.yaml"

    fake.set_password(secret_store.KEYRING_SERVICE, "default", "jwt-legacy")
    assert secret_store.load_token(cfg, secret_store.KEYRING_SENTINEL) == "jwt-legacy"
