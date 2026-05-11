"""Client HTTP vers l'API portail (authentification username/password)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def ssl_verify():
    """Retourne le bundle CA certifi (chemin absolu).

    En mode binaire PyInstaller (sys.frozen), certifi.where() pointe vers le
    fichier extrait dans sys._MEIPASS. Le chemin absolu est préférable à True
    (qui ferait une résolution interne à l'import) pour éviter un éventuel
    problème de lookup dans certains builds Windows.
    """
    try:
        import certifi
        return certifi.where()
    except Exception:
        return True


def trust_env() -> bool:
    """Respecte les proxies d'environnement si RAGUIA_TRUST_ENV=1.

    Par défaut False (sécurité : évite les détournements via HTTP_PROXY).
    À activer sur les réseaux d'entreprise nécessitant un proxy explicite.
    """
    return os.environ.get("RAGUIA_TRUST_ENV", "").lower() in ("1", "true", "yes")


# Alias internes (compat code existant)
_ssl_verify = ssl_verify
_trust_env = trust_env


def http_response_detail(response: httpx.Response) -> str:
    """Extrait le message FastAPI ``detail`` (ou un extrait du corps) pour les logs."""
    try:
        data = response.json()
        d = data.get("detail")
        if isinstance(d, str):
            return d
        if isinstance(d, list) and d:
            parts: list[str] = []
            for item in d[:5]:
                if isinstance(item, dict):
                    parts.append(str(item.get("msg", item)))
                else:
                    parts.append(str(item))
            return "; ".join(parts)
    except Exception:
        pass
    return (response.text or "").strip().replace("\n", " ")[:400]
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # secondes (x2 a chaque tentative)


def validate_api_base(api_base: str) -> str:
    """Valide et normalise l'URL du portail.

    - HTTPS requis, sauf localhost (dev local)
    - interdit les URL de page (/portal/...) au lieu de la racine
    - normalise les fins de chemin courantes (``/api``, ``/api/portal``)
      qu'un utilisateur pourrait copier-coller depuis sa barre d'adresse.
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
    path = parsed.path or ""
    if "/portal/" in path:
        raise ValueError(
            "Utilisez la racine du portail (ex: https://mon-domaine.tld), "
            "pas une page /portal/<slug>."
        )
    # Tolérance : strip un suffixe /api ou /api/portal si présent (erreur courante).
    for suffix in ("/api/portal", "/api"):
        if path.lower().rstrip("/").endswith(suffix):
            cut = len(parsed.scheme) + 3 + len(parsed.netloc)
            base = base[:cut] + path[: len(path) - len(suffix)].rstrip("/")
            break
    return base.rstrip("/")


def _request_with_retry(
    client: httpx.Client, method: str, url: str, *, retries: int = _MAX_RETRIES, **kwargs
) -> httpx.Response:
    """Effectue une requête HTTP avec retry exponentiel sur erreurs transitoires."""
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
            if attempt < retries:
                log.warning("Erreur reseau %s (tentative %d/%d), retry dans %.1fs: %s",
                            url, attempt + 1, retries, delay, e)
                time.sleep(delay)
                delay *= 2
            else:
                raise
    # Inatteignable : la dernière itération renvoie ou propage explicitement.
    raise RuntimeError(f"_request_with_retry: epuisement des tentatives pour {url}")


def portal_agent_login(api_base: str, slug: str, password: str) -> dict[str, Any]:
    """Connexion agent (slug + mot de passe portail) -> session agent."""
    base = validate_api_base(api_base)
    s = (slug or "").strip().lower()
    p = (password or "").strip()
    if not s:
        raise ValueError("Slug client manquant")
    if not p:
        raise ValueError("Mot de passe portail manquant")
    with httpx.Client(
        verify=_ssl_verify(), trust_env=_trust_env(), follow_redirects=False
    ) as client:
        r = _request_with_retry(
            client,
            "POST",
            f"{base}/api/portal/agent/login",
            json={"slug": s, "password": p},
            timeout=30.0,
        )

        if r.status_code in (404, 405):
            # Compat migration : backend non encore mis à jour avec /agent/login.
            # Fallback : login portail puis issue-token (sans revoke).
            login_r = _request_with_retry(
                client,
                "POST",
                f"{base}/api/portal/login",
                json={"slug": s, "password": p},
                timeout=30.0,
            )
            login_r.raise_for_status()
            login_payload = login_r.json()
            if not isinstance(login_payload, dict):
                raise ValueError("Réponse login portail invalide (JSON objet attendu).")
            portal_access_token = str(login_payload.get("access_token") or "").strip()
            if not portal_access_token:
                raise ValueError("Réponse login portail sans access_token.")

            issue_r = _request_with_retry(
                client,
                "POST",
                f"{base}/api/portal/agent/issue-token",
                headers={"Authorization": f"Bearer {portal_access_token}"},
                timeout=30.0,
            )
            issue_r.raise_for_status()
            issue_payload = issue_r.json()
            if not isinstance(issue_payload, dict):
                raise ValueError("Réponse issue-token invalide (JSON objet attendu).")
            token = str(issue_payload.get("access_token") or "").strip()
            if not token:
                raise ValueError("Réponse issue-token sans access_token.")
            return {
                "agent_access_token": token,
                "token_type": issue_payload.get("token_type", "bearer"),
                "expires_in_days": issue_payload.get("expires_in_days"),
                "expires_at": issue_payload.get("expires_at"),
                "client_slug": s,
            }

        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            raise ValueError("Réponse login agent invalide (JSON objet attendu).")
        return payload


def auto_login(api_base: str, client_slug: str, agent_password: str) -> str:
    """Tente la connexion agent et retourne le token. Lève ValueError si échec.

    Fonction utilitaire partagée entre le wizard, le démarrage CLI et le tray.
    """
    if not agent_password:
        raise ValueError("Mot de passe portail manquant")
    payload = portal_agent_login(api_base, client_slug, agent_password)
    token = str(payload.get("agent_access_token") or "").strip()
    if not token:
        raise ValueError("Réponse login sans token agent")
    return token


class PortalApiClient:
    def __init__(self, api_base: str, agent_token: str):
        self.api_base = validate_api_base(api_base)
        self.agent_token = agent_token
        self._headers = {"Authorization": f"Bearer {agent_token}"}
        self._client = httpx.Client(
            verify=_ssl_verify(),
            trust_env=_trust_env(),
            follow_redirects=False,
            headers=self._headers,
        )

    def _ensure_http_client(self) -> None:
        """Recree le client httpx si la boucle agent a appele close() (diagnostic)."""
        if getattr(self._client, "is_closed", False):
            self._client = httpx.Client(
                verify=_ssl_verify(),
                trust_env=_trust_env(),
                follow_redirects=False,
                headers=dict(self._headers),
            )

    def _parse_json_or_raise(self, r: httpx.Response, endpoint: str) -> dict[str, Any]:
        try:
            payload = r.json()
        except json.JSONDecodeError:
            ct = (r.headers.get("content-type") or "").lower()
            preview = (r.text or "").strip().replace("\n", " ")[:200]
            parsed = urlparse(self.api_base)
            hint = ""
            path = parsed.path or ""
            if "/portal/" in path:
                hint = (
                    " Utilisez la racine du site (ex: https://mon-domaine.tld), "
                    "pas une URL de page comme /portal/<slug>."
                )
            elif "text/html" in ct or preview.startswith("<!DOCTYPE") or preview.startswith("<html"):
                hint = (
                    " Le serveur a renvoye une page HTML au lieu du JSON API — souvent "
                    "`api_base` pointe vers le site statique ou le frontend seul. "
                    "Mettez l'URL exacte du backend Raguia (meme origine que GET /health -> {\"status\":\"ok\"}), "
                    "sans chemin supplementaire sauf si votre hebergeur expose l API sous un prefixe."
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
            raise ValueError("Session vide")
        self.agent_token = token
        self._headers = {"Authorization": f"Bearer {token}"}
        if getattr(self._client, "is_closed", False):
            self._client = httpx.Client(
                verify=_ssl_verify(),
                trust_env=_trust_env(),
                follow_redirects=False,
                headers=dict(self._headers),
            )
        else:
            self._client.headers.clear()
            self._client.headers.update(self._headers)

    def sync_status(self) -> dict[str, Any]:
        self._ensure_http_client()
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
        self._ensure_http_client()
        r = _request_with_retry(
            self._client,
            "POST",
            f"{self.api_base}/api/portal/agent/refresh-token",
            timeout=30.0,
        )
        r.raise_for_status()
        return self._parse_json_or_raise(r, "refresh-token")

    def agent_version_info(self) -> dict[str, Any]:
        """Metadonnees MAJ agent (GET /api/portal/agent/version)."""
        self._ensure_http_client()
        r = _request_with_retry(
            self._client,
            "GET",
            f"{self.api_base}/api/portal/agent/version",
            timeout=30.0,
        )
        r.raise_for_status()
        return self._parse_json_or_raise(r, "agent/version")

    def delete_local(self, relative_path: str) -> dict[str, Any]:
        """Met en corbeille sur le portail le document lié à ce chemin relatif."""
        self._ensure_http_client()
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
        self._ensure_http_client()
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

        self._ensure_http_client()
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
            # Reprendre explicitement Authorization : en multipart, certains chemins httpx
            # mélangent mal les en-têtes par défaut du client.
            r = self._client.post(
                f"{self.api_base}/api/portal/agent/upload",
                data=data,
                files=file_tuples,
                headers=dict(self._headers),
                timeout=600.0,
            )
        r.raise_for_status()
        return self._parse_json_or_raise(r, "upload")

    def close(self) -> None:
        self._client.close()
