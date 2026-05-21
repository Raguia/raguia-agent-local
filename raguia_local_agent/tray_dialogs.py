"""Dialogues Tk dans un processus separe — requis sur macOS (callbacks pystray / AppKit).

Sans cela, askstring / messagebox depuis le thread du menu ne s'affichent pas ou ne reagissent pas.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _resolved_runner_python() -> str | None:
    """Python pour sous-processus Tk.

    Retourne le chemin vers un interpréteur Python capable d'exécuter ``-c <code>``,
    ou ``None`` si aucun n'est trouvé (les appelants tombent alors sur leur fallback
    natif : PowerShell/osascript/zenity).

    En mode binaire PyInstaller, ``sys.executable`` pointe vers le bootloader de
    l'application (pas un interpréteur Python). Le passer comme interpréteur
    démarrerait une seconde instance de l'agent au lieu d'exécuter le script Tk.
    """
    if getattr(sys, "frozen", False):
        # Binaire PyInstaller : chercher un Python système dans le PATH uniquement.
        for name in ("python3", "python"):
            p = shutil.which(name)
            if p:
                return p
        # Aucun Python système trouvé → les appelants doivent utiliser leur fallback natif.
        return None

    exe = Path(sys.executable)
    if exe.is_file():
        return str(exe.resolve())
    parent = exe.parent
    if sys.platform == "win32":
        for name in ("python.exe", "python3.exe"):
            p = parent / name
            if p.is_file():
                return str(p.resolve())
    else:
        for name in ("python", "python3"):
            p = parent / name
            if p.is_file():
                return str(p.resolve())
    return None


def _run_osascript(script: str) -> subprocess.CompletedProcess | None:
    if sys.platform != "darwin" or shutil.which("osascript") is None:
        return None
    return subprocess.run(
        ["osascript", "-e", script],
        timeout=120,
        capture_output=True,
        text=True,
    )


def _as_quote(value: str) -> str:
    """Quote une chaine pour un littéral AppleScript (double quotes uniquement)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _run_zenity(args: list[str]) -> subprocess.CompletedProcess | None:
    if not sys.platform.startswith("linux") or shutil.which("zenity") is None:
        return None
    return subprocess.run(
        ["zenity", *args],
        timeout=600,
        capture_output=True,
        text=True,
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_powershell(script: str) -> subprocess.CompletedProcess | None:
    if sys.platform != "win32":
        return None
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        return None
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=120,
        capture_output=True,
        text=True,
    )


def _windows_messagebox(title: str, message: str, kind: str = "info") -> None:
    icon_map = {
        "info": "Information",
        "warning": "Warning",
        "error": "Error",
    }
    icon = icon_map.get(kind, "Information")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        f"[void][System.Windows.Forms.MessageBox]::Show({_ps_quote(message)}, {_ps_quote(title)}, "
        "[System.Windows.Forms.MessageBoxButtons]::OK, "
        f"[System.Windows.Forms.MessageBoxIcon]::{icon})"
    )
    _run_powershell(script)


def _windows_confirm(title: str, body: str, ok_label: str = "Oui") -> bool:
    # On garde les boutons Yes/No standards pour fiabilite.
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$r=[System.Windows.Forms.MessageBox]::Show("
        f"{_ps_quote(body)}, {_ps_quote(title)}, "
        "[System.Windows.Forms.MessageBoxButtons]::YesNo, "
        "[System.Windows.Forms.MessageBoxIcon]::Question); "
        "if ($r -eq [System.Windows.Forms.DialogResult]::Yes) { '1' } else { '0' }"
    )
    r = _run_powershell(script)
    return bool(r and r.returncode == 0 and (r.stdout or "").strip() == "1")


def _windows_prompt_text(title: str, prompt: str, masked: bool = False) -> str | None:
    # Petit formulaire WinForms pour permettre le mode masque (mot de passe / PIN).
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$f=New-Object System.Windows.Forms.Form; "
        f"$f.Text={_ps_quote(title)}; "
        "$f.Width=520; $f.Height=180; $f.StartPosition='CenterScreen'; "
        "$f.TopMost=$true; "
        "$l=New-Object System.Windows.Forms.Label; "
        f"$l.Text={_ps_quote(prompt)}; $l.Left=12; $l.Top=12; $l.Width=480; "
        "$t=New-Object System.Windows.Forms.TextBox; "
        "$t.Left=12; $t.Top=40; $t.Width=480; "
        f"$t.UseSystemPasswordChar={'$true' if masked else '$false'}; "
        "$ok=New-Object System.Windows.Forms.Button; "
        "$ok.Text='Valider'; $ok.Left=332; $ok.Top=76; $ok.DialogResult=[System.Windows.Forms.DialogResult]::OK; "
        "$ca=New-Object System.Windows.Forms.Button; "
        "$ca.Text='Annuler'; $ca.Left=417; $ca.Top=76; $ca.DialogResult=[System.Windows.Forms.DialogResult]::Cancel; "
        "$f.AcceptButton=$ok; $f.CancelButton=$ca; "
        "$f.Controls.AddRange(@($l,$t,$ok,$ca)); "
        "$r=$f.ShowDialog(); "
        "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { $t.Text }"
    )
    r = _run_powershell(script)
    if not r or r.returncode != 0:
        return None
    raw = (r.stdout or "").strip()
    return raw if raw else None


def _run_tk_subprocess(code: str) -> subprocess.CompletedProcess | None:
    """Lance un sous-processus Python pour afficher une fenêtre Tk.

    Retourne ``None`` si aucun interpréteur Python n'est disponible (binaire gelé
    sans Python système) afin que les appelants activent leur fallback natif.
    """
    python = _resolved_runner_python()
    if python is None:
        return None
    return subprocess.run(
        [python, "-c", code],
        env={**os.environ, "TK_SILENCE_DEPRECATION": "1", "PYTHONUTF8": "1"},
        timeout=600,
        capture_output=True,
        text=True,
    )


def prompt_portal_login() -> tuple[str, str] | None:
    """Demande slug + mot de passe portail. Retourne ``None`` si annulation."""
    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    out_path.close()
    path = out_path.name
    try:
        # Formulaire explicite (2 champs) pour éviter les dialogues ambigus
        # selon les backends UI (notamment macOS/AppKit + pystray).
        script = (
            "import tkinter as tk\n"
            "root = tk.Tk()\n"
            "root.title('Raguia — Connexion portail')\n"
            "root.resizable(False, False)\n"
            "root.geometry('520x230')\n"
            "try:\n"
            "    root.lift()\n"
            "    root.attributes('-topmost', True)\n"
            "    root.update_idletasks()\n"
            "except Exception:\n"
            "    pass\n"
            "card = tk.Frame(root, bd=1, relief='solid')\n"
            "card.pack(fill='both', expand=True, padx=14, pady=12)\n"
            "form = tk.Frame(card)\n"
            "form.pack(fill='both', expand=True, padx=14, pady=12)\n"
            "tk.Label(form, text='Slug client (ex: entreprise-demo) :').pack(anchor='w')\n"
            "slug_entry = tk.Entry(form, width=50)\n"
            "slug_entry.pack(fill='x', pady=(4, 10))\n"
            "tk.Label(form, text='Mot de passe portail :').pack(anchor='w')\n"
            "pwd_entry = tk.Entry(form, width=50, show='*')\n"
            "pwd_entry.pack(fill='x', pady=(4, 10))\n"
            "result = {'ok': False, 'slug': '', 'pwd': ''}\n"
            "def submit():\n"
            "    result['slug'] = slug_entry.get().strip().lower()\n"
            "    result['pwd'] = pwd_entry.get().strip()\n"
            "    if not result['slug'] or not result['pwd']:\n"
            "        return\n"
            "    result['ok'] = True\n"
            "    root.destroy()\n"
            "def cancel():\n"
            "    root.destroy()\n"
            "btns = tk.Frame(form)\n"
            "btns.pack(fill='x', side='bottom', pady=(8, 0))\n"
            "tk.Button(btns, text='Annuler', command=cancel).pack(side='right')\n"
            "tk.Button(btns, text='Valider', command=submit).pack(side='right', padx=(0, 8))\n"
            "root.bind('<Return>', lambda e: submit())\n"
            "root.bind('<Escape>', lambda e: cancel())\n"
            "slug_entry.focus_set()\n"
            "root.mainloop()\n"
            f"with open({path!r}, 'w', encoding='utf-8') as f:\n"
            "    if result['ok']:\n"
            "        f.write('__OK__\\n' + result['slug'] + '\\n' + result['pwd'])\n"
            "    else:\n"
            "        f.write('__CANCEL__\\n')\n"
        )
        r = _run_tk_subprocess(script)
        if r and r.returncode == 0:
            raw = Path(path).read_text(encoding="utf-8").splitlines()
            if raw and raw[0].strip() == "__CANCEL__":
                return None
            if len(raw) >= 3 and raw[0].strip() == "__OK__":
                slug = raw[1].strip().lower()
                password = raw[2].strip()
                if slug and password:
                    return slug, password

        # Fallback cross-platform (ancien comportement en 2 prompts)
        slug = prompt_text(
            "Raguia — Connexion portail",
            "Slug client (ex: entreprise-demo) :",
            masked=False,
        )
        if slug is None:
            return None
        password = prompt_text(
            "Raguia — Connexion portail",
            "Mot de passe portail :",
            masked=True,
        )
        if password is None:
            return None
        slug = slug.strip().lower()
        password = password.strip()
        if not slug or not password:
            return None
        return slug, password
    except Exception as e:
        log.exception("prompt_portal_login: %s", e)
        return None
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def prompt_text(title: str, prompt: str, *, masked: bool = False) -> str | None:
    """Affiche un input texte simple. Retourne None si annule/vide."""
    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    out_path.close()
    path = out_path.name
    show_value = "*" if masked else ""
    try:
        script = (
            "import tkinter as tk\n"
            "from tkinter import simpledialog\n"
            "root = tk.Tk()\n"
            "root.withdraw()\n"
            "try:\n"
            "    root.lift()\n"
            "    root.attributes('-topmost', True)\n"
            "    root.update_idletasks()\n"
            "except Exception:\n"
            "    pass\n"
            "try:\n"
            f"    t = simpledialog.askstring({title!r}, {prompt!r}, show={show_value!r}, parent=root)\n"
            "finally:\n"
            "    root.destroy()\n"
            f"with open({path!r}, 'w', encoding='utf-8') as f:\n"
            "    f.write((t or '').strip())\n"
        )
        r = _run_tk_subprocess(script)
        if not r or r.returncode != 0:
            log.warning(
                "prompt_text: code=%s stderr=%s",
                r.returncode if r else "no-python",
                (r.stderr or "")[:500] if r else "",
            )
            if sys.platform == "darwin":
                hidden = " with hidden answer" if masked else ""
                prompt_as = _as_quote(prompt)
                title_as = _as_quote(title)
                a = _run_osascript(
                    (
                        "text returned of (display dialog "
                        f'{prompt_as} with title {title_as} default answer ""{hidden} '
                        'buttons {"Annuler", "Valider"} default button "Valider" '
                        'cancel button "Annuler")'
                    )
                )
                if a and a.returncode == 0:
                    raw = (a.stdout or "").strip()
                    return raw if raw else None
            if sys.platform.startswith("linux"):
                args = [
                    "--entry",
                    f"--title={title}",
                    f"--text={prompt}",
                ]
                if masked:
                    args.append("--hide-text")
                z = _run_zenity(args)
                if z and z.returncode == 0:
                    raw = (z.stdout or "").strip()
                    return raw if raw else None
            if sys.platform == "win32":
                return _windows_prompt_text(title, prompt, masked=masked)
            return None
        raw = Path(path).read_text(encoding="utf-8").strip()
        return raw if raw else None
    except Exception as e:
        log.exception("prompt_text: %s", e)
        return None
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def show_message(title: str, message: str, *, kind: str = "info") -> None:
    """kind: info | warning | error"""
    fn = {"info": "showinfo", "warning": "showwarning", "error": "showerror"}.get(
        kind, "showinfo"
    )
    script = (
        "import tkinter as tk\n"
        "from tkinter import messagebox\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "try:\n"
        "    root.lift()\n"
        "    root.attributes('-topmost', True)\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        f"    messagebox.{fn}({title!r}, {message!r}, parent=root)\n"
        "finally:\n"
        "    root.destroy()\n"
    )
    try:
        r = _run_tk_subprocess(script)
        if not r or r.returncode != 0:
            log.warning(
                "show_message: %s", (r.stderr or "")[:300] if r else "no-python"
            )
            if sys.platform == "darwin":
                icon = (
                    "stop"
                    if kind == "error"
                    else ("caution" if kind == "warning" else "note")
                )
                message_as = _as_quote(message)
                title_as = _as_quote(title)
                _run_osascript(
                    f'display dialog {message_as} with title {title_as} buttons {{"OK"}} '
                    f'default button "OK" with icon {icon}'
                )
            elif sys.platform.startswith("linux"):
                if kind == "error":
                    args = ["--error"]
                elif kind == "warning":
                    args = ["--warning"]
                else:
                    args = ["--info"]
                _run_zenity([*args, f"--title={title}", f"--text={message}"])
            elif sys.platform == "win32":
                _windows_messagebox(title, message, kind=kind)
    except Exception as e:
        log.exception("show_message: %s", e)


def confirm_git_pull_update(
    local_version: str,
    info_block: str = "",
) -> bool:
    """MAJ réelle git pull + pip — menu icône."""
    info = info_block.strip()
    prefix = (info + "\n\n") if info else ""
    body = (
        f"{prefix}"
        "Cette opération va exécuter dans le dossier du clone Git :\n"
        "  • git pull\n"
        '  • pip install -e ".[tray]" (venv .raguia_agent)\n\n'
        f"Version du paquet actuel : {local_version}\n\n"
        "Continuer ?"
    )
    script = (
        "import tkinter as tk\n"
        "from tkinter import messagebox\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "try:\n"
        "    root.lift()\n"
        "    root.attributes('-topmost', True)\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        f"    ok = messagebox.askyesno({repr('Raguia — Mise à jour depuis Git')}, {repr(body)}, parent=root, icon='question')\n"
        "finally:\n"
        "    root.destroy()\n"
        "print('1' if ok else '0')\n"
    )
    try:
        r = _run_tk_subprocess(script)
        if r and r.returncode == 0:
            return (r.stdout or "").strip() == "1"
        log.warning(
            "confirm_git_pull_update: code=%s stderr=%s",
            r.returncode if r else "no-python",
            (r.stderr or "")[:300] if r else "",
        )
        if sys.platform == "darwin":
            body_as = _as_quote(body)
            title_as = _as_quote("Raguia — Mise a jour depuis Git")
            a = _run_osascript(
                (
                    "button returned of (display dialog "
                    f"{body_as} with title {title_as} "
                    'buttons {"Annuler", "Continuer"} default button "Continuer" '
                    'cancel button "Annuler")'
                )
            )
            return bool(a and a.returncode == 0 and "Continuer" in (a.stdout or ""))
        if sys.platform.startswith("linux"):
            z = _run_zenity(
                [
                    "--question",
                    "--ok-label=Continuer",
                    "--cancel-label=Annuler",
                    "--title=Raguia - Mise a jour depuis Git",
                    f"--text={body}",
                ]
            )
            return bool(z and z.returncode == 0)
        if sys.platform == "win32":
            return _windows_confirm("Raguia - Mise a jour depuis Git", body)
        return False
    except Exception as e:
        log.exception("confirm_git_pull_update: %s", e)
        return False


def confirm_agent_update(current_version: str, new_version: str) -> bool:
    """Téléchargement du nouveau binaire — demande confirmation."""
    body = (
        f"Une mise a jour de l'agent est disponible.\n\n"
        f"Version installee : {current_version}\n"
        f"Version proposee  : {new_version}\n\n"
        "Le nouveau binaire sera telecharge depuis le serveur (HTTPS, hash SHA256 verifie).\n"
        "L'agent va s'arreter puis redemarrer automatiquement.\n\n"
        "Continuer ?"
    )
    script = (
        "import tkinter as tk\n"
        "from tkinter import messagebox\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "try:\n"
        "    root.lift()\n"
        "    root.attributes('-topmost', True)\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        f"    ok = messagebox.askyesno({repr('Raguia — Mise a jour')}, {repr(body)}, parent=root, icon='question')\n"
        "finally:\n"
        "    root.destroy()\n"
        "print('1' if ok else '0')\n"
    )
    try:
        r = _run_tk_subprocess(script)
        if r and r.returncode == 0:
            return (r.stdout or "").strip() == "1"
        log.warning(
            "confirm_agent_update: code=%s stderr=%s",
            r.returncode if r else "no-python",
            (r.stderr or "")[:300] if r else "",
        )
        if sys.platform == "darwin":
            body_as = _as_quote(body)
            title_as = _as_quote("Raguia — Mise a jour")
            a = _run_osascript(
                (
                    "button returned of (display dialog "
                    f"{body_as} with title {title_as} "
                    'buttons {"Annuler", "Continuer"} default button "Continuer" '
                    'cancel button "Annuler")'
                )
            )
            return bool(a and a.returncode == 0 and "Continuer" in (a.stdout or ""))
        if sys.platform.startswith("linux"):
            z = _run_zenity(
                [
                    "--question",
                    "--ok-label=Continuer",
                    "--cancel-label=Annuler",
                    "--title=Raguia - Mise a jour",
                    f"--text={body}",
                ]
            )
            return bool(z and z.returncode == 0)
        if sys.platform == "win32":
            return _windows_confirm("Raguia - Mise a jour", body)
        return False
    except Exception as e:
        log.exception("confirm_agent_update: %s", e)
        return False


def confirm_uninstall() -> bool:
    body = (
        "Confirmer la desinstallation complete de l'agent ?\n\n"
        "- Arret de l'agent\n"
        "- Suppression du demarrage automatique\n"
        "- Suppression des fichiers agent/config locaux\n\n"
        "Le dossier de documents RAGUIA n'est pas supprime."
    )
    script = (
        "import tkinter as tk\n"
        "from tkinter import messagebox\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "try:\n"
        "    root.lift()\n"
        "    root.attributes('-topmost', True)\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        f"    ok = messagebox.askyesno({repr('Raguia — Desinstallation')}, {repr(body)}, parent=root, icon='warning')\n"
        "finally:\n"
        "    root.destroy()\n"
        "print('1' if ok else '0')\n"
    )
    try:
        r = _run_tk_subprocess(script)
        if r and r.returncode == 0:
            return (r.stdout or "").strip() == "1"
        log.warning(
            "confirm_uninstall: code=%s stderr=%s",
            r.returncode if r else "no-python",
            (r.stderr or "")[:300] if r else "",
        )
        if sys.platform == "darwin":
            body_as = _as_quote(body)
            title_as = _as_quote("Raguia — Desinstallation")
            a = _run_osascript(
                (
                    "button returned of (display dialog "
                    f"{body_as} with title {title_as} with icon caution "
                    'buttons {"Annuler", "Desinstaller"} default button "Desinstaller" '
                    'cancel button "Annuler")'
                )
            )
            return bool(a and a.returncode == 0 and "Desinstaller" in (a.stdout or ""))
        if sys.platform.startswith("linux"):
            z = _run_zenity(
                [
                    "--question",
                    "--ok-label=Desinstaller",
                    "--cancel-label=Annuler",
                    "--title=Raguia - Desinstallation",
                    f"--text={body}",
                ]
            )
            return bool(z and z.returncode == 0)
        if sys.platform == "win32":
            return _windows_confirm("Raguia - Desinstallation", body)
        return False
    except Exception as e:
        log.exception("confirm_uninstall: %s", e)
        return False
