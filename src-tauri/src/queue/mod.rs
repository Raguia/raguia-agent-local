// SQLite-backed queue for pending file syncs
//
// Replaces the Python queue_store.py. Uses rusqlite with bundled SQLite.
// Schema designed for atomic dequeue and crash recovery.

mod store;

pub use store::*;
