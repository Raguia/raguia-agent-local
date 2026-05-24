// Raguia Agent — core library
//
// Tray-only desktop sync agent for the Raguia SaaS platform.
// Watches local directories, queues file changes, and syncs them to the
// Raguia API server. Built with Tauri 2 for native cross-platform support.

mod api;
mod config;
mod engine;
mod queue;
mod updater;
mod watcher;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
    Manager, WebviewUrl, WebviewWindowBuilder,
};
use image::GenericImageView;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_autostart::ManagerExt;

/// Shared application state accessible from Tauri commands and tray handlers.
pub struct AppState {
    pub tray: TrayIcon<tauri::Wry>,
    pub api_client: Arc<api::Client>,
    pub config_manager: Arc<config::Manager>,
    pub queue_store: Arc<queue::Store>,
    pub watcher: Arc<tokio::sync::Mutex<watcher::Watcher>>,
    pub wake_signal: Arc<tokio::sync::Notify>,
    pub stop_signal: Arc<AtomicBool>,
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
    state.wake_signal.notify_one();
    tracing::info!("Force sync triggered");
    Ok(())
}

/// Tauri command: return the user's home directory path
#[tauri::command]
fn get_home_dir() -> String {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| "/home".into())
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

/// Update the tray icon and tooltip based on sync status.
///
/// Decodes the correct PNG icon from embedded assets and sets it on the tray.
/// Status values: "idle", "syncing", "error", "disconnected"
pub fn set_tray_icon(app: &tauri::AppHandle, status: &str) {
    let icon_bytes: &[u8] = match status {
        "idle" => include_bytes!("../icons/tray-idle-22.png") as &[u8],
        "syncing" => include_bytes!("../icons/tray-syncing-22.png") as &[u8],
        "error" => include_bytes!("../icons/tray-error-22.png") as &[u8],
        "disconnected" => include_bytes!("../icons/tray-disconnected-22.png") as &[u8],
        _ => return,
    };

    let tooltip = match status {
        "idle" => "Raguia Agent — synchronisé",
        "syncing" => "Raguia Agent — synchronisation en cours…",
        "error" => "Raguia Agent — erreur de connexion",
        "disconnected" => "Raguia Agent — non connecté",
        _ => return,
    };

    if let Some(state) = app.try_state::<AppState>() {
        // Decode PNG to RGBA using the `image` crate
        if let Ok(img) = image::load_from_memory(icon_bytes) {
            let rgba = img.to_rgba8();
            let (w, h) = img.dimensions();
            let icon = Image::new_owned(rgba.into_raw(), w, h);
            let _ = state.tray.set_icon(Some(icon));
        }
        let _ = state.tray.set_tooltip(Some(tooltip));
    }
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
    let sync_now = MenuItem::with_id(
        app,
        "sync_now",
        "Synchroniser maintenant",
        true,
        None::<&str>,
    )?;
    let configure = MenuItem::with_id(
        app,
        "configure",
        "Se connecter / Reconnecter",
        true,
        None::<&str>,
    )?;
    let separator = PredefinedMenuItem::separator(app)?;
    let open = MenuItem::with_id(app, "open", "Ouvrir Raguia", true, None::<&str>)?;
    let check_updates = MenuItem::with_id(
        app,
        "check_updates",
        "Vérifier les mises à jour",
        true,
        None::<&str>,
    )?;
    let about = MenuItem::with_id(app, "about", "À propos", true, None::<&str>)?;
    let separator2 = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Quitter", true, Some("cmd+q"))?;

    let menu = Menu::with_items(
        app,
        &[&sync_now, &configure, &separator, &open, &check_updates, &about, &separator2, &quit],
    )?;
    Ok(menu)
}

/// Handle tray menu events
fn handle_tray_event(app: &tauri::AppHandle, event: tauri::menu::MenuEvent) {
    match event.id().as_ref() {
        "quit" => {
            tracing::info!("User requested quit");
            if let Some(state) = app.try_state::<AppState>() {
                state.stop_signal.store(true, Ordering::Release);
                state.wake_signal.notify_one();
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
            app.exit(0);
        }
        "sync_now" => {
            if let Some(state) = app.try_state::<AppState>() {
                state.wake_signal.notify_one();
                tracing::info!("Manual sync triggered via tray");
            }
        }
        "configure" => {
            tracing::info!("Open configuration requested");
            if let Err(e) = show_wizard(app) {
                tracing::error!("Failed to open configuration wizard: {}", e);
            }
        }
        "open" => {
            tracing::info!("Open Raguia requested");
            if let Some(state) = app.try_state::<AppState>() {
                let cfg = state.config_manager.load_config().ok();
                if let Some(url) = cfg.map(|c| c.api_url) {
                    let _ = open::that(&url);
                }
            }
        }
        "check_updates" => {
            tracing::info!("Update check requested");
            let app_clone = app.clone();
            tauri::async_runtime::spawn(async move {
                let msg = crate::updater::check_and_show_dialog(&app_clone).await;
                app_clone.dialog()
                    .message(msg)
                    .title("Mise à jour")
                    .kind(tauri_plugin_dialog::MessageDialogKind::Info)
                    .show(|_| {});
            });
        }
        "about" => {
            tracing::info!("About requested");
            let app_clone = app.clone();
            tauri::async_runtime::spawn(async move {
                let version = env!("CARGO_PKG_VERSION");
                let msg = format!(
                    "Raguia Agent v{}\n\nAgent de synchronisation de bureau\npour la plateforme Raguia.\n\n© Raguia",
                    version
                );
                app_clone.dialog()
                    .message(msg)
                    .title("À propos")
                    .kind(tauri_plugin_dialog::MessageDialogKind::Info)
                    .show(|_| {});
            });
        }
        _ => {}
    }
}

/// Handle tray icon click events
fn handle_tray_icon_event(tray: &TrayIcon<tauri::Wry>, event: TrayIconEvent) {
    if let TrayIconEvent::Click {
        button: MouseButton::Left,
        button_state: MouseButtonState::Up,
        ..
    } = event
    {
        tracing::debug!("Tray icon left-clicked");
        if let Err(e) = tray.set_tooltip(Some("Raguia Agent — clic droit pour le menu")) {
            tracing::warn!("Failed to update tooltip: {}", e);
        }
    }
}

/// Run the Raguia Agent application
pub fn run() {
    // Initialize structured logging
    tracing_subscriber::fmt()
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
            // ── Build tray menu ──
            let menu = build_tray_menu(app.handle())?;
            let tray = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("Raguia Agent")
                .on_menu_event(handle_tray_event)
                .on_tray_icon_event(handle_tray_icon_event)
                .build(app)?;

            // ── Initialize core services ──
            let config_manager = Arc::new(config::Manager::new(app.handle()));
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
            });

            if is_configured {
                // Start the sync engine background task
                let engine_wake = wake_signal.clone();
                let engine_stop = stop_signal.clone();
                let engine_app = app.handle().clone();
                let engine_config = config_manager.clone();
                let engine_api = api_client.clone();
                let engine_queue = queue_store.clone();
                let engine_watcher = watcher.clone();

                tauri::async_runtime::spawn(async move {
                    engine::run_sync_loop(
                        engine_app,
                        engine_config,
                        engine_api,
                        engine_queue,
                        engine_watcher,
                        engine_wake,
                        engine_stop,
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

            // Hide tray menu on left-click (maps to right-click menu only)
            let _ = tray.set_show_menu_on_left_click(false);

            tracing::info!("Raguia Agent initialized successfully");
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            health,
            get_stats,
            sync_now,
            login,
            get_home_dir,
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
