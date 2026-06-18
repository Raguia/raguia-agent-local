use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::path::PathBuf;
use tauri::AppHandle;
use tauri::Manager as _;
use tauri_plugin_store::StoreExt;
use thiserror::Error;

/// Service name for macOS Keychain entries
const KEYCHAIN_SERVICE: &str = "com.raguia.agent";

/// Build the encrypted-store fallback key for a credential.
/// Prefixed with `_kc_fallback_` so it never collides with normal config keys.
fn fallback_key(cred_key: &str) -> String {
    format!("_kc_fallback_{}", cred_key)
}

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
/// Mirrors the Python ``AgentConfig`` fields. Non-sensitive fields stored in
/// raguia-config.json; secrets (auth_token, agent_password) stored in macOS Keychain.
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
    #[serde(rename = "_sk")]
    pub admin_mode: bool,
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
            admin_mode: false,
            supported_extensions: vec![
                ".pdf", ".txt", ".md", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".html", ".htm",
                ".pptx", ".png", ".jpg", ".jpeg", ".webp",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        }
    }
}

impl AppConfig {
    pub fn normalized(mut self) -> Self {
        let defaults = Self::default();

        self.api_url = self.api_url.trim().trim_end_matches('/').to_string();
        if self.api_url.is_empty() {
            self.api_url = defaults.api_url;
        }

        self.client_slug = self.client_slug.trim().to_string();
        let mut root_name = self
            .root_folder_name
            .trim()
            .trim_matches(|c| c == '/' || c == '\\')
            .chars()
            .map(|c| {
                if c.is_control()
                    || matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|')
                {
                    '_'
                } else {
                    c
                }
            })
            .collect::<String>()
            .trim()
            .to_string();
        if root_name == "." || root_name == ".." {
            root_name.clear();
        }
        self.root_folder_name = if root_name.is_empty() {
            defaults.root_folder_name
        } else {
            root_name
        };

        if self.watch_parent.as_os_str().is_empty() {
            self.watch_parent = defaults.watch_parent;
        }

        self.poll_interval_secs = self.poll_interval_secs.clamp(5, 3600);
        self.stability_secs = self.stability_secs.clamp(1, 3600);
        self.sync_cooldown_secs = self.sync_cooldown_secs.min(86_400);
        self.burst_threshold = self.burst_threshold.clamp(1, 10_000);
        self.max_files_per_cycle = self.max_files_per_cycle.clamp(1, 500);
        self.auto_update_check_hours = self.auto_update_check_hours.clamp(1, 168);

        let mut seen = BTreeSet::new();
        let extensions: Vec<String> = self
            .supported_extensions
            .into_iter()
            .filter_map(|ext| {
                let ext = ext.trim().trim_start_matches('.').to_lowercase();
                if ext.is_empty() {
                    return None;
                }
                let ext = format!(".{}", ext);
                if seen.insert(ext.clone()) {
                    Some(ext)
                } else {
                    None
                }
            })
            .collect();
        self.supported_extensions = if extensions.is_empty() {
            defaults.supported_extensions
        } else {
            extensions
        };

        self
    }

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
                    .or_else(|_| std::env::var("USERPROFILE"))
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
                self.supported_extensions.iter().any(|s| s == &ext)
            })
            .unwrap_or(false)
    }
}

fn dirs_documents() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        let userprofile = std::env::var("USERPROFILE").ok()?;
        Some(PathBuf::from(userprofile).join("Documents"))
    }
    #[cfg(not(target_os = "windows"))]
    {
        let home = std::env::var("HOME").ok()?;
        let candidates = [
            PathBuf::from(&home).join("Documents"),
            PathBuf::from(&home).join("Library/Documents"),
        ];
        candidates
            .iter()
            .find(|p| p.exists())
            .or_else(|| candidates.first())
            .cloned()
    }
}

/// Manages application configuration and secrets.
///
/// Secrets (auth_token, agent_password) stored in macOS Keychain via `keyring` crate.
/// Non-sensitive config persisted in raguia-config.json via tauri-plugin-store.
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

    /// Path to the config store file (for display/debug)
    pub fn store_path(&self) -> &std::path::Path {
        &self.store_path
    }

    /// Access the underlying store
    fn store(&self) -> Result<std::sync::Arc<tauri_plugin_store::Store<tauri::Wry>>, ConfigError> {
        self.app_handle
            .store(&self.store_path)
            .map_err(|e| ConfigError::Store(e.to_string()))
    }

    // ─── Keychain helpers ─────────────────────────────────────

    /// Try the OS keychain/credential manager. On failure (locked vault,
    /// restricted service, etc.), transparently fall back to the encrypted
    /// Tauri store so the agent remains functional.
    fn keychain_set(&self, key: &str, value: &str) -> Result<(), ConfigError> {
        match keyring::Entry::new(KEYCHAIN_SERVICE, key) {
            Ok(entry) => match entry.set_password(value) {
                Ok(()) => {
                    // Also clear the fallback copy
                    if let Ok(store) = self.store() {
                        store.delete(fallback_key(key));
                        let _ = store.save();
                    }
                    Ok(())
                }
                Err(e) => {
                    tracing::warn!(
                        "Keychain set failed for {} ({}), using encrypted store fallback",
                        key,
                        e
                    );
                    self.store_fallback_set(key, value)
                }
            },
            Err(e) => {
                tracing::warn!(
                    "Keychain entry init failed for {} ({}), using encrypted store fallback",
                    key,
                    e
                );
                self.store_fallback_set(key, value)
            }
        }
    }

    fn keychain_get(&self, key: &str) -> Option<String> {
        if let Ok(entry) = keyring::Entry::new(KEYCHAIN_SERVICE, key) {
            if let Ok(v) = entry.get_password() {
                return Some(v);
            }
        }
        // Bug 3: fallback to encrypted store if keychain unavailable
        self.store_fallback_get(key)
    }

    fn keychain_delete(&self, key: &str) -> Result<(), ConfigError> {
        // Try to clean both locations; ignore keychain errors so we still
        // wipe the fallback copy.
        if let Ok(entry) = keyring::Entry::new(KEYCHAIN_SERVICE, key) {
            let _ = entry.delete_password();
        }
        if let Ok(store) = self.store() {
            store.delete(fallback_key(key));
            let _ = store.save();
        }
        Ok(())
    }

    fn store_fallback_set(&self, key: &str, value: &str) -> Result<(), ConfigError> {
        let store = self.store()?;
        store.set(fallback_key(key), serde_json::json!(value));
        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }

    fn store_fallback_get(&self, key: &str) -> Option<String> {
        let store = self.store().ok()?;
        store
            .get(fallback_key(key))
            .and_then(|v| v.as_str().map(String::from))
    }

    // ─── Auth token (macOS Keychain) ──────────────────────────

    /// Store the JWT auth token in the macOS Keychain
    pub fn set_token(&self, token: &str) -> Result<(), ConfigError> {
        self.keychain_set("auth_token", token)?;
        // Also clear from old store location
        if let Ok(store) = self.store() {
            store.delete("auth_token");
            let _ = store.save();
        }
        Ok(())
    }

    /// Retrieve the JWT auth token from Keychain (with store fallback)
    pub fn get_token(&self) -> Option<String> {
        self.keychain_get("auth_token").or_else(|| {
            // Migration from old store location
            let token = self
                .store()
                .ok()?
                .get("auth_token")?
                .as_str()
                .map(String::from);
            if let Some(ref t) = token {
                let _ = self.keychain_set("auth_token", t);
                if let Ok(store) = self.store() {
                    store.delete("auth_token");
                    let _ = store.save();
                }
            }
            token
        })
    }

    /// Remove the auth token from Keychain
    pub fn clear_token(&self) -> Result<(), ConfigError> {
        let _ = self.keychain_delete("auth_token");
        let store = self.store()?;
        store.delete("auth_token");
        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }

    // ─── Agent password (macOS Keychain) ──────────────────────

    /// Store the agent password in the macOS Keychain
    pub fn set_password(&self, password: &str) -> Result<(), ConfigError> {
        self.keychain_set("agent_password", password)?;
        if let Ok(store) = self.store() {
            store.delete("agent_password");
            let _ = store.save();
        }
        Ok(())
    }

    /// Retrieve the stored agent password from Keychain (with store fallback)
    pub fn get_password(&self) -> Option<String> {
        self.keychain_get("agent_password").or_else(|| {
            let pw = self
                .store()
                .ok()?
                .get("agent_password")?
                .as_str()
                .map(String::from);
            if let Some(ref p) = pw {
                let _ = self.keychain_set("agent_password", p);
                if let Ok(store) = self.store() {
                    store.delete("agent_password");
                    let _ = store.save();
                }
            }
            pw
        })
    }

    /// Clear the stored password from Keychain
    pub fn clear_password(&self) -> Result<(), ConfigError> {
        let _ = self.keychain_delete("agent_password");
        let store = self.store()?;
        store.delete("agent_password");
        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }

    // ─── Config load / save ──────────────────────────────────

    /// Read _sk directly from the store JSON file to survive store rewrites
    fn read_sk_from_file(&self) -> Option<bool> {
        let content = std::fs::read_to_string(&self.store_path).ok()?;
        let json: serde_json::Value = serde_json::from_str(&content).ok()?;
        json.get("_sk").and_then(|v| v.as_bool())
    }

    /// Load the full application configuration from the store.
    ///
    /// Falls back to defaults for any missing field (graceful migration).
    pub fn load_config(&self) -> Result<AppConfig, ConfigError> {
        let store = self.store()?;
        // Force-load from disk so manual edits (_sk, etc.) are picked up
        let _ = store.reload();
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
            admin_mode: self
                .read_sk_from_file()
                .or_else(|| store.get("_sk").and_then(|v| v.as_bool()))
                .unwrap_or(defaults.admin_mode),
            supported_extensions,
        }
        .normalized())
    }

    /// Persist the full application configuration to the encrypted store.
    pub fn save_config(&self, config: &AppConfig) -> Result<(), ConfigError> {
        let store = self.store()?;
        let config = config.clone().normalized();

        store.set("api_url", serde_json::json!(config.api_url));
        store.set("client_slug", serde_json::json!(config.client_slug));
        store.set(
            "watch_parent",
            serde_json::json!(config.watch_parent.to_string_lossy().to_string()),
        );
        store.set(
            "root_folder_name",
            serde_json::json!(config.root_folder_name),
        );
        store.set(
            "poll_interval_secs",
            serde_json::json!(config.poll_interval_secs),
        );
        store.set("stability_secs", serde_json::json!(config.stability_secs));
        store.set(
            "sync_cooldown_secs",
            serde_json::json!(config.sync_cooldown_secs),
        );
        store.set("burst_threshold", serde_json::json!(config.burst_threshold));
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
        store.set("_sk", serde_json::json!(config.admin_mode));
        store.set(
            "supported_extensions",
            serde_json::to_value(&config.supported_extensions).unwrap_or_default(),
        );

        store.save().map_err(|e| ConfigError::Store(e.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::AppConfig;
    use std::path::PathBuf;

    #[test]
    fn normalized_clamps_cost_and_sync_settings() {
        let cfg = AppConfig {
            api_url: " https://example.test/ ".into(),
            watch_parent: PathBuf::new(),
            root_folder_name: " /RAGUIA/ ".into(),
            poll_interval_secs: 0,
            stability_secs: 0,
            sync_cooldown_secs: 999_999,
            burst_threshold: 0,
            max_files_per_cycle: 0,
            auto_update_check_hours: 0,
            supported_extensions: vec!["PDF".into(), ".pdf".into(), "  txt ".into(), "".into()],
            ..AppConfig::default()
        }
        .normalized();

        assert_eq!(cfg.api_url, "https://example.test");
        assert_eq!(cfg.root_folder_name, "RAGUIA");
        assert_eq!(cfg.poll_interval_secs, 5);
        assert_eq!(cfg.stability_secs, 1);
        assert_eq!(cfg.sync_cooldown_secs, 86_400);
        assert_eq!(cfg.burst_threshold, 1);
        assert_eq!(cfg.max_files_per_cycle, 1);
        assert_eq!(cfg.auto_update_check_hours, 1);
        assert_eq!(cfg.supported_extensions, vec![".pdf", ".txt"]);
        assert!(!cfg.watch_parent.as_os_str().is_empty());
    }

    #[test]
    fn normalized_restores_safe_root_and_extensions() {
        let cfg = AppConfig {
            api_url: "".into(),
            root_folder_name: " / ".into(),
            supported_extensions: vec![],
            ..AppConfig::default()
        }
        .normalized();

        assert_eq!(cfg.api_url, AppConfig::default().api_url);
        assert_eq!(cfg.root_folder_name, "RAGUIA");
        assert_eq!(
            cfg.supported_extensions,
            AppConfig::default().supported_extensions
        );
    }

    #[test]
    fn normalized_keeps_root_folder_name_as_single_directory() {
        let nested = AppConfig {
            root_folder_name: " RAGUIA/Archive\\2026 ".into(),
            ..AppConfig::default()
        }
        .normalized();
        let parent = AppConfig {
            root_folder_name: "..".into(),
            ..AppConfig::default()
        }
        .normalized();

        assert_eq!(nested.root_folder_name, "RAGUIA_Archive_2026");
        assert_eq!(parent.root_folder_name, "RAGUIA");
    }

    #[test]
    fn normalized_removes_windows_invalid_folder_chars() {
        let cfg = AppConfig {
            root_folder_name: r#"RA:GU*IA?"<>|"#.into(),
            ..AppConfig::default()
        }
        .normalized();

        assert_eq!(cfg.root_folder_name, "RA_GU_IA_____");
    }
}
