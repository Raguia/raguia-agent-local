"""Gestion du stockage de secret (jeton agent) avec keyring OS + fallback fichier."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

KEYRING_SENTINEL = "__RAGUIA_KEYRING__"
KEYRING_SERVICE = "raguia-local-agent"


def _credential_id(config_path: Path | None) -> str:
    if config_path is None:
        return "default"
    try:
        return str(config_path.expanduser().resolve())
    except Exception:
        return str(config_path)


def _get_keyring_module():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def keyring_available() -> bool:
    return _get_keyring_module() is not None


def save_token(config_path: Path | None, token: str) -> str:
    """Sauvegarde le token dans le trousseau OS si possible.

    Retourne la valeur a serialiser dans le YAML:
    - KEYRING_SENTINEL si stockage OS reussi
    - token en clair si keyring indisponible (fallback compat)
    """
    token = (token or "").strip()
    if not token:
        return ""
    keyring = _get_keyring_module()
    if keyring is None:
        return token
    try:
        keyring.set_password(KEYRING_SERVICE, _credential_id(config_path), token)
        return KEYRING_SENTINEL
    except Exception as e:
        log.warning("keyring set_password indisponible, fallback YAML: %s", e)
        return token


def load_token(config_path: Path | None, stored_value: str) -> str:
    """Charge le token depuis keyring si le sentinel est present."""
    value = (stored_value or "").strip()
    if not value:
        return ""
    if value != KEYRING_SENTINEL:
        return value

    keyring = _get_keyring_module()
    if keyring is None:
        log.warning("Token configure en keyring mais module indisponible.")
        return ""
    try:
        return keyring.get_password(KEYRING_SERVICE, _credential_id(config_path)) or ""
    except Exception as e:
        log.warning("Impossible de lire le token depuis keyring: %s", e)
        return ""

