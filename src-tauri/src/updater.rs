// Custom GitHub-Releases-based updater. We deliberately don't use
// tauri-plugin-updater here because it requires signed `latest.json` and a
// pubkey baked into the bundle — keeping the simpler "fetch metadata,
// download installer, run it" flow we already had in 1.x, just with proper
// progress reporting and chunked download.

use anyhow::{anyhow, Result};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tauri::{AppHandle, Emitter};
use tokio::fs::File;
use tokio::io::AsyncWriteExt;
use tokio::sync::Mutex;

const GITHUB_REPO: &str = "mauriceboe/Tune";

fn user_agent() -> String {
    format!("Tune/{} (+https://github.com/{})", env!("CARGO_PKG_VERSION"), GITHUB_REPO)
}

fn parse_version(s: &str) -> Vec<u32> {
    let trimmed = s.trim_start_matches(|c: char| c == 'v' || c == 'V');
    let stripped = trimmed.split(|c: char| c == '-' || c == '+').next().unwrap_or("");
    stripped
        .split('.')
        .map(|p| p.parse::<u32>().unwrap_or(0))
        .collect()
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum UpdateState {
    Idle { current: String },
    Checking { current: String },
    UpToDate { current: String },
    Available { current: String, version: String, size: u64, notes: String },
    Downloading { current: String, version: String, progress: u32, bytes: u64, total: u64 },
    Verifying { current: String, version: String },
    Ready { current: String, version: String, installer_path: String },
    Error { current: String, error: String },
}

#[derive(Deserialize)]
struct ReleaseAsset {
    name: String,
    browser_download_url: String,
    size: u64,
}

#[derive(Deserialize)]
struct Release {
    tag_name: String,
    body: Option<String>,
    #[serde(default)]
    prerelease: bool,
    #[serde(default)]
    draft: bool,
    assets: Vec<ReleaseAsset>,
}

pub struct Updater {
    app: AppHandle,
    state: Arc<Mutex<UpdateState>>,
    busy: Arc<Mutex<bool>>,
    pending_installer: Arc<Mutex<Option<PathBuf>>>,
}

impl Updater {
    pub fn new(app: AppHandle) -> Self {
        let current = env!("CARGO_PKG_VERSION").to_string();
        Updater {
            app,
            state: Arc::new(Mutex::new(UpdateState::Idle { current })),
            busy: Arc::new(Mutex::new(false)),
            pending_installer: Arc::new(Mutex::new(None)),
        }
    }

    async fn set_state(&self, s: UpdateState) {
        let payload = s.clone();
        {
            let mut guard = self.state.lock().await;
            *guard = s;
        }
        let _ = self.app.emit("update://event", &payload);
    }

    pub async fn current_state(&self) -> UpdateState {
        self.state.lock().await.clone()
    }

    pub async fn check(self: Arc<Self>, auto_download: bool) {
        {
            let mut busy = self.busy.lock().await;
            if *busy {
                return;
            }
            *busy = true;
        }
        let me = self.clone();
        tokio::spawn(async move {
            me.run_check(auto_download).await;
            let mut busy = me.busy.lock().await;
            *busy = false;
        });
    }

    async fn run_check(&self, auto_download: bool) {
        let current = env!("CARGO_PKG_VERSION").to_string();
        self.set_state(UpdateState::Checking { current: current.clone() }).await;

        let release = match self.fetch_latest().await {
            Ok(r) => r,
            Err(e) => {
                self.set_state(UpdateState::Error {
                    current: current.clone(),
                    error: format!("fetch failed: {e}"),
                })
                .await;
                return;
            }
        };

        if release.draft || release.prerelease {
            self.set_state(UpdateState::UpToDate { current }).await;
            return;
        }

        let latest = parse_version(&release.tag_name);
        let me = parse_version(&current);
        if latest <= me {
            self.set_state(UpdateState::UpToDate { current }).await;
            return;
        }

        let setup_asset = release
            .assets
            .iter()
            .find(|a| a.name.to_lowercase().ends_with("setup.exe") || a.name.to_lowercase().ends_with(".exe"));
        let setup = match setup_asset {
            Some(a) => a,
            None => {
                self.set_state(UpdateState::Error {
                    current,
                    error: "no installer asset in release".into(),
                })
                .await;
                return;
            }
        };

        let version = release.tag_name.trim_start_matches('v').to_string();
        self.set_state(UpdateState::Available {
            current: current.clone(),
            version: version.clone(),
            size: setup.size,
            notes: release.body.clone().unwrap_or_default(),
        })
        .await;

        if auto_download {
            self.download_and_stage(&setup.browser_download_url, &version, setup.size, &release).await;
        }
    }

    async fn fetch_latest(&self) -> Result<Release> {
        let url = format!("https://api.github.com/repos/{}/releases/latest", GITHUB_REPO);
        let client = reqwest::Client::builder()
            .user_agent(user_agent())
            .timeout(Duration::from_secs(15))
            .build()?;
        let resp = client
            .get(&url)
            .header("Accept", "application/vnd.github+json")
            .send()
            .await?
            .error_for_status()?;
        let release: Release = resp.json().await?;
        Ok(release)
    }

    async fn download_and_stage(&self, url: &str, version: &str, total_hint: u64, release: &Release) {
        let current = env!("CARGO_PKG_VERSION").to_string();
        let temp_dir = std::env::temp_dir().join("Tune-update");
        if let Err(e) = tokio::fs::create_dir_all(&temp_dir).await {
            self.set_state(UpdateState::Error {
                current,
                error: format!("create temp dir: {e}"),
            })
            .await;
            return;
        }
        let installer_path = temp_dir.join(format!("Tune-{}-setup.exe", version));

        for attempt in 1..=3u32 {
            self.set_state(UpdateState::Downloading {
                current: current.clone(),
                version: version.to_string(),
                progress: 0,
                bytes: 0,
                total: total_hint,
            })
            .await;

            match self.do_download(url, &installer_path, &current, version, total_hint).await {
                Ok(()) => {
                    self.set_state(UpdateState::Verifying {
                        current: current.clone(),
                        version: version.to_string(),
                    })
                    .await;
                    if !self.verify_sha256(&installer_path, release).await {
                        let _ = tokio::fs::remove_file(&installer_path).await;
                        self.set_state(UpdateState::Error {
                            current: current.clone(),
                            error: "checksum mismatch".into(),
                        })
                        .await;
                        return;
                    }
                    let mut pending = self.pending_installer.lock().await;
                    *pending = Some(installer_path.clone());
                    drop(pending);
                    self.set_state(UpdateState::Ready {
                        current: current.clone(),
                        version: version.to_string(),
                        installer_path: installer_path.to_string_lossy().to_string(),
                    })
                    .await;
                    return;
                }
                Err(e) => {
                    if attempt < 3 {
                        tokio::time::sleep(Duration::from_secs(2 * attempt as u64)).await;
                        continue;
                    }
                    self.set_state(UpdateState::Error {
                        current,
                        error: format!("download failed: {e}"),
                    })
                    .await;
                    return;
                }
            }
        }
    }

    async fn do_download(
        &self,
        url: &str,
        dest: &PathBuf,
        current: &str,
        version: &str,
        total_hint: u64,
    ) -> Result<()> {
        let client = reqwest::Client::builder()
            .user_agent(user_agent())
            .timeout(Duration::from_secs(0))
            .connect_timeout(Duration::from_secs(15))
            .read_timeout(Duration::from_secs(30))
            .build()?;
        let resp = client.get(url).send().await?.error_for_status()?;
        let total = resp.content_length().unwrap_or(total_hint);
        let mut file = File::create(dest).await?;
        let mut stream = resp.bytes_stream();
        let mut received: u64 = 0;
        let mut last_emit = std::time::Instant::now();
        while let Some(chunk) = stream.next().await {
            let bytes = chunk?;
            file.write_all(&bytes).await?;
            received += bytes.len() as u64;
            if last_emit.elapsed() >= Duration::from_millis(250) {
                last_emit = std::time::Instant::now();
                let progress = if total > 0 {
                    ((received as f64 / total as f64) * 100.0) as u32
                } else {
                    0
                };
                self.set_state(UpdateState::Downloading {
                    current: current.to_string(),
                    version: version.to_string(),
                    progress,
                    bytes: received,
                    total,
                })
                .await;
            }
        }
        file.flush().await?;
        if total > 0 && received < total {
            return Err(anyhow!("truncated: {}/{}", received, total));
        }
        self.set_state(UpdateState::Downloading {
            current: current.to_string(),
            version: version.to_string(),
            progress: 100,
            bytes: received,
            total: if total > 0 { total } else { received },
        })
        .await;
        Ok(())
    }

    async fn verify_sha256(&self, path: &PathBuf, release: &Release) -> bool {
        // Look for a sha256 sidecar matching the installer asset.
        let installer_name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        let sha_asset = release
            .assets
            .iter()
            .find(|a| a.name.eq_ignore_ascii_case(&format!("{installer_name}.sha256")))
            .or_else(|| {
                release
                    .assets
                    .iter()
                    .find(|a| a.name.to_lowercase().ends_with(".sha256"))
            });
        let Some(sha_asset) = sha_asset else {
            return true; // no sidecar, accept download
        };
        let client = match reqwest::Client::builder()
            .user_agent(user_agent())
            .timeout(Duration::from_secs(15))
            .build()
        {
            Ok(c) => c,
            Err(_) => return true,
        };
        let body = match client.get(&sha_asset.browser_download_url).send().await {
            Ok(r) => match r.text().await {
                Ok(t) => t,
                Err(_) => return true,
            },
            Err(_) => return true,
        };
        let expected = body.split_whitespace().next().unwrap_or("").to_lowercase();
        if expected.is_empty() {
            return true;
        }
        let mut hasher = Sha256::new();
        let bytes = match tokio::fs::read(path).await {
            Ok(b) => b,
            Err(_) => return false,
        };
        hasher.update(&bytes);
        let actual = hex::encode(hasher.finalize());
        actual == expected
    }

    pub async fn install_pending(&self) -> bool {
        let path = {
            let guard = self.pending_installer.lock().await;
            guard.clone()
        };
        let Some(installer) = path else {
            return false;
        };
        // Spawn the NSIS installer detached. /S = silent, /UPDATE tells NSIS
        // to use the upgrade flow (preserves install dir, suppresses prompts).
        let r = std::process::Command::new(&installer)
            .arg("/S")
            .arg("/UPDATE")
            .spawn();
        if r.is_err() {
            return false;
        }
        // Give the installer a beat to grab the executable handle, then exit
        // ourselves so it can replace the running EXE.
        let app = self.app.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(300)).await;
            app.exit(0);
        });
        true
    }
}
