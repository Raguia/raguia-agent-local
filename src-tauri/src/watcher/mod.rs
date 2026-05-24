// File system watcher — monitors configured directories for changes
//
// Uses the `notify` crate (cross-platform filesystem events).
// Debounces rapid changes and enqueues files for sync.

mod watcher;

pub use watcher::*;
