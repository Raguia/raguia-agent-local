// Sync engine — the core polling loop that drives file synchronization.
//
// Replaces Python ``sync_agent.py::SyncAgent.run_forever()``.
// Runs as a background tokio task, polling the server API and processing
// the sync queue. Communicates with the tray via Tauri events.

mod sync;

pub use sync::*;
