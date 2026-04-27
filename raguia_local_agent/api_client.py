"""Client HTTP vers l'API portail (JWT agent)."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # secondes (x2 a chaque tentative)


def validate_api_base(api_base: str) -> str:
    """Valide et normalise l'URL du portail.

    - HTTPS requis, sauf localhost (dev local)
    - interdit les URL de page (/portal/...) au lieu de la racine
    """
    base = (api_base or "").strip().rstrip("/")
    if not base:
        raise ValueError("URL du portail manquante.")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL invalide: utilisez http:// ou https://.")
    host = parsed.hostname or ""
    local_hosts = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    if parsed.scheme == "http" and host not in local_hosts:
        raise ValueError("L'URL du portail doit utiliser https:// (sauf localhost).")
    if "/portal/" in (parsed.path or ""):
        raise ValueError("Utilisez la racine du portail (ex: https://mon-domaine.tld), pas une page /portal/...")
    return base


def _request_with_retry(
    client: httpx.Client, method: str, url: str, *, retries: int = _MAX_RETRIES, **kwargs
) -> httpx.Response:
    """Effectue une requete HTTP avec retry exponentiel sur erreurs transitoires."""
    last_exc: Exception | None = None
    delay = _RETRY_BACKOFF
    for attempt in range(retries + 1):
        try:
            r = client.request(method, url, **kwargs)
            if r.status_code in _RETRYABLE_STATUS and attempt < retries:
                log.warning("HTTP %s depuis %s (tentative %d/%d), retry dans %.1fs...",
                            r.status_code, url, attempt + 1, retries, delay)
                time.sleep(delay)
                delay *= 2
                continue
            return r
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
            last_exc = e
            if attempt < retries:
                log.warning("Erreur reseau %s (tentative %d/%d), retry dans %.1fs: %s",
                            url, attempt + 1, retries, delay, e)
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_exc  # type: ignore[misc]


class PortalApiClient:
    def __init__(self, api_base: str, agent_token: str):
        self.api_base = validate_api_base(api_base)
        self.agent_token = agent_token
        self._headers = {"Authorization": f"Bearer {agent_token}"}
        # Securite: ignorer les proxies d'environnement (HTTP(S)_PROXY)
        self._client = httpx.Client(
            trust_env=False,
            follow_redirects=False,
            headers=self._headers,
        )

    def _parse_json_or_raise(self, r: httpx.Response, endpoint: str) -> dict[str, Any]:
        try:
            payload = r.json()
        except json.JSONDecodeError:
            ct = (r.headers.get("content-type") or "").lower()
            preview = (r.text or "").strip().replace("\n", " ")[:200]
            parsed = urlparse(self.api_base)
            hint = ""
            if "/portal/" in (parsed.path or "") or "text/html" in ct or preview.startswith("<!DOCTYPE") or preview.startswith("<html"):
                hint = (
                    " URL portail invalide : utilisez la racine (ex: https://mon-domaine.tld), "
                    "pas une URL de page comme /portal/<slug>."
                )
            raise ValueError(
                f"{endpoint}: reponse 200 non-JSON (content-type={ct!r}, body={preview!r}).{hint}"
            )
        if not isinstance(payload, dict):
            raise ValueError(f"{endpoint}: reponse JSON invalide (objet attendu).")
        return payload

    def set_agent_token(self, token: str) -> None:
        token = (token or "").strip()
        if not token:
            raise ValueError("Jeton vide")
        self.agent_token = token
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client.headers.clear()
        self._client.headers.update(self._headers)

    def sync_status(self) -> dict[str, Any]:
        r = _request_with_retry(
            self._client,
            "GET",
            f"{self.api_base}/api/portal/agent/sync-status",
            timeout=60.0,
        )
        r.raise_for_status()
        return self._parse_json_or_raise(r, "sync-status")

    def refresh_token(self) -> dict[str, Any]:
        """Demande un nouveau token au portail."""
        r = _request_with_retry(
            self._client,
            "POST",
            f"{self.api_base}/api/portal/agent/refresh-token",
            timeout=30.0,
        )
        r.raise_for_status()
        return self._parse_json_or_raise(r, "refresh-token")

    def delete_local(self, relative_path: str) -> dict[str, Any]:
        """Met en corbeille sur le portail le document lié à ce chemin relatif."""
        r = _request_with_retry(
            self._client,
            "POST",
            f"{self.api_base}/api/portal/agent/delete-local",
            json={"relative_path": relative_path},
            timeout=60.0,
        )
        r.raise_for_status()
        return self._parse_json_or_raise(r, "delete-local")

    def sync_complete(
        self, metrics: Optional[dict[str, Any]] = None, error: Optional[str] = None
    ) -> None:
        r = _request_with_retry(
            self._client,
            "POST",
            f"{self.api_base}/api/portal/agent/sync-complete",
            json={"metrics": metrics or {}, "error": error},
            timeout=120.0,
        )
        r.raise_for_status()

    def upload_files(
        self,
        paths: list[Path],
        metadata: list[dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if len(paths) != len(metadata):
            raise ValueError("paths et metadata doivent avoir la meme longueur")

        data = {
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "dry_run": str(dry_run).lower(),
        }
        with contextlib.ExitStack() as stack:
            file_tuples = []
            for p in paths:
                fh = stack.enter_context(open(p, "rb"))
                file_tuples.append(
                    ("files", (p.name, fh, "application/octet-stream")),
                )
            # Upload sans retry (fichiers ouverts, non re-openable dans ExitStack)
            r = self._client.post(
                f"{self.api_base}/api/portal/agent/upload",
                data=data,
                files=file_tuples,
                timeout=600.0,
            )
        r.raise_for_status()
        return self._parse_json_or_raise(r, "upload")

    def close(self) -> None:
        self._client.close()
