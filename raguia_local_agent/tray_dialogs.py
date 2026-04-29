"""Dialogues Tk dans un processus separe — requis sur macOS (callbacks pystray / AppKit).

Sans cela, askstring / messagebox depuis le thread du menu ne s'affichent pas ou ne reagissent pas.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _resolved_runner_python() -> str:
    """Python pour sous-processus Tk : ``sys.executable`` peut pointer vers un binaire absent
    (venv recréé avec seulement ``python``, ancien ``python3`` supprimé, lien cassé).
    """
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
        return (r.stdout or "").strip() == "1"
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
        return (r.stdout or "").strip() == "1"
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
        return (r.stdout or "").strip() == "1"
    except Exception as e:
        log.exception("confirm_uninstall: %s", e)
        return False
