// Application updater — manages automatic and manual updates
//
// Uses the Tauri updater plugin for atomic binary replacement.
// Replaces the fragile shell-script-based update system from the Python agent.

mod updater;
pub use updater::*;
