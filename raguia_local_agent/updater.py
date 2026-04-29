"""Vérification et installation des mises à jour de l'agent.

Deux modes selon le contexte d'exécution :

* Mode binaire gelé (``sys.frozen == True``, distribution client PyInstaller) :
  ``perform_update()`` télécharge le nouveau binaire, vérifie le SHA256, puis
  spawne un processus shell détaché qui remplace le binaire courant après
  l'arrêt de l'agent (spawn-and-replace).

* Mode source Python (développement) :
  ``perform_update()`` n'est pas utilisé ; le menu tray appelle
  ``local_git_update`` à la place.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

# Délai (secondes) accordé au processus courant pour se terminer avant que
# le shell de remplacement ne tente le move/rename.
_REPLACE_DELAY_S = 4


def _pending_path(suffix: str) -> Path:
    """Chemin du fichier temporaire pendant le téléchargement."""
    return Path.home() / ".raguia" / f"raguia-agent-pending{suffix}"


def _current_app_bundle() -> Path:
    """Racine du .app bundle macOS à partir de sys.executable.

    Structure : raguia-agent.app/Contents/MacOS/raguia-agent
                                  ^-- parent  ^-- parent  ^-- sys.executable
    """
    return Path(sys.executable).resolve().parent.parent.parent


class AgentUpdater:
    """Vérifie et installe les mises à jour de l'agent.

    Utilisé par SyncAgent (auto_update_check_hours).
    """

    def __init__(self, client, current_version: str) -> None:
        self.client = client
        self.current_version = current_version

    # ------------------------------------------------------------------
    # Vérification (commune aux deux modes)
    # ------------------------------------------------------------------

    def check_and_log(self, current_version: str) -> bool:
        """Interroge /api/portal/agent/version et logue si une MAJ est dispo.

        Retourne True si une mise à jour est disponible.
        """
        try:
            data = self.client.agent_version_info()
            latest_raw = data.get("version")
            latest = str(latest_raw).strip() if latest_raw else ""
            if not latest:
                return False
            if latest != str(current_version).strip():
                log.info(
                    "Mise a jour disponible : %s -> %s. "
                    "Menu icone : Verifier / installer mise a jour.",
                    current_version,
                    latest,
                )
                return True
        except Exception as e:
            log.debug("Verification mise a jour echouee : %s", e)
        return False

    # ------------------------------------------------------------------
    # Installation — mode binaire gelé (PyInstaller)
    # ------------------------------------------------------------------

    def perform_update(self, update_info: dict) -> bool:
        """Télécharge et installe le nouveau binaire via spawn-and-replace.

        Retourne True si le processus de remplacement a été lancé : l'appelant
        doit quitter l'agent immédiatement après.
        Retourne False en cas d'erreur (aucune action effectuée).

        Appelé uniquement en mode binaire gelé (sys.frozen=True).
        En mode source Python (développement), le menu tray délègue à
        local_git_update à la place.
        """
        if not getattr(sys, "frozen", False):
            log.warning(
                "perform_update appelé en mode non-gelé (Python source). "
                "Utilisez le menu 'Mise à jour Git' pour le développement."
            )
            return False

        download_url = update_info.get("download_url")
        if not download_url:
            log.error("URL de mise a jour manquante dans update_info")
            return False

        expected_sha256 = (update_info.get("sha256") or "").strip()
        if not expected_sha256:
            log.error("Mise a jour refusee : checksum SHA256 manquante.")
            return False

        # Validation minimale de confiance : HTTPS + même hôte que le portail
        parsed_base = urlparse(self.client.api_base)
        parsed_dl = urlparse(download_url)
        if parsed_dl.scheme != "https":
            log.error("Mise a jour refusee : URL de telechargement non HTTPS.")
            return False
        if (parsed_dl.hostname or "").lower() != (parsed_base.hostname or "").lower():
            log.error("Mise a jour refusee : source non approuvee.")
            return False

        # ---- Téléchargement ----
        is_win = sys.platform == "win32"
        is_mac = sys.platform == "darwin"

        pending_suffix = ".exe" if is_win else (".zip" if is_mac else "")
        pending_file = _pending_path(pending_suffix)
        pending_file.parent.mkdir(parents=True, exist_ok=True)

        log.info(
            "Telechargement de la mise a jour %s vers %s ...",
            update_info.get("version", "?"),
            pending_file,
        )
        try:
            r = httpx.get(
                download_url,
                timeout=300.0,
                follow_redirects=False,
                trust_env=False,
            )
            r.raise_for_status()
        except Exception:
            log.exception("Echec du telechargement")
            return False

        # ---- Vérification SHA256 ----
        actual_sha256 = hashlib.sha256(r.content).hexdigest()
        if actual_sha256.lower() != expected_sha256.lower():
            log.error(
                "SECURITE : hash SHA256 invalide. Attendu=%s Obtenu=%s",
                expected_sha256,
                actual_sha256,
            )
            return False

        # ---- Écriture du fichier téléchargé ----
        try:
            pending_file.write_bytes(r.content)
        except Exception:
            log.exception("Echec de l'ecriture du binaire en attente")
            return False

        # ---- Résolution des chemins courant / nouveau ----
        current_exe = Path(sys.executable).resolve()

        if is_mac:
            # Sur macOS le binaire est dans .app/Contents/MacOS/raguia-agent
            # Le .app à remplacer est 3 niveaux au-dessus.
            current_app = _current_app_bundle()
            pending_app = pending_file.parent / "raguia-agent-pending.app"

            # Extraire le zip vers un .app temporaire
            try:
                if pending_app.exists():
                    import shutil as _shutil
                    _shutil.rmtree(pending_app, ignore_errors=True)

                with zipfile.ZipFile(pending_file) as zf:
                    zf.extractall(pending_app.parent / "_unzip_tmp")

                # Le zip contient raguia-agent.app/ à la racine
                extracted_app = pending_app.parent / "_unzip_tmp" / "raguia-agent.app"
                if not extracted_app.exists():
                    # Chercher le premier .app dans l'archive
                    candidates = list((pending_app.parent / "_unzip_tmp").glob("*.app"))
                    if not candidates:
                        log.error("Aucun .app trouve dans le zip de mise a jour.")
                        return False
                    extracted_app = candidates[0]

                extracted_app.rename(pending_app)
            except Exception:
                log.exception("Echec de l'extraction du .app")
                return False
            finally:
                try:
                    pending_file.unlink(missing_ok=True)
                    import shutil as _shutil2
                    tmp_dir = pending_app.parent / "_unzip_tmp"
                    if tmp_dir.exists():
                        _shutil2.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

            return _spawn_replace_macos(current_app, pending_app)

        elif is_win:
            return _spawn_replace_windows(current_exe, pending_file)

        else:
            # Linux : chmod + mv + relance
            try:
                pending_file.chmod(0o755)
            except Exception:
                pass
            return _spawn_replace_linux(current_exe, pending_file)


# ------------------------------------------------------------------
# Helpers spawn-and-replace (hors classe pour testabilité)
# ------------------------------------------------------------------

def _spawn_replace_windows(current_exe: Path, pending_exe: Path) -> bool:
    """Spawne cmd.exe détaché : attend la sortie de l'agent, déplace, relance."""
    cur = str(current_exe)
    new = str(pending_exe)
    # Guillemets doublés dans la commande cmd pour gérer les espaces dans les chemins
    cmd_str = (
        f'timeout /t {_REPLACE_DELAY_S} /nobreak >nul'
        f' & move /y "{new}" "{cur}"'
        f' & start "" "{cur}"'
    )
    try:
        subprocess.Popen(
            ["cmd", "/c", cmd_str],
            creationflags=0x08000000 | 0x00000008,  # CREATE_NO_WINDOW | DETACHED_PROCESS
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Processus de remplacement Windows lance.")
        return True
    except Exception:
        log.exception("Echec du spawn Windows")
        return False


def _spawn_replace_macos(current_app: Path, pending_app: Path) -> bool:
    """Spawne bash détaché : attend, supprime l'ancien .app, déplace, ouvre."""
    cur = shlex.quote(str(current_app))
    new = shlex.quote(str(pending_app))
    bash_cmd = (
        f"sleep {_REPLACE_DELAY_S} "
        f"&& rm -rf {cur} "
        f"&& mv {new} {cur} "
        f"&& open {cur}"
    )
    try:
        subprocess.Popen(
            ["bash", "-c", bash_cmd],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Processus de remplacement macOS lance.")
        return True
    except Exception:
        log.exception("Echec du spawn macOS")
        return False


def _spawn_replace_linux(current_exe: Path, pending_exe: Path) -> bool:
    """Spawne bash détaché : attend, remplace, relance."""
    cur = shlex.quote(str(current_exe))
    new = shlex.quote(str(pending_exe))
    bash_cmd = (
        f"sleep {_REPLACE_DELAY_S} "
        f"&& mv -f {new} {cur} "
        f"&& chmod +x {cur} "
        f"&& {cur} &"
    )
    try:
        subprocess.Popen(
            ["bash", "-c", bash_cmd],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Processus de remplacement Linux lance.")
        return True
    except Exception:
        log.exception("Echec du spawn Linux")
        return False
