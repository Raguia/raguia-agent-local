"""Icone systray cross-platform (pystray + Pillow).

Etats :
  idle     -> cercle vert  (tout va bien)
  syncing  -> cercle bleu  (upload en cours)
  warning  -> cercle orange (token expire bientot, fichiers bloques)
  error    -> cercle rouge  (erreur connexion, token expire)
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
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .sync_agent import SyncAgent

from . import tray_dialogs
from .doctor import run_doctor
from .logging_utils import export_support_bundle
from .secret_store import save_token

_COLORS = {
    "idle":    "#22c55e",   # vert
    "syncing": "#3b82f6",   # bleu
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


class TrayStatus(str, Enum):
    IDLE    = "idle"
    SYNCING = "syncing"
    WARNING = "warning"
    ERROR   = "error"
    STOPPED = "stopped"


def _make_icon(color: str, size: int = 64):
    """Genere une image PIL avec un cercle colore."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
        outline="#ffffff",
        width=2,
    )
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

        # Pre-generer les icones
        for name, color in _COLORS.items():
            self._icons[name] = _make_icon(color)

        # L'agent pousse son statut via ce callback
        agent.on_status_change = self._on_agent_status

    # ------------------------------------------------------------------
    # Callback depuis sync_agent (thread background -> thread tray)
    # ------------------------------------------------------------------
    def _on_agent_status(self, status: TrayStatus, message: str = "") -> None:
        self._status = status
        self._message = message
        self._refresh()

    def _refresh(self) -> None:
        if self._tray is None:
            return
        try:
            self._tray.icon  = self._icons[self._status.value]
            self._tray.title = self._title()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------
    def _title(self) -> str:
        labels = {
            TrayStatus.IDLE:    "Raguia — Actif",
            TrayStatus.SYNCING: "Raguia — Synchronisation...",
            TrayStatus.WARNING: f"Raguia — Attention : {self._message}",
            TrayStatus.ERROR:   f"Raguia — Erreur : {self._message}",
            TrayStatus.STOPPED: "Raguia — Arrete",
        }
        return labels.get(self._status, "Raguia")

    def _menu(self):
        pystray = self._pystray

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

        def update_jwt(icon, item):
            try:
                import yaml
            except Exception:
                self._on_agent_status(TrayStatus.ERROR, "PyYAML indisponible")
                return

            new_token = tray_dialogs.prompt_agent_token()
            if new_token is None:
                return
            new_token = new_token.strip()
            if not new_token:
                tray_dialogs.show_message(
                    "Jeton vide",
                    "Aucun jeton saisi.",
                    kind="warning",
                )
                return

            old_token = self._agent.cfg.agent_token
            self._agent.update_agent_token(new_token)
            try:
                self._agent.client.sync_status()
            except Exception as e:
                self._agent.update_agent_token(old_token)
                tray_dialogs.show_message(
                    "Jeton invalide",
                    f"Le jeton n'a pas ete accepte:\n{e}",
                    kind="error",
                )
                return

            cfg_path = os.environ.get("RAGUIA_AGENT_CONFIG")
            if cfg_path:
                cfg_file = Path(cfg_path)
            else:
                cfg_file = Path.home() / ".raguia" / "config.yaml"
            cfg_file.parent.mkdir(parents=True, exist_ok=True)

            data = {}
            if cfg_file.is_file():
                with open(cfg_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            data["agent_token"] = save_token(cfg_file, new_token)
            with open(cfg_file, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            try:
                os.chmod(cfg_file, 0o600)
            except Exception:
                pass

            tray_dialogs.show_message(
                "Jeton mis a jour",
                "Le nouveau jeton est actif immediatement et enregistre.",
                kind="info",
            )
            self._on_agent_status(TrayStatus.IDLE, "Jeton mis a jour")

        def uninstall_agent(icon, item):
            if not tray_dialogs.confirm_uninstall():
                return

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
                        startup = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"))
                        lnk = startup / "Raguia Agent.lnk"
                        if lnk.exists():
                            lnk.unlink()
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

        def run_doctor_ui(icon, item):
            try:
                ok, report = run_doctor(self._agent.cfg, self._agent)
                title = "Diagnostic OK" if ok else "Diagnostic - attention"
                kind = "info" if ok else "warning"
                tray_dialogs.show_message(title, report, kind=kind)
            except Exception:
                tray_dialogs.show_message(
                    "Diagnostic indisponible",
                    "Le diagnostic n'a pas pu etre execute.",
                    kind="error",
                )

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
            pystray.MenuItem("Exporter un bundle support…", export_support),
            pystray.MenuItem("Mettre a jour le jeton JWT…", update_jwt),
            pystray.MenuItem("Desinstaller l'agent…", uninstall_agent),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"{pending} fichier(s) en attente", None, enabled=False),
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
        icon.run()
