<div align="center">

<img src="docs/logo-256.png" alt="Tune" height="120" />

# Tune

Discord Rich Presence for Apple Music on Windows — with synced lyrics, listening stats, and a Liquid Glass interface that mirrors the album you're playing.

<br />

<a href="https://github.com/mauriceboe/Tune/releases/latest"><img alt="Download" src="https://img.shields.io/badge/Download-latest-fc3c44?style=for-the-badge" /></a>
&nbsp;
<a href="https://github.com/mauriceboe/Tune/releases"><img alt="Releases" src="https://img.shields.io/github/v/release/mauriceboe/Tune?include_prereleases&style=for-the-badge&color=111827" /></a>
<br />
<a href="https://ko-fi.com/mauriceboe"><img alt="Ko-fi" src="https://img.shields.io/badge/Ko--fi-support-FF5E5B?style=for-the-badge" /></a>
&nbsp;
<a href="https://www.buymeacoffee.com/mauriceboe"><img alt="BMAC" src="https://img.shields.io/badge/BMAC-support-FFDD00?style=for-the-badge" /></a>
<br />
<a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-6B7280?style=flat-square" /></a>
<a href="https://github.com/mauriceboe/Tune"><img alt="Stars" src="https://img.shields.io/github/stars/mauriceboe/Tune?style=flat-square&color=6B7280" /></a>
<img alt="Platform" src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4?style=flat-square" />

</div>

---

<div align="center">
  <a href="docs/screenshots/now-playing.png"><img src="docs/screenshots/now-playing.png" alt="Now playing" width="32%" /></a>
  <a href="docs/screenshots/stats.png"><img src="docs/screenshots/stats.png" alt="Stats" width="32%" /></a>
  <a href="docs/screenshots/settings.png"><img src="docs/screenshots/settings.png" alt="Settings" width="32%" /></a>
</div>

---

## What you get

- **Live Discord Rich Presence** — title, artist, album, progress, real album art, and "Listening to Apple Music" header
- **Synced lyrics** from LRCLIB, highlighted in time
- **Listening stats** — daily totals, top artist, last 80 plays
- **Liquid Glass UI** — backdrop blur, dynamic accent from the cover, parallax cover, animated backdrop, waveform progress
- **Mini player** with a fixed bottom bar showing your last played track
- **System tray** — media controls, show/hide, update check
- **Auto-updates** straight from GitHub Releases, with progress in Settings
- **Autostart**, **always-on-top**, **frameless resizable window**

## Get started

1. Download the latest **Tune-x.y.z-setup.exe** from [Releases](https://github.com/mauriceboe/Tune/releases/latest)
2. Run the installer — installs to `%LOCALAPPDATA%\Programs\Tune\` (per-user, no admin)
3. Open Apple Music, hit play, and Discord lights up

> Tune appears under *Settings → Apps → Installed apps* and can be uninstalled the regular Windows way. Settings, history and artwork cache live in `%APPDATA%\Tune\` and are kept across updates and reinstalls.

## How it works

Two processes:

- **The Tauri shell** (Rust) owns the window, the system tray, the WebView2 surface that paints the Liquid Glass UI, the auto-updater, and the JS↔backend bridge. Single-instance enforced.
- **A Python sidecar** (`tune-backend.exe`) reads the Windows System Media Transport Controls every second, talks to Discord IPC, fetches lyrics from [LRCLIB](https://lrclib.net), uploads cover art to [catbox.moe](https://catbox.moe) so Discord shows the exact image, and stores history.

They communicate over line-delimited JSON-RPC on stdin/stdout. The sidecar is bundled with the installer; the user never sees it.

## Build from source

You'll need:

- Python 3.12+
- Rust stable
- Node 22+

```powershell
# 1. Build the Python sidecar
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --onefile --noconsole --name tune-backend `
  --collect-all winsdk --collect-all PIL `
  --distpath dist-backend backend/__main__.py

# 2. Drop it where Tauri expects it
mkdir src-tauri\binaries -ErrorAction SilentlyContinue
copy dist-backend\tune-backend.exe src-tauri\binaries\tune-backend-x86_64-pc-windows-msvc.exe

# 3. Build the Tauri app
npm install
npm run build -- --target x86_64-pc-windows-msvc
```

Output: `src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\Tune-x.y.z-setup.exe`

For dev iteration on the frontend you can run the sidecar in a terminal and the Tauri host with `npm run dev`.

## Privacy

Tune runs locally and stores everything in `%APPDATA%\Tune\`:

- `settings.json` — your preferences
- `history.json` — your play history (never sent anywhere)
- `artwork_cache.json` — a `sha256 → catbox.moe url` map
- `backend.log` — diagnostic log of the Python sidecar

Data leaves your machine in only three cases, all triggered by playback:

- **Cover art** is uploaded to catbox.moe so Discord can display it. Each unique image is uploaded once.
- **Lyrics** are looked up at lrclib.net using the artist + title.
- **Discord Rich Presence** is sent to your local Discord client over an IPC socket.

The auto-updater fetches release metadata from `api.github.com` and downloads new installers from `github.com/mauriceboe/Tune/releases`. No identifiers other than a `User-Agent` of `Tune/<version>` are sent.

## Disclaimer

Tune is an unofficial third-party tool. It is not affiliated with, endorsed by, or sponsored by Apple Inc., Discord Inc., or LRCLIB. *Apple Music* is a trademark of Apple Inc.

## License

[MIT](LICENSE) — do whatever you want, just don't blame me if it breaks.
