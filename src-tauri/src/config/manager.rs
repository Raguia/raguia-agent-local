use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use thiserror::Error;
use tauri::AppHandle;
use tauri::Manager as _;
use tauri_plugin_store::StoreExt;

/// Errors for configuration operations
#[derive(Error, Debug)]
pub enum ConfigError {
    #[error("Store error: {0}")]
    Store(String),

    #[error("Not authenticated — run the setup wizard first")]
    NotAuthenticated,
}

/// Core application configuration.
///
/// Mirrors the Python ``AgentConfig`` fields. Stored encrypted via ``tauri-plugin-store``
/// (AES-256-GCM on macOS Keychain / Windows Credential Manager / Linux Secret Service).
#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AppConfig {
    /// URL du portail Raguia (ex: https://raguia.valentin-fiess.fr)
    pub api_url: String,
    /// Slug identifiant le workspace client
    pub client_slug: String,
    /// Dossier parent contenant RAGUIA/
    pub watch_parent: PathBuf,
    /// Nom du dossier racine (ex: "RAGUIA")
    pub root_folder_name: String,
    /// Intervalle de poll sync-status (secondes)
    pub poll_interval_secs: u64,
    /// Stabilité avant upload (secondes)
    pub stability_secs: u64,
    /// Cooldown entre syncs auto (secondes)
    pub sync_cooldown_secs: u64,
    /// Seuil de fichiers pour déclencher une sync auto
    pub burst_threshold: u32,
    /// Max fichiers par cycle
    pub max_files_per_cycle: u32,
    /// Vérifier les mises à jour automatiquement
    pub auto_update: bool,
    /// Intervalle de vérif MAJ (heures)
    pub auto_update_check_hours: u64,
    /// Mode dry-run (ne pas uploader)
    pub dry_run: bool,
    /// Extensions de fichiers supportées (en minuscules, avec le point)
    pub supported_extensions: Vec<String>,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            api_url: "https://raguia.valentin-fiess.fr".into(),
            client_slug: String::new(),
            watch_parent: dirs_documents().unwrap_or_else(|| PathBuf::from(".")),
            root_folder_name: "RAGUIA".into(),
            poll_interval_secs: 30,
            stability_secs: 300,
            sync_cooldown_secs: 900,
            burst_threshold: 1,
            max_files_per_cycle: 100,
            auto_update: true,
            auto_update_check_hours: 24,
            dry_run: false,
            supported_extensions: vec![
                ".pdf", ".txt", ".md", ".docx", ".doc", ".xlsx", ".xls",
                ".csv", ".html", ".htm", ".pptx", ".png", ".jpg", ".jpeg", ".webp",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        }
    }
}

impl AppConfig {
    /// Chemin complet du dossier surveillé : watch_parent / root_folder_name
    pub fn root_path(&self) -> PathBuf {
        self.watch_parent.join(&self.root_folder_name)
    }

    /// Dossier des données applicatives (~/Library/Application Support/.../raguia-agent/)
    pub fn app_data_dir(&self, app_handle: &AppHandle) -> PathBuf {
        app_handle
            .path()
            .app_data_dir()
            .unwrap_or_else(|_| {
                // fallback pour le test / usage sans Tauri
                let home = std::env::var("HOME")
                    .map(PathBuf::from)
                    .unwrap_or_else(|_| PathBuf::from("."));
                home.join(".raguia")
            })
            .join("raguia-agent")
    }

    /// Vérifie si l'extension est supportée (comparaison insensible à la casse)
    pub fn is_extension_supported(&self, path: &std::path::Path) -> bool {
        path.extension()
            .and_then(|e| e.to_str())
            .map(|e| {
                let ext = format!(".{}", e.to_lowercase());
                self.supported_extensions
                    .iter()
                    .any(|s| s == &ext)
            })
            .unwrap_or(false)
    }
}

fn dirs_documents() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    let candidates = [
        PathBuf::from(&home).join("Documents"),
        PathBuf::from(&home).join("Library/Documents"),
    ];
    candidates
        .iter()
        .find(|p| p.exists())
        .or_else(|| {
            // fallback : retourner le premier même s'il n'existe pas
            candidates.first()
        })
        .cloned()
}

/// Manages application configuration and secrets via tauri-plugin-store.
///
/// Single source of truth for API URL, auth tokens, passwords, and watched paths.
/// Replaces the fragmented config system from the Python agent (config.yaml + keyring).
pub struct Manager {
    store_path: PathBuf,
    app_handle: AppHandle,
}

impl Manager {
    /// Create a new config manager backed by the Tauri encrypted store.
    pub fn new(app_handle: &AppHandle) -> Self {
        let store_path = app_handle
            .path()
            .app_config_dir()
            .expect("App config dir should be available")
            .join("raguia-config.json");

        Self {
            store_path,
            app_handle: app_handle.clone(),
        }
    }

    /// Access the underlying store
    fn store(&self) -> Result<std::sync::Arc<tauri_plugin_store::Store<tauri::Wry>>, ConfigError> {
        self.app_handle
            .store(&self.store_path)
            .map_err(|e| ConfigError::Store(e.to_string()))
    }

    // ─── Auth token ───────────────────────────────────────────

    /// Store the JWT auth token (encrypted at rest by tauri-plugin-store)
    pub fn set_token(&self, token: &str) -> Result<(), ConfigError> {
        let store = self.store()?;
        store.set("auth_token", serde_json::json!(token));
        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }

    /// Retrieve the JWT auth token
    pub fn get_token(&self) -> Option<String> {
        let store = self.store().ok()?;
        store.get("auth_token")?.as_str().map(String::from)
    }

    /// Remove the auth token (on logout or token expiry)
    pub fn clear_token(&self) -> Result<(), ConfigError> {
        let store = self.store()?;
        store.delete("auth_token");
        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }

    // ─── Agent password ───────────────────────────────────────

    /// Store the agent password (for auto-reconnect on 401)
    pub fn set_password(&self, password: &str) -> Result<(), ConfigError> {
        let store = self.store()?;
        store.set("agent_password", serde_json::json!(password));
        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }

    /// Retrieve the stored agent password
    pub fn get_password(&self) -> Option<String> {
        let store = self.store().ok()?;
        store.get("agent_password")?.as_str().map(String::from)
    }

    /// Clear the stored password
    pub fn clear_password(&self) -> Result<(), ConfigError> {
        let store = self.store()?;
        store.delete("agent_password");
        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }

    // ─── Config load / save ──────────────────────────────────

    /// Load the full application configuration from the encrypted store.
    ///
    /// Falls back to defaults for any missing field (graceful migration).
    pub fn load_config(&self) -> Result<AppConfig, ConfigError> {
        let store = self.store()?;
        let defaults = AppConfig::default();

        let api_url = store
            .get("api_url")
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or(defaults.api_url);

        let client_slug = store
            .get("client_slug")
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or(defaults.client_slug);

        let watch_parent_str = store
            .get("watch_parent")
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or_else(|| defaults.watch_parent.to_string_lossy().to_string());

        let root_folder_name = store
            .get("root_folder_name")
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or(defaults.root_folder_name);

        let supported_extensions = store
            .get("supported_extensions")
            .and_then(|v| serde_json::from_value::<Vec<String>>(v.clone()).ok())
            .unwrap_or(defaults.supported_extensions);

        Ok(AppConfig {
            api_url,
            client_slug,
            watch_parent: PathBuf::from(watch_parent_str),
            root_folder_name,
            poll_interval_secs: store
                .get("poll_interval_secs")
                .and_then(|v| v.as_u64())
                .unwrap_or(defaults.poll_interval_secs),
            stability_secs: store
                .get("stability_secs")
                .and_then(|v| v.as_u64())
                .unwrap_or(defaults.stability_secs),
            sync_cooldown_secs: store
                .get("sync_cooldown_secs")
                .and_then(|v| v.as_u64())
                .unwrap_or(defaults.sync_cooldown_secs),
            burst_threshold: store
                .get("burst_threshold")
                .and_then(|v| v.as_u64().map(|u| u as u32))
                .unwrap_or(defaults.burst_threshold),
            max_files_per_cycle: store
                .get("max_files_per_cycle")
                .and_then(|v| v.as_u64().map(|u| u as u32))
                .unwrap_or(defaults.max_files_per_cycle),
            auto_update: store
                .get("auto_update")
                .and_then(|v| v.as_bool())
                .unwrap_or(defaults.auto_update),
            auto_update_check_hours: store
                .get("auto_update_check_hours")
                .and_then(|v| v.as_u64())
                .unwrap_or(defaults.auto_update_check_hours),
            dry_run: store
                .get("dry_run")
                .and_then(|v| v.as_bool())
                .unwrap_or(defaults.dry_run),
            supported_extensions,
        })
    }

    /// Persist the full application configuration to the encrypted store.
    pub fn save_config(&self, config: &AppConfig) -> Result<(), ConfigError> {
        let store = self.store()?;

        store.set("api_url", serde_json::json!(config.api_url));
        store.set("client_slug", serde_json::json!(config.client_slug));
        store.set(
            "watch_parent",
            serde_json::json!(config.watch_parent.to_string_lossy().to_string()),
        );
        store.set("root_folder_name", serde_json::json!(config.root_folder_name));
        store.set(
            "poll_interval_secs",
            serde_json::json!(config.poll_interval_secs),
        );
        store.set(
            "stability_secs",
            serde_json::json!(config.stability_secs),
        );
        store.set(
            "sync_cooldown_secs",
            serde_json::json!(config.sync_cooldown_secs),
        );
        store.set(
            "burst_threshold",
            serde_json::json!(config.burst_threshold),
        );
        store.set(
            "max_files_per_cycle",
            serde_json::json!(config.max_files_per_cycle),
        );
        store.set("auto_update", serde_json::json!(config.auto_update));
        store.set(
            "auto_update_check_hours",
            serde_json::json!(config.auto_update_check_hours),
        );
        store.set("dry_run", serde_json::json!(config.dry_run));
        store.set(
            "supported_extensions",
            serde_json::to_value(&config.supported_extensions)
                .unwrap_or_default(),
        );

        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }
}
