// Raguia Agent — Tauri entry point
// Prevents a console window from appearing on Windows in release builds
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    raguia_agent_lib::run()
}
