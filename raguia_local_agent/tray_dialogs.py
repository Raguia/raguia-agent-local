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


def _resolved_runner_python() -> str:
    """Python pour sous-processus Tk : ``sys.executable`` peut pointer vers un binaire absent
    (venv recréé avec seulement ``python``, ancien ``python3`` supprimé, lien cassé).
    """
    if getattr(sys, "frozen", False):
        # En binaire PyInstaller, sys.executable pointe vers l'app/bootloader
        # (pas toujours capable d'executer "python -c ...").
        for name in ("python3", "python"):
            p = shutil.which(name)
            if p:
                return p
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
    return sys.executable


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
    # Petit formulaire WinForms pour permettre le mode masque (JWT/PIN).
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


def _run_tk_subprocess(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_resolved_runner_python(), "-c", code],
        env={**os.environ, "TK_SILENCE_DEPRECATION": "1", "PYTHONUTF8": "1"},
        timeout=600,
        capture_output=True,
        text=True,
    )


def prompt_agent_token() -> str | None:
    """Demande le jeton JWT (masque). Retourne None si annule ou vide."""
    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    out_path.close()
    path = out_path.name
    try:
        script = (
            "import sys\n"
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
            "    t = simpledialog.askstring(\n"
            "        'Raguia — Mettre a jour le jeton',\n"
            "        'Collez le nouveau jeton JWT agent :',\n"
            "        show='*',\n"
            "        parent=root,\n"
            "    )\n"
            "finally:\n"
            "    root.destroy()\n"
            f"with open({path!r}, 'w', encoding='utf-8') as f:\n"
            "    f.write((t or '').strip())\n"
        )
        r = _run_tk_subprocess(script)
        if r.returncode != 0:
            log.warning(
                "prompt_agent_token: code=%s stderr=%s",
                r.returncode,
                (r.stderr or "")[:500],
            )
            if sys.platform == "darwin":
                prompt = _as_quote("Collez le nouveau jeton JWT agent :")
                title = _as_quote("Raguia — Mettre a jour le jeton")
                a = _run_osascript(
                    (
                        "text returned of (display dialog "
                        f"{prompt} "
                        f"with title {title} "
                        "default answer \"\" with hidden answer "
                        "buttons {\"Annuler\", \"Valider\"} default button \"Valider\" "
                        "cancel button \"Annuler\")"
                    )
                )
                if a and a.returncode == 0:
                    raw = (a.stdout or "").strip()
                    return raw if raw else None
            if sys.platform.startswith("linux"):
                z = _run_zenity(
                    [
                        "--entry",
                        "--title=Raguia - Mettre a jour le jeton",
                        "--text=Collez le nouveau jeton JWT agent :",
                        "--hide-text",
                    ]
                )
                if z and z.returncode == 0:
                    raw = (z.stdout or "").strip()
                    return raw if raw else None
            if sys.platform == "win32":
                return _windows_prompt_text(
                    "Raguia - Mettre a jour le jeton",
                    "Collez le nouveau jeton JWT agent :",
                    masked=True,
                )
            return None
        raw = Path(path).read_text(encoding="utf-8").strip()
        return raw if raw else None
    except Exception as e:
        log.exception("prompt_agent_token: %s", e)
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
        if r.returncode != 0:
            log.warning("prompt_text: code=%s stderr=%s", r.returncode, (r.stderr or "")[:500])
            if sys.platform == "darwin":
                hidden = " with hidden answer" if masked else ""
                prompt_as = _as_quote(prompt)
                title_as = _as_quote(title)
                a = _run_osascript(
                    (
                        "text returned of (display dialog "
                        f"{prompt_as} with title {title_as} default answer \"\"{hidden} "
                        "buttons {\"Annuler\", \"Valider\"} default button \"Valider\" "
                        "cancel button \"Annuler\")"
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
        if r.returncode != 0:
            log.warning("show_message: %s", (r.stderr or "")[:300])
            if sys.platform == "darwin":
                icon = "stop" if kind == "error" else ("caution" if kind == "warning" else "note")
                message_as = _as_quote(message)
                title_as = _as_quote(title)
                _run_osascript(
                    f"display dialog {message_as} with title {title_as} buttons {{\"OK\"}} "
                    f"default button \"OK\" with icon {icon}"
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
        "  • pip install -e \".[tray]\" (venv .raguia_agent)\n\n"
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
        if r.returncode == 0:
            return (r.stdout or "").strip() == "1"
        log.warning("confirm_git_pull_update: code=%s stderr=%s", r.returncode, (r.stderr or "")[:300])
        if sys.platform == "darwin":
            body_as = _as_quote(body)
            title_as = _as_quote("Raguia — Mise a jour depuis Git")
            a = _run_osascript(
                (
                    "button returned of (display dialog "
                    f"{body_as} with title {title_as} "
                    "buttons {\"Annuler\", \"Continuer\"} default button \"Continuer\" "
                    "cancel button \"Annuler\")"
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
        if r.returncode == 0:
            return (r.stdout or "").strip() == "1"
        log.warning("confirm_agent_update: code=%s stderr=%s", r.returncode, (r.stderr or "")[:300])
        if sys.platform == "darwin":
            body_as = _as_quote(body)
            title_as = _as_quote("Raguia — Mise a jour")
            a = _run_osascript(
                (
                    "button returned of (display dialog "
                    f"{body_as} with title {title_as} "
                    "buttons {\"Annuler\", \"Continuer\"} default button \"Continuer\" "
                    "cancel button \"Annuler\")"
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
        if r.returncode == 0:
            return (r.stdout or "").strip() == "1"
        log.warning("confirm_uninstall: code=%s stderr=%s", r.returncode, (r.stderr or "")[:300])
        if sys.platform == "darwin":
            body_as = _as_quote(body)
            title_as = _as_quote("Raguia — Desinstallation")
            a = _run_osascript(
                (
                    "button returned of (display dialog "
                    f"{body_as} with title {title_as} with icon caution "
                    "buttons {\"Annuler\", \"Desinstaller\"} default button \"Desinstaller\" "
                    "cancel button \"Annuler\")"
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
