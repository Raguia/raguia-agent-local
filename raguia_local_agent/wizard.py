"""Assistant de premier lancement (Tkinter, sans dependances externes)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import yaml
from .api_client import validate_api_base
from .secret_store import save_token

log = logging.getLogger(__name__)


def _detect_default_parent() -> str:
    home = Path.home()
    docs = home / "Documents"
    return str(docs) if docs.exists() else str(home)


def _register_autostart() -> None:
    """Enregistre le démarrage automatique au login (mode binaire gelé uniquement).

    Conditionnel sur ``sys.frozen`` : en mode source Python (développement)
    l'agent est lancé manuellement, pas besoin de registre/plist.
    """
    if not getattr(sys, "frozen", False):
        return

    exe = sys.executable

    if sys.platform == "win32":
        # Clé de registre HKCU Run — ne nécessite pas de droits admin
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, "Raguia Agent", 0, winreg.REG_SZ, f'"{exe}"')
            winreg.CloseKey(key)
            log.info("Autostart Windows enregistre : %s", exe)
        except Exception as e:
            log.warning("Impossible d'enregistrer le demarrage auto Windows : %s", e)

    elif sys.platform == "darwin":
        # LaunchAgent plist pointant directement vers le binaire/l'app bundle
        try:
            plist_dir = Path.home() / "Library" / "LaunchAgents"
            plist_dir.mkdir(parents=True, exist_ok=True)
            plist_path = plist_dir / "com.raguia.local.agent.plist"

            # sys.executable est le binaire dans .app/Contents/MacOS/ ;
            # on ouvre le .app bundle pour un démarrage propre via open(1).
            # Si ce n'est pas un .app, on lance l'exécutable directement.
            app_bundle = Path(exe).parent.parent.parent
            if app_bundle.suffix == ".app" and app_bundle.is_dir():
                program_args = ["<string>/usr/bin/open</string>", f"<string>{app_bundle}</string>"]
                program_args_xml = "\n    ".join(program_args)
            else:
                program_args_xml = f"<string>{exe}</string>"

            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.raguia.local.agent</string>
  <key>ProgramArguments</key>
  <array>
    {program_args_xml}
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
</dict>
</plist>
"""
            plist_path.write_text(plist_content, encoding="utf-8")
            plist_path.chmod(0o644)

            uid = str(os.getuid()) if hasattr(os, "getuid") else ""
            if uid:
                subprocess.run(
                    ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
                    capture_output=True,
                    timeout=10,
                )
            log.info("Autostart macOS enregistre : %s", plist_path)
        except Exception as e:
            log.warning("Impossible d'enregistrer le demarrage auto macOS : %s", e)


class SetupWizard:
    """Fenetre Tkinter en 3 etapes.

    Retourne la config via .result (dict) apres fermeture.
    """

    def __init__(self, api_base: str = "http://127.0.0.1:8000") -> None:
        self.result: dict | None = None
        self._api_base_default = api_base

        self.root = tk.Tk()
        self.root.title("Raguia — Configuration initiale")
        self.root.resizable(False, False)
        self._center(500, 400)

        self._step = 0
        self._frames: list[tk.Frame] = []

        # Variables Tk
        self.var_api   = tk.StringVar(value=api_base)
        self.var_token = tk.StringVar()
        self.var_dir   = tk.StringVar(value=_detect_default_parent())

        self._build_ui()
        self._show_step(0)

    def _center(self, w: int, h: int) -> None:
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth()  - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Header
        hdr = tk.Frame(self.root, bg="#1e293b", height=60)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="Raguia  —  Configuration",
            fg="#f8fafc", bg="#1e293b",
            font=("Helvetica", 14, "bold"),
        ).pack(pady=15)

        # Container pages
        self._container = tk.Frame(self.root, padx=24, pady=16)
        self._container.pack(fill="both", expand=True)

        # -- Page 0 : API + Token --
        p0 = tk.Frame(self._container)
        tk.Label(p0, text="Etape 1 / 3 — Connexion au portail",
                 font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 12))
        tk.Label(p0, text="URL du portail Raguia :").pack(anchor="w")
        ttk.Entry(p0, textvariable=self.var_api, width=52).pack(fill="x", pady=(2, 10))
        tk.Label(p0, text="Jeton agent (obtenu depuis le portail → Parametres) :").pack(anchor="w")
        ttk.Entry(p0, textvariable=self.var_token, width=52, show="*").pack(fill="x", pady=(2, 0))
        tk.Label(
            p0,
            text="Le jeton est un JWT valable plusieurs annees.",
            fg="#64748b", font=("Helvetica", 9),
        ).pack(anchor="w", pady=(4, 0))
        self._frames.append(p0)

        # -- Page 1 : Dossier --
        p1 = tk.Frame(self._container)
        tk.Label(p1, text="Etape 2 / 3 — Dossier de synchronisation",
                 font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 12))
        tk.Label(
            p1,
            text="Choisissez le dossier PARENT.\nL'agent creera automatiquement un dossier 'RAGUIA' a l'interieur.",
            justify="left",
        ).pack(anchor="w")
        row = tk.Frame(p1)
        row.pack(fill="x", pady=(10, 0))
        ttk.Entry(row, textvariable=self.var_dir, width=40).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Parcourir…", command=self._browse).pack(side="left", padx=(6, 0))
        self._frames.append(p1)

        # -- Page 2 : Test --
        p2 = tk.Frame(self._container)
        tk.Label(p2, text="Etape 3 / 3 — Test de connexion",
                 font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 12))
        self._test_label = tk.Label(p2, text="Appuyez sur 'Tester' pour verifier la connexion.",
                                    justify="left", wraplength=440)
        self._test_label.pack(anchor="w")
        ttk.Button(p2, text="Tester la connexion", command=self._run_test).pack(anchor="w", pady=10)
        self._frames.append(p2)

        # Barre de navigation
        nav = tk.Frame(self.root, pady=10, padx=24)
        nav.pack(fill="x", side="bottom")
        self._btn_back = ttk.Button(nav, text="← Retour", command=self._prev)
        self._btn_back.pack(side="left")
        self._btn_next = ttk.Button(nav, text="Suivant →", command=self._next)
        self._btn_next.pack(side="right")
        self._btn_save = ttk.Button(nav, text="Enregistrer & Demarrer", command=self._save)
        # Affiché seulement page 2

    def _show_step(self, step: int) -> None:
        for f in self._frames:
            f.pack_forget()
        self._frames[step].pack(fill="both", expand=True)
        self._step = step
        self._btn_back.config(state="normal" if step > 0 else "disabled")
        self._btn_next.config(state="normal" if step < 2 else "disabled")
        if step == 2:
            self._btn_save.pack(side="right", padx=(6, 0))
        else:
            self._btn_save.pack_forget()

    def _prev(self) -> None:
        if self._step > 0:
            self._show_step(self._step - 1)

    def _next(self) -> None:
        if self._step == 0:
            if not self.var_token.get().strip():
                messagebox.showwarning("Jeton manquant", "Entrez votre jeton agent.")
                return
            try:
                validate_api_base(self.var_api.get())
            except ValueError as e:
                messagebox.showwarning("URL invalide", str(e))
                return
        if self._step < 2:
            self._show_step(self._step + 1)

    def _browse(self) -> None:
        d = filedialog.askdirectory(
            title="Choisir le dossier parent",
            initialdir=self.var_dir.get(),
        )
        if d:
            self.var_dir.set(d)

    def _run_test(self) -> None:
        import httpx
        self._test_label.config(text="Test en cours…", fg="black")
        self.root.update()

        def _do():
            try:
                api_base = validate_api_base(self.var_api.get())
                r = httpx.get(
                    f"{api_base}/api/portal/agent/sync-status",
                    headers={"Authorization": f"Bearer {self.var_token.get().strip()}"},
                    timeout=10.0,
                    trust_env=False,
                )
                if r.status_code == 200:
                    return True, "Connexion reussie !"
                elif r.status_code == 401:
                    return False, "Jeton invalide ou expire."
                else:
                    return False, f"Erreur HTTP {r.status_code}"
            except Exception as e:
                return False, f"Impossible de joindre le portail : {e}"

        ok, msg = _do()
        color = "#16a34a" if ok else "#dc2626"
        self._test_label.config(text=msg, fg=color)

    def _save(self) -> None:
        token = self.var_token.get().strip()
        if not token:
            messagebox.showwarning("Jeton manquant", "Entrez votre jeton agent.")
            return
        config_dir = Path.home() / ".raguia"
        config_dir.mkdir(exist_ok=True)
        config_path = config_dir / "config.yaml"
        try:
            api_base = validate_api_base(self.var_api.get())
        except ValueError as e:
            messagebox.showwarning("URL invalide", str(e))
            return
        data = {
            "api_base":      api_base,
            "agent_token":   save_token(config_path, token),
            "watch_parent":  self.var_dir.get(),
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)

        try:
            os.chmod(config_path, 0o600)
        except Exception:
            pass

        _register_autostart()

        self.result = data
        messagebox.showinfo(
            "Configuration sauvegardee",
            f"Configuration enregistree dans {config_path}\nL'agent va demarrer.",
        )
        self.root.destroy()

    def run(self) -> dict | None:
        """Bloque jusqu'a fermeture. Retourne la config ou None si annule."""
        self.root.mainloop()
        return self.result


def run_wizard(api_base: str = "http://127.0.0.1:8000") -> dict | None:
    """Lance le wizard et retourne la config, ou None si annule."""
    try:
        w = SetupWizard(api_base=api_base)
        return w.run()
    except Exception as e:
        print(f"Wizard indisponible (tkinter manquant ?) : {e}", file=sys.stderr)
        return None
