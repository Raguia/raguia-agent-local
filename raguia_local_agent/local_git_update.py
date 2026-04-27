"""Mise à jour locale depuis le dépôt Git : même logique que ``update.sh`` / ``builtin_update.py``.

Utilisé par le menu icône « Vérifier / installer mise à jour » (sans téléchargement distant).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> tuple[Path | None, str]:
    """Retourne (racine du clone, message d'erreur si introuvable)."""
    env = (os.environ.get("RAGUIA_AGENT_REPO") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "pyproject.toml").is_file():
            return p, ""
        return None, (
            f"RAGUIA_AGENT_REPO pointe vers un dossier invalide (pas de pyproject.toml) :\n{p}"
        )
    cwd = Path.cwd().resolve()
    if (cwd / "pyproject.toml").is_file():
        return cwd, ""
    return None, (
        "Variable RAGUIA_AGENT_REPO absente ou dossier inconnu.\n\n"
        "Relancez l'agent avec .raguia_agent/start.sh (ou start.bat) : "
        "elle définit automatiquement le chemin du clone Git."
    )


def _venv_python(root: Path) -> tuple[Path | None, str]:
    if sys.platform == "win32":
        p = root / ".raguia_agent" / "venv" / "Scripts" / "python.exe"
    else:
        p = root / ".raguia_agent" / "venv" / "bin" / "python"
    if not p.is_file():
        return None, (
            f"Environnement virtuel introuvable :\n{p}\n\n"
            "Exécutez install.sh / install.bat une fois depuis la racine du clone."
        )
    return p, ""


def run_local_git_update() -> tuple[bool, str]:
    """Exécute ``git pull`` puis ``pip install -e ".[tray]"`` dans le venv du clone.

    Retourne (succès, message lisible pour l'utilisateur).
    """
    root, err = _repo_root()
    if root is None:
        return False, err

    if not (root / ".git").is_dir():
        return (
            False,
            "Pas de dossier .git dans le clone — impossible de faire git pull.\n\n"
            "Réinstallez depuis : git clone https://github.com/ValMtp3/raguia-agent-local.git",
        )

    branch = (os.environ.get("RAGUIA_AGENT_BRANCH") or "main").strip()
    py, verr = _venv_python(root)
    if py is None:
        return False, verr

    lines: list[str] = []

    fetch = subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if fetch.stderr:
        lines.append(fetch.stderr.strip())
    pull = subprocess.run(
        ["git", "pull", "--ff-only", "origin", branch],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if pull.returncode != 0:
        pull = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    if pull.stdout:
        lines.append(pull.stdout.strip())
    if pull.stderr:
        lines.append(pull.stderr.strip())
    if pull.returncode != 0:
        msg = "\n".join(lines) if lines else "(git pull sans sortie)"
        return False, f"git pull a échoué (code {pull.returncode}) :\n\n{msg}"

    pip_r = subprocess.run(
        [str(py), "-m", "pip", "install", "-e", ".[tray]"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = (pip_r.stdout or "") + (pip_r.stderr or "")
    if pip_r.returncode != 0:
        tail = out.strip()[-1500:] if out else "(pas de sortie)"
        return False, f"pip install a échoué :\n\n{tail}"

    ok_msg = (
        "Mise à jour terminée : dépôt Git synchronisé et paquet réinstallé.\n\n"
        "Quittez l'agent (menu Icône → Quitter) puis relancez start.sh / start.bat."
    )
    if out.strip():
        ok_msg += "\n\n--- pip ---\n" + out.strip()[-800:]
    return True, ok_msg


def main() -> int:
    ok, msg = run_local_git_update()
    print(msg, file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
