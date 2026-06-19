use crate::config;
use crate::queue;
use notify::{
    Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher as NotifyWatcher,
};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::sync::Arc;
use thiserror::Error;

/// Watcher errors
#[derive(Error, Debug)]
pub enum WatcherError {
    #[error("Notify error: {0}")]
    Notify(#[from] notify::Error),

    #[error("Config error: {0}")]
    Config(String),
}

/// The file system watcher that feeds changes into the sync queue.
///
/// Uses ``notify`` crate with recommended backend:
/// - macOS: FSEvents (kernel-level, efficient)
/// - Linux: inotify
/// - Windows: ReadDirectoryChangesW
///
/// Design vs Python ``watcher.py``:
/// - Same ``_should_ignore`` logic for temp/hidden files
/// - Same extension filtering
/// - But typed errors and proper Send+Sync
pub struct Watcher {
    config: Arc<config::Manager>,
    queue: Arc<queue::Store>,
    inner: Option<RecommendedWatcher>,
    root: PathBuf,
}

impl Watcher {
    /// Create a new file watcher (starts in paused state).
    pub fn new(config: Arc<config::Manager>, queue: Arc<queue::Store>) -> Self {
        let root = config
            .load_config()
            .map(|c| c.root_path())
            .unwrap_or_else(|_| PathBuf::from("."));

        Self {
            config,
            queue,
            inner: None,
            root,
        }
    }

    /// Start watching the configured directory.
    pub fn start(&mut self) -> Result<(), WatcherError> {
        let app_config = self
            .config
            .load_config()
            .map_err(|e| WatcherError::Config(e.to_string()))?;

        let root = app_config.root_path();
        let queue = self.queue.clone();
        let root_for_thread = root.clone();
        let extensions: Vec<String> = app_config.supported_extensions.clone();

        let (tx, rx) = mpsc::channel::<Result<Event, notify::Error>>();

        let mut watcher = RecommendedWatcher::new(tx, Config::default())?;
        watcher.watch(&root, RecursiveMode::Recursive)?;

        // Spawn a thread to process file system events
        std::thread::Builder::new()
            .name("raguia-watcher".into())
            .spawn(move || {
                // Bug 6: exponential backoff on repeated errors
                // (e.g. Windows ReadDirectoryChangesW when the dir is renamed)
                let mut consecutive_errors: u32 = 0;
                let mut backoff_ms: u64 = 0;
                for event in rx {
                    if backoff_ms > 0 {
                        std::thread::sleep(std::time::Duration::from_millis(backoff_ms));
                    }
                    let queue = queue.clone();
                    let root = root_for_thread.clone();
                    let exts = extensions.clone();
                    // Catch panics in the handler so the watcher thread survives
                    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || {
                        match event {
                            Ok(event) => {
                                Self::handle_notify_event(&queue, &event, &root, &exts);
                            }
                            Err(e) => {
                                tracing::error!("Watcher error: {}", e);
                            }
                        }
                    }));
                    match result {
                        Ok(()) => {
                            if consecutive_errors > 0 {
                                tracing::info!(
                                    "Watcher recovered after {} error(s)",
                                    consecutive_errors
                                );
                            }
                            consecutive_errors = 0;
                            backoff_ms = 0;
                        }
                        Err(e) => {
                            consecutive_errors = consecutive_errors.saturating_add(1);
                            backoff_ms = (1000u64 << consecutive_errors.min(5)).min(30_000);
                            let msg = e.downcast_ref::<String>().map(|s| s.as_str())
                                .or_else(|| e.downcast_ref::<&str>().copied())
                                .unwrap_or("unknown panic");
                            tracing::error!(
                                "Watcher handler panicked and recovered: {} (backoff {}ms, attempt #{})",
                                msg, backoff_ms, consecutive_errors
                            );
                        }
                    }
                }
            })
            .expect("Failed to spawn watcher thread");

        self.inner = Some(watcher);
        self.root = root.clone();
        tracing::info!("File watcher started on: {:?}", root);
        Ok(())
    }

    /// Stop the file watcher.
    pub fn stop(&mut self) {
        self.inner = None;
        tracing::info!("File watcher stopped");
    }

    /// Process a single notify event and enqueue changed files.
    ///
    /// Matches Python ``watcher._on_fs_event()`` + ``_should_ignore()`` logic.
    /// Filters by supported extensions from the current config.
    fn handle_notify_event(
        queue: &queue::Store,
        event: &Event,
        root: &Path,
        supported_extensions: &[String],
    ) {
        // Determine event type
        let event_type = match event.kind {
            EventKind::Remove(_) => "deleted",
            EventKind::Create(_) | EventKind::Modify(_) => "modified",
            _ => return, // Skip metadata-only events
        };

        for path in &event.paths {
            // Skip hidden files, temp files, OS specials (Python _should_ignore)
            if should_ignore(path) {
                continue;
            }

            // Filter by supported extensions (Python _on_fs_event: path.suffix.lower not in cfg.supported_extensions)
            let ext = path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e.to_lowercase());
            let ext_match = ext
                .as_ref()
                .map(|e| {
                    let with_dot = format!(".{}", e);
                    supported_extensions.iter().any(|s| s == &with_dot)
                })
                .unwrap_or(false);
            if !ext_match {
                continue;
            }

            // Get relative path from root
            let rel_path = match path.strip_prefix(root) {
                Ok(rel) => rel.to_string_lossy().to_string(),
                Err(_) => {
                    // Path outside root — ignore
                    tracing::debug!("Path outside root: {:?}", path);
                    continue;
                }
            };

            // Skip if rel_path is empty (root itself)
            if rel_path.is_empty() {
                continue;
            }

            let abs_path = path.to_string_lossy().to_string();

            // Enqueue: queue handles dedup via UNIQUE rel_path
            if let Err(e) = queue.enqueue(&rel_path, &abs_path, event_type) {
                tracing::warn!("Failed to enqueue {}: {}", rel_path, e);
            } else {
                tracing::debug!("Enqueued {}: {}", event_type, rel_path);
            }
        }
    }

    /// Get the configured root directory
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Reload config and update watch directory
    pub fn reload(&mut self) -> Result<(), WatcherError> {
        self.stop();
        if let Some(old_watcher) = self.inner.take() {
            drop(old_watcher);
        }
        self.start()
    }
}

/// Should this file path be ignored?
///
/// Mirrors Python ``watcher._should_ignore()``
fn should_ignore(path: &Path) -> bool {
    path.file_name()
        .and_then(|n| n.to_str())
        .map(|n| {
            n.starts_with('.')
                || n.starts_with("~$")
                || n.ends_with(".tmp")
                || n.ends_with('~')
                || n.ends_with(".swp")
                || n == "Thumbs.db"
                || n == ".DS_Store"
        })
        .unwrap_or(false)
}
