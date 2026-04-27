from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from .secret_store import KEYRING_SENTINEL, _get_keyring_module


def _ok(label: str, detail: str = "") -> str:
    return f"[OK] {label}" + (f" - {detail}" if detail else "")


def _warn(label: str, detail: str = "") -> str:
    return f"[ATTENTION] {label}" + (f" - {detail}" if detail else "")


def _fail(label: str, detail: str = "") -> str:
    return f"[ERREUR] {label}" + (f" - {detail}" if detail else "")


def run_doctor(cfg, agent) -> tuple[bool, str]:
    lines: list[str] = []
    has_error = False

    # Config URL
    parsed = urlparse(cfg.api_base)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}):
        lines.append(_ok("URL portail valide"))
    else:
        has_error = True
        lines.append(_fail("URL portail non securisee", "Utilisez https://"))

    # Token storage
    cfg_path = cfg.cfg_path or (Path.home() / ".raguia" / "config.yaml")
    raw_token = ""
    if cfg_path.is_file():
        try:
            import yaml

            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            raw_token = str(raw.get("agent_token") or "")
        except Exception:
            raw_token = ""
    keyring = _get_keyring_module()
    keyring_available = keyring is not None
    if raw_token == KEYRING_SENTINEL:
        lines.append(_ok("Token stocke dans le trousseau OS"))
    elif keyring_available:
        lines.append(_warn("Token encore en YAML", "Lancez une mise a jour du jeton pour migrer"))
    else:
        lines.append(_warn("Trousseau OS indisponible", "Mode compatibilite actif"))

    # Queue state
    pending = agent.queue.pending_count()
    stuck = agent.queue.stuck_count()
    if stuck > 0:
        lines.append(_warn("Fichiers bloques", f"{stuck} element(s)"))
    else:
        lines.append(_ok("Aucun fichier bloque"))
    lines.append(_ok("File locale", f"{pending} en attente"))

    # API status test (message sans info sensible)
    try:
        agent.client.sync_status()
        lines.append(_ok("Connexion portail"))
    except Exception:
        has_error = True
        lines.append(_fail("Connexion portail", "Impossible de joindre le service ou token invalide"))

    # Autostart check
    try:
        if os.name == "nt":
            startup = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"))
            enabled = (startup / "Raguia Agent.lnk").is_file()
        elif sys.platform == "darwin":
            enabled = (Path.home() / "Library" / "LaunchAgents" / "com.raguia.local.agent.plist").is_file()
        else:
            unit = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd" / "user" / "raguia-agent.service"
            enabled = unit.is_file()
        if enabled:
            lines.append(_ok("Demarrage automatique configure"))
        else:
            lines.append(_warn("Demarrage automatique non detecte"))
    except Exception:
        lines.append(_warn("Etat demarrage automatique inconnu"))

    summary = "Diagnostic Agent Raguia\n\n" + "\n".join(lines)
    try:
        app_data = cfg.app_data_dir
        (app_data / "doctor_latest.txt").write_text(summary, encoding="utf-8")
    except Exception:
        pass
    return (not has_error), summary

