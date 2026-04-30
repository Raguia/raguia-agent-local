"""Icone systray cross-platform (pystray + Pillow).

Etats :
  idle     -> cercle vert  (tout va bien)
  syncing  -> cercle bleu  (upload en cours)
  warning  -> cercle orange (session expire bientot, fichiers bloques)
  error    -> cercle rouge  (erreur connexion, session expiree)
  stopped  -> cercle gris   (agent arrete)

Necessite : pystray>=0.19, Pillow>=10
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import threading
import time
from contextlib import suppress
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .sync_agent import SyncAgent

from . import tray_dialogs
from .api_client import http_response_detail, portal_agent_login, validate_api_base
from .config import APP_DATA_DIR
from .doctor import run_doctor
from .logging_utils import export_support_bundle
from .secret_store import save_token

_DEFAULT_PROD_API_BASE = "https://raguia.valentin-fiess.fr"
_DEFAULT_DEV_API_BASE = "http://127.0.0.1:8000"
_DEFAULT_ADMIN_SWITCH_FILENAME = ".raguia-admin.json"

_COLORS = {
    "idle":    "#22c55e",   # vert
    "syncing": "#3b82f6",   # bleu
    "update":  "#a855f7",   # violet (maj dispo)
    "warning": "#f59e0b",   # orange
    "error":   "#ef4444",   # rouge
    "stopped": "#6b7280",   # gris
}


def _safe_run(cmd: list[str]) -> None:
    """Execute une commande sans faire echouer le flux UI."""
    try:
        subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _remove_windows_autostart() -> None:
    """Supprime les deux mécanismes d'auto-start Windows (registre + Startup)."""
    # 1) Clé registre HKCU\...\Run
    try:
        import winreg  # type: ignore

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, "Raguia Agent")
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass

    # 2) Raccourci Startup (compat ancien comportement)
    try:
        startup = Path(
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
        )
        lnk = startup / "Raguia Agent.lnk"
        if lnk.exists():
            lnk.unlink()
    except Exception:
        pass


def _resolve_agent_config_path() -> Path:
    cfg_path = os.environ.get("RAGUIA_AGENT_CONFIG")
    if cfg_path:
        return Path(cfg_path).expanduser()
    return Path.home() / ".raguia" / "config.yaml"


def _sanitize_admin_filename(name: str) -> str:
    """Autorise uniquement un nom de fichier simple (pas de chemin)."""
    raw = (name or "").strip()
    if not raw:
        return ""
    if Path(raw).name != raw:
        return ""
    if any(ch in raw for ch in ("/", "\\", "\x00")):
        return ""
    return raw


def _read_admin_filename_from_file(path: Path) -> str:
    try:
        return _sanitize_admin_filename(path.read_text(encoding="utf-8").strip())
    except Exception:
        return ""


def _admin_filename_namefile_candidates() -> list[Path]:
    """Emplacements possibles du fichier contenant le nom secret."""
    paths = [Path.home() / ".raguia" / ".raguia-admin-name.txt"]
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        paths += [
            exe.parent / "assets" / ".raguia-admin-name.txt",
            exe.parent.parent / "Resources" / "assets" / ".raguia-admin-name.txt",
            exe.parent.parent.parent / "Resources" / "assets" / ".raguia-admin-name.txt",
        ]
    else:
        here = Path(__file__).resolve()
        paths.append(here.parents[1] / "assets" / ".raguia-admin-name.txt")
    return paths


def _resolve_admin_switch_filename() -> str:
    """Nom du JSON admin recherché pour activer le switch caché.

    Priorite:
      1) env RAGUIA_ADMIN_SWITCH_FILENAME
      2) contenu d'un fichier .raguia-admin-name.txt
      3) valeur par defaut (compat)
    """
    from_env = _sanitize_admin_filename(os.environ.get("RAGUIA_ADMIN_SWITCH_FILENAME", ""))
    if from_env:
        return from_env
    for p in _admin_filename_namefile_candidates():
        if p.is_file():
            from_file = _read_admin_filename_from_file(p)
            if from_file:
                return from_file
    return _DEFAULT_ADMIN_SWITCH_FILENAME


def _admin_switch_candidate_paths() -> list[Path]:
    """Fichiers admin autorisant le menu cache de bascule env."""
    admin_filename = _resolve_admin_switch_filename()
    paths = [Path.home() / ".raguia" / admin_filename]
    env_override = os.environ.get("RAGUIA_ADMIN_SWITCH_FILE")
    if env_override:
        paths.insert(0, Path(env_override))

    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        paths += [
            exe.parent / "assets" / admin_filename,
            exe.parent.parent / "Resources" / "assets" / admin_filename,
            exe.parent.parent.parent / "Resources" / "assets" / admin_filename,
        ]
    else:
        here = Path(__file__).resolve()
        paths.append(here.parents[1] / "assets" / admin_filename)
    return paths


def _load_admin_switch_config() -> dict | None:
    """Lit le fichier admin optionnel; retourne None si absent/desactive."""
    for p in _admin_switch_candidate_paths():
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if not bool(data.get("enable_env_switch")):
            continue
        prod_api = str(data.get("prod_api_base") or _DEFAULT_PROD_API_BASE).strip().rstrip("/")
        dev_api = str(data.get("dev_api_base") or _DEFAULT_DEV_API_BASE).strip().rstrip("/")
        pin = str(data.get("pin") or "").strip()
        return {"prod_api_base": prod_api, "dev_api_base": dev_api, "pin": pin}
    return None


class TrayStatus(str, Enum):
    IDLE    = "idle"
    SYNCING = "syncing"
    UPDATE  = "update"
    WARNING = "warning"
    ERROR   = "error"
    STOPPED = "stopped"


def _make_icon(status: str, size: int = 64, phase: int = 0):
    """Genere une icone plus lisible (forme + couleur).

    - idle    : coche
    - syncing : animation d'arc tournant
    - update  : fleche vers le bas
    - warning : point d'exclamation
    - error   : croix
    - stopped : barre horizontale
    """
    from PIL import Image, ImageDraw

    color = _COLORS.get(status, "#6b7280")
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 5
    center = size // 2
    glyph = "#ffffff"

    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
        outline="#ffffff",
        width=2,
    )

    if status == "syncing":
        ring_m = 11
        start = phase % 360
        draw.arc(
            [ring_m, ring_m, size - ring_m, size - ring_m],
            start=start,
            end=start + 120,
            fill="#ffffff",
            width=5,
        )
        draw.ellipse([center - 4, center - 4, center + 4, center + 4], fill="#ffffff")
        return img

    if status == "idle":
        draw.line(
            [(center - 13, center + 1), (center - 4, center + 10), (center + 14, center - 10)],
            fill=glyph,
            width=5,
            joint="curve",
        )
    elif status == "update":
        draw.line([(center, center - 14), (center, center + 6)], fill=glyph, width=5)
        draw.polygon(
            [(center - 9, center + 1), (center + 9, center + 1), (center, center + 13)],
            fill=glyph,
        )
    elif status == "warning":
        draw.line([(center, center - 13), (center, center + 3)], fill=glyph, width=5)
        draw.ellipse([center - 3, center + 8, center + 3, center + 14], fill=glyph)
    elif status == "error":
        draw.line([(center - 11, center - 11), (center + 11, center + 11)], fill=glyph, width=5)
        draw.line([(center - 11, center + 11), (center + 11, center - 11)], fill=glyph, width=5)
    elif status == "stopped":
        draw.rectangle([center - 11, center - 3, center + 11, center + 3], fill=glyph)

    return img


class RaguiaTray:
    """Icone dans la barre des taches.

    IMPORTANT (macOS) : .run() doit etre appele depuis le thread principal.
    Lancer l'agent dans un thread daemon avant d'appeler run().
    """

    def __init__(
        self,
        agent: "SyncAgent",
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        import pystray
        self._agent = agent
        self._on_quit = on_quit
        self._status = TrayStatus.IDLE
        self._message = ""
        self._icons: dict[str, object] = {}
        self._pystray = pystray
        self._tray: pystray.Icon | None = None
        self._sync_anim_stop = threading.Event()
        self._sync_anim_thread: threading.Thread | None = None
        self._sync_anim_phase = 0
        self._signal_watch_stop = threading.Event()
        self._signal_watch_thread: threading.Thread | None = None
        self._show_tray_signal_file = APP_DATA_DIR / "show_tray.signal"
        self._status_lock = threading.Lock()
        self._busy_depth = 0
        self._status_before_busy = TrayStatus.IDLE
        self._message_before_busy = ""

        # Pre-generer les icones statiques
        for name in _COLORS.keys():
            if name == "syncing":
                continue
            self._icons[name] = _make_icon(name)
        self._icons["syncing"] = _make_icon("syncing", phase=0)

        # L'agent pousse son statut via ce callback
        agent.on_status_change = self._on_agent_status

    # ------------------------------------------------------------------
    # Callback depuis sync_agent (thread background -> thread tray)
    # ------------------------------------------------------------------
    def _on_agent_status(self, status: TrayStatus, message: str = "") -> None:
        with self._status_lock:
            if self._busy_depth > 0:
                self._status_before_busy = status
                self._message_before_busy = message
                return
            self._status = status
            self._message = message
        self._refresh()

    def _begin_busy(self, message: str = "") -> None:
        should_refresh = False
        with self._status_lock:
            self._busy_depth += 1
            if self._busy_depth == 1:
                self._status_before_busy = self._status
                self._message_before_busy = self._message
                self._status = TrayStatus.SYNCING
                self._message = message
                should_refresh = True
            elif message and self._status == TrayStatus.SYNCING:
                self._message = message
                should_refresh = True
        if should_refresh:
            self._refresh()

    def _end_busy(self) -> None:
        should_refresh = False
        with self._status_lock:
            if self._busy_depth == 0:
                return
            self._busy_depth -= 1
            if self._busy_depth == 0:
                self._status = self._status_before_busy
                self._message = self._message_before_busy
                should_refresh = True
        if should_refresh:
            self._refresh()

    def _set_busy_message(self, message: str) -> None:
        should_refresh = False
        with self._status_lock:
            if self._busy_depth <= 0 or self._status != TrayStatus.SYNCING:
                return
            self._message = message
            should_refresh = True
        if should_refresh:
            self._refresh()

    def _refresh(self) -> None:
        if self._tray is None:
            return
        if self._status == TrayStatus.SYNCING:
            self._ensure_sync_animator()
        else:
            self._stop_sync_animator()
        try:
            self._tray.icon  = self._icons[self._status.value]
            self._tray.title = self._title()
        except Exception:
            pass

    def _ensure_sync_animator(self) -> None:
        if self._sync_anim_thread and self._sync_anim_thread.is_alive():
            return
        self._sync_anim_stop.clear()

        def _loop() -> None:
            while not self._sync_anim_stop.wait(0.12):
                if self._tray is None or self._status != TrayStatus.SYNCING:
                    continue
                self._sync_anim_phase = (self._sync_anim_phase + 28) % 360
                self._icons["syncing"] = _make_icon("syncing", phase=self._sync_anim_phase)
                try:
                    self._tray.icon = self._icons["syncing"]
                    self._tray.title = self._title()
                except Exception:
                    pass

        self._sync_anim_thread = threading.Thread(target=_loop, daemon=True, name="raguia-tray-sync-anim")
        self._sync_anim_thread.start()

    def _stop_sync_animator(self) -> None:
        self._sync_anim_stop.set()

    def _start_signal_watcher(self) -> None:
        if self._signal_watch_thread and self._signal_watch_thread.is_alive():
            return
        self._signal_watch_stop.clear()

        def _loop() -> None:
            while not self._signal_watch_stop.wait(0.8):
                try:
                    if not self._show_tray_signal_file.exists():
                        continue
                    self._show_tray_signal_file.unlink(missing_ok=True)
                    self._restore_tray_icon()
                except Exception:
                    pass

        self._signal_watch_thread = threading.Thread(
            target=_loop, daemon=True, name="raguia-tray-signal-watch"
        )
        self._signal_watch_thread.start()

    def _stop_signal_watcher(self) -> None:
        self._signal_watch_stop.set()

    def _restore_tray_icon(self) -> None:
        if self._tray is None:
            return
        try:
            # pystray expose "visible" sur la plupart des backends.
            if hasattr(self._tray, "visible"):
                self._tray.visible = True
        except Exception:
            pass
        self._refresh()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------
    def _title(self) -> str:
        labels = {
            TrayStatus.IDLE:    "Raguia — Actif",
            TrayStatus.SYNCING: "Raguia — Synchronisation...",
            TrayStatus.UPDATE:  "Raguia — Mise a jour disponible",
            TrayStatus.WARNING: f"Raguia — Attention : {self._message}",
            TrayStatus.ERROR:   f"Raguia — Erreur : {self._message}",
            TrayStatus.STOPPED: "Raguia — Arrete",
        }
        return labels.get(self._status, "Raguia")

    def _menu(self):
        pystray = self._pystray
        admin_switch_cfg = _load_admin_switch_config()

        def open_folder(icon, item):
            import subprocess, sys
            root = self._agent.root
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(root)])
            elif sys.platform == "win32":
                subprocess.Popen(["explorer", str(root)])
            else:
                subprocess.Popen(["xdg-open", str(root)])

        def sync_now(icon, item):
            threading.Thread(
                target=self._agent.force_sync, daemon=True
            ).start()

        def reset_stuck(icon, item):
            n = self._agent.queue.reset_stuck()
            self._on_agent_status(TrayStatus.IDLE, f"{n} fichier(s) remis en file")

        def quit_agent(icon, item):
            try:
                self._agent.stop()
            except Exception:
                pass
            if self._on_quit:
                try:
                    self._on_quit()
                except Exception:
                    pass
            try:
                icon.stop()
            except Exception:
                pass

        def reconnect_portal(icon, item):
            try:
                import yaml
            except Exception:
                self._on_agent_status(TrayStatus.ERROR, "PyYAML indisponible")
                tray_dialogs.show_message(
                    "Connexion portail impossible",
                    "Le module PyYAML est indisponible.",
                    kind="error",
                )
                return

            def work() -> None:
                self._begin_busy("Connexion portail...")
                try:
                    self._set_busy_message("Saisie des identifiants portail...")
                    creds = tray_dialogs.prompt_portal_login()
                    if creds is None:
                        tray_dialogs.show_message(
                            "Connexion annulee",
                            "Aucune information de connexion n'a ete saisie.",
                            kind="info",
                        )
                        return
                    slug, password = creds

                    self._set_busy_message("Connexion au portail...")
                    old_token = self._agent.cfg.agent_token

                    # Etape 1 : authentification aupres du portail.
                    # Toute erreur ici signifie de mauvais identifiants ou reseau
                    # inaccessible -> connexion abandonnee.
                    try:
                        payload = portal_agent_login(self._agent.cfg.api_base, slug, password)
                        new_token = str(payload.get("agent_access_token") or "").strip()
                        if not new_token:
                            raise ValueError("Le portail n'a pas retourne de session agent.")
                    except Exception as e:
                        detail = http_response_detail(e.response) if hasattr(e, "response") else str(e)  # type: ignore[attr-defined]
                        tray_dialogs.show_message(
                            "Connexion refusee",
                            f"Echec de connexion portail:\n{detail}",
                            kind="error",
                        )
                        return

                    # Etape 2 : session obtenue -> activation en memoire.
                    self._agent.update_agent_token(new_token)

                    # Etape 3 : verification que le portail accepte cette session.
                    # On distingue les erreurs :
                    #   401 -> session invalide cote serveur : on restaure l'ancienne.
                    #   autre (reseau, 500...) -> la session peut quand meme etre valide,
                    #   on la sauvegarde et on previent l'utilisateur.
                    _sync_warning: str | None = None
                    try:
                        self._agent.client.sync_status()
                    except Exception as _se:
                        _is_401 = (
                            hasattr(_se, "response")
                            and getattr(_se.response, "status_code", 0) == 401  # type: ignore[attr-defined]
                        )
                        if _is_401:
                            with suppress(Exception):
                                self._agent.update_agent_token(old_token)
                            _detail = http_response_detail(_se.response) if hasattr(_se, "response") else str(_se)  # type: ignore[attr-defined]
                            tray_dialogs.show_message(
                                "Session refusee",
                                f"Le portail a refuse la connexion (401) :\n{_detail}",
                                kind="error",
                            )
                            return
                        # Erreur non-auth : on garde la session mais on avertit.
                        _sync_warning = str(_se)

                    cfg_file = _resolve_agent_config_path()
                    cfg_file.parent.mkdir(parents=True, exist_ok=True)

                    data = {}
                    if cfg_file.is_file():
                        with open(cfg_file, encoding="utf-8") as f:
                            data = yaml.safe_load(f) or {}
                    # Conserver une config complete meme si le fichier n'existe plus.
                    data.setdefault("api_base", self._agent.cfg.api_base)
                    data.setdefault("watch_parent", self._agent.cfg.watch_parent)
                    data.setdefault("root_folder_name", self._agent.cfg.root_folder_name)
                    data["client_slug"] = slug
                    data["agent_token"] = save_token(cfg_file, new_token)
                    with open(cfg_file, "w", encoding="utf-8") as f:
                        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
                    try:
                        os.chmod(cfg_file, 0o600)
                    except Exception:
                        pass

                    if _sync_warning:
                        tray_dialogs.show_message(
                            "Connexion enregistree",
                            "Session enregistree. La verification du portail a echoue "
                            f"(reseau ?) mais la connexion sera reprise au prochain cycle.\n"
                            f"Erreur: {_sync_warning[:120]}",
                            kind="warning",
                        )
                    else:
                        tray_dialogs.show_message(
                            "Connexion reussie",
                            "Session agent mise a jour et enregistree avec succes.",
                            kind="info",
                        )
                    self._on_agent_status(TrayStatus.IDLE, "Session agent reconnectee")
                finally:
                    self._end_busy()

            threading.Thread(target=work, daemon=True).start()

        def switch_environment(icon, item):
            if not admin_switch_cfg:
                return
            pin_required = (admin_switch_cfg.get("pin") or "").strip()
            if pin_required:
                pin = tray_dialogs.prompt_text(
                    "Raguia — Acces maintenance",
                    "Entrez le code maintenance pour changer d'environnement :",
                    masked=True,
                )
                if pin is None:
                    return
                if pin != pin_required:
                    tray_dialogs.show_message(
                        "Code invalide",
                        "Code maintenance incorrect.",
                        kind="error",
                    )
                    return

            current = (self._agent.cfg.api_base or "").strip().rstrip("/")
            prod_api = str(admin_switch_cfg["prod_api_base"]).strip().rstrip("/")
            dev_api = str(admin_switch_cfg["dev_api_base"]).strip().rstrip("/")
            target = dev_api if current == prod_api else prod_api
            target_label = "DEV local" if target == dev_api else "PROD"

            try:
                target = validate_api_base(target)
            except ValueError as e:
                tray_dialogs.show_message(
                    "Configuration admin invalide",
                    f"URL cible invalide : {e}",
                    kind="error",
                )
                return

            try:
                self._agent.update_api_base(target)
            except Exception as e:
                tray_dialogs.show_message(
                    "Bascule environnement impossible",
                    f"Echec de mise a jour runtime :\n{e}",
                    kind="error",
                )
                return

            try:
                import yaml

                cfg_file = _resolve_agent_config_path()
                cfg_file.parent.mkdir(parents=True, exist_ok=True)
                data = {}
                if cfg_file.is_file():
                    with open(cfg_file, encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                data["api_base"] = target
                with open(cfg_file, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
                try:
                    os.chmod(cfg_file, 0o600)
                except Exception:
                    pass
            except Exception as e:
                tray_dialogs.show_message(
                    "Bascule partielle",
                    f"Runtime OK, mais config non sauvegardee:\n{e}",
                    kind="warning",
                )
                self._on_agent_status(TrayStatus.WARNING, f"Mode {target_label}")
                return

            self._agent.force_sync()
            tray_dialogs.show_message(
                "Environnement change",
                f"Agent bascule vers {target_label}:\n{target}",
                kind="info",
            )
            self._on_agent_status(TrayStatus.WARNING, f"Mode {target_label}")

        def uninstall_agent(icon, item):
            if not tray_dialogs.confirm_uninstall():
                return

            self._begin_busy("Desinstallation en cours...")
            try:
                cfg_path = os.environ.get("RAGUIA_AGENT_CONFIG")
                cfg_file = Path(cfg_path) if cfg_path else (Path.home() / ".raguia" / "config.yaml")
                agent_dirs: list[Path] = []
                if cfg_file.name == "raguia_agent.yaml":
                    agent_dirs.append(cfg_file.parent)
                try:
                    cwd = Path.cwd()
                except Exception:
                    cwd = None
                if cwd and (cwd / "raguia_agent.yaml").is_file():
                    agent_dirs.append(cwd)
                app_data_dir = Path.home() / ".raguia"

                # 1) Desactiver le demarrage automatique selon l'OS
                try:
                    if os.name == "nt":
                        _remove_windows_autostart()
                    elif sys.platform == "darwin":
                        plist = Path.home() / "Library" / "LaunchAgents" / "com.raguia.local.agent.plist"
                        uid = str(os.getuid()) if hasattr(os, "getuid") else ""
                        if uid:
                            if plist.is_file():
                                _safe_run(["launchctl", "bootout", f"gui/{uid}", str(plist)])
                            # fallback possible selon versions macOS / etat de l'agent
                            _safe_run(["launchctl", "bootout", f"gui/{uid}/com.raguia.local.agent"])
                        _safe_run(["launchctl", "remove", "com.raguia.local.agent"])
                        if plist.is_file():
                            _safe_run(["launchctl", "unload", str(plist)])
                            plist.unlink(missing_ok=True)
                    else:
                        user_cfg = Path(
                            os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
                        )
                        unit = user_cfg / "systemd" / "user" / "raguia-agent.service"
                        if shutil.which("systemctl"):
                            _safe_run(["systemctl", "--user", "disable", "--now", "raguia-agent.service"])
                            _safe_run(["systemctl", "--user", "daemon-reload"])
                        if unit.exists():
                            unit.unlink()
                except Exception:
                    pass

                # 2) Programmer la suppression des fichiers apres extinction du process
                to_delete: list[Path] = []
                for d in agent_dirs:
                    if d.name == ".raguia_agent":
                        to_delete.append(d)
                to_delete.append(app_data_dir)
                # dedupe + keep only existing
                norm = []
                seen = set()
                for p in to_delete:
                    try:
                        rp = p.resolve()
                    except Exception:
                        rp = p
                    key = str(rp)
                    if key in seen:
                        continue
                    seen.add(key)
                    if p.exists():
                        norm.append(p)

                if norm:
                    cleanup_script = (
                        "import json, os, shutil, sys, time; "
                        "time.sleep(2); "
                        "paths=json.loads(sys.argv[1]); "
                        "[(shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) "
                        "else (os.remove(p) if os.path.exists(p) else None)) for p in paths]"
                    )
                    kwargs = {
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                    }
                    if os.name == "nt":
                        kwargs["creationflags"] = 0x08000000
                    else:
                        kwargs["start_new_session"] = True
                    subprocess.Popen(
                        [sys.executable, "-c", cleanup_script, json.dumps([str(p) for p in norm])],
                        **kwargs,
                    )

                tray_dialogs.show_message(
                    "Desinstallation",
                    "Desinstallation lancee. L'agent va s'arreter.",
                    kind="info",
                )
                quit_agent(icon, item)
            except Exception as e:
                tray_dialogs.show_message(
                    "Erreur desinstallation",
                    f"La desinstallation a echoue:\n{e}",
                    kind="error",
                )
            finally:
                self._end_busy()

        def run_doctor_ui(icon, item):
            def work() -> None:
                self._begin_busy("Diagnostic en cours...")
                try:
                    self._set_busy_message("Execution des verifications Doctor...")
                    ok, report = run_doctor(self._agent.cfg, self._agent)
                    title = "Diagnostic OK" if ok else "Diagnostic - attention"
                    kind = "info" if ok else "warning"
                    tray_dialogs.show_message(title, report, kind=kind)
                except Exception as e:
                    tray_dialogs.show_message(
                        "Diagnostic indisponible",
                        f"Le diagnostic n'a pas pu etre execute:\n{e}",
                        kind="error",
                    )
                finally:
                    self._end_busy()

            threading.Thread(target=work, daemon=True).start()

        def export_support(icon, item):
            try:
                _, report = run_doctor(self._agent.cfg, self._agent)
                ts = int(time.time())
                out = self._agent.cfg.app_data_dir / f"support_bundle_{ts}.zip"
                export_support_bundle(self._agent.cfg.app_data_dir, out, report)
                tray_dialogs.show_message(
                    "Export support cree",
                    f"Fichier genere: {out}",
                    kind="info",
                )
            except Exception:
                tray_dialogs.show_message(
                    "Export support impossible",
                    "La creation du bundle support a echoue.",
                    kind="error",
                )

        def run_update_ui(icon, item):
            """Vérifie et installe la mise à jour de l'agent.

            Mode binaire gelé (distribution client) : télécharge le nouveau binaire
            depuis le portail, vérifie le SHA256, puis spawne un processus de
            remplacement et quitte l'agent.

            Mode source Python (développement) : git pull + pip install dans le clone.
            """

            def work() -> None:
                from . import __version__

                current_version = __version__.strip()
                current_version_known = bool(current_version and current_version != "0.0.0")
                current_label = current_version if current_version_known else "inconnue"
                self._begin_busy("Recherche de mise a jour...")

                try:
                    # --- Récupérer la dernière version depuis GitHub releases ---
                    try:
                        data = self._agent.updater.latest_github_release()
                    except Exception as e:
                        tray_dialogs.show_message(
                            "Mise à jour — erreur",
                            f"Impossible de recuperer la derniere release GitHub :\n{e}",
                            kind="error",
                        )
                        return

                    latest_version = str(data.get("version") or "").strip()

                    # ----------------------------------------------------------------
                    # Mode binaire gelé (PyInstaller, distribution client)
                    # ----------------------------------------------------------------
                    if getattr(sys, "frozen", False):
                        if not latest_version:
                            tray_dialogs.show_message(
                                "Mise à jour",
                                (
                                    "Impossible de determiner la version distante pour le moment.\n"
                                    f"Version locale : {current_label}"
                                ),
                                kind="warning",
                            )
                            return
                        cmp = self._agent.updater.compare_versions(current_version, latest_version)
                        if current_version_known and cmp == 0:
                            tray_dialogs.show_message(
                                "Mise à jour",
                                f"L'agent est à jour (version {current_version}).",
                                kind="info",
                            )
                            return
                        if current_version_known and cmp == 1:
                            tray_dialogs.show_message(
                                "Mise à jour",
                                (
                                    f"La version locale ({current_label}) est plus recente que "
                                    f"la derniere release GitHub ({latest_version})."
                                ),
                                kind="info",
                            )
                            return

                        self._set_busy_message(
                            f"Mise a jour detectee ({current_label} -> {latest_version})"
                        )
                        tray_dialogs.show_message(
                            "Mise a jour detectee",
                            (
                                "Nouvelle version disponible.\n"
                                f"Version actuelle : {current_label}\n"
                                f"Nouvelle version : {latest_version}"
                            ),
                            kind="info",
                        )
                        if not tray_dialogs.confirm_agent_update(
                            current_version, latest_version
                        ):
                            tray_dialogs.show_message(
                                "Mise a jour annulee",
                                "Aucune modification n'a ete appliquee.",
                                kind="info",
                            )
                            return

                        try:
                            update_info = self._agent.updater.build_update_info_from_release(data)
                        except Exception as e:
                            tray_dialogs.show_message(
                                "Mise à jour — erreur",
                                f"Impossible de preparer le telechargement depuis GitHub :\n{e}",
                                kind="error",
                            )
                            return

                        self._set_busy_message(
                            f"Installation de la mise a jour {latest_version}..."
                        )
                        ok = self._agent.updater.perform_update(update_info)
                        if ok:
                            tray_dialogs.show_message(
                                "Mise à jour",
                                "Téléchargement terminé. L'agent va redémarrer.",
                                kind="info",
                            )
                            # Supprimer le lock PID maintenant, AVANT que le processus
                            # courant se ferme, afin que la nouvelle instance lancee
                            # par le script de remplacement ne la confonde pas avec
                            # une instance encore active et ne quitte pas silencieusement.
                            try:
                                pid_file = APP_DATA_DIR / "agent.pid"
                                pid_file.unlink(missing_ok=True)
                            except Exception:
                                pass
                            quit_agent(icon, item)
                        else:
                            tray_dialogs.show_message(
                                "Mise à jour — erreur",
                                "La mise à jour a échoué. Consultez les logs pour le détail.",
                                kind="error",
                            )
                        return

                    # ----------------------------------------------------------------
                    # Mode source Python (développement) : git pull + pip install
                    # ----------------------------------------------------------------
                    from .local_git_update import run_local_git_update

                    info_parts: list[str] = []
                    if latest_version:
                        info_parts.append(f"Version annoncée par GitHub : {latest_version}")
                    info_parts.append(f"Version du paquet actuel : {current_label}")
                    info_block = "\n".join(info_parts)

                    cmp = self._agent.updater.compare_versions(current_version, latest_version)
                    has_update = bool(latest_version and (cmp == -1 or (cmp is None and latest_version != current_version)))
                    if has_update:
                        self._set_busy_message(
                            f"Mise a jour detectee ({current_label} -> {latest_version})"
                        )
                        tray_dialogs.show_message(
                            "Mise a jour detectee",
                            (
                                "Le serveur annonce une nouvelle version.\n"
                                # Source de verite: latest release GitHub
                                f"Version actuelle : {current_label}\n"
                                f"Nouvelle version : {latest_version}"
                            ),
                            kind="info",
                        )
                    else:
                        self._set_busy_message("Aucune mise a jour plus recente detectee.")
                        if latest_version and cmp == 1:
                            tray_dialogs.show_message(
                                "Mise à jour",
                                (
                                    f"La version locale ({current_label}) est plus recente que "
                                    f"la derniere release GitHub ({latest_version})."
                                ),
                                kind="info",
                            )
                            return
                        if latest_version and cmp == 0:
                            tray_dialogs.show_message(
                                "Mise à jour",
                                f"L'agent est à jour (version {current_label}).",
                                kind="info",
                            )
                            return

                    if not tray_dialogs.confirm_git_pull_update(current_version, info_block):
                        tray_dialogs.show_message(
                            "Mise a jour annulee",
                            "La mise a jour locale a ete annulee.",
                            kind="info",
                        )
                        return

                    self._set_busy_message("Execution de la mise a jour locale...")
                    ok, msg = run_local_git_update()
                    tray_dialogs.show_message(
                        "Mise à jour",
                        msg,
                        kind="info" if ok else "error",
                    )
                finally:
                    self._end_busy()

            threading.Thread(target=work, daemon=True).start()

        pending = self._agent.queue.pending_count()
        stuck   = self._agent.queue.stuck_count()
        last_ts = self._agent.queue.last_sync_at()

        last_str = "Jamais"
        if last_ts:
            dt = time.time() - last_ts
            if dt < 60:
                last_str = "Il y a < 1 min"
            elif dt < 3600:
                last_str = f"Il y a {int(dt/60)} min"
            else:
                last_str = f"Il y a {int(dt/3600)} h"

        items = [
            pystray.MenuItem(self._title(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Ouvrir le dossier RAGUIA", open_folder),
            pystray.MenuItem("Synchroniser maintenant", sync_now),
            pystray.MenuItem("Lancer un diagnostic (Doctor)…", run_doctor_ui),
            pystray.MenuItem("Verifier / installer mise a jour…", run_update_ui),
            pystray.MenuItem("Exporter un bundle support…", export_support),
            pystray.MenuItem("Se connecter / Reconnecter…", reconnect_portal),
            *(
                [pystray.MenuItem("Maintenance (cache) — Basculer PROD/DEV", switch_environment)]
                if admin_switch_cfg
                else []
            ),
            pystray.MenuItem("Desinstaller l'agent…", uninstall_agent),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                (
                    f"{pending} fichier(s) en attente ({stuck} bloque(s))"
                    if stuck
                    else f"{pending} fichier(s) en attente"
                ),
                None,
                enabled=False,
            ),
            pystray.MenuItem(f"Derniere sync : {last_str}", None, enabled=False),
        ]
        if stuck > 0:
            items += [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    f"⚠ {stuck} fichier(s) bloques — Reinitialiser", reset_stuck
                ),
            ]
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", quit_agent),
        ]
        return pystray.Menu(*items)

    # ------------------------------------------------------------------
    # Lancement (bloque dans le thread appelant — main thread sur macOS)
    # ------------------------------------------------------------------
    def run(self) -> None:
        import pystray
        icon = pystray.Icon(
            "raguia",
            self._icons["idle"],
            title="Raguia",
            menu=pystray.Menu(lambda: self._menu()._items),
        )
        self._tray = icon
        self._start_signal_watcher()
        try:
            icon.run()
        finally:
            self._stop_sync_animator()
            self._stop_signal_watcher()
