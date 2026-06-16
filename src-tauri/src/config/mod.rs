// Configuration and secret management
//
// Uses tauri-plugin-store for encrypted on-disk persistence.
// Single source of truth for API URL, auth tokens, watched paths.
// Replaces the fragmented config system from the Python agent.

mod manager;

pub use manager::*;
