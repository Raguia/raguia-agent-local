"""Verification de mise a jour de l'agent via le portail."""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)


class AgentUpdater:
    """Verifie et signale les mises a jour disponibles.

    Utilise par SyncAgent (auto_update_check_hours).
    """

    def __init__(self, client, current_version: str) -> None:
        self.client = client
        self.current_version = current_version

    def check_and_log(self, current_version: str) -> bool:
        """Interroge /api/portal/agent/version et logue si une MAJ est dispo.

        Retourne True si une mise a jour est disponible.
        """
        try:
            r = httpx.get(
                f"{self.client.api_base}/api/portal/agent/version",
                headers=self.client._headers,
                timeout=15.0,
                trust_env=False,
            )
            if r.status_code == 404:
                return False  # endpoint non implemente, silencieux
            r.raise_for_status()
            data = r.json()
            latest = data.get("version", "")
            if latest and latest != current_version:
                log.info(
                    "Mise a jour disponible : %s -> %s. "
                    "Relancez l'agent pour l'appliquer.",
                    current_version, latest,
                )
                return True
        except Exception as e:
            log.debug("Verification mise a jour echouee : %s", e)
        return False

    def perform_update(self, update_info: dict) -> bool:
        """Telecharge et execute un script de mise a jour fourni par le portail."""
        download_url = update_info.get("download_url")
        if not download_url:
            log.error("URL de mise a jour manquante dans update_info")
            return False

        expected_sha256 = (update_info.get("sha256") or "").strip()
        if not expected_sha256:
            log.error("Mise a jour refusee : checksum SHA256 manquante.")
            return False

        # Chain trust minimal: URL HTTPS et meme host que le portail
        parsed_base = urlparse(self.client.api_base)
        parsed_dl = urlparse(download_url)
        if parsed_dl.scheme != "https":
            log.error("Mise a jour refusee : URL de telechargement non HTTPS.")
            return False
        if (parsed_dl.hostname or "").lower() != (parsed_base.hostname or "").lower():
            log.error("Mise a jour refusee : source non approuvee.")
            return False

        log.info("Telechargement de la mise a jour %s...", update_info.get("version"))
        try:
            r = httpx.get(
                download_url,
                timeout=300.0,
                follow_redirects=False,
                trust_env=False,
            )
            r.raise_for_status()

            # Verifier le hash avant d'ecrire/executer
            if expected_sha256:
                import hashlib
                actual_sha256 = hashlib.sha256(r.content).hexdigest()
                if actual_sha256.lower() != expected_sha256.lower():
                    log.error("SECURITE CRITIQUE : Le hash SHA256 du script ne correspond pas ! (RCE evitee)")
                    log.error("Attendu : %s, Obtenu : %s", expected_sha256, actual_sha256)
                    return False

            # Ecrire dans un repertoire temporaire systeme (evite TOCTOU et dossiers partages)
            fd, tmp_path_str = tempfile.mkstemp(suffix="_raguia_update.py", prefix="raguia_")
            script_path = Path(tmp_path_str)
            try:
                import os as _os
                _os.write(fd, r.content)
                _os.close(fd)
                _os.chmod(script_path, 0o700)

                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            finally:
                script_path.unlink(missing_ok=True)

            if result.returncode == 0:
                log.info("Mise a jour appliquee. Redemarrage recommande.")
                return True
            log.error("Script de mise a jour echoue : %s", result.stderr[:500])
            return False
        except Exception:
            log.exception("Mise a jour echouee")
            return False