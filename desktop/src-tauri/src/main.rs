#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Mitra desktop shell. It owns the window only; all brains stay in the
// local Python API, which must already run at the configured URL.
fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running Mitra");
}
