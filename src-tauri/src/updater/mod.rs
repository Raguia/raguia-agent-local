use tauri::AppHandle;
use tauri_plugin_updater::UpdaterExt;

/// Result of an update check
#[derive(Debug, Clone)]
pub enum UpdateStatus {
    /// No update available
    UpToDate,

    /// New version available
    Available {
        version: String,
        release_notes: Option<String>,
        mandatory: bool,
    },

    /// Update plugin not configured (pubkey/endpoints missing)
    NotConfigured,

    /// Error during update check (network, server, etc.)
    Error(String),
}

/// Manages application updates using the Tauri updater plugin.
///
/// Design decisions vs Python agent:
/// - No PID file race: updater plugin swaps binary atomically
/// - No shell scripts: pure Rust, cryptographically signed
/// - Retry + rollback built into plugin
pub struct Updater {
    app_handle: AppHandle,
    current_version: String,
}

impl Updater {
    /// Create a new updater with a reference to the app handle.
    pub fn new(app_handle: AppHandle) -> Self {
        Self {
            app_handle,
            current_version: env!("CARGO_PKG_VERSION").to_string(),
        }
    }

    /// Check for updates using the Tauri updater plugin.
    ///
    /// The plugin fetches the configured endpoint, compares versions,
    /// and returns the latest update info if available.
    pub async fn check_for_update(&self) -> UpdateStatus {
        let updater = match self.app_handle.updater() {
            Ok(u) => u,
            Err(e) => {
                tracing::warn!("Updater plugin not available: {}", e);
                return UpdateStatus::NotConfigured;
            }
        };

        match updater.check().await {
            Ok(Some(update)) => {
                tracing::info!(
                    "Update available: {} (current: {})",
                    update.version,
                    self.current_version
                );
                let version = update.version.clone();
                UpdateStatus::Available {
                    version: version.clone(),
                    release_notes: update.body.clone().or(Some(version)),
                    mandatory: false,
                }
            }
            Ok(None) => {
                tracing::debug!("No update available (current: {})", self.current_version);
                UpdateStatus::UpToDate
            }
            Err(e) => {
                tracing::warn!("Update check failed: {}", e);
                UpdateStatus::Error(e.to_string())
            }
        }
    }

    /// Check for update silently (for auto-update mode).
    /// Returns true if an update is available.
    pub async fn check_silent(&self) -> bool {
        match self.check_for_update().await {
            UpdateStatus::Available { .. } => {
                tracing::info!("Update available — dialog will be shown by the plugin");
                true
            }
            _ => false,
        }
    }
}

/// Check for an update and show a status message (for tray menu action).
pub async fn check_and_show_dialog(app: &AppHandle) -> String {
    let updater = Updater::new(app.clone());
    match updater.check_for_update().await {
        UpdateStatus::UpToDate => {
            format!("Raguia Agent est à jour (v{})", env!("CARGO_PKG_VERSION"))
        }
        UpdateStatus::Available { version, release_notes, mandatory } => {
            let notes = release_notes
                .as_deref()
                .unwrap_or("Aucune note de version disponible");
            let tag = if mandatory { " (obligatoire)" } else { "" };
            format!(
                "Nouvelle version disponible : v{}{}\n\n{}",
                version, tag, notes
            )
        }
        UpdateStatus::NotConfigured => {
            "Vérification des mises à jour non configurée (clé de signature manquante)\n\nConfigurez la clé publique et les endpoints dans tauri.conf.json.".into()
        }
        UpdateStatus::Error(e) => {
            format!("Erreur lors de la vérification des mises à jour :\n{}", e)
        }
    }
}
