// Manages the Python backend sidecar process and the JSON-RPC pipe.
//
// The backend is spawned once at startup. We write line-delimited JSON
// requests on stdin and read line-delimited JSON responses (or unsolicited
// events) on stdout. Each request gets a unique `id`; responses are routed
// back to the awaiting Future via a oneshot channel.

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{oneshot, Mutex};
use tokio::task::JoinHandle;
use tauri::{AppHandle, Emitter, Manager};

pub struct Sidecar {
    next_id: AtomicU64,
    pending: Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>>,
    stdin: Arc<Mutex<ChildStdin>>,
    _child: Arc<Mutex<Child>>,
    _reader: JoinHandle<()>,
}

impl Sidecar {
    pub fn spawn(app: &AppHandle) -> Result<Self> {
        // Tauri places sidecars next to the main exe at runtime under the
        // bundled name (without the target-triple suffix). We probe a few
        // candidates so dev builds and packaged installs both work.
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

        // CREATE_NO_WINDOW so the sidecar doesn't flash a console.
        #[cfg(windows)]
        const CREATE_NO_WINDOW: u32 = 0x08000000;

        let mut cmd = Command::new(&backend_path);
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);
        let mut child = cmd.spawn().context("spawn tune-backend")?;
        let stdin = child.stdin.take().context("backend stdin")?;
        let stdout = child.stdout.take().context("backend stdout")?;
        let pending: Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>> =
            Arc::new(Mutex::new(HashMap::new()));

        let app_handle = app.clone();
        let pending_clone = pending.clone();
        let reader = tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let parsed: Value = match serde_json::from_str(trimmed) {
                    Ok(v) => v,
                    Err(e) => {
                        eprintln!("sidecar: bad JSON: {} ({})", e, trimmed);
                        continue;
                    }
                };
                if let Some(id) = parsed.get("id").and_then(|v| v.as_u64()) {
                    let mut guard = pending_clone.lock().await;
                    if let Some(tx) = guard.remove(&id) {
                        let result = if let Some(err) = parsed.get("error") {
                            Err(err.as_str().unwrap_or("unknown error").to_string())
                        } else {
                            Ok(parsed.get("result").cloned().unwrap_or(Value::Null))
                        };
                        let _ = tx.send(result);
                    }
                } else if parsed.get("event").is_some() {
                    if let Err(e) = app_handle.emit("backend://event", &parsed) {
                        eprintln!("emit backend event failed: {}", e);
                    }
                }
            }
            eprintln!("sidecar: stdout closed");
        });

        Ok(Sidecar {
            next_id: AtomicU64::new(1),
            pending,
            stdin: Arc::new(Mutex::new(stdin)),
            _child: Arc::new(Mutex::new(child)),
            _reader: reader,
        })
    }

    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        {
            let mut guard = self.pending.lock().await;
            guard.insert(id, tx);
        }
        let request = json!({ "id": id, "method": method, "params": params });
        let line = format!("{}\n", request);
        {
            let mut stdin = self.stdin.lock().await;
            if let Err(e) = stdin.write_all(line.as_bytes()).await {
                let mut guard = self.pending.lock().await;
                guard.remove(&id);
                return Err(format!("stdin write failed: {}", e));
            }
            if let Err(e) = stdin.flush().await {
                eprintln!("stdin flush warning: {}", e);
            }
        }

        // Strict 30s ceiling so a stuck sidecar can never wedge the UI.
        match tokio::time::timeout(std::time::Duration::from_secs(30), rx).await {
            Ok(Ok(Ok(v))) => Ok(v),
            Ok(Ok(Err(e))) => Err(e),
            Ok(Err(_)) => Err("sidecar dropped response channel".into()),
            Err(_) => {
                let mut guard = self.pending.lock().await;
                guard.remove(&id);
                Err("sidecar call timed out after 30s".into())
            }
        }
    }
}
