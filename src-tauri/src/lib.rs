mod sidecar;
mod updater;

use serde_json::Value;
use std::sync::Arc;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{TrayIconBuilder, TrayIconEvent, MouseButton, MouseButtonState},
    AppHandle, Manager, RunEvent, WindowEvent,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tokio::sync::OnceCell;

use sidecar::Sidecar;
use updater::{Updater, UpdateState};

static SIDECAR: OnceCell<Arc<Sidecar>> = OnceCell::const_new();
static UPDATER: OnceCell<Arc<Updater>> = OnceCell::const_new();
static MINI: OnceCell<tokio::sync::Mutex<bool>> = OnceCell::const_new();

async fn sidecar() -> Result<Arc<Sidecar>, String> {
    SIDECAR
        .get()
        .cloned()
        .ok_or_else(|| "sidecar not initialised".to_string())
}

async fn updater() -> Result<Arc<Updater>, String> {
    UPDATER
        .get()
        .cloned()
        .ok_or_else(|| "updater not initialised".to_string())
}

fn focus_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

#[tauri::command]
async fn rpc(method: String, params: Value) -> Result<Value, String> {
    let sc = sidecar().await?;
    sc.call(&method, params).await
}

#[tauri::command]
async fn set_always_on_top(app: AppHandle, enabled: bool) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("main") {
        w.set_always_on_top(enabled).map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
async fn set_autostart(app: AppHandle, enabled: bool) -> Result<bool, String> {
    let manager = app.autolaunch();
    if enabled {
        manager.enable().map_err(|e| e.to_string())?;
    } else {
        manager.disable().map_err(|e| e.to_string())?;
    }
    manager.is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
async fn toggle_mini(app: AppHandle) -> Result<bool, String> {
    let mini_mutex = MINI
        .get_or_init(|| async { tokio::sync::Mutex::new(false) })
        .await;
    let mut mini = mini_mutex.lock().await;
    *mini = !*mini;
    let next = *mini;
    drop(mini);

    if let Some(w) = app.get_webview_window("main") {
        let (width, height) = if next { (380.0_f64, 360.0_f64) } else { (560.0_f64, 800.0_f64) };
        let _ = w.set_size(tauri::LogicalSize::new(width, height));
        let js = format!("document.body.classList.toggle('mini', {});", next);
        let _ = w.eval(&js);
    }
    Ok(next)
}

#[tauri::command]
async fn check_for_updates() -> Result<(), String> {
    let u = updater().await?;
    u.check(true).await;
    Ok(())
}

#[tauri::command]
async fn get_update_state() -> Result<UpdateState, String> {
    let u = updater().await?;
    Ok(u.current_state().await)
}

#[tauri::command]
async fn install_update() -> Result<bool, String> {
    let u = updater().await?;
    Ok(u.install_pending().await)
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Show", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "Hide", true, None::<&str>)?;
    let toggle_play = MenuItem::with_id(app, "toggle", "Play/Pause", true, None::<&str>)?;
    let next = MenuItem::with_id(app, "next", "Next", true, None::<&str>)?;
    let prev = MenuItem::with_id(app, "prev", "Previous", true, None::<&str>)?;
    let check_updates = MenuItem::with_id(app, "check_updates", "Check for updates", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &show,
            &hide,
            &PredefinedMenuItem::separator(app)?,
            &toggle_play,
            &next,
            &prev,
            &PredefinedMenuItem::separator(app)?,
            &check_updates,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;

    TrayIconBuilder::with_id("main")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| {
            let id = event.id().as_ref();
            match id {
                "show" => focus_main(app),
                "hide" => {
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.hide();
                    }
                }
                "toggle" | "next" | "prev" => {
                    let action = id.to_string();
                    let app_clone = app.clone();
                    tauri::async_runtime::spawn(async move {
                        if let Ok(sc) = sidecar().await {
                            let _ = sc.call("media_action", serde_json::json!({"action": action})).await;
                        }
                        let _ = app_clone;
                    });
                }
                "check_updates" => {
                    let app_clone = app.clone();
                    tauri::async_runtime::spawn(async move {
                        if let Ok(u) = updater().await {
                            u.check(true).await;
                            // Surface the panel.
                            focus_main(&app_clone);
                        }
                    });
                }
                "quit" => {
                    app.exit(0);
                }
                _ => {}
            }
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                let app = tray.app_handle();
                if let Some(w) = app.get_webview_window("main") {
                    if w.is_visible().unwrap_or(false) {
                        let _ = w.hide();
                    } else {
                        focus_main(app);
                    }
                }
            }
        })
        .icon(app.default_window_icon().unwrap().clone())
        .build(app)?;

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
        focus_main(app);
    }));

    builder = builder.plugin(tauri_plugin_autostart::init(
        MacosLauncher::LaunchAgent,
        None,
    ));

    builder
        .invoke_handler(tauri::generate_handler![
            rpc,
            set_always_on_top,
            set_autostart,
            toggle_mini,
            check_for_updates,
            get_update_state,
            install_update
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            // Spawn the Python sidecar.
            let sc = Sidecar::spawn(&handle).expect("spawn sidecar");
            let _ = SIDECAR.set(Arc::new(sc));

            // Wire the updater.
            let u = Updater::new(handle.clone());
            let _ = UPDATER.set(Arc::new(u));

            // Tray.
            build_tray(&handle)?;

            // Close button hides instead of exits — Quit is in the tray.
            if let Some(window) = app.get_webview_window("main") {
                let app_handle = handle.clone();
                window.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        if let Some(w) = app_handle.get_webview_window("main") {
                            let _ = w.hide();
                        }
                    }
                });
            }

            // Initial update check after a short delay so we don't fight with startup.
            tauri::async_runtime::spawn(async {
                tokio::time::sleep(std::time::Duration::from_secs(20)).await;
                if let Ok(u) = updater().await {
                    u.check(true).await;
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, event| {
            // We live in the tray — explicit `app.exit(0)` from the tray menu
            // is the only legitimate way to quit. Anything else (last window
            // closed, OS-level exit signal) gets prevented so the sidecar
            // stays alive.
            if let RunEvent::ExitRequested { api, .. } = event {
                api.prevent_exit();
            }
        });
}
