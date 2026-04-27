"""Boucle principale : polling sync-status, queue SQLite, upload.

Scenarios couverts :
- Reseau coupe pendant upload      -> fichier reste en queue (attempts++), requeue apres N echecs
- Fichier ouvert par Word/Excel    -> filtre watcher + stability check (min_age_seconds)
- Fichier corrompu ou vide         -> skip si size==0, log warning
- Dossier renomme/deplace          -> detection au demarrage + log clair + tentative relocalisation
- Agent plante entre deux syncs    -> queue SQLite persistante, reprise automatique
- Antivirus / doublons d'events    -> PRIMARY KEY SQLite deduplique
- Gros fichier reseau lent         -> timeout=600, log du chemin + taille
- Token expire                     -> warning/error tray, message clair
- local_agent_enabled=false        -> arret propre avec message
- Modifications perdues            -> JAMAIS : on requeue en cas d'echec
- Suppression locale               -> evenement deleted / corbeille OS -> API delete-local
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from . import __version__ as AGENT_VERSION
from .api_client import PortalApiClient, http_response_detail
from .config import AgentConfig
from .queue_store import MAX_TRIES_BEFORE_STUCK, QueueStore
from .state_store import StateStore
from .updater import AgentUpdater
from .watcher import _should_ignore, start_observer

log = logging.getLogger(__name__)

# Pour « Synchroniser maintenant » et la demande serveur : reprendre aussi les lignes
# après MAX_TRIES_BEFORE_STUCK échecs (sinon pending_count > 0 mais pop_batch vide).
_FORCE_POP_ATTEMPT_CAP = 10**9

# Seuil (octets) au-delà duquel on logue un avertissement « gros fichier » avant upload.
_MAX_FILE_SIZE_WARN = 50 * 1024 * 1024


class SyncAgent:
    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self.root = cfg.root_path
        app_data = cfg.app_data_dir

        # Queue SQLite (en dehors du dossier surveille)
        self.queue = QueueStore(app_data / "queue.db")
        # Registre inode (en dehors du dossier surveille)
        self.store = StateStore(app_data / "state.json")

        self.client = PortalApiClient(cfg.api_base, cfg.agent_token)
        self.updater = AgentUpdater(self.client, AGENT_VERSION)

        self._stop = threading.Event()
        self._syncing = threading.Event()

        # Callback pour le tray (optionnel)
        self.on_status_change: Optional[Callable] = None
        self._pending_hint_ts: float = 0.0
        self._last_401_log_ts: float = 0.0
        self._last_auth_skip_log_ts: float = 0.0

    def _get_local_folder_size(self) -> int:
        total = 0
        try:
            for p in self.root.rglob("*"):
                if p.is_file() and not _should_ignore(p):
                    total += p.stat().st_size
        except Exception as e:
            log.warning("Erreur calcul taille dossier: %s", e)
        return total

    # ------------------------------------------------------------------
    # Status tray
    # ------------------------------------------------------------------
    def _emit(self, status: str, message: str = "") -> None:
        if self.on_status_change:
            try:
                from .tray import TrayStatus
                self.on_status_change(TrayStatus(status), message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Filesystem events
    # ------------------------------------------------------------------
    def _on_fs_event(self, path: Path, kind: str) -> None:
        path = Path(path)
        if path.suffix.lower() not in self.cfg.supported_extensions:
            return
        if _should_ignore(path):
            return
        try:
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            return
        abs_ref = str(path) if kind != "deleted" else str(self.root / rel)
        self.queue.enqueue(rel, abs_ref, kind)

    # ------------------------------------------------------------------
    # Cycle de synchronisation
    # ------------------------------------------------------------------
    def run_cycle(self, reason: str, limit_bytes: Optional[int] = None) -> dict:
        metrics: dict = {"reason": reason, "uploaded": 0, "deleted": 0, "errors": []}
        # Demande explicite (portail ou menu) : ne pas attendre stability_seconds sinon le 1er
        # poll envoyait sync-complete « vide » et effaçait la demande sans upload (bug).
        min_age = (
            0.0
            if reason in ("force", "server_request")
            else self.cfg.stability_seconds
        )
        attempt_cap = (
            _FORCE_POP_ATTEMPT_CAP
            if reason in ("force", "server_request")
            else MAX_TRIES_BEFORE_STUCK
        )
        batch = self.queue.pop_batch(
            self.cfg.max_files_per_cycle,
            min_age_seconds=min_age,
            max_attempts=attempt_cap,
        )
        if not batch:
            if reason in ("force", "server_request"):
                pend = self.queue.pending_count()
                stk = self.queue.stuck_count()
                if pend > 0:
                    log.warning(
                        "Sync (%s) : %d entrée(s) en queue SQLite mais aucun lot "
                        "(vérifiez chemins abs_path, ou doublons anormaux — bloquées=%d).",
                        reason,
                        pend,
                        stk,
                    )
                else:
                    log.warning(
                        "Sync (%s) : file d'attente vide ou aucun fichier eligible "
                        "(extensions prévues par le watcher, fichier ignoré vide).",
                        reason,
                    )
            return metrics

        delete_items = [
            i for i in batch if (i.get("event_type") or "modified") == "deleted"
        ]
        upload_items = [
            i for i in batch if (i.get("event_type") or "modified") != "deleted"
        ]

        if limit_bytes is not None and upload_items:
            current_size = self._get_local_folder_size()
            if current_size > limit_bytes:
                msg = f"Quota depasse: taille locale ({current_size // 1024 // 1024} Mo) > limite ({limit_bytes // 1024 // 1024} Mo)"
                log.error(msg)
                self._emit("error", "Quota depasse")
                upload_items = []
                metrics["errors"].append(msg)

        self._emit("syncing")

        for item in delete_items:
            rel = item["rel_path"]
            try:
                if self.cfg.dry_run:
                    log.info("dry-run : mettrait en corbeille sur le portail : %s", rel)
                    self.queue.mark_done(rel)
                    self.store.remove_rel(rel)
                    metrics["deleted"] += 1
                    continue
                res = self.client.delete_local(rel)
                if res.get("status") in ("trashed", "not_found"):
                    self.queue.mark_done(rel)
                    self.store.remove_rel(rel)
                    metrics["deleted"] += 1
                    if res.get("status") == "not_found":
                        log.info(
                            "Suppression locale : aucun document distant pour %s (deja absent ?)",
                            rel,
                        )
                else:
                    raise ValueError(f"reponse inattendue : {res!r}")
            except httpx.HTTPStatusError as e:
                detail = http_response_detail(e.response)
                err_txt = detail or str(e)
                self.queue.mark_error(rel, err_txt)
                metrics["errors"].append(err_txt)
                if e.response.status_code == 401:
                    log.error("delete-local refuse (401) : %s", detail)
                else:
                    log.exception("Suppression distante impossible pour %s", rel)
            except Exception as e:
                log.exception("Suppression distante impossible pour %s", rel)
                self.queue.mark_error(rel, str(e))
                metrics["errors"].append(str(e))

        if delete_items:
            self.store.save()
            if metrics["deleted"]:
                log.info(
                    "Cycle '%s' : %d suppression(s) reportee(s) au portail",
                    reason,
                    metrics["deleted"],
                )

        paths_ok: list[Path] = []
        metas_ok: list[dict] = []

        for item in upload_items:
            rel = item["rel_path"]
            p = Path(item["abs_path"])

            # Fichier disparu entre l'event et l'upload
            if not p.is_file():
                log.debug("Fichier absent, ignore : %s", rel)
                self.queue.mark_done(rel)
                continue

            # Fichier vide (corrompu ou en cours d'ecriture)
            if p.stat().st_size == 0:
                log.warning("Fichier vide ignore : %s", rel)
                self.queue.mark_done(rel)
                continue

            # Warning gros fichier
            size = p.stat().st_size
            if size > _MAX_FILE_SIZE_WARN:
                log.warning("Gros fichier (%d MB) : %s", size // 1024 // 1024, rel)

            try:
                rel_out, old_rel, needs_review, ext_id = self.store.register_or_replace(
                    self.root, p
                )
                if not rel_out:
                    self.queue.mark_done(rel)
                    continue
                if old_rel:
                    log.info("Renommage detecte : %s -> %s", old_rel, rel_out)
                meta = {
                    "relative_path": rel_out,
                    "root_label": self.cfg.root_folder_name,
                    "external_id": ext_id,
                    "sync_origin": "local_agent",
                    "needs_review": needs_review,
                }
                paths_ok.append(p)
                metas_ok.append(meta)
            except Exception as e:
                log.warning("Metadata erreur pour %s : %s", rel, e)
                self.queue.mark_error(rel, str(e))

        if not paths_ok:
            self.store.save()
            return metrics

        try:
            res = self.client.upload_files(paths_ok, metas_ok, dry_run=self.cfg.dry_run)
            metrics["api"] = res
            metrics["uploaded"] = len(paths_ok)
            for p, meta in zip(paths_ok, metas_ok):
                self.queue.mark_done(meta["relative_path"])
            log.info("Cycle '%s' : %d fichier(s) uploades", reason, len(paths_ok))
        except httpx.HTTPStatusError as e:
            detail = http_response_detail(e.response)
            err_txt = detail or str(e)
            metrics["errors"].append(err_txt)
            for _p, meta in zip(paths_ok, metas_ok):
                self.queue.mark_error(meta["relative_path"], err_txt)
            if e.response.status_code == 401:
                log.error(
                    "Upload refuse (401) : %s — regenerez le jeton depuis le portail "
                    "(meme URL api_base / meme backend qu'a l'emission du JWT en dev).",
                    detail,
                )
                self._emit("error", (detail or "401 Unauthorized")[:120])
            else:
                log.exception("Upload echoue (%s)", reason)
                self._emit("error", err_txt[:80])
        except Exception as e:
            log.exception("Upload echoue (%s)", reason)
            metrics["errors"].append(str(e))
            for p, meta in zip(paths_ok, metas_ok):
                rel = meta["relative_path"]
                self.queue.mark_error(rel, str(e))
            self._emit("error", str(e)[:80])

        self.store.save()
        return metrics

    def force_sync(self) -> None:
        """Declenche un cycle immediatement (depuis le tray)."""
        if not self._syncing.is_set():
            self._syncing.set()

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------
    def run_forever(self) -> None:
        # Verifier / relocaliser le dossier surveille
        if not self.cfg.check_root_exists():
            relocated = self.cfg.find_relocated_root()
            if relocated:
                log.warning(
                    "Dossier introuvable a '%s'. Trouve ici : %s. "
                    "Mettez a jour watch_parent dans votre config.",
                    self.root, relocated,
                )
                self._emit("warning", f"Dossier deplace ? Trouve : {relocated}")
            else:
                log.warning("Dossier '%s' absent — creation...", self.root)

        self.root.mkdir(parents=True, exist_ok=True)
        obs, _ = start_observer(self.root, self._on_fs_event)
        self._check_token_expiry()

        last_update_check = 0.0
        last_cooldown_ts = 0.0

        try:
            while not self._stop.is_set():
                # --- Auto-update check ---
                if self.cfg.auto_update:
                    elapsed = time.time() - last_update_check
                    if elapsed >= self.cfg.auto_update_check_hours * 3600:
                        last_update_check = time.time()
                        self.updater.check_and_log(AGENT_VERSION)
                        self._check_token_expiry()

                # --- Polling sync-status ---
                st: dict = {}
                sync_ok = True
                auth_failure_401 = False
                try:
                    st = self.client.sync_status()
                except Exception as e:
                    sync_ok = False
                    # Un seul bloc : garantit la detection 401 meme si httpx change ou branche mal ordonnée.
                    he = e if isinstance(e, httpx.HTTPStatusError) else None
                    detail = http_response_detail(he.response) if he else ""
                    if he and he.response.status_code == 401:
                        auth_failure_401 = True
                        now_t = time.time()
                        if now_t - self._last_401_log_ts >= 120:
                            self._last_401_log_ts = now_t
                            log.error(
                                "Jeton agent refuse par le portail (401) : %s\n"
                                "  → Portail : emettre un nouveau jeton (POST …/agent/issue-token) "
                                "et menu icone « Mettre a jour le jeton JWT », ou verifier api_base.\n"
                                "  → Dev local : le JWT doit etre signe par ce backend (SECRET_KEY), "
                                "pas copie depuis la prod.",
                                detail or e,
                            )
                        self._emit(
                            "error",
                            (detail or "401 — jeton agent invalide")[:160],
                        )
                    elif he:
                        log.warning(
                            "sync-status HTTP %s : %s",
                            he.response.status_code,
                            detail or e,
                        )
                        self._emit("warning", "Portail inaccessible")
                    else:
                        log.warning("sync-status inaccessible : %s", e)
                        self._emit("warning", "Portail inaccessible")

                if sync_ok:
                    self._apply_remote_deletions(st)

                # --- Evaluer si on doit syncer ---
                pending   = self.queue.pending_count()
                pending_delete = self.queue.pending_delete_count()
                stuck     = self.queue.stuck_count()
                cooldown_ok = (time.time() - last_cooldown_ts) >= self.cfg.sync_cooldown_seconds
                burst = pending >= self.cfg.burst_threshold
                force = self._syncing.is_set()
                self._syncing.clear()

                reason = None
                if sync_ok and st.get("sync_requested"):
                    reason = "server_request"
                elif force:
                    # Sync manuelle : doit tourner meme si sync-status vient d'echouer (sinon clic ignore).
                    reason = "force"
                elif pending_delete > 0:
                    reason = "local_delete"
                elif cooldown_ok and burst:
                    # Synchro auto seulement apres cooldown ET rafale (limite cout vectorisation)
                    reason = "local_burst"

                if stuck > 0:
                    log.warning("%d fichier(s) bloques (trop d'echecs). Clic droit -> Reinitialiser.", stuck)
                    self._emit("warning", f"{stuck} fichier(s) bloques")

                # Sans sync-status : pas de quota serveur (on laisse run_cycle sans plafond distant)
                limit_b = st.get("max_storage_bytes") if sync_ok else None

                skip_due_auth = auth_failure_401 and reason not in ("force",)
                if reason and skip_due_auth:
                    now_s = time.time()
                    if now_s - self._last_auth_skip_log_ts >= 120:
                        self._last_auth_skip_log_ts = now_s
                        log.warning(
                            "Cycle %s ignore tant que le jeton est refuse (401). "
                            "Forcez « Synchroniser maintenant » apres correction, ou mettez a jour le JWT.",
                            reason,
                        )

                if reason and not skip_due_auth:
                    last_cooldown_ts = time.time()
                    m = self.run_cycle(reason, limit_bytes=limit_b)
                    err_str = ("; ".join(m["errors"])[:2000] if m.get("errors") else None)
                    errs = m.get("errors") or []
                    idle_cycle = (
                        (m.get("uploaded") or 0) == 0
                        and (m.get("deleted") or 0) == 0
                        and len(errs) == 0
                    )
                    # Ne pas appeler sync-complete si rien n'a été traité : sinon le portail
                    # croyait la synchro terminée et effaçait la demande sans upload.
                    if idle_cycle and reason in ("server_request", "force"):
                        log.info(
                            "Cycle '%s' sans envoi — sync-complete omis (nouvel essai au prochain poll).",
                            reason,
                        )
                    else:
                        try:
                            self.client.sync_complete(metrics=m, error=err_str)
                        except Exception as e:
                            log.warning("sync-complete : %s", e)
                    if not errs:
                        self._emit("idle")
                elif not reason:
                    if auth_failure_401:
                        # Ne pas repasser en vert : l'erreur 401 a deja ete emise.
                        pass
                    elif pending == 0 and stuck == 0:
                        self._emit("idle")
                    elif pending > 0:
                        self._emit("idle", f"{pending} en attente")
                        # Expliquer pourquoi aucune synchro auto ne part (sans erreur HTTP).
                        now = time.time()
                        if now - self._pending_hint_ts >= 120:
                            self._pending_hint_ts = now
                            if pending < self.cfg.burst_threshold:
                                log.warning(
                                    "Synchro auto inactive : %d fichier(s) en attente mais "
                                    "burst_threshold=%d (il faut au moins autant de fichiers "
                                    "pour declencher un envoi automatique). "
                                    "Menu : « Synchroniser maintenant », ou baissez burst_threshold dans la config.",
                                    pending,
                                    self.cfg.burst_threshold,
                                )
                            elif not cooldown_ok:
                                left = self.cfg.sync_cooldown_seconds - (
                                    now - last_cooldown_ts
                                )
                                log.info(
                                    "Synchro auto en cooldown (~%.0f s). %d fichier(s) en attente.",
                                    max(0.0, left),
                                    pending,
                                )

                # Si le portail est toujours injoignable et aucune synchro lancee, attendre avant le prochain poll
                if not sync_ok and not reason:
                    self._stop.wait(self.cfg.poll_interval_seconds)
                    continue

                self._stop.wait(self.cfg.poll_interval_seconds)

        finally:
            obs.stop()
            obs.join(timeout=5)
            self.queue.close()
            try:
                self.client.close()
            except Exception:
                pass
            self._emit("stopped")
            log.info("Agent arrete.")

    # ------------------------------------------------------------------
    def _check_token_expiry(self) -> None:
        import base64, json as _json, time as _time
        try:
            parts = self.cfg.agent_token.split(".")
            if len(parts) != 3:
                return
            
            payload_b64 = parts[1]
            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
            
            exp = payload.get("exp")
            if not exp:
                return
            days = (exp - _time.time()) / 86400
            if days <= 0:
                log.error("Token EXPIRE ! Renouvelez depuis le portail.")
                self._emit("error", "Token expire")
            elif days <= 7:
                log.info("Token expire dans %.1f jours. Tentative de renouvellement automatique...", days)
                try:
                    res = self.client.refresh_token()
                    new_token = res.get("access_token")
                    if new_token:
                        self.cfg.save_token(new_token)
                        self.client.set_agent_token(new_token)
                        log.info("Token renouvele avec succes !")
                        self._emit("idle")
                except Exception as e:
                    log.error("Echec du renouvellement auto du token : %s", e)
                    self._emit("warning", f"Token expire dans {days:.0f} j (echec refresh)")
            else:
                log.debug("Token valide (%.0f jours restants)", days)
        except Exception as e:
            log.warning("Erreur verification token : %s", e)

    def _apply_remote_deletions(self, st: dict) -> None:
        """Supprime localement les fichiers mis en corbeille depuis le portail."""
        deletions = st.get("remote_deletions")
        if not isinstance(deletions, list) or not deletions:
            return

        deleted = 0
        failed = 0
        for item in deletions:
            if not isinstance(item, dict):
                continue
            rel = (item.get("relative_path") or "").strip()
            if not rel:
                continue
            try:
                target = (self.root / rel).resolve()
                target.relative_to(self.root.resolve())
            except Exception:
                log.warning("Suppression distante ignoree (chemin invalide): %s", rel)
                continue

            try:
                if target.exists() and target.is_file():
                    os.remove(target)
                    deleted += 1
                    log.info("Suppression locale (depuis portail): %s", rel)
                # Nettoyer queue + registre local, meme si deja absent
                self.queue.mark_done(rel)
                self.store.remove_path(self.root, target)
            except Exception as e:
                failed += 1
                log.warning("Suppression locale impossible pour %s : %s", rel, e)

        if deleted or failed:
            self.store.save()
            if failed == 0:
                self._emit("warning", f"{deleted} suppression(s) appliquee(s)")
            else:
                self._emit("warning", f"{deleted} suppr. OK, {failed} echec(s)")

    def stop(self) -> None:
        self._stop.set()

    def update_agent_token(self, token: str) -> None:
        """Met a jour le JWT en memoire (effet immediat pour les appels API suivants)."""
        token = (token or "").strip()
        if not token:
            raise ValueError("Jeton vide")
        self.cfg.agent_token = token
        self.client.set_agent_token(token)
