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


def _format_portal_check_error(exc: Exception) -> str:
    """Message explicite pour l'essai sync-status (pas de fuite de secret)."""
    try:
        import httpx
    except ImportError:
        return f"{type(exc).__name__}: {exc!s}"[:300]

    if isinstance(exc, httpx.HTTPStatusError):
        r = exc.response
        code = r.status_code
        detail_hint = ""
        try:
            payload = r.json()
            if isinstance(payload, dict):
                raw = payload.get("detail")
                if raw is not None:
                    if isinstance(raw, list) and raw:
                        raw = raw[0].get("msg") if isinstance(raw[0], dict) else raw[0]
                    detail_hint = str(raw).strip()[:220]
        except Exception:
            pass

        by_code = {
            401: "jeton invalide / expire / generation obsolete (regenerez depuis le portail)",
            403: "agent local desactive pour ce client ou droits refuses",
            404: "route ou client introuvable — verifiez api_base (racine backend, pas une page /portal)",
        }
        hint = by_code.get(code, "")
        if detail_hint:
            return f"HTTP {code}: {detail_hint}"
        return f"HTTP {code}" + (f" — {hint}" if hint else "")

    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
        ),
    ):
        return f"{type(exc).__name__}: {exc!s}"[:280]

    if isinstance(exc, ValueError):
        msg = str(exc).strip()
        return msg[:300] if msg else repr(exc)[:300]

    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "closed" in msg and "client" in msg:
            return (
                "Client HTTP interne deja ferme — redemarrez l'agent une fois pour appliquer "
                "(sinon mise a jour applicative)."
            )

    return f"{type(exc).__name__}: {exc!s}"[:300]


def _windows_autostart_enabled() -> bool:
    """Détecte l'auto-start Windows (registre HKCU Run ou Startup .lnk)."""
    try:
        import winreg  # type: ignore

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        try:
            value, _ = winreg.QueryValueEx(key, "Raguia Agent")
            if str(value or "").strip():
                return True
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass

    try:
        startup = Path(
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
        )
        return (startup / "Raguia Agent.lnk").is_file()
    except Exception:
        return False


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

    # API status test — detail reel (HTTP / reseau / detail FastAPI)
    try:
        agent.client.sync_status()
        lines.append(_ok("Connexion portail"))
    except Exception as e:
        has_error = True
        lines.append(_fail("Connexion portail", _format_portal_check_error(e)))

    # Autostart check
    try:
        if os.name == "nt":
            enabled = _windows_autostart_enabled()
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

