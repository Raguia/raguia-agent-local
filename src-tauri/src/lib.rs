// Raguia Agent — core library
//
// Tray-only desktop sync agent for the Raguia SaaS platform.
// Watches local directories, queues file changes, and syncs them to the
// Raguia API server. Built with Tauri 2 for native cross-platform support.

mod api;
mod config;
mod engine;
mod log_capture;
mod queue;
mod watcher;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, LazyLock};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem, Submenu},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
    Manager, WebviewUrl, WebviewWindowBuilder,
};
use image::GenericImageView;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_updater::UpdaterExt;

/// Shared application state accessible from Tauri commands and tray handlers.
pub struct AppState {
    pub tray: TrayIcon<tauri::Wry>,
    pub api_client: Arc<api::Client>,
    pub config_manager: Arc<config::Manager>,
    pub queue_store: Arc<queue::Store>,
    pub watcher: Arc<tokio::sync::Mutex<watcher::Watcher>>,
    pub wake_signal: Arc<tokio::sync::Notify>,
    pub stop_signal: Arc<AtomicBool>,
    pub force_sync: Arc<AtomicBool>,
    /// Signaled by the engine when it has fully stopped (allows graceful exit).
    pub engine_stopped: Arc<tokio::sync::Notify>,
}

/// Tauri command: health check
#[tauri::command]
fn health() -> String {
    "ok".into()
}

/// Tauri command: get sync statistics
#[tauri::command]
fn get_stats(state: tauri::State<'_, AppState>) -> Result<serde_json::Value, String> {
    let stats = state.queue_store.get_stats().map_err(|e| e.to_string())?;
    let stuck = state.queue_store.stuck_count().unwrap_or(0);
    Ok(serde_json::json!({
        "pending": stats.pending,
        "pending_delete": stats.pending_delete,
        "synced": stats.synced,
        "stuck": stuck,
    }))
}

/// Tauri command: trigger a force sync
#[tauri::command]
fn sync_now(state: tauri::State<'_, AppState>) -> Result<(), String> {
    state.force_sync.store(true, std::sync::atomic::Ordering::Release);
    state.wake_signal.notify_one();
    tracing::info!("Force sync triggered");
    Ok(())
}

/// Tauri command: return the user's home directory as a normalized string.
///
/// On Windows, `USERPROFILE` is checked first (canonical); `HOME` is only used
/// as a fallback (Git Bash / MSYS may set it incorrectly).
/// Returns a path with platform-native separators.
#[tauri::command]
fn get_home_dir() -> String {
    #[cfg(target_os = "windows")]
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| "C:\\Users".into());
    #[cfg(not(target_os = "windows"))]
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| "/Users".into());

    let pb = std::path::PathBuf::from(home);
    pb.to_string_lossy().to_string()
}

/// Tauri command: return the current OS kind for the wizard UI.
/// Avoids the deprecated `navigator.platform` Web API.
#[tauri::command]
fn get_os_kind() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        "windows"
    }
    #[cfg(target_os = "macos")]
    {
        "macos"
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        "linux"
    }
}

/// Tauri command: authenticate and save configuration.
///
/// Called by the wizard HTML on first launch or reconnection.
/// On success, saves config, wakes the sync engine, and closes the wizard window.
#[tauri::command]
async fn login(
    app: tauri::AppHandle,
    slug: String,
    password: String,
    api_url: String,
    watch_dir: Option<String>,
    state: tauri::State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    // Save configuration
    let mut cfg = state.config_manager.load_config().unwrap_or_default();
    cfg.api_url = api_url.trim_end_matches('/').to_string();
    cfg.client_slug = slug.clone();
    if let Some(dir) = watch_dir.filter(|d| !d.is_empty()) {
        cfg.watch_parent = std::path::PathBuf::from(dir);
    }
    state
        .config_manager
        .save_config(&cfg)
        .map_err(|e| format!("Erreur sauvegarde config : {}", e))?;
    state
        .config_manager
        .set_password(&password)
        .map_err(|e| format!("Erreur sauvegarde mot de passe : {}", e))?;

    // Update API client URL to use the newly configured endpoint
    state.api_client.set_api_url(&cfg.api_url);

    // Authenticate
    let resp = state
        .api_client
        .login(&slug, &password)
        .await
        .map_err(|e| e.to_string())?;

    tracing::info!(
        "Login successful for slug={}, expires_in={:?} days",
        slug,
        resp.expires_in_days
    );

    // Enable autostart on login
    if let Err(e) = app.autolaunch().enable() {
        tracing::warn!("Failed to enable autostart: {}", e);
    } else {
        tracing::info!("Autostart enabled");
    }

    // Set tray icon to idle
    set_tray_icon(&app, "idle");

    // Wake sync engine so it picks up the new config immediately
    state.wake_signal.notify_one();

    // Close wizard window if open
    if let Some(window) = app.get_webview_window("wizard") {
        let _ = window.close();
    }

    Ok(serde_json::json!({
        "token": resp.agent_access_token,
        "expires_in_days": resp.expires_in_days,
    }))
}

/// Cached decoded tray icons (decoded once, reused on every status update).
fn tray_icon_cache() -> &'static std::sync::OnceLock<std::collections::HashMap<&'static str, Image<'static>>> {
    static CACHE: std::sync::OnceLock<std::collections::HashMap<&'static str, Image<'static>>> = std::sync::OnceLock::new();
    &CACHE
}

fn get_cached_icon(status: &str) -> Option<Image<'static>> {
    let cache = tray_icon_cache();
    let map = cache.get_or_init(|| {
        let mut m = std::collections::HashMap::new();
        for (key, bytes) in [
            ("idle", include_bytes!("../icons/tray-idle-22.png") as &[u8]),
            ("syncing", include_bytes!("../icons/tray-syncing-22.png") as &[u8]),
            ("error", include_bytes!("../icons/tray-error-22.png") as &[u8]),
            ("disconnected", include_bytes!("../icons/tray-disconnected-22.png") as &[u8]),
        ] {
            if let Ok(img) = image::load_from_memory(bytes) {
                let rgba = img.to_rgba8();
                let (w, h) = img.dimensions();
                m.insert(key, Image::new_owned(rgba.into_raw(), w, h));
            }
        }
        m
    });
    map.get(status).cloned()
}

/// Update the tray icon and tooltip based on sync status.
///
/// Uses a once-initialized cache to avoid re-decoding the same PNG icon
/// on every status change.
/// Status values: "idle", "syncing", "error", "disconnected"
pub fn set_tray_icon(app: &tauri::AppHandle, status: &str) {
    let tooltip = match status {
        "idle" => "Raguia Agent — synchronisé",
        "syncing" => "Raguia Agent — synchronisation en cours…",
        "error" => "Raguia Agent — erreur de connexion",
        "disconnected" => "Raguia Agent — non connecté",
        _ => return,
    };

    if let Some(state) = app.try_state::<AppState>() {
        if let Some(icon) = get_cached_icon(status) {
            let _ = state.tray.set_icon(Some(icon));
        }
        let _ = state.tray.set_tooltip(Some(tooltip));
    }
}

/// Tauri command: get recent logs for admin debug
#[tauri::command]
fn get_logs(count: Option<usize>) -> Vec<String> {
    LOG_CAPTURE.get_logs(count.unwrap_or(50))
}

/// Tauri command: toggle admin mode
#[tauri::command]
fn toggle_admin_mode(app: tauri::AppHandle) -> Result<bool, String> {
    let state = app
        .try_state::<AppState>()
        .ok_or_else(|| String::from("AppState non initialise"))?;
    let mut cfg = state
        .config_manager
        .load_config()
        .map_err(|e| e.to_string())?;
    cfg.admin_mode = !cfg.admin_mode;
    state
        .config_manager
        .save_config(&cfg)
        .map_err(|e| e.to_string())?;
    Ok(cfg.admin_mode)
}

/// Tauri command: check if admin mode is enabled
#[tauri::command]
fn is_admin_mode(app: tauri::AppHandle) -> bool {
    app.try_state::<AppState>()
        .and_then(|s| s.config_manager.load_config().ok())
        .map(|c| c.admin_mode)
        .unwrap_or(false)
}

/// Open the configuration wizard window.
fn show_wizard(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let window = WebviewWindowBuilder::new(app, "wizard", WebviewUrl::App("index.html".into()))
        .title("Configuration Raguia Agent")
        .inner_size(500.0, 620.0)
        .center()
        .resizable(false)
        .build()?;

    // Keep window above others for discoverability
    let _ = window.set_always_on_top(true);

    tracing::info!("Configuration wizard opened");
    Ok(())
}

/// Build the native tray menu
fn build_tray_menu(app: &tauri::AppHandle) -> Result<Menu<tauri::Wry>, tauri::Error> {
    let is_admin = config::Manager::new(app)
        .load_config()
        .map(|c| c.admin_mode)
        .unwrap_or(false);

    let sync_now = MenuItem::with_id(app, "sync_now", "Synchroniser maintenant", true, None::<&str>)?;
    let configure = MenuItem::with_id(app, "configure", "Se connecter / Reconnecter", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let open = MenuItem::with_id(app, "open", "Ouvrir Raguia", true, None::<&str>)?;
    let check_updates = MenuItem::with_id(app, "check_updates", "Verifier les mises a jour", true, None::<&str>)?;
    let about = MenuItem::with_id(app, "about", "A propos", true, None::<&str>)?;
    let separator2 = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Quitter", true, Some("cmd+q"))?;

    if is_admin {
        let admin_info = MenuItem::with_id(app, "admin_info", "Info Admin", true, None::<&str>)?;
        let sep_a1 = PredefinedMenuItem::separator(app)?;
        let dry_run = MenuItem::with_id(app, "dry_run", "Dry-Run: ON/OFF", true, None::<&str>)?;
        let toggle_autostart = MenuItem::with_id(app, "toggle_autostart", "Autostart: ON/OFF", true, None::<&str>)?;
        let sep_a2 = PredefinedMenuItem::separator(app)?;
        let endpoint = MenuItem::with_id(app, "endpoint", "Changer l'endpoint", true, None::<&str>)?;
        let reload_config = MenuItem::with_id(app, "reload_config", "Recharger config", true, None::<&str>)?;
        let test_api = MenuItem::with_id(app, "test_api", "Tester API", true, None::<&str>)?;
        let export_logs = MenuItem::with_id(app, "export_logs", "Exporter logs", true, None::<&str>)?;
        let sep_a3 = PredefinedMenuItem::separator(app)?;
        let config_path = MenuItem::with_id(app, "config_path", "Chemin config", true, None::<&str>)?;
        let show_queue = MenuItem::with_id(app, "show_queue", "File d'attente", true, None::<&str>)?;

        let admin_submenu = Submenu::with_items(app, "Admin", true, &[
            &admin_info,
            &sep_a1,
            &dry_run,
            &toggle_autostart,
            &sep_a2,
            &endpoint,
            &reload_config,
            &test_api,
            &export_logs,
            &sep_a3,
            &config_path,
            &show_queue,
        ])?;

        Menu::with_items(app, &[
            &sync_now,
            &configure,
            &separator,
            &open,
            &check_updates,
            &about,
            &admin_submenu,
            &separator2,
            &quit,
        ])
    } else {
        Menu::with_items(app, &[
            &sync_now,
            &configure,
            &separator,
            &open,
            &check_updates,
            &about,
            &separator2,
            &quit,
        ])
    }
}

/// Handle tray menu events
fn handle_tray_event(app: &tauri::AppHandle, event: tauri::menu::MenuEvent) {
    match event.id().as_ref() {
        "quit" => {
            tracing::info!("User requested quit");
            if let Some(state) = app.try_state::<AppState>() {
                state.stop_signal.store(true, Ordering::Release);
                state.wake_signal.notify_one();
                // Wait for the engine to finish (bounded to 5s, then force-exit)
                let app_clone = app.clone();
                let stopped = state.engine_stopped.clone();
                std::thread::spawn(move || {
                    let timeout = std::time::Duration::from_secs(5);
                    let fut = async {
                        tokio::time::timeout(timeout, stopped.notified()).await
                    };
                    // Block this OS thread (not the Tauri runtime) to wait for the engine.
                    let _ = tauri::async_runtime::block_on(fut);
                    tracing::info!("Engine stopped — exiting");
                    app_clone.exit(0);
                });
            } else {
                app.exit(0);
            }
        }
        "sync_now" => {
            if let Some(state) = app.try_state::<AppState>() {
                state.wake_signal.notify_one();
                tracing::info!("Manual sync triggered via tray");
            }
        }
        "configure" => {
            if let Err(e) = show_wizard(app) {
                tracing::error!("Failed to open configuration wizard: {}", e);
            }
        }
        "open" => {
            if let Some(state) = app.try_state::<AppState>() {
                if let Ok(cfg) = state.config_manager.load_config() {
                    let slug = &cfg.client_slug;
                    let portal_url = if slug.is_empty() {
                        cfg.api_url
                    } else {
                        format!("{}/portal/{}", cfg.api_url.trim_end_matches('/'), slug)
                    };
                    let _ = open::that(&portal_url);
                }
            }
        }
        "check_updates" => {
            let app_clone = app.clone();
            tauri::async_runtime::spawn(async move {
                let updater = match app_clone.updater() {
                    Ok(u) => u,
                    Err(e) => {
                        let _ = app_clone.dialog()
                            .message(format!("Updater non configuré : {}", e))
                            .title("Mise à jour")
                            .kind(MessageDialogKind::Error)
                            .show(|_| {});
                        return;
                    }
                };
                match updater.check().await {
                    Ok(Some(update)) => {
                        let version = update.version.clone();
                        let body = update.body.clone().unwrap_or_default();
                        let msg = if body.is_empty() {
                            format!("v{} disponible. Installer maintenant ?", version)
                        } else {
                            format!("v{} disponible.\n\n{}\n\nInstaller maintenant ?", version, body)
                        };
                        let app_for_install = app_clone.clone();
                        let _ = app_clone.dialog()
                            .message(msg)
                            .title("Mise à jour")
                            .kind(MessageDialogKind::Info)
                            .buttons(MessageDialogButtons::OkCancel)
                            .show(move |accepted| {
                                if !accepted {
                                    return;
                                }
                                let app = app_for_install.clone();
                                tauri::async_runtime::spawn(async move {
                                    match update.download_and_install(|_chunk, _total| {}, || {}).await {
                                        Ok(_) => {
                                            tracing::info!("Update v{} installed — exiting", version);
                                            app.exit(0);
                                        }
                                        Err(e) => {
                                            tracing::error!("Update install failed: {}", e);
                                            let _ = app.dialog()
                                                .message(format!("Échec installation : {}", e))
                                                .title("Mise à jour")
                                                .kind(MessageDialogKind::Error)
                                                .show(|_| {});
                                        }
                                    }
                                });
                            });
                    }
                    Ok(None) => {
                        let _ = app_clone.dialog()
                            .message(format!("Raguia Agent est à jour (v{})", env!("CARGO_PKG_VERSION")))
                            .title("Mise à jour")
                            .kind(MessageDialogKind::Info)
                            .show(|_| {});
                    }
                    Err(e) => {
                        let _ = app_clone.dialog()
                            .message(format!("Erreur vérification : {}", e))
                            .title("Mise à jour")
                            .kind(MessageDialogKind::Error)
                            .show(|_| {});
                    }
                }
            });
        }
        "about" => {
            let app_clone = app.clone();
            tauri::async_runtime::spawn(async move {
                let version = env!("CARGO_PKG_VERSION");
                let msg = format!("Raguia Agent v{}\n\nAgent de synchronisation de bureau\npour la plateforme Raguia.\n\n© Raguia", version);
                app_clone.dialog()
                    .message(msg).title("À propos")
                    .kind(tauri_plugin_dialog::MessageDialogKind::Info)
                    .show(|_| {});
            });
        }

        // ── Admin submenu items ──────────────────────────────

        "admin_info" => {
            let logs = LOG_CAPTURE.get_logs(100).join("\n");
            let logs_section = if logs.is_empty() { "Aucun log.".into() } else { logs };
            let queue_section = app.try_state::<AppState>()
                .and_then(|s| {
                    let stats = s.queue_store.get_stats().ok()?;
                    let stuck = s.queue_store.stuck_count().unwrap_or(0);
                    Some(format!("Attente:{}  Suppr:{}  Sync:{}  Bloque:{}", stats.pending, stats.pending_delete, stats.synced, stuck))
                })
                .unwrap_or_else(|| "File: N/A".into());
            let config_section = app.try_state::<AppState>()
                .and_then(|s| s.config_manager.load_config().ok())
                .map(|c| format!("API:{}  Slug:{}  Poll:{}s  Dry:{}", c.api_url, c.client_slug, c.poll_interval_secs, c.dry_run))
                .unwrap_or_else(|| "Config: N/A".into());
            let msg = format!("=== MODE ADMIN ===\n\n--- LOGS ---\n{}\n\n--- FILE ---\n{}\n\n--- CONFIG ---\n{}", logs_section, queue_section, config_section);
            let a = app.clone();
            tauri::async_runtime::spawn(async move {
                a.dialog().message(&msg).title("Admin Panel").kind(tauri_plugin_dialog::MessageDialogKind::Info).show(|_| {});
            });
        }
        "dry_run" => {
            if let Some(state) = app.try_state::<AppState>() {
                let mut cfg = state.config_manager.load_config().unwrap_or_default();
                cfg.dry_run = !cfg.dry_run;
                let msg = match state.config_manager.save_config(&cfg) {
                    Ok(_) => format!("Dry-Run: {}", if cfg.dry_run { "ACTIVÉ" } else { "DÉSACTIVÉ" }),
                    Err(e) => format!("Erreur: {}", e),
                };
                let a = app.clone();
                a.dialog().message(&msg).title("Dry-Run")
                    .kind(tauri_plugin_dialog::MessageDialogKind::Info)
                    .show(|_| {});
                tracing::info!("Dry-Run toggled to {}", cfg.dry_run);
            }
        }
        "toggle_autostart" => {
            let a = app.clone();
            let enabled = a.autolaunch().is_enabled().unwrap_or(false);
            let result = if enabled { a.autolaunch().disable() } else { a.autolaunch().enable() };
            let msg = match result {
                Ok(_) => format!("Autostart: {}", if !enabled { "ACTIVÉ" } else { "DÉSACTIVÉ" }),
                Err(e) => format!("Erreur: {}", e),
            };
            a.dialog().message(&msg).title("Autostart")
                .kind(tauri_plugin_dialog::MessageDialogKind::Info)
                .show(|_| {});
            tracing::info!("Autostart toggled (was enabled={})", enabled);
        }
        "endpoint" => {
            #[cfg(target_os = "macos")]
            {
                let ac = app.clone();
                std::thread::spawn(move || {
                    let current = ac.try_state::<AppState>()
                        .and_then(|s| s.config_manager.load_config().ok())
                        .map(|c| c.api_url).unwrap_or_default();
                    let script = format!(r#"display dialog "Nouvel endpoint API:" default answer "{}" buttons {{"Annuler", "OK"}} default button "OK""#, current);
                    let out = std::process::Command::new("osascript").args(["-e", &script]).output();
                    match out {
                        Ok(o) => {
                            let s = String::from_utf8_lossy(&o.stdout);
                            if !s.contains("button returned:OK") { return; }
                            let url = s.lines().find_map(|l| l.strip_prefix("text returned:")).unwrap_or("").trim().trim_end_matches('/').to_string();
                            if url.is_empty() || (!url.starts_with("http://") && !url.starts_with("https://")) {
                                ac.dialog().message("URL invalide. Doit commencer par http:// ou https://").title("Erreur")
                                    .kind(tauri_plugin_dialog::MessageDialogKind::Error).show(|_| {}); return;
                            }
                            if let Some(st) = ac.try_state::<AppState>() {
                                let mut cfg = match st.config_manager.load_config() {
                                    Ok(c) => c, Err(e) => {
                                        ac.dialog().message(format!("Erreur config: {}", e)).title("Erreur")
                                            .kind(tauri_plugin_dialog::MessageDialogKind::Error).show(|_| {}); return;
                                    }
                                };
                                cfg.api_url = url.clone();
                                if let Err(e) = st.config_manager.save_config(&cfg) {
                                    ac.dialog().message(format!("Erreur sauvegarde: {}", e)).title("Erreur")
                                        .kind(tauri_plugin_dialog::MessageDialogKind::Error).show(|_| {}); return;
                                }
                                st.api_client.set_api_url(&url);
                                ac.dialog().message(format!("Endpoint changé vers :\n{}", url)).title("Succès")
                                    .kind(tauri_plugin_dialog::MessageDialogKind::Info).show(|_| {});
                                tracing::info!("API endpoint changed to {}", url);
                            }
                        }
                        Err(e) => tracing::error!("Endpoint dialog failed: {}", e),
                    }
                });
            }
            #[cfg(not(target_os = "macos"))]
            {
                let ac = app.clone();
                tauri::async_runtime::spawn(async move {
                    ac.dialog()
                        .message("Modifiez l'endpoint API dans la configuration directement.")
                        .title("Changer l'endpoint")
                        .kind(tauri_plugin_dialog::MessageDialogKind::Info)
                        .show(|_| {});
                });
            }
        }
        "reload_config" => {
            if let Some(state) = app.try_state::<AppState>() {
                match state.config_manager.load_config() {
                    Ok(cfg) => {
                        let msg = format!("Config rechargée.\n\nAPI: {}\nSlug: {}\nWatch: {}\nDry-Run: {}\nAdmin: {}",
                            cfg.api_url, cfg.client_slug, cfg.root_path().display(), cfg.dry_run, cfg.admin_mode);
                        let a = app.clone();
                        a.dialog().message(&msg).title("Config rechargée")
                            .kind(tauri_plugin_dialog::MessageDialogKind::Info).show(|_| {});
                    }
                    Err(e) => {
                        let a = app.clone();
                        a.dialog().message(format!("Erreur: {}", e)).title("Erreur")
                            .kind(tauri_plugin_dialog::MessageDialogKind::Error).show(|_| {});
                    }
                }
            }
        }
        "test_api" => {
            let ac = app.clone();
            tauri::async_runtime::spawn(async move {
                let url = ac.try_state::<AppState>()
                    .and_then(|s| s.config_manager.load_config().ok())
                    .map(|c| format!("{}/health", c.api_url.trim_end_matches('/')))
                    .unwrap_or_default();
                let result = match reqwest::get(&url).await {
                    Ok(r) => format!("✅ {} {}\n\nStatus: {}", url, if r.status().is_success() { "OK" } else { "ERREUR" }, r.status()),
                    Err(e) => format!("❌ {}\n\n{}", url, e),
                };
                ac.dialog().message(&result).title("Test API")
                    .kind(tauri_plugin_dialog::MessageDialogKind::Info).show(|_| {});
            });
        }
        "export_logs" => {
            let ac = app.clone();
            std::thread::spawn(move || {
                let logs = LOG_CAPTURE.get_logs(500).join("\n");
                let path = ac.dialog().file()
                    .add_filter("Logs", &["txt", "log"])
                    .set_file_name("raguia-agent.log")
                    .blocking_save_file();
                if let Some(p) = path {
                    let save_path = p.as_path().map(|p| p.to_path_buf()).unwrap_or_else(|| {
                        let mut tmp = std::env::temp_dir();
                        tmp.push("raguia-agent-export.log");
                        tmp
                    });
                    match std::fs::write(&save_path, &logs) {
                        Ok(_) => ac.dialog().message("Logs exportés ✓").title("Succès")
                            .kind(tauri_plugin_dialog::MessageDialogKind::Info).show(|_| {}),
                        Err(e) => ac.dialog().message(format!("Erreur écriture: {}", e)).title("Erreur")
                            .kind(tauri_plugin_dialog::MessageDialogKind::Error).show(|_| {}),
                    }
                }
            });
        }
        "config_path" => {
            if app.try_state::<AppState>().is_some() {
                let m = config::Manager::new(app);
                let path = m.store_path().to_string_lossy().to_string();
                let msg = format!("Fichier config :\n{}\n\nÉditez-le manuellement puis utilisez « Recharger config »", path);
                app.dialog().message(&msg).title("Chemin config")
                    .kind(tauri_plugin_dialog::MessageDialogKind::Info).show(|_| {});
            }
        }
        "show_queue" => {
            if let Some(state) = app.try_state::<AppState>() {
                let msg = match state.queue_store.get_stats() {
                    Ok(s) => format!("File d'attente\n\nEn attente: {}\nSuppressions: {}\nSynced: {}\nBloqués: {}\n\nUtilisez « Recharger config » pour réinitialiser les bloqués.",
                        s.pending, s.pending_delete, s.synced, s.stuck),
                    Err(e) => format!("Erreur: {}", e),
                };
                app.dialog().message(&msg).title("File d'attente")
                    .kind(tauri_plugin_dialog::MessageDialogKind::Info).show(|_| {});
            }
        }
        _ => {}
    }
}

/// Handle tray icon click events
fn handle_tray_icon_event(_tray: &TrayIcon<tauri::Wry>, event: TrayIconEvent) {
    if let TrayIconEvent::Click {
        button: MouseButton::Left,
        button_state: MouseButtonState::Up,
        ..
    } = event
    {
        tracing::debug!("Tray icon left-clicked");
    }
}

/// Run the Raguia Agent application
static LOG_CAPTURE: LazyLock<log_capture::LogCapture> =
    LazyLock::new(log_capture::LogCapture::new);

pub fn run() {
    let log_capture = LOG_CAPTURE.clone();
    tracing_subscriber::fmt()
        .with_writer(log_capture)
        .with_ansi(false)
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "raguia_agent=info,tower_http=warn".into()),
        )
        .compact()
        .init();

    tracing::info!("Starting Raguia Agent v{}", env!("CARGO_PKG_VERSION"));

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_autostart::Builder::new().build())
        .setup(|app| {
            // ── Initialize core services ──
            let config_manager = Arc::new(config::Manager::new(app.handle()));

            // ── Initialize persistent log file (best-effort) ──
            {
                let log_dir = config_manager
                    .load_config()
                    .ok()
                    .map(|c| c.app_data_dir(app.handle()))
                    .unwrap_or_else(|| std::path::PathBuf::from("."));
                let log_path = log_dir.join("raguia-agent.log");
                if let Err(e) = LOG_CAPTURE.init_file(&log_path) {
                    tracing::warn!("Failed to init log file at {:?}: {}", log_path, e);
                } else {
                    tracing::info!("Log file: {:?}", log_path);
                }
            }

            // ── Force admin mode from env ──
            if std::env::var("RAGUIA_ADMIN").as_deref() == Ok("1") {
                let mut cfg = config_manager.load_config().unwrap_or_default();
                cfg.admin_mode = true;
                let _ = config_manager.save_config(&cfg);
                tracing::info!("Admin mode enabled via RAGUIA_ADMIN=1");
            }

            // ── Build tray menu ──
            let menu = build_tray_menu(app.handle())?;
            let tray = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("Raguia Agent")
                .on_menu_event(handle_tray_event)
                .on_tray_icon_event(handle_tray_icon_event)
                .build(app)?;
            let api_client = Arc::new(api::Client::new(config_manager.clone()));

            let db_dir = {
                let cfg = config_manager.load_config().unwrap_or_default();
                cfg.app_data_dir(app.handle())
            };
            let queue_store = match queue::Store::new(&db_dir) {
                Ok(s) => Arc::new(s),
                Err(e) => {
                    tracing::error!("Failed to init queue store: {}", e);
                    return Err(Box::new(std::io::Error::other(e.to_string())).into());
                }
            };

            let watcher = Arc::new(tokio::sync::Mutex::new(
                watcher::Watcher::new(config_manager.clone(), queue_store.clone()),
            ));

            // ── Sync engine signals ──
            let wake_signal = Arc::new(tokio::sync::Notify::new());
            let stop_signal = Arc::new(AtomicBool::new(false));
            let force_sync = Arc::new(AtomicBool::new(false));
            let engine_stopped = Arc::new(tokio::sync::Notify::new());

            // ── Check if configured → show wizard or start engine ──
            let is_configured = config_manager
                .load_config()
                .ok()
                .map(|c| !c.client_slug.is_empty())
                .unwrap_or(false);

            // Set initial tray icon
            let initial_status = if is_configured { "idle" } else { "disconnected" };
            {
                let rgba = decode_png_icon(initial_status);
                if let Some((data, w, h)) = rgba {
                    let icon = Image::new_owned(data, w, h);
                    let _ = tray.set_icon(Some(icon));
                }
                let _ = tray.set_tooltip(Some(match initial_status {
                    "idle" => "Raguia Agent — synchronisé",
                    _ => "Raguia Agent — non connecté",
                }));
            }

            // ── Manage state (includes the tray handle) ──
            app.manage(AppState {
                tray: tray.clone(),
                api_client: api_client.clone(),
                config_manager: config_manager.clone(),
                queue_store: queue_store.clone(),
                watcher: watcher.clone(),
                wake_signal: wake_signal.clone(),
                stop_signal: stop_signal.clone(),
                force_sync: force_sync.clone(),
                engine_stopped: engine_stopped.clone(),
            });

            if is_configured {
                // Start the sync engine background task
                let engine_wake = wake_signal.clone();
                let engine_stop = stop_signal.clone();
                let engine_force = force_sync.clone();
                let engine_app = app.handle().clone();
                let engine_config = config_manager.clone();
                let engine_api = api_client.clone();
                let engine_queue = queue_store.clone();
                let engine_watcher = watcher.clone();
                let engine_done = engine_stopped.clone();

                tauri::async_runtime::spawn(async move {
                    engine::run_sync_loop(
                        engine_app,
                        engine_config,
                        engine_api,
                        engine_queue,
                        engine_watcher,
                        engine_wake,
                        engine_stop,
                        engine_force,
                        engine_done,
                    )
                    .await;
                });

                tracing::info!("Sync engine started (existing configuration)");
            } else {
                // No configuration yet: show the setup wizard
                tracing::info!("No configuration found — launching setup wizard");
                if let Err(e) = show_wizard(app.handle()) {
                    tracing::error!("Failed to open wizard: {}", e);
                }
            }

            // Show tray menu on left-click (direct access to parameters)
            let _ = tray.set_show_menu_on_left_click(true);

            tracing::info!("Raguia Agent initialized successfully");
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            health,
            get_stats,
            sync_now,
            login,
            get_home_dir,
            get_os_kind,
            get_logs,
            toggle_admin_mode,
            is_admin_mode,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Raguia Agent");
}

/// Decode a PNG icon from embedded assets into RGBA pixel data.
/// Returns `(Vec<u8>, width, height)` or `None` on decode failure.
fn decode_png_icon(status: &str) -> Option<(Vec<u8>, u32, u32)> {
    let bytes: &[u8] = match status {
        "idle" => include_bytes!("../icons/tray-idle-22.png") as &[u8],
        "syncing" => include_bytes!("../icons/tray-syncing-22.png") as &[u8],
        "error" => include_bytes!("../icons/tray-error-22.png") as &[u8],
        "disconnected" => include_bytes!("../icons/tray-disconnected-22.png") as &[u8],
        _ => return None,
    };
    let img = image::load_from_memory(bytes).ok()?;
    let rgba = img.to_rgba8();
    let (w, h) = img.dimensions();
    Some((rgba.into_raw(), w, h))
}

#[allow(dead_code)]
#[inline]
fn icon_unused<T>(_: T) {}
