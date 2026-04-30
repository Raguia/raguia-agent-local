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


def _credential_id_candidates(config_path: Path | None) -> list[str]:
    """IDs possibles (compat anciennes versions / chemins variants)."""
    ids: list[str] = []
    default_config = Path.home() / ".raguia" / "config.yaml"
    is_default_config = config_path is None
    if config_path is not None:
        raw = str(config_path)
        expanded = str(config_path.expanduser())
        try:
            resolved_path = config_path.expanduser().resolve()
            resolved = str(resolved_path)
            is_default_config = resolved_path == default_config.resolve()
        except Exception:
            resolved = expanded
            is_default_config = Path(expanded) == default_config.expanduser()
        ids.extend([resolved, expanded, raw])
    # Compat historique uniquement pour la config par defaut. Ne pas exposer
    # un token d'une config custom a une autre config avec le sentinel keyring.
    if is_default_config:
        ids.append(str(default_config.resolve()))
        ids.append("default")

    dedup: list[str] = []
    seen = set()
    for ident in ids:
        key = (ident or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        dedup.append(key)
    return dedup


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
        # Ecrit aussi les alias "legacy" pour survivre aux variations de chemin/config.
        for ident in _credential_id_candidates(config_path):
            keyring.set_password(KEYRING_SERVICE, ident, token)
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
        for ident in _credential_id_candidates(config_path):
            value = keyring.get_password(KEYRING_SERVICE, ident) or ""
            if value.strip():
                return value
        return ""
    except Exception as e:
        log.warning("Impossible de lire le token depuis keyring: %s", e)
        return ""

