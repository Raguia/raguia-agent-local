use crate::api::{self, ApiError};
use crate::config;
use crate::queue::{self, MAX_TRIES_BEFORE_STUCK};
use crate::updater;
use crate::watcher;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::AppHandle;
use tauri::Emitter as _;

/// Status string emitted to the tray
#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TrayStatus {
    Idle,
    Syncing,
    Error { message: String },
    Warning { message: String },
    #[allow(dead_code)]
    UpdateAvailable { version: String },
    Stopped,
}

/// Emit a status event to the Tauri window manager AND update the tray icon.
fn emit_status(app_handle: &AppHandle, status: &TrayStatus) {
    // Update tray icon image based on status
    let icon_status = match status {
        TrayStatus::Idle => "idle",
        TrayStatus::Syncing => "syncing",
        TrayStatus::Error { .. } => "error",
        TrayStatus::Warning { .. } => "idle",
        TrayStatus::UpdateAvailable { .. } => "idle",
        TrayStatus::Stopped => "disconnected",
    };
    crate::set_tray_icon(app_handle, icon_status);

    // Also emit event for future UI usage
    if let Err(e) = app_handle.emit("tray-status", status) {
        tracing::warn!("Failed to emit tray status: {}", e);
    }
}

/// Token expiry check result
enum TokenHealth {
    Valid,
    ExpiringSoon { days: f64 },
    Expired,
    Unknown, // non-JWT token or parse error
}

/// Check if the JWT token is expiring soon (< 7 days)
fn check_token_expiry(token: &str) -> TokenHealth {
    use base64::Engine as _;

    let parts: Vec<&str> = token.split('.').collect();
    if parts.len() != 3 {
        return TokenHealth::Unknown;
    }

    let payload_b64 = parts[1];
    // Add padding
    let padded = format!("{}{}", payload_b64, "=".repeat((4 - payload_b64.len() % 4) % 4));

    let decoded = match base64::engine::general_purpose::URL_SAFE.decode(padded.as_bytes()) {
        Ok(d) => d,
        Err(_) => return TokenHealth::Unknown,
    };

    let payload: serde_json::Value = match serde_json::from_slice(&decoded) {
        Ok(v) => v,
        Err(_) => return TokenHealth::Unknown,
    };

    let exp = match payload.get("exp").and_then(|v| v.as_f64()) {
        Some(e) => e,
        None => return TokenHealth::Unknown,
    };

    let now = chrono::Utc::now().timestamp() as f64;
    let days = (exp - now) / 86400.0;

    if days <= 0.0 {
        TokenHealth::Expired
    } else if days <= 7.0 {
        TokenHealth::ExpiringSoon { days }
    } else {
        TokenHealth::Valid
    }
}

/// Check for file staleness before upload (file should be older than stability window).
/// Python: ``stability_seconds`` (default 2.0).
fn is_file_stable(path: &PathBuf, stability_secs: f64) -> bool {
    let metadata = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(_) => return false,
    };
    let modified = match metadata.modified() {
        Ok(t) => t,
        Err(_) => return false,
    };
    let modified_dt: chrono::DateTime<chrono::Utc> = modified.into();
    let elapsed = chrono::Utc::now()
        .signed_duration_since(modified_dt)
        .num_seconds() as f64;
    elapsed >= stability_secs
}

/// Compute folder size recursively (for quota check).
fn get_local_folder_size(root: &Path) -> u64 {
    let mut total = 0u64;
    if !root.is_dir() {
        return 0;
    }
    fn scan_dir(path: &std::path::Path, total: &mut u64) {
        if let Ok(entries) = std::fs::read_dir(path) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    scan_dir(&path, total);
                } else if path.is_file() {
                    let name = path
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("");
                    if !name.starts_with('.') && !name.starts_with("~$") && !name.ends_with(".tmp")
                    {
                        if let Ok(meta) = entry.metadata() {
                            *total += meta.len();
                        }
                    }
                }
            }
        }
    }
    scan_dir(root, &mut total);
    total
}

/// Minimum age in seconds before a queue entry is eligible for processing.
/// Acts as a debounce to avoid picking up files while the watcher is still settling.
const QUEUE_MIN_AGE_SECS: f64 = 5.0;

// ─── Main sync loop ──────────────────────────────────────────

/// The core sync engine running as a background tokio task.
///
/// Mirrors Python ``SyncAgent.run_forever()``.
pub async fn run_sync_loop(
    app_handle: AppHandle,
    config: Arc<config::Manager>,
    api: Arc<api::Client>,
    queue: Arc<queue::Store>,
    watcher: Arc<tokio::sync::Mutex<watcher::Watcher>>,
    wake: Arc<tokio::sync::Notify>,
    stop: Arc<std::sync::atomic::AtomicBool>,
) {
    tracing::info!("Sync engine started");

    // Load initial config
    let cfg = match config.load_config() {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("Failed to load config in sync engine: {}", e);
            emit_status(&app_handle, &TrayStatus::Error { message: format!("Erreur de configuration: {}", e) });
            return;
        }
    };

    let root = cfg.root_path();
    tracing::info!("Watching directory: {:?}", root);

    // Ensure root exists
    if !root.is_dir() {
        tracing::warn!("Root directory {:?} does not exist — creating", root);
        if let Err(e) = std::fs::create_dir_all(&root) {
            tracing::error!("Failed to create root directory: {}", e);
        }
    }

    // Start the file watcher
    {
        let mut w = watcher.lock().await;
        if let Err(e) = w.start() {
            tracing::error!("Failed to start file watcher: {}", e);
            emit_status(&app_handle, &TrayStatus::Error { message: format!("Watcher: {}", e) });
        }
    }

    let root_label = cfg.root_folder_name.clone();
    let stability_secs = cfg.stability_secs as f64;
    let max_files_per_cycle = cfg.max_files_per_cycle;
    let burst_threshold = cfg.burst_threshold;
    let sync_cooldown_secs = cfg.sync_cooldown_secs;
    let poll_interval = Duration::from_secs(cfg.poll_interval_secs.max(5));
    let auto_update_check = Duration::from_secs(cfg.auto_update_check_hours * 3600);
    let dry_run = cfg.dry_run;

    // State
    let mut last_cooldown_ts = Instant::now();
    let mut last_update_check = Instant::now();
    let mut last_401_log = Instant::now();
    let mut last_pending_hint = Instant::now();
    let mut wal_ops_since_checkpoint: u64 = 0;
    let mut config_reload_counter: u64 = 0;

    let mut poll_timer = tokio::time::interval(poll_interval);
    poll_timer.reset(); // Don't fire immediately

    emit_status(&app_handle, &TrayStatus::Idle);

    while !stop.load(std::sync::atomic::Ordering::Acquire) {
        tokio::select! {
            _ = poll_timer.tick() => {
                // Normal poll interval
            }
            _ = wake.notified() => {
                // Force sync requested from tray menu
                tracing::debug!("Sync engine woken by force_sync signal");
                poll_timer.reset();
            }
        }

        if stop.load(std::sync::atomic::Ordering::Acquire) {
            break;
        }

        // ── Reload config periodically (detect wizard/UI changes) ──
        config_reload_counter += 1;
        if config_reload_counter.is_multiple_of(6) {
            if let Ok(refreshed) = config.load_config() {
                // Only log on actual changes
                if refreshed.api_url != cfg.api_url
                    || refreshed.client_slug != cfg.client_slug
                    || refreshed.watch_parent != cfg.watch_parent
                {
                    tracing::info!("Configuration changed — applying update");
                    // Update cached values that affect the loop
                    let new_root = refreshed.root_path();
                    if new_root != root {
                        tracing::warn!("Watch path changed from {:?} to {:?} — restart required", root, new_root);
                    }
                }
            }

            // Clean up orphaned 'syncing' entries (left after a crash)
            if let Ok(n) = queue.reset_stuck() {
                if n > 0 {
                    tracing::info!("{} entrée(s) orpheline(s) réinitialisée(s)", n);
                }
            }
        }

        // ── 1. Check token expiry ──
        if let Some(token) = config.get_token() {
            match check_token_expiry(&token) {
                TokenHealth::Expired => {
                    tracing::warn!("Session expirée — tentative de renouvellement");
                    match api.refresh_token().await {
                        Ok(new_token) => {
                            tracing::info!("Token renouvelé avec succès");
                            api.set_token(&new_token).ok();
                        }
                        Err(e) => {
                            tracing::error!("Échec renouvellement token: {}", e);
                            emit_status(&app_handle, &TrayStatus::Warning {
                                message: "Session expirée — reconnectez-vous".into(),
                            });
                        }
                    }
                }
                TokenHealth::ExpiringSoon { days } => {
                    tracing::info!("Session expire dans {:.0} jours", days);
                    if days <= 1.0 {
                        if let Err(e) = api.refresh_token().await {
                            tracing::warn!("Échec refresh préventif: {}", e);
                        }
                    }
                }
                TokenHealth::Valid | TokenHealth::Unknown => {}
            }
        }

        // ── 2. Check for updates ──
        if cfg.auto_update && last_update_check.elapsed() >= auto_update_check {
            last_update_check = Instant::now();
            if updater::Updater::new(app_handle.clone())
                .check_silent()
                .await
            {
                tracing::info!("Update available — plugin will show dialog");
            }
        }

        // ── 3. Poll sync-status from server ──
        let sync_status = match api.sync_status().await {
            Ok(st) => Some(st),
            Err(ApiError::Unauthorized) => {
                let now = Instant::now();
                if now.duration_since(last_401_log) > Duration::from_secs(120) {
                    last_401_log = now;
                    tracing::error!("Connexion refusée par le portail (401)");

                    // Try auto-reconnect with stored password
                    if let Some(password) = config.get_password() {
                        let slug = config.load_config()
                            .map(|c| c.client_slug)
                            .unwrap_or_default();
                        if !slug.is_empty() && !password.is_empty() {
                            match api.login(&slug, &password).await {
                                Ok(resp) => {
                                    tracing::info!("Reconnexion auto réussie");
                                    api.set_token(&resp.agent_access_token).ok();

                                    // Reset stuck files so they can retry
                                    if let Ok(n) = queue.reset_stuck() {
                                        if n > 0 {
                                            tracing::info!("{} fichier(s) débloqués après reconnexion", n);
                                        }
                                    }

                                    // Retry sync-status with new token
                                    match api.sync_status().await {
                                        Ok(st) => Some(st),
                                        Err(e) => {
                                            tracing::warn!("sync-status après reconnexion: {}", e);
                                            None
                                        }
                                    }
                                }
                                Err(login_err) => {
                                    tracing::warn!("Reconnexion auto échouée: {}", login_err);
                                    None
                                }
                            }
                        } else {
                            emit_status(&app_handle, &TrayStatus::Error {
                                message: "Session invalide — reconnectez-vous via l'icone".into(),
                            });
                            None
                        }
                    } else {
                        emit_status(&app_handle, &TrayStatus::Error {
                            message: "Session invalide — reconnectez-vous via l'icone".into(),
                        });
                        None
                    }
                } else {
                    None
                }
            }
            Err(ApiError::Forbidden(detail)) => {
                if detail.to_lowercase().contains("local_agent_enabled")
                    || detail.to_lowercase().contains("agent")
                {
                    tracing::error!("Agent local désactivé par l'administrateur: {}", detail);
                    emit_status(&app_handle, &TrayStatus::Error {
                        message: "Agent local désactivé — contactez votre administrateur".into(),
                    });
                    // Stop the engine
                    break;
                } else {
                    tracing::warn!("Accès refusé (403): {}", detail);
                    emit_status(&app_handle, &TrayStatus::Warning {
                        message: format!("Accès refusé (403): {}", detail),
                    });
                    None
                }
            }
            Err(e) => {
                tracing::warn!("sync-status inaccessible: {}", e);
                None
            }
        };

        if stop.load(std::sync::atomic::Ordering::Acquire) {
            break;
        }

        // Reset poll timer after processing
        poll_timer.reset();

        // ── 4. Apply remote deletions ──
        if let Some(ref st) = sync_status {
            apply_remote_deletions(&queue, &root, &st.remote_deletions, &app_handle);
        }

        // ── 5. Evaluate if we should sync ──
        let pending = queue.pending_count().unwrap_or(0);
        let pending_delete = queue.pending_delete_count().unwrap_or(0);
        let stuck = queue.stuck_count().unwrap_or(0);
        let cooldown_ok = last_cooldown_ts.elapsed() >= Duration::from_secs(sync_cooldown_secs);
        let burst = pending >= burst_threshold as u64;

        // Check for server-requested sync
        let server_requested = sync_status
            .as_ref()
            .map(|st| st.sync_requested)
            .unwrap_or(false);

        let should_sync = server_requested || pending_delete > 0 || (cooldown_ok && burst);

        if stuck > 0 {
            tracing::warn!("{} fichier(s) bloqué(s) — clic droit → Réinitialiser", stuck);
            emit_status(&app_handle, &TrayStatus::Warning {
                message: format!("{} fichier(s) bloqué(s)", stuck),
            });
        }

        // ── 6. Run sync cycle ──
        if should_sync {
            last_cooldown_ts = Instant::now();
            tracing::info!(
                "Sync cycle: server_requested={}, pending_delete={}, pending={}",
                server_requested,
                pending_delete,
                pending,
            );

            emit_status(&app_handle, &TrayStatus::Syncing);

            let quota = if server_requested {
                sync_status.as_ref().and_then(|st| st.max_storage_bytes)
            } else {
                None
            };

            let metrics = run_cycle(
                &api,
                &queue,
                &root,
                &root_label,
                max_files_per_cycle,
                stability_secs,
                dry_run,
                quota,
                &app_handle,
            )
            .await;

            // WAL checkpoint periodically
            wal_ops_since_checkpoint += metrics.uploaded + metrics.deleted;
            if wal_ops_since_checkpoint >= 100 {
                if let Err(e) = queue.wal_checkpoint("PASSIVE") {
                    tracing::warn!("WAL checkpoint failed: {}", e);
                } else {
                    wal_ops_since_checkpoint = 0;
                }
            }

            // Report sync-complete to server
            let err_str = if metrics.errors.is_empty() {
                None
            } else {
                Some(metrics.errors.join("; "))
            };

            let metrics_json = serde_json::json!({
                "uploaded": metrics.uploaded,
                "deleted": metrics.deleted,
                "errors": metrics.errors,
                "reason": metrics.reason,
            });

            // Only send sync-complete if we actually uploaded or deleted something,
            // to avoid clearing server request prematurely
            if metrics.uploaded > 0 || metrics.deleted > 0 || !metrics.errors.is_empty() {
                if let Err(e) = api.sync_complete(&metrics_json, err_str.as_deref()).await {
                    tracing::warn!("sync-complete failed: {}", e);
                }
            } else if server_requested {
                tracing::info!("Server-requested sync but nothing to upload — will retry");
            }

            // Update tray status
            if metrics.errors.is_empty() {
                emit_status(&app_handle, &TrayStatus::Idle);
            } else {
                let err_msg = metrics.errors.join("; ");
                emit_status(&app_handle, &TrayStatus::Warning {
                    message: err_msg.chars().take(80).collect(),
                });
            }
        } else if pending == 0 && stuck == 0 {
            emit_status(&app_handle, &TrayStatus::Idle);
        } else if pending > 0 && last_pending_hint.elapsed() >= Duration::from_secs(120) {
            last_pending_hint = Instant::now();
            if pending < burst_threshold as u64 {
                tracing::warn!(
                    "Sync auto inactive: {} fichier(s) en attente mais burst_threshold={}",
                    pending,
                    burst_threshold,
                );
            } else if !cooldown_ok {
                let remaining = Duration::from_secs(sync_cooldown_secs)
                    .saturating_sub(last_cooldown_ts.elapsed());
                tracing::info!(
                    "Sync auto en cooldown (~{}s). {} fichier(s) en attente.",
                    remaining.as_secs(),
                    pending,
                );
            }
        }
    }

    // ── Cleanup ──
    {
        let mut w = watcher.lock().await;
        w.stop();
    }
    emit_status(&app_handle, &TrayStatus::Stopped);
    tracing::info!("Sync engine stopped");
}

// ─── Sync cycle metrics ──────────────────────────────────────

pub struct CycleMetrics {
    pub reason: String,
    pub uploaded: u64,
    pub deleted: u64,
    pub errors: Vec<String>,
}

// ─── Single sync cycle ───────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn run_cycle(
    api: &api::Client,
    queue: &queue::Store,
    root: &Path,
    root_label: &str,
    max_files_per_cycle: u32,
    stability_secs: f64,
    dry_run: bool,
    quota: Option<u64>,
    app_handle: &AppHandle,
) -> CycleMetrics {
    let mut metrics = CycleMetrics {
        reason: String::new(),
        uploaded: 0,
        deleted: 0,
        errors: vec![],
    };

    // Pop batch with stability filter to avoid uploading files still being written
    let batch = match queue.pop_batch(max_files_per_cycle, QUEUE_MIN_AGE_SECS, MAX_TRIES_BEFORE_STUCK) {
        Ok(b) => b,
        Err(e) => {
            tracing::error!("pop_batch failed: {}", e);
            return metrics;
        }
    };

    if batch.is_empty() {
        return metrics;
    }

    // Separate delete items and upload items
    let delete_items: Vec<_> = batch
        .iter()
        .filter(|e| e.event_type == "deleted")
        .collect();
    let upload_items: Vec<_> = batch
        .iter()
        .filter(|e| e.event_type != "deleted")
        .collect();

    // Check quota for uploads
    if let Some(limit) = quota {
        if !upload_items.is_empty() {
            let current_size = get_local_folder_size(root);
            if current_size > limit {
                let msg = format!(
                    "Quota dépassé: taille locale ({} Mo) > limite ({} Mo)",
                    current_size / 1024 / 1024,
                    limit / 1024 / 1024,
                );
                tracing::error!("{}", msg);
                metrics.errors.push(msg);
                emit_status(app_handle, &TrayStatus::Error {
                    message: "Quota dépassé".into(),
                });
                return metrics;
            }
        }
    }

    // ── Process deletions ──
    for item in &delete_items {
        if dry_run {
            tracing::info!("dry-run: mettrait en corbeille: {}", item.rel_path);
            let _ = queue.mark_done(&item.rel_path);
            metrics.deleted += 1;
            continue;
        }

        match api.delete_local(&item.rel_path).await {
            Ok(resp) if resp.status == "trashed" || resp.status == "not_found" => {
                let _ = queue.mark_done(&item.rel_path);
                metrics.deleted += 1;
                if resp.status == "not_found" {
                    tracing::info!(
                        "Suppression locale: aucun document distant pour {}",
                        item.rel_path,
                    );
                }
            }
            Ok(resp) => {
                let err = format!("Réponse inattendue: {:?}", resp);
                tracing::warn!("{}", err);
                let _ = queue.mark_error(&item.rel_path, &err);
                metrics.errors.push(err);
            }
            Err(e) => {
                let err = e.to_string();
                tracing::error!("Suppression distante impossible pour {}: {}", item.rel_path, err);
                let _ = queue.mark_error(&item.rel_path, &err);
                metrics.errors.push(err);
            }
        }
    }

    // ── Collect upload metadata ──
    let mut paths_ok: Vec<PathBuf> = Vec::new();
    let mut metas_ok: Vec<api::FileMetadata> = Vec::new();

    for item in &upload_items {
        let p = PathBuf::from(&item.abs_path);

        // File disappeared between event and upload
        if !p.is_file() {
            tracing::debug!("Fichier absent, ignoré: {}", item.rel_path);
            let _ = queue.mark_done(&item.rel_path);
            continue;
        }

        // Empty file (in-progress write) — don't mark done, wait for next cycle
        let file_size = match std::fs::metadata(&p) {
            Ok(m) if m.len() == 0 => {
                tracing::debug!("Fichier vide (écriture en cours ?), différé: {}", item.rel_path);
                // Reset to pending so it stays in queue for the next cycle
                let _ = queue.enqueue(&item.rel_path, &item.abs_path, "modified");
                continue;
            }
            Ok(m) => m.len(),
            Err(_) => {
                let _ = queue.mark_done(&item.rel_path);
                continue;
            }
        };

        // Warn on large files
        if file_size > 50 * 1024 * 1024 {
            tracing::warn!(
                "Gros fichier ({} MB): {}",
                file_size / 1024 / 1024,
                item.rel_path,
            );
        }

        // Check file stability (wait for writes to finish)
        if !is_file_stable(&p, stability_secs) {
            tracing::debug!("Fichier instable (modifié récemment), différé: {}", item.rel_path);
            // Don't mark done — it'll be re-enqueued by the watcher
            // Reset to pending so it stays in queue
            let _ = queue.enqueue(&item.rel_path, &item.abs_path, "modified");
            continue;
        }

        // Build metadata matching Python format
        let meta = api::FileMetadata {
            relative_path: item.rel_path.clone(),
            root_label: root_label.to_string(),
            external_id: None,
            sync_origin: "local_agent".into(),
            needs_review: false,
        };

        paths_ok.push(p);
        metas_ok.push(meta);
    }

    // ── Upload files ──
    if paths_ok.is_empty() {
        return metrics;
    }

    let path_refs: Vec<&std::path::Path> = paths_ok.iter().map(|p| p.as_path()).collect();

    match api.upload_files(&path_refs, &metas_ok, dry_run).await {
        Ok(resp) => {
            metrics.uploaded = paths_ok.len() as u64;
            for meta in &metas_ok {
                let _ = queue.mark_done(&meta.relative_path);
            }
            tracing::info!("Cycle: {} fichier(s) uploadés", paths_ok.len());

            // Log server status
            if !resp.results.is_empty() {
                for result in &resp.results {
                    tracing::debug!("Upload result: {} - {}", result.status, result.document_id.as_deref().unwrap_or("-"));
                }
            }
        }
        Err(ApiError::Unauthorized) => {
            let err = "Upload refusé (401) — reconnectez l'agent".to_string();
            tracing::error!("{}", err);
            metrics.errors.push(err.clone());
            for meta in &metas_ok {
                let _ = queue.mark_error(&meta.relative_path, &err);
            }
            emit_status(app_handle, &TrayStatus::Error {
                message: "Session invalide (401) — reconnectez-vous".into(),
            });
        }
        Err(e) => {
            let err = e.to_string();
            tracing::error!("Upload échoué: {}", err);
            metrics.errors.push(err.clone());
            for meta in &metas_ok {
                let _ = queue.mark_error(&meta.relative_path, &err);
            }
        }
    }

    metrics
}

// ─── Apply remote deletions ─────────────────────────────────

fn apply_remote_deletions(
    queue: &queue::Store,
    root: &Path,
    deletions: &[api::RemoteDeletion],
    app_handle: &AppHandle,
) {
    if deletions.is_empty() {
        return;
    }

    // Canonicalize root once for path traversal safety
    let canonical_root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());

    let mut deleted = 0u64;
    let mut failed = 0u64;

    for deletion in deletions {
        let rel = &deletion.relative_path;
        let target = root.join(rel);

        // Path traversal safety: ensure resolved path is under canonical root
        match target.canonicalize() {
            Ok(resolved) => {
                if !resolved.starts_with(&canonical_root) {
                    tracing::warn!("Remote deletion path traversal blocked: {} (resolved={:?}, root={:?})", rel, resolved, canonical_root);
                    failed += 1;
                    continue;
                }
            }
            Err(_) => {
                // Path might not exist (already deleted) — still mark as done
                let _ = queue.mark_done(rel);
                continue;
            }
        }

        if target.exists() && target.is_file() {
            if let Err(e) = std::fs::remove_file(&target) {
                tracing::warn!("Suppression locale impossible pour {}: {}", rel, e);
                failed += 1;
                continue;
            }
            tracing::info!("Suppression locale (depuis portail): {}", rel);
            deleted += 1;
        }

        // Clean up queue + any inode tracking
        let _ = queue.mark_done(rel);
    }

    if deleted > 0 || failed > 0 {
        let msg = if failed == 0 {
            format!("{} suppression(s) appliquée(s)", deleted)
        } else {
            format!("{} suppr. OK, {} échec(s)", deleted, failed)
        };
        emit_status(app_handle, &TrayStatus::Warning { message: msg });
    }
}
