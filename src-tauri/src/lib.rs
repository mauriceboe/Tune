mod sidecar;
mod updater;

use serde_json::Value;
use std::io::Write;
use std::sync::Arc;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{TrayIconBuilder, TrayIconEvent, MouseButton, MouseButtonState},
    AppHandle, Manager, RunEvent, WindowEvent,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tokio::sync::OnceCell;

fn tauri_log(msg: &str) {
    if let Ok(appdata) = std::env::var("APPDATA") {
        let dir = std::path::PathBuf::from(appdata).join("Tune");
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("tauri.log");
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = writeln!(f, "[ts={now} pid={}] {msg}", std::process::id());
        }
    }
}

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
    // Install a panic hook so any unwinding panic ends up in tauri.log
    // instead of dying silently in --windowed builds.
    std::panic::set_hook(Box::new(|info| {
        tauri_log(&format!("PANIC: {info}"));
    }));

    tauri_log(&format!("boot: tune v{} starting", env!("CARGO_PKG_VERSION")));

    let mut builder = tauri::Builder::default();

    builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
        tauri_log("single-instance: another launch redirected to existing window");
        focus_main(app);
    }));

    builder = builder.plugin(tauri_plugin_autostart::init(
        MacosLauncher::LaunchAgent,
        None,
    ));

    let result = builder
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
            tauri_log("setup: enter");
            let handle = app.handle().clone();

            // Spawn the Python sidecar.
            match Sidecar::spawn(&handle) {
                Ok(sc) => {
                    tauri_log("setup: sidecar spawned");
                    let _ = SIDECAR.set(Arc::new(sc));
                }
                Err(e) => {
                    tauri_log(&format!("setup: sidecar spawn FAILED: {e:?}"));
                    return Err(Box::new(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())));
                }
            }

            // Wire the updater.
            let u = Updater::new(handle.clone());
            let _ = UPDATER.set(Arc::new(u));
            tauri_log("setup: updater wired");

            // Tray.
            if let Err(e) = build_tray(&handle) {
                tauri_log(&format!("setup: tray build FAILED: {e:?}"));
                return Err(Box::new(e));
            }
            tauri_log("setup: tray built");

            // Close button hides instead of exits — Quit is in the tray.
            if let Some(window) = app.get_webview_window("main") {
                let app_handle = handle.clone();
                window.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        tauri_log("window: close requested -> hide");
                        api.prevent_close();
                        if let Some(w) = app_handle.get_webview_window("main") {
                            let _ = w.hide();
                        }
                    }
                });
                tauri_log("setup: window event handler attached");
            } else {
                tauri_log("setup: WARNING no main window found");
            }

            // Initial update check after a short delay so we don't fight with startup.
            tauri::async_runtime::spawn(async {
                tokio::time::sleep(std::time::Duration::from_secs(20)).await;
                if let Ok(u) = updater().await {
                    u.check(true).await;
                }
            });

            tauri_log("setup: done");
            Ok(())
        })
        .build(tauri::generate_context!());

    match result {
        Ok(app) => {
            tauri_log("build: app constructed, entering run loop");
            app.run(|_app_handle, event| {
                match &event {
                    RunEvent::ExitRequested { code, .. } => {
                        tauri_log(&format!("event: ExitRequested code={code:?} (preventing)"));
                    }
                    RunEvent::WindowEvent { label, event: we, .. } => {
                        tauri_log(&format!("event: WindowEvent label={label} event={we:?}"));
                    }
                    RunEvent::Exit => tauri_log("event: Exit"),
                    _ => {}
                }
                if let RunEvent::ExitRequested { api, .. } = event {
                    api.prevent_exit();
                }
            });
        }
        Err(e) => {
            tauri_log(&format!("FATAL: build() failed: {e:?}"));
            // Surface to the user so the silent crash isn't invisible.
            #[cfg(windows)]
            {
                let msg = format!("Tune failed to start:\n\n{e}\n\nSee %APPDATA%\\Tune\\tauri.log");
                show_error_dialog(&msg);
            }
            std::process::exit(1);
        }
    }

    tauri_log("run: returned (process exiting)");
}

#[cfg(windows)]
fn show_error_dialog(msg: &str) {
    use std::os::windows::ffi::OsStrExt;
    let wide: Vec<u16> = std::ffi::OsStr::new(msg).encode_wide().chain(Some(0)).collect();
    let title: Vec<u16> = std::ffi::OsStr::new("Tune").encode_wide().chain(Some(0)).collect();
    extern "system" {
        fn MessageBoxW(hwnd: *mut std::ffi::c_void, text: *const u16, caption: *const u16, utype: u32) -> i32;
    }
    unsafe {
        MessageBoxW(std::ptr::null_mut(), wide.as_ptr(), title.as_ptr(), 0x10);
    }
}
