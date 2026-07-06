use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

#[tauri::command]
async fn open_screen2(app: tauri::AppHandle) -> Result<(), String> {
    // Hvis vinduet allerede er åbent — bring det i fokus
    if let Some(existing) = app.get_webview_window("screen2") {
        existing.set_focus().map_err(|e| e.to_string())?;
        return Ok(());
    }

    // Ellers opret et nyt vindue — start maksimeret så hele skærmen bruges
    WebviewWindowBuilder::new(
        &app,
        "screen2",
        WebviewUrl::App("index.html?screen=2".into()),
    )
    .title("Ibens Trading Dash — Skærm 2")
    .inner_size(1920.0, 1080.0)   // Fallback hvis maximized fejler
    .maximized(true)
    .build()
    .map_err(|e| e.to_string())?;

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![open_screen2])
        .on_window_event(|window, event| {
            // Naar HOVEDVINDUET (label "main") lukkes, afsluttes hele appen — saa
            // skaerm 2 lukker med. Lukning af skaerm 2 alene lukker kun skaerm 2.
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if window.label() == "main" {
                    window.app_handle().exit(0);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}