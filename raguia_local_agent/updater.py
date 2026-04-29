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
_REPLACE_DELAY_S = 8
_TRUSTED_DOWNLOAD_HOSTS = {
    # Workflow release GitHub courant
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}
_DEFAULT_GITHUB_REPO = "ValMtp3/raguia-agent-local"
_GITHUB_API_BASE = "https://api.github.com"


def _pending_path(suffix: str) -> Path:
    """Chemin du fichier temporaire pendant le téléchargement."""
    return Path.home() / ".raguia" / f"raguia-agent-pending{suffix}"


def _current_app_bundle() -> Path:
    """Racine du .app bundle macOS à partir de sys.executable.

    Structure : raguia-agent.app/Contents/MacOS/raguia-agent
                                  ^-- parent  ^-- parent  ^-- sys.executable
    """
    return Path(sys.executable).resolve().parent.parent.parent


def _extract_allowed_hosts(update_info: dict, api_host: str) -> set[str]:
    """Construit l'ensemble des hôtes de téléchargement autorisés.

    - api_host est toujours autorisé.
    - update_info["allowed_download_hosts"] peut étendre la liste.
    - Les hôtes GitHub de release sont explicitement autorisés pour le workflow actuel.
    """
    allowed = set(_TRUSTED_DOWNLOAD_HOSTS)
    if api_host:
        allowed.add(api_host.lower())

    raw = update_info.get("allowed_download_hosts")
    if isinstance(raw, str):
        for host in raw.split(","):
            h = host.strip().lower()
            if h:
                allowed.add(h)
    elif isinstance(raw, list):
        for host in raw:
            h = str(host or "").strip().lower()
            if h:
                allowed.add(h)
    return allowed


def _normalize_version(value: str) -> str:
    v = str(value or "").strip()
    if v.lower().startswith("v"):
        return v[1:].strip()
    return v


def _semver_tuple(value: str) -> tuple[int, int, int] | None:
    v = _normalize_version(value)
    if not v:
        return None
    core = v.split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _parse_sha256_text(text: str) -> str:
    for token in (text or "").replace("\n", " ").split():
        t = token.strip().lower()
        if len(t) == 64 and all(c in "0123456789abcdef" for c in t):
            return t
    return ""


def _validate_download_chain_https_and_hosts(
    responses: list[httpx.Response], allowed_hosts: set[str]
) -> bool:
    """Valide chaque URL de requête (redirects inclus): HTTPS + host autorisé."""
    for resp in responses:
        req_url = resp.request.url
        scheme = (req_url.scheme or "").lower()
        host = (req_url.host or "").lower()
        if scheme != "https":
            log.error(
                "Mise a jour refusee : URL non HTTPS detectee (%s).",
                str(req_url),
            )
            return False
        if host not in allowed_hosts:
            log.error(
                "Mise a jour refusee : hote de telechargement non approuve (%s).",
                host or "?",
            )
            return False
    return True


class AgentUpdater:
    """Vérifie et installe les mises à jour de l'agent.

    Utilisé par SyncAgent (auto_update_check_hours).
    """

    def __init__(self, client, current_version: str) -> None:
        self.client = client
        self.current_version = current_version

    def _github_repo(self) -> str:
        return (os.environ.get("RAGUIA_GITHUB_REPO") or _DEFAULT_GITHUB_REPO).strip()

    def latest_github_release(self) -> dict:
        """Retourne les infos de la dernière release GitHub (latest)."""
        repo = self._github_repo()
        url = f"{_GITHUB_API_BASE}/repos/{repo}/releases/latest"
        r = httpx.get(
            url,
            timeout=20.0,
            trust_env=False,
            headers={"Accept": "application/vnd.github+json"},
            follow_redirects=True,
        )
        r.raise_for_status()
        payload = r.json() or {}
        if not isinstance(payload, dict):
            raise ValueError("Reponse GitHub invalide (objet attendu).")

        tag = str(payload.get("tag_name") or "").strip()
        version = _normalize_version(tag)
        assets = payload.get("assets") or []
        if not isinstance(assets, list):
            assets = []
        if not version:
            raise ValueError("Tag GitHub latest introuvable.")
        return {"version": version, "tag_name": tag, "assets": assets}

    def latest_github_version(self) -> str:
        return str(self.latest_github_release().get("version") or "").strip()

    @staticmethod
    def compare_versions(local_version: str, remote_version: str) -> int | None:
        """Compare deux versions semver.

        Retour:
          -1 si remote > local
           0 si egales
           1 si local > remote
        None si non comparable.
        """
        local_t = _semver_tuple(local_version)
        remote_t = _semver_tuple(remote_version)
        if local_t is None or remote_t is None:
            return None
        if remote_t > local_t:
            return -1
        if remote_t < local_t:
            return 1
        return 0

    def build_update_info_from_release(self, release: dict) -> dict:
        """Construit update_info compatible perform_update() à partir d'une release GitHub."""
        assets = release.get("assets") or []
        if not isinstance(assets, list):
            assets = []

        def _asset_name(a: dict) -> str:
            return str(a.get("name") or "").strip().lower()

        binary_asset: dict | None = None
        if sys.platform == "win32":
            binary_asset = next(
                (a for a in assets if _asset_name(a).endswith(".exe") and "windows" in _asset_name(a)),
                None,
            ) or next((a for a in assets if _asset_name(a).endswith(".exe")), None)
        elif sys.platform == "darwin":
            binary_asset = next(
                (a for a in assets if _asset_name(a).endswith(".zip") and "macos" in _asset_name(a)),
                None,
            ) or next((a for a in assets if _asset_name(a).endswith(".zip")), None)
        else:
            binary_asset = next(
                (a for a in assets if _asset_name(a).endswith((".bin", ".appimage", ".tar.gz"))),
                None,
            )

        if not binary_asset:
            raise ValueError("Aucun binaire compatible trouve dans la derniere release GitHub.")

        download_url = str(binary_asset.get("browser_download_url") or "").strip()
        if not download_url:
            raise ValueError("URL de telechargement GitHub manquante pour le binaire.")

        bin_name = str(binary_asset.get("name") or "").strip()
        sha_asset = next(
            (
                a
                for a in assets
                if _asset_name(a).endswith(".sha256")
                and (
                    _asset_name(a).startswith(bin_name.lower())
                    or bin_name.lower() in _asset_name(a)
                )
            ),
            None,
        ) or next((a for a in assets if _asset_name(a).endswith(".sha256")), None)

        if not sha_asset:
            raise ValueError("Fichier SHA256 introuvable dans la release GitHub.")

        sha_url = str(sha_asset.get("browser_download_url") or "").strip()
        if not sha_url:
            raise ValueError("URL du fichier SHA256 manquante.")

        r = httpx.get(sha_url, timeout=20.0, trust_env=False, follow_redirects=True)
        r.raise_for_status()
        sha256 = _parse_sha256_text(r.text or "")
        if not sha256:
            raise ValueError("Impossible de lire le hash SHA256 depuis l'asset .sha256.")

        return {
            "version": str(release.get("version") or "").strip(),
            "download_url": download_url,
            "sha256": sha256,
            "allowed_download_hosts": list(_TRUSTED_DOWNLOAD_HOSTS),
        }

    # ------------------------------------------------------------------
    # Vérification (commune aux deux modes)
    # ------------------------------------------------------------------

    def check_and_log(self, current_version: str) -> bool:
        """Interroge la dernière release GitHub et logue si une MAJ est dispo.

        Retourne True si une mise à jour est disponible.
        """
        try:
            latest = self.latest_github_version()
            if not latest:
                return False
            cmp = self.compare_versions(current_version, latest)
            if cmp == -1:
                log.info(
                    "Mise a jour disponible : %s -> %s. "
                    "Menu icone : Verifier / installer mise a jour.",
                    current_version,
                    latest,
                )
                return True
            if cmp == 1:
                log.info(
                    "Version locale plus recente que GitHub latest : %s > %s",
                    current_version,
                    latest,
                )
                return False
            if cmp is None and latest != _normalize_version(str(current_version).strip()):
                # Fallback non-semver: rester permissif (ancienne logique).
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

        # Validation de confiance : HTTPS + hôte autorisé (portail, GitHub releases,
        # et éventuellement hôtes additionnels envoyés par le portail).
        parsed_base = urlparse(self.client.api_base)
        parsed_dl = urlparse(download_url)
        allowed_hosts = _extract_allowed_hosts(update_info, (parsed_base.hostname or ""))
        if parsed_dl.scheme != "https":
            log.error("Mise a jour refusee : URL de telechargement non HTTPS.")
            return False
        if (parsed_dl.hostname or "").lower() not in allowed_hosts:
            log.error(
                "Mise a jour refusee : source non approuvee (%s).",
                (parsed_dl.hostname or "").lower() or "?",
            )
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
                follow_redirects=True,
                trust_env=False,
            )
            r.raise_for_status()
        except Exception:
            log.exception("Echec du telechargement")
            return False

        if not _validate_download_chain_https_and_hosts([*r.history, r], allowed_hosts):
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
    """Spawne bash détaché : swap sûr de .app + relance.

    Stratégie défensive:
    - ne supprime jamais l'app courante avant d'avoir un backup,
    - restaure le backup si le move du nouveau bundle échoue,
    - retire le flag de quarantaine sur le bundle final.
    """
    cur = shlex.quote(str(current_app))
    new = shlex.quote(str(pending_app))
    bak = shlex.quote(str(current_app.with_suffix(".old.app")))
    bash_cmd = (
        "set -euo pipefail; "
        f"sleep {_REPLACE_DELAY_S}; "
        f"rm -rf {bak}; "
        f'if [ -d {cur} ]; then mv {cur} {bak}; fi; '
        f'if ! mv {new} {cur}; then '
        f'  if [ -d {bak} ]; then mv {bak} {cur}; fi; '
        f"  exit 1; "
        f"fi; "
        f"xattr -dr com.apple.quarantine {cur} || true; "
        f"chmod +x {cur}/Contents/MacOS/raguia-agent || true; "
        # -n force macOS a ouvrir une nouvelle instance meme si l'ancienne
        # est encore en cours de fermeture (sinon open redirige vers l'existante
        # qui est en train de mourir, et rien ne demarre).
        f"open -n {cur} || open {cur}; "
        f"rm -rf {bak} || true"
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
