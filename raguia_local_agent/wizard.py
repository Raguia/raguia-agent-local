"""Assistant de premier lancement (Tkinter, sans dependances externes)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml

from .api_client import PortalApiClient, portal_agent_login, validate_api_base
from .config import DEFAULT_API_BASE
from .secret_store import save_token

log = logging.getLogger(__name__)
_BRAND_RED = "#A43032"
_BRAND_BLACK = "#010101"
_BRAND_MUTED = "#6b7280"
_BRAND_BG = "#f8fafc"
_BRAND_LIGHT_RED = "#fff1f2"


def _resolve_asset_path(relative: str) -> Path | None:
    """Resolve un asset en mode source ou binaire PyInstaller."""
    rel = Path(relative)
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        candidates += [
            exe.parent / "assets" / rel,
            exe.parent.parent / "Resources" / "assets" / rel,
            exe.parent.parent.parent / "Resources" / "assets" / rel,
        ]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "assets" / rel)

    here = Path(__file__).resolve()
    candidates += [
        here.parents[1] / "assets" / rel,
        Path.cwd() / "assets" / rel,
    ]

    for c in candidates:
        if c.is_file():
            return c
    return None


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
                program_args = [
                    "<string>/usr/bin/open</string>",
                    f"<string>{app_bundle}</string>",
                ]
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

    def __init__(self, api_base: str = DEFAULT_API_BASE) -> None:
        self.result: dict | None = None
        self._api_base_default = api_base

        self.root = tk.Tk()
        self.root.title("Raguia — Configuration initiale")
        self.root.configure(bg=_BRAND_BG)
        self.root.resizable(False, False)
        self._center(500, 400)
        self._logo_img_src: tk.PhotoImage | None = None
        self._logo_img: tk.PhotoImage | None = None

        self._step = 0
        self._frames: list[tk.Frame] = []

        # Variables Tk
        self.var_api = tk.StringVar(value=api_base)
        self.var_slug = tk.StringVar()
        self.var_password = tk.StringVar()
        self.var_dir = tk.StringVar(value=_detect_default_parent())

        self._build_ui()
        self._show_step(0)

    def _center(self, w: int, h: int) -> None:
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Raguia.TFrame", background=_BRAND_BG)
        style.configure(
            "Raguia.TButton",
            padding=(12, 7),
            background=_BRAND_RED,
            foreground="#ffffff",
            borderwidth=0,
        )
        style.map(
            "Raguia.TButton",
            background=[
                ("active", "#8a2729"),
                ("pressed", "#7f2224"),
                ("disabled", "#cbd5e1"),
            ],
            foreground=[("disabled", "#f8fafc")],
        )
        style.configure(
            "Raguia.TEntry",
            fieldbackground="#ffffff",
            bordercolor="#d1d5db",
            lightcolor="#d1d5db",
            darkcolor="#d1d5db",
            insertcolor=_BRAND_BLACK,
            padding=6,
        )
        style.map("Raguia.TEntry", bordercolor=[("focus", _BRAND_RED)])

        # Header
        hdr = tk.Frame(self.root, bg=_BRAND_RED, height=88)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        hdr_inner = tk.Frame(hdr, bg=_BRAND_RED)
        hdr_inner.pack(fill="both", expand=True, padx=16)
        logo_path = _resolve_asset_path("logo_agent-local.png")
        if logo_path:
            try:
                self._logo_img_src = tk.PhotoImage(file=str(logo_path))
                img_h = max(1, int(self._logo_img_src.height()))
                # Evite de rogner le logo dans l'entete (hauteur cible ~52 px).
                factor = max(1, img_h // 52)
                hdr_logo = self._logo_img_src.subsample(factor)
                tk.Label(hdr_inner, image=hdr_logo, bg=_BRAND_RED).pack(
                    side="left", pady=6, padx=(0, 10)
                )
                self._logo_img = hdr_logo
            except Exception:
                self._logo_img_src = None
                self._logo_img = None
        tk.Label(
            hdr_inner,
            text="Raguia  —  Configuration",
            fg="#ffffff",
            bg=_BRAND_RED,
            font=("Helvetica", 14, "bold"),
        ).pack(side="left", pady=15)

        # Container pages
        self._container = tk.Frame(self.root, padx=24, pady=16, bg=_BRAND_BG)
        self._container.pack(fill="both", expand=True)

        # -- Page 0 : API + Login --
        p0 = tk.Frame(self._container, bg=_BRAND_BG)
        tk.Label(
            p0,
            text="Etape 1 / 3 — Connexion au portail",
            font=("Helvetica", 11, "bold"),
            bg=_BRAND_BG,
            fg=_BRAND_BLACK,
        ).pack(anchor="w", pady=(0, 12))
        tk.Label(
            p0, text="URL du portail Raguia :", bg=_BRAND_BG, fg=_BRAND_BLACK
        ).pack(anchor="w")
        ttk.Entry(p0, textvariable=self.var_api, width=52, style="Raguia.TEntry").pack(
            fill="x", pady=(2, 10)
        )
        tk.Label(
            p0,
            text="Slug client (ex: entreprise-demo) :",
            bg=_BRAND_BG,
            fg=_BRAND_BLACK,
        ).pack(anchor="w")
        ttk.Entry(p0, textvariable=self.var_slug, width=52, style="Raguia.TEntry").pack(
            fill="x", pady=(2, 10)
        )
        tk.Label(
            p0,
            text="Mot de passe portail client :",
            bg=_BRAND_BG,
            fg=_BRAND_BLACK,
        ).pack(anchor="w")
        ttk.Entry(
            p0,
            textvariable=self.var_password,
            width=52,
            show="*",
            style="Raguia.TEntry",
        ).pack(fill="x", pady=(2, 0))
        tk.Label(
            p0,
            text="Le mot de passe ne sera jamais stocke. Seule la session de connexion est conservee de facon securisee.",
            fg=_BRAND_MUTED,
            bg=_BRAND_BG,
            font=("Helvetica", 9),
        ).pack(anchor="w", pady=(4, 0))
        self._frames.append(p0)

        # -- Page 1 : Dossier --
        p1 = tk.Frame(self._container, bg=_BRAND_BG)
        tk.Label(
            p1,
            text="Etape 2 / 3 — Dossier de synchronisation",
            font=("Helvetica", 11, "bold"),
            bg=_BRAND_BG,
            fg=_BRAND_BLACK,
        ).pack(anchor="w", pady=(0, 12))
        tk.Label(
            p1,
            text="Choisissez le dossier PARENT.\nL'agent creera automatiquement un dossier 'RAGUIA' a l'interieur.",
            justify="left",
            bg=_BRAND_BG,
            fg=_BRAND_BLACK,
        ).pack(anchor="w")
        row = tk.Frame(p1, bg=_BRAND_BG)
        row.pack(fill="x", pady=(10, 0))
        ttk.Entry(row, textvariable=self.var_dir, width=40, style="Raguia.TEntry").pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            row, text="Parcourir…", command=self._browse, style="Raguia.TButton"
        ).pack(side="left", padx=(6, 0))
        self._frames.append(p1)

        # -- Page 2 : Test --
        p2 = tk.Frame(self._container, bg=_BRAND_BG)
        tk.Label(
            p2,
            text="Etape 3 / 3 — Test de connexion",
            font=("Helvetica", 11, "bold"),
            bg=_BRAND_BG,
            fg=_BRAND_BLACK,
        ).pack(anchor="w", pady=(0, 12))
        self._test_label = tk.Label(
            p2,
            text="Appuyez sur 'Tester' pour verifier la connexion.",
            justify="left",
            wraplength=440,
            bg=_BRAND_BG,
            fg=_BRAND_BLACK,
        )
        self._test_label.pack(anchor="w")
        ttk.Button(
            p2,
            text="Tester la connexion",
            command=self._run_test,
            style="Raguia.TButton",
        ).pack(anchor="w", pady=10)
        self._frames.append(p2)

        # Barre de navigation
        nav = tk.Frame(self.root, pady=10, padx=24, bg=_BRAND_LIGHT_RED)
        nav.pack(fill="x", side="bottom")
        self._btn_back = ttk.Button(
            nav, text="← Retour", command=self._prev, style="Raguia.TButton"
        )
        self._btn_back.pack(side="left")
        self._btn_next = ttk.Button(
            nav, text="Suivant →", command=self._next, style="Raguia.TButton"
        )
        self._btn_next.pack(side="right")
        self._btn_save = ttk.Button(
            nav,
            text="Enregistrer & Demarrer",
            command=self._save,
            style="Raguia.TButton",
        )
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
            if not self.var_slug.get().strip():
                messagebox.showwarning("Slug manquant", "Entrez le slug client.")
                return
            if not self.var_password.get().strip():
                messagebox.showwarning(
                    "Mot de passe manquant", "Entrez le mot de passe portail."
                )
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

    def _login_and_validate(self) -> tuple[bool, str, str]:
        import httpx

        api_base = validate_api_base(self.var_api.get())
        slug = self.var_slug.get().strip().lower()
        password = self.var_password.get().strip()
        if not slug:
            return False, "Slug manquant.", ""
        if not password:
            return False, "Mot de passe portail manquant.", ""

        try:
            login_payload = portal_agent_login(api_base, slug, password)
            token = str(login_payload.get("agent_access_token") or "").strip()
            if not token:
                return (
                    False,
                    "Connexion impossible : reponse login sans token agent.",
                    "",
                )

            client = PortalApiClient(api_base, token)
            try:
                client.sync_status()
            finally:
                client.close()
            return True, "Connexion reussie !", token
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return False, "Identifiants invalides ou agent local desactive.", ""
            return False, f"Erreur HTTP {e.response.status_code}", ""
        except Exception as e:
            return False, f"Impossible de joindre le portail : {e}", ""

    def _run_test(self) -> None:
        self._test_label.config(text="Test en cours…", fg="black")
        self.root.update()
        ok, msg, _token = self._login_and_validate()
        color = "#16a34a" if ok else "#dc2626"
        self._test_label.config(text=msg, fg=color)

    def _save(self) -> None:
        config_dir = Path.home() / ".raguia"
        config_dir.mkdir(exist_ok=True)
        config_path = config_dir / "config.yaml"
        try:
            api_base = validate_api_base(self.var_api.get())
        except ValueError as e:
            messagebox.showwarning("URL invalide", str(e))
            return

        ok, msg, token = self._login_and_validate()
        if not ok or not token:
            messagebox.showwarning("Connexion impossible", msg)
            return

        data = {
            "api_base": api_base,
            "client_slug": self.var_slug.get().strip().lower(),
            "agent_password": save_token(config_path, self.var_password.get().strip()),
            "watch_parent": self.var_dir.get(),
            "root_folder_name": "RAGUIA",
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)

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


def run_wizard(api_base: str = DEFAULT_API_BASE) -> dict | None:
    """Lance le wizard et retourne la config, ou None si annule."""
    try:
        w = SetupWizard(api_base=api_base)
        return w.run()
    except Exception as e:
        print(f"Wizard indisponible (tkinter manquant ?) : {e}", file=sys.stderr)
        return None
