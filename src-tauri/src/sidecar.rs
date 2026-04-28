// Manages the Python backend sidecar process and the JSON-RPC pipe.
//
// The reader runs in a std::thread (not a Tokio task) so we can be created
// from Tauri's sync `setup` callback without needing an active runtime.
// The writer uses synchronous std::io for the same reason — each request is
// a tiny line, so blocking writes from inside an async command are fine.

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::{oneshot, Mutex};

fn sidecar_log(msg: &str) {
    if let Ok(appdata) = std::env::var("APPDATA") {
        let dir = std::path::PathBuf::from(appdata).join("Tune");
        let _ = std::fs::create_dir_all(&dir);
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("tauri.log"))
        {
            use std::io::Write as _;
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = writeln!(f, "[ts={now}] sidecar: {msg}");
        }
    }
}

pub struct Sidecar {
    next_id: AtomicU64,
    pending: Arc<StdMutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>>,
    stdin: Arc<Mutex<ChildStdin>>,
    _child: Arc<StdMutex<Child>>,
}

impl Sidecar {
    pub fn spawn(app: &AppHandle) -> Result<Self> {
        let mut search_dirs = vec![];
        if let Ok(exe) = std::env::current_exe() {
            if let Some(parent) = exe.parent() {
                search_dirs.push(parent.to_path_buf());
            }
        }
        if let Ok(d) = app.path().resource_dir() {
            search_dirs.push(d);
        }
        if let Ok(d) = app.path().app_local_data_dir() {
            search_dirs.push(d);
        }

        let names = [
            "tune-backend.exe",
            "tune-backend-x86_64-pc-windows-msvc.exe",
        ];
        let backend_path = search_dirs
            .iter()
            .flat_map(|d| names.iter().map(move |n| d.join(n)))
            .find(|p| p.exists())
            .ok_or_else(|| anyhow!("tune-backend.exe not found in {:?}", search_dirs))?;

        #[cfg(windows)]
        const CREATE_NO_WINDOW: u32 = 0x08000000;

        let mut cmd = Command::new(&backend_path);
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = cmd.spawn().context("spawn tune-backend")?;
        let stdin = child.stdin.take().context("backend stdin")?;
        let stdout = child.stdout.take().context("backend stdout")?;
        let pending: Arc<StdMutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>> =
            Arc::new(StdMutex::new(HashMap::new()));

        // Reader thread — pure blocking IO, no async runtime needed.
        let app_handle = app.clone();
        let pending_clone = pending.clone();
        thread::spawn(move || {
            sidecar_log("reader thread started");
            // Read raw bytes and lossy-decode each line so a single odd byte
            // can never kill the whole pipe. Python emits one JSON object per
            // newline, so we just split on \n.
            let mut reader = BufReader::new(stdout);
            let mut buf: Vec<u8> = Vec::with_capacity(64 * 1024);
            loop {
                buf.clear();
                let mut byte = [0u8; 1];
                let line_done = loop {
                    match reader.read(&mut byte) {
                        Ok(0) => break true,    // EOF
                        Ok(_) => {
                            if byte[0] == b'\n' {
                                break false;
                            }
                            if byte[0] != b'\r' {
                                buf.push(byte[0]);
                            }
                        }
                        Err(e) => {
                            sidecar_log(&format!("stdout read error: {e}"));
                            return;
                        }
                    }
                };
                if buf.is_empty() {
                    if line_done {
                        break;
                    }
                    continue;
                }
                let line = String::from_utf8_lossy(&buf).into_owned();
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let parsed: Value = match serde_json::from_str(trimmed) {
                    Ok(v) => v,
                    Err(e) => {
                        sidecar_log(&format!("bad JSON ({e}): {trimmed}"));
                        continue;
                    }
                };
                if let Some(id) = parsed.get("id").and_then(|v| v.as_u64()) {
                    sidecar_log(&format!("response id={id}"));
                    let mut guard = pending_clone.lock().unwrap();
                    if let Some(tx) = guard.remove(&id) {
                        let result = if let Some(err) = parsed.get("error") {
                            Err(err.as_str().unwrap_or("unknown error").to_string())
                        } else {
                            Ok(parsed.get("result").cloned().unwrap_or(Value::Null))
                        };
                        let _ = tx.send(result);
                    }
                } else if let Some(ev) = parsed.get("event").and_then(|v| v.as_str()) {
                    sidecar_log(&format!("emit backend://event kind={ev}"));
                    match app_handle.emit("backend://event", &parsed) {
                        Ok(()) => {}
                        Err(e) => sidecar_log(&format!("emit failed: {e}")),
                    }
                } else {
                    sidecar_log(&format!("unknown line: {trimmed}"));
                }
            }
            sidecar_log("reader thread exit (stdout closed)");
        });

        Ok(Sidecar {
            next_id: AtomicU64::new(1),
            pending,
            stdin: Arc::new(Mutex::new(stdin)),
            _child: Arc::new(StdMutex::new(child)),
        })
    }

    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        {
            let mut guard = self.pending.lock().unwrap();
            guard.insert(id, tx);
        }
        let request = json!({ "id": id, "method": method, "params": params });
        let line = format!("{}\n", request);
        {
            let mut stdin = self.stdin.lock().await;
            if let Err(e) = stdin.write_all(line.as_bytes()) {
                let mut guard = self.pending.lock().unwrap();
                guard.remove(&id);
                return Err(format!("stdin write failed: {}", e));
            }
            if let Err(e) = stdin.flush() {
                eprintln!("stdin flush warning: {}", e);
            }
        }
        match tokio::time::timeout(Duration::from_secs(30), rx).await {
            Ok(Ok(Ok(v))) => Ok(v),
            Ok(Ok(Err(e))) => Err(e),
            Ok(Err(_)) => Err("sidecar dropped response channel".into()),
            Err(_) => {
                let mut guard = self.pending.lock().unwrap();
                guard.remove(&id);
                Err("sidecar call timed out after 30s".into())
            }
        }
    }
}
