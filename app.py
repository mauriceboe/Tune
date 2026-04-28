"""Tune — Discord Rich Presence for Apple Music on Windows."""

import asyncio
import base64
import collections
import colorsys
import ctypes
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import winreg
import webbrowser
from datetime import datetime, date
from pathlib import Path

APP_NAME = "Tune"
APP_DISPLAY_NAME = "Tune"
APP_VERSION = "1.3.0"
APP_AUMID = "dev.maurice.tune"

DISCORD_CLIENT_ID = os.environ.get("TUNE_DISCORD_CLIENT_ID", "861702238472241162")
POLL_SECONDS = 1
APPLE_MUSIC_AUMID_HINT = "applemusicwin"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = APP_NAME
DEFAULT_ACCENT = "#fc3c44"

INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Programs" / APP_NAME
INSTALLED_EXE = INSTALL_DIR / f"{APP_NAME}.exe"
START_MENU = (
    Path(os.environ.get("APPDATA", str(Path.home())))
    / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_DISPLAY_NAME}.lnk"
)
PUBLISHER = "mauriceboe"
HOMEPAGE_URL = "https://github.com/mauriceboe/Tune"
UNINSTALL_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Tune"


def _create_shortcut(target: Path, link_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        f'$s = (New-Object -ComObject WScript.Shell).CreateShortcut("{link_path}");'
        f'$s.TargetPath = "{target}";'
        f'$s.WorkingDirectory = "{target.parent}";'
        f'$s.Description = "{APP_DISPLAY_NAME}";'
        f'$s.Save()'
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        creationflags=0x08000000,
    )


def _register_uninstall_entry(target_exe: Path) -> None:
    """Register Tune in 'Apps & features' / 'Programs and Features' (per-user)."""
    try:
        size_kb = max(1, target_exe.stat().st_size // 1024)
    except Exception:
        size_kb = 1
    install_date = datetime.now().strftime("%Y%m%d")
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_KEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_DISPLAY_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(target_exe))
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
            winreg.SetValueEx(key, "URLInfoAbout", 0, winreg.REG_SZ, HOMEPAGE_URL)
            winreg.SetValueEx(key, "HelpLink", 0, winreg.REG_SZ, HOMEPAGE_URL)
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(INSTALL_DIR))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{target_exe}" --uninstall')
            winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, f'"{target_exe}" --uninstall --quiet')
            winreg.SetValueEx(key, "InstallDate", 0, winreg.REG_SZ, install_date)
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    except Exception:
        pass


def _unregister_uninstall_entry() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_KEY)
    except Exception:
        pass


def _confirm_install() -> bool:
    """Modal confirmation before copying ourselves into the user profile."""
    MB_YESNO = 0x04
    MB_ICONQUESTION = 0x20
    IDYES = 6
    text = (
        f"Install {APP_DISPLAY_NAME} {APP_VERSION}?\n\n"
        f"It will be copied to:\n  {INSTALL_DIR}\n\n"
        "A Start Menu shortcut will be created and the app will appear under "
        "Settings -> Apps -> Installed apps for easy uninstall.\n\n"
        "No admin rights are required. Continue?"
    )
    result = ctypes.windll.user32.MessageBoxW(
        0, text, f"Install {APP_DISPLAY_NAME}", MB_YESNO | MB_ICONQUESTION
    )
    return result == IDYES


def _self_install_if_needed() -> bool:
    """Copy this exe into %LOCALAPPDATA%\\Programs, create shortcut + uninstall entry."""
    if not getattr(sys, "frozen", False):
        return False
    current = Path(sys.executable).resolve()
    target = INSTALLED_EXE.resolve()
    if current == target:
        # Already running from the install location: refresh registry entry so
        # the version stays in sync after an auto-update.
        _register_uninstall_entry(target)
        return False
    if not _confirm_install():
        return False
    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, target)
        _create_shortcut(target, START_MENU)
        _register_uninstall_entry(target)
        ctypes.windll.user32.MessageBoxW(
            0,
            f"{APP_DISPLAY_NAME} has been installed.\n\n"
            f"Find it in the Start Menu, or under\n"
            f"Settings -> Apps -> Installed apps to uninstall.",
            APP_DISPLAY_NAME,
            0x40,
        )
        subprocess.Popen([str(target)], cwd=str(target.parent), creationflags=0x00000008)
        return True
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(0, f"Install failed: {e}", APP_DISPLAY_NAME, 0x10)
        return False


def _run_uninstaller(quiet: bool) -> int:
    """Handle `--uninstall` invocation. Confirms, then schedules removal and exits."""
    if not getattr(sys, "frozen", False):
        ctypes.windll.user32.MessageBoxW(
            0, "Uninstall is only available for the installed build.", APP_DISPLAY_NAME, 0x30
        )
        return 1

    if not quiet:
        MB_YESNO = 0x04
        MB_ICONWARNING = 0x30
        IDYES = 6
        text = (
            f"Uninstall {APP_DISPLAY_NAME}?\n\n"
            "This removes the application, the Start Menu shortcut, and the "
            "Windows autostart entry.\n\n"
            "Your settings, history, and artwork cache in\n"
            f"  {SETTINGS_DIR}\nwill be left in place.\n\n"
            "Continue?"
        )
        if ctypes.windll.user32.MessageBoxW(0, text, f"Uninstall {APP_DISPLAY_NAME}", MB_YESNO | MB_ICONWARNING) != IDYES:
            return 0

    # Best-effort cleanup we can do from the running process before handing
    # the rest to a self-deleting batch script.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass
    except Exception:
        pass

    _unregister_uninstall_entry()

    try:
        if START_MENU.exists():
            START_MENU.unlink()
    except Exception:
        pass

    # The batch script removes the still-locked EXE and the install folder
    # once this process has exited.
    bat = INSTALL_DIR / "_uninstall.bat"
    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        bat.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            "set TRIES=0\r\n"
            ":wait\r\n"
            f'tasklist /fi "imagename eq {APP_NAME}.exe" 2>nul | find /i "{APP_NAME}.exe" >nul\r\n'
            "if errorlevel 1 goto remove\r\n"
            "set /a TRIES+=1\r\n"
            "if %TRIES% GEQ 60 goto remove\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            "goto wait\r\n"
            ":remove\r\n"
            f'del /f /q "{INSTALLED_EXE}" 2>nul\r\n'
            f'rmdir /s /q "{INSTALL_DIR}" 2>nul\r\n',
            encoding="ascii",
        )
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            cwd=str(Path(os.environ.get("TEMP", str(Path.home())))),
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
        )
    except Exception:
        pass

    if not quiet:
        ctypes.windll.user32.MessageBoxW(
            0,
            f"{APP_DISPLAY_NAME} has been uninstalled.\n\n"
            f"User data was left in {SETTINGS_DIR}\n"
            "and can be deleted manually if no longer needed.",
            APP_DISPLAY_NAME,
            0x40,
        )
    return 0


# CLI entry points handled before any GUI initialisation.
if "--uninstall" in sys.argv[1:]:
    _quiet = "--quiet" in sys.argv[1:] or "/quiet" in sys.argv[1:]
    sys.exit(_run_uninstaller(_quiet))

if _self_install_if_needed():
    sys.exit(0)

# We're now running as the user-facing instance. If a previous update attempt
# left a Tune.exe.new or _update.bat in the install dir (because the swap
# raced with another launch, or the user installed manually mid-update), get
# rid of it before the periodic updater starts staging another one.
if getattr(sys, "frozen", False) and Path(sys.executable).resolve() == INSTALLED_EXE.resolve():
    for _stale in (INSTALL_DIR / f"{APP_NAME}.exe.new", INSTALL_DIR / "_update.bat"):
        try:
            if _stale.exists():
                _stale.unlink()
        except Exception:
            pass

# Set the AppUserModelID before any window is created so the taskbar groups
# the application under our own icon instead of the host process icon.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_AUMID)
except Exception:
    pass

# WebView2 holds an exclusive lock on its user-data folder while running. If
# the previous Tune instance crashed, the lock files survive and a fresh
# launch fails with HRESULT 0x800700AA ("resource already in use") — the app
# silently hangs at startup. To stay robust against that, give every launch
# its own per-PID subfolder and garbage-collect the folders of any PIDs that
# are no longer alive on the way in.
def _pid_alive(pid: int) -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def _setup_webview_data_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME / "WebView2"
    base.mkdir(parents=True, exist_ok=True)
    for child in base.iterdir():
        if not child.is_dir() or not child.name.startswith("pid-"):
            continue
        try:
            old_pid = int(child.name[4:])
        except ValueError:
            continue
        if not _pid_alive(old_pid):
            shutil.rmtree(child, ignore_errors=True)
    my_dir = base / f"pid-{os.getpid()}"
    my_dir.mkdir(exist_ok=True)
    return my_dir


_WV2_DATA = _setup_webview_data_dir()
os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(_WV2_DATA)

import atexit
atexit.register(lambda: shutil.rmtree(_WV2_DATA, ignore_errors=True))

import pystray
import webview
from PIL import Image, ImageDraw
from pypresence import ActivityType, Presence, StatusDisplayType
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
    EXE_PATH = Path(sys.executable).resolve()
else:
    BASE_DIR = Path(__file__).parent
    EXE_PATH = Path(sys.executable).resolve()

WEB_DIR = BASE_DIR / "web"
SETTINGS_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
SETTINGS_PATH = SETTINGS_DIR / "settings.json"
HISTORY_PATH = SETTINGS_DIR / "history.json"
ARTWORK_CACHE_PATH = SETTINGS_DIR / "artwork_cache.json"

DEFAULT_SETTINGS = {
    "show_on_discord": True,
    "minimize_to_tray": True,
    "start_minimized": False,
    "always_on_top": False,
    "auto_check_updates": True,
}

GITHUB_REPO = "mauriceboe/Tune"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_CHECK_INTERVAL_SECONDS = 6 * 3600
UPDATE_USER_AGENT = f"Tune/{APP_VERSION} (+https://github.com/{GITHUB_REPO})"


def load_json(path: Path, default):
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data):
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_settings() -> dict:
    return {**DEFAULT_SETTINGS, **load_json(SETTINGS_PATH, {})}


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
        return True
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool):
    if not getattr(sys, "frozen", False):
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, f'"{EXE_PATH}"')
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass


def boost_saturation(rgb):
    r, g, b = [c / 255 for c in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(0.50, min(0.65, l))
    s = max(0.65, s)
    nr, ng, nb = colorsys.hls_to_rgb(h, l, s)
    return (int(nr * 255), int(ng * 255), int(nb * 255))


def dominant_color(img: Image.Image) -> str:
    try:
        small = img.convert("RGB").resize((60, 60))
        pixels = list(small.getdata())
        filtered = []
        for r, g, b in pixels:
            avg = (r + g + b) / 3
            if 25 < avg < 230:
                mx, mn = max(r, g, b), min(r, g, b)
                if mx - mn > 25:
                    filtered.append((r // 16 * 16, g // 16 * 16, b // 16 * 16))
        if not filtered:
            return DEFAULT_ACCENT
        most = collections.Counter(filtered).most_common(1)[0][0]
        r, g, b = boost_saturation(most)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return DEFAULT_ACCENT


def parse_lrc(lrc: str):
    out = []
    for line in lrc.splitlines():
        m = re.match(r"\[(\d+):(\d+)\.(\d+)\](.*)", line)
        if not m:
            continue
        mm, ss, cs, text = m.groups()
        t = int(mm) * 60 + int(ss) + int(cs) / (10 ** len(cs))
        out.append([t, text.strip()])
    out.sort(key=lambda x: x[0])
    return out


class HistoryStore:
    def __init__(self):
        self._lock = threading.Lock()
        data = load_json(HISTORY_PATH, [])
        self.entries = data if isinstance(data, list) else []

    def add(self, entry):
        with self._lock:
            self.entries.append(entry)
            if len(self.entries) > 5000:
                self.entries = self.entries[-5000:]
            save_json(HISTORY_PATH, self.entries)

    def today_stats(self):
        today = date.today().isoformat()
        with self._lock:
            todays = [e for e in self.entries if e.get("date") == today]
        total = sum(e.get("listened", 0) or e.get("duration", 0) for e in todays)
        artists = collections.Counter(e["artist"] for e in todays if e.get("artist"))
        top = artists.most_common(1)[0] if artists else None
        return {
            "tracks": len(todays),
            "total_seconds": int(total),
            "top_artist": top[0] if top else None,
        }

    def recent_unique(self, n):
        with self._lock:
            out = []
            seen = set()
            for e in reversed(self.entries):
                key = f"{e.get('artist', '')}|{e.get('title', '')}".lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(e)
                if len(out) >= n:
                    break
            return out

    def recent(self, n=80):
        with self._lock:
            return list(reversed(self.entries[-n:]))


class LyricsCache:
    """Fetches synced lyrics from LRCLIB.net (free, no auth)."""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, artist, title, album, duration):
        key = f"{artist}|{title}|{int(duration or 0)}".lower()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        result = self._fetch(artist, title, album, duration)
        with self._lock:
            self._cache[key] = result
        return result

    def _fetch(self, artist, title, album, duration):
        ua = {"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
        try:
            params = {
                "artist_name": artist,
                "track_name": title,
                "album_name": album or "",
                "duration": str(int(duration or 0)),
            }
            url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(urllib.request.Request(url, headers=ua), timeout=5) as resp:
                data = json.loads(resp.read())
            synced = data.get("syncedLyrics") or ""
            if synced:
                return parse_lrc(synced)
        except Exception:
            pass
        try:
            url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": f"{artist} {title}"})
            with urllib.request.urlopen(urllib.request.Request(url, headers=ua), timeout=5) as resp:
                data = json.loads(resp.read())
            for hit in data[:3]:
                synced = hit.get("syncedLyrics") or ""
                if synced:
                    return parse_lrc(synced)
        except Exception:
            pass
        return None


class ArtworkHost:
    """Uploads cover art to catbox.moe so Discord receives the exact playing artwork.
    Results are cached locally by SHA-256 of the image bytes — each unique cover is
    uploaded only once."""

    def __init__(self):
        self._lock = threading.Lock()
        data = load_json(ARTWORK_CACHE_PATH, {})
        self._cache = data if isinstance(data, dict) else {}

    def _save(self):
        save_json(ARTWORK_CACHE_PATH, self._cache)

    def upload(self, image_bytes: bytes):
        if not image_bytes:
            return None
        h = hashlib.sha256(image_bytes).hexdigest()
        with self._lock:
            cached = self._cache.get(h)
        if cached:
            return cached
        try:
            boundary = uuid.uuid4().hex
            crlf = b"\r\n"
            body = b""
            body += f"--{boundary}".encode() + crlf
            body += b'Content-Disposition: form-data; name="reqtype"' + crlf + crlf
            body += b"fileupload" + crlf
            body += f"--{boundary}".encode() + crlf
            body += b'Content-Disposition: form-data; name="fileToUpload"; filename="cover.jpg"' + crlf
            body += b"Content-Type: image/jpeg" + crlf + crlf
            body += image_bytes + crlf
            body += f"--{boundary}--".encode() + crlf
            req = urllib.request.Request(
                "https://catbox.moe/user/api.php",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                url = resp.read().decode("utf-8", errors="ignore").strip()
            if url.startswith("http"):
                with self._lock:
                    self._cache[h] = url
                    self._save()
                return url
        except Exception:
            return None
        return None


class PresenceWorker:
    """Polls SMTC for the active Apple Music session and mirrors it to Discord."""

    def __init__(self, on_update, history, artwork_host):
        self.on_update = on_update
        self.history = history
        self.artwork_host = artwork_host
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._show_on_discord = True
        self._lock = threading.Lock()
        self._current_session = None
        self._session_lock = threading.Lock()
        self._last_recorded_key = None

    def set_discord_enabled(self, enabled):
        with self._lock:
            self._show_on_discord = enabled

    def get_session(self):
        with self._session_lock:
            return self._current_session

    def kick(self):
        self._wake.set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    async def _read_thumbnail(self, props):
        thumb = props.thumbnail
        if thumb is None:
            return None
        try:
            from winsdk.windows.storage.streams import DataReader
            stream = await thumb.open_read_async()
            size = stream.size
            if not size:
                return None
            reader = DataReader(stream.get_input_stream_at(0))
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            return bytes(buf)
        except Exception:
            return None

    async def _read_session(self):
        manager = await MediaManager.request_async()
        for s in manager.get_sessions():
            if APPLE_MUSIC_AUMID_HINT in (s.source_app_user_model_id or "").lower():
                return s
        return None

    @staticmethod
    def _decode_repeat(mode) -> str:
        # winsdk MediaPlaybackAutoRepeatMode: 0=None, 1=Track, 2=List
        try:
            v = int(mode)
        except Exception:
            return "none"
        return {0: "none", 1: "track", 2: "list"}.get(v, "none")

    @staticmethod
    def _decode_shuffle(value) -> bool:
        # IsShuffleActive is a Nullable<bool>; winsdk surfaces it as bool or None.
        return bool(value) if value is not None else False

    async def _read_track(self, session):
        info = session.get_playback_info()
        playing = info.playback_status == PlaybackStatus.PLAYING
        try:
            shuffle = self._decode_shuffle(info.is_shuffle_active)
        except Exception:
            shuffle = False
        try:
            repeat = self._decode_repeat(info.auto_repeat_mode)
        except Exception:
            repeat = "none"
        props = await session.try_get_media_properties_async()
        timeline = session.get_timeline_properties()
        position = timeline.position.total_seconds() if timeline.position else 0
        end = timeline.end_time.total_seconds() if timeline.end_time else 0
        now = int(time.time())
        start_ts = now - int(position)
        end_ts = start_ts + int(end) if end > 0 else None
        thumb_bytes = await self._read_thumbnail(props)
        return {
            "playing": playing,
            "title": props.title or "",
            "artist": props.artist or "",
            "album": props.album_title or "",
            "position": position,
            "duration": end,
            "start": start_ts,
            "end": end_ts,
            "shuffle": shuffle,
            "repeat": repeat,
            "thumb_bytes": thumb_bytes,
        }

    def _record_play(self, track):
        key = f"{track['artist']}|{track['title']}|{track['start']}"
        if key == self._last_recorded_key:
            return
        if track.get("position", 0) > 5:
            return
        self._last_recorded_key = key
        self.history.add({
            "date": date.today().isoformat(),
            "ts": int(time.time()),
            "artist": track["artist"],
            "title": track["title"],
            "album": track["album"],
            "duration": int(track.get("duration") or 0),
            "listened": int(track.get("duration") or 0),
        })

    def _run(self):
        rpc = None
        last_key = None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while not self._stop.is_set():
            try:
                with self._lock:
                    discord_on = self._show_on_discord

                if discord_on and rpc is None:
                    rpc = Presence(DISCORD_CLIENT_ID)
                    rpc.connect()

                session = loop.run_until_complete(self._read_session())
                with self._session_lock:
                    self._current_session = session
                if not session:
                    if rpc and last_key is not None:
                        rpc.clear()
                        last_key = None
                    self.on_update({"status": "Apple Music not running", "track": None, "discord_on": discord_on})
                    self._wake_or_wait()
                    continue

                track = loop.run_until_complete(self._read_track(session))

                if not track["playing"]:
                    if rpc and last_key is not None:
                        rpc.clear()
                        last_key = None
                    self.on_update({"status": "Paused", "track": self._serialize_track(track), "discord_on": discord_on})
                    self._wake_or_wait()
                    continue

                self._record_play(track)
                key = f"{track['artist']}|{track['title']}|{track['start']}"
                discord_artwork = None
                if track.get("thumb_bytes"):
                    discord_artwork = self.artwork_host.upload(track["thumb_bytes"])

                if discord_on and rpc and key != last_key:
                    q = urllib.parse.quote(f"{track['artist']} {track['title']}")
                    payload = {
                        "name": "Apple Music",
                        "details": (track["title"] or "Unknown")[:128],
                        "state": (track["artist"] or "Unknown")[:128],
                        "large_image": discord_artwork or "apple_music",
                        "large_text": (track["album"] or track["title"])[:128],
                        "small_image": "apple_music",
                        "small_text": "Apple Music",
                        "start": track["start"],
                        "activity_type": ActivityType.LISTENING,
                        "status_display_type": StatusDisplayType.DETAILS,
                        "buttons": [
                            {"label": "Search on YouTube", "url": f"https://music.youtube.com/search?q={q}"},
                            {"label": "Search on Spotify", "url": f"https://open.spotify.com/search/{q}"},
                        ],
                    }
                    if track["end"]:
                        payload["end"] = track["end"]
                    try:
                        rpc.update(**payload)
                    except Exception:
                        payload.pop("status_display_type", None)
                        try:
                            rpc.update(**payload)
                        except Exception:
                            payload.pop("activity_type", None)
                            payload.pop("name", None)
                            try:
                                rpc.update(**payload)
                            except Exception:
                                payload.pop("buttons", None)
                                rpc.update(**payload)
                    last_key = key

                if not discord_on and rpc:
                    try:
                        rpc.clear(); rpc.close()
                    except Exception:
                        pass
                    rpc = None
                    last_key = None

                self.on_update({"status": "Playing", "track": self._serialize_track(track), "discord_on": discord_on})

            except Exception as e:
                rpc = None
                self.on_update({"status": f"Error: {e}", "track": None, "discord_on": False})
            self._wake_or_wait()

        try:
            if rpc:
                rpc.clear(); rpc.close()
        except Exception:
            pass

    def _serialize_track(self, track):
        thumb_bytes = track.get("thumb_bytes")
        cover_data_url = None
        accent = None
        if thumb_bytes:
            try:
                img = Image.open(io.BytesIO(thumb_bytes)).convert("RGB")
                accent = dominant_color(img)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                cover_data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                pass
        return {
            "playing": track.get("playing"),
            "title": track.get("title"),
            "artist": track.get("artist"),
            "album": track.get("album"),
            "duration": int(track.get("duration") or 0),
            "start": int(track.get("start") or 0),
            "shuffle": bool(track.get("shuffle")),
            "repeat": track.get("repeat") or "none",
            "cover_data_url": cover_data_url,
            "accent": accent,
        }

    def _wake_or_wait(self):
        self._wake.wait(POLL_SECONDS)
        self._wake.clear()


def _parse_version(s: str) -> tuple:
    s = (s or "").lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in s.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


UPDATE_LOG_PATH = SETTINGS_DIR / "update.log"


def _update_log(msg: str) -> None:
    """Append a single line to the update log. Never raises."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(UPDATE_LOG_PATH, "a", encoding="utf-8") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def cleanup_stale_update_artifacts() -> None:
    """Remove leftover .new / .bat files from a previous update attempt.

    Run on every start. If we're alive at this point, we're the binary the
    user wants to run; any stray .new file is from an aborted swap and is
    safe to nuke.
    """
    new_path = INSTALL_DIR / f"{APP_NAME}.exe.new"
    bat_path = INSTALL_DIR / "_update.bat"
    for p in (new_path, bat_path):
        try:
            if p.exists():
                p.unlink()
                _update_log(f"cleanup: removed stale {p.name}")
        except Exception as e:
            _update_log(f"cleanup: failed to remove {p.name}: {e}")


class Updater:
    """Checks GitHub Releases for newer versions and swaps the EXE on quit.

    Only active when running as a frozen, self-installed EXE. Running from
    source (python app.py) or from any path other than INSTALLED_EXE is a
    no-op so contributors don't get update prompts during development.
    """

    DOWNLOAD_CHUNK = 64 * 1024
    DOWNLOAD_PROGRESS_INTERVAL = 0.25  # seconds between progress callbacks
    DOWNLOAD_RETRIES = 3

    def __init__(self, on_status):
        self.on_status = on_status
        self._lock = threading.Lock()
        self._busy = False
        self._latest: dict | None = None
        self._ready_swap_bat: Path | None = None
        self._state: dict = {"kind": "idle"}

    def is_active(self) -> bool:
        return getattr(sys, "frozen", False) and EXE_PATH == INSTALLED_EXE

    def has_pending(self) -> bool:
        return self._ready_swap_bat is not None and self._ready_swap_bat.exists()

    def state(self) -> dict:
        return dict(self._state)

    def latest(self) -> dict | None:
        return dict(self._latest) if self._latest else None

    def _set_state(self, **fields):
        self._state = fields
        try:
            self.on_status(dict(fields))
        except Exception:
            pass

    def start_periodic(self):
        if not self.is_active():
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        # Initial delay so update I/O doesn't fight with startup.
        time.sleep(20)
        while True:
            try:
                self.check(silent=True, auto_download=True)
            except Exception as e:
                _update_log(f"loop: {e}")
            time.sleep(UPDATE_CHECK_INTERVAL_SECONDS)

    # ---- public API ----

    def check_async(self, auto_download: bool = True) -> bool:
        """Kick off a check on a background thread. Returns False if already busy."""
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(
            target=self._run_check, args=(auto_download,), daemon=True
        ).start()
        return True

    def _run_check(self, auto_download: bool):
        try:
            self._set_state(kind="checking")
            info = self._fetch_latest()
            if not info.get("ok"):
                self._set_state(kind="error", error=info.get("error") or "fetch failed")
                _update_log(f"check failed: {info.get('error')}")
                return
            if not info.get("available"):
                self._set_state(kind="up_to_date", current=APP_VERSION)
                _update_log(f"up to date (current={APP_VERSION}, latest={info.get('tag')})")
                return
            self._latest = {k: v for k, v in info.items() if k != "ok"}
            _update_log(f"update available: {self._latest.get('version')}")
            self._set_state(
                kind="available",
                version=self._latest.get("version"),
                tag=self._latest.get("tag"),
                size=self._latest.get("size", 0),
                notes=self._latest.get("notes", ""),
            )
            if auto_download:
                self._do_download()
        finally:
            with self._lock:
                self._busy = False

    # Legacy synchronous helpers kept for the periodic loop.
    def check(self, silent: bool = True, auto_download: bool = False) -> dict:
        with self._lock:
            if self._busy:
                return {"ok": False, "error": "busy"}
            self._busy = True
        try:
            info = self._fetch_latest()
            if not info.get("ok"):
                self._set_state(kind="error", error=info.get("error") or "fetch failed")
                return info
            if not info.get("available"):
                self._set_state(kind="up_to_date", current=APP_VERSION)
                return info
            self._latest = {k: v for k, v in info.items() if k != "ok"}
            self._set_state(
                kind="available",
                version=self._latest.get("version"),
                tag=self._latest.get("tag"),
                size=self._latest.get("size", 0),
                notes=self._latest.get("notes", ""),
            )
            if auto_download:
                self._do_download()
            return info
        finally:
            with self._lock:
                self._busy = False

    def _fetch_latest(self) -> dict:
        try:
            req = urllib.request.Request(
                GITHUB_API_LATEST,
                headers={
                    "User-Agent": UPDATE_USER_AGENT,
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if data.get("draft") or data.get("prerelease"):
            return {"ok": True, "available": False, "reason": "prerelease"}

        tag = data.get("tag_name") or ""
        latest = _parse_version(tag)
        current = _parse_version(APP_VERSION)
        if latest <= current:
            return {"ok": True, "available": False, "tag": tag}

        assets = data.get("assets", []) or []
        exe_asset = next(
            (a for a in assets if (a.get("name") or "").lower().endswith(".exe")),
            None,
        )
        if not exe_asset:
            return {"ok": False, "error": "no exe asset in release"}
        sha_asset = next(
            (a for a in assets if (a.get("name") or "").lower().endswith(".sha256")),
            None,
        )
        return {
            "ok": True,
            "available": True,
            "tag": tag,
            "version": ".".join(str(x) for x in latest),
            "url": exe_asset.get("browser_download_url"),
            "size": int(exe_asset.get("size") or 0),
            "sha256_url": sha_asset.get("browser_download_url") if sha_asset else None,
            "notes": data.get("body") or "",
            "html_url": data.get("html_url") or "",
        }

    # ---- download + verify + stage ----

    def _do_download(self) -> dict:
        if not self._latest:
            return {"ok": False, "error": "no update info"}
        if not self.is_active():
            return {"ok": False, "error": "not installed"}

        new_path = INSTALL_DIR / f"{APP_NAME}.exe.new"
        url = self._latest["url"]
        total = int(self._latest.get("size") or 0)
        version = self._latest.get("version", "")
        last_error = ""

        for attempt in range(1, self.DOWNLOAD_RETRIES + 1):
            try:
                _update_log(f"download attempt {attempt}/{self.DOWNLOAD_RETRIES}: {url}")
                self._set_state(
                    kind="downloading",
                    version=version,
                    progress=0,
                    bytes=0,
                    total=total,
                )
                INSTALL_DIR.mkdir(parents=True, exist_ok=True)
                # Best-effort: clear any stale partial file before each attempt.
                try:
                    if new_path.exists():
                        new_path.unlink()
                except Exception:
                    pass

                req = urllib.request.Request(
                    url, headers={"User-Agent": UPDATE_USER_AGENT}
                )
                received = 0
                last_emit = 0.0
                with urllib.request.urlopen(req, timeout=30) as resp, open(new_path, "wb") as f:
                    if total <= 0:
                        try:
                            total = int(resp.headers.get("Content-Length") or 0)
                        except Exception:
                            total = 0
                    while True:
                        chunk = resp.read(self.DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                        now = time.time()
                        if now - last_emit >= self.DOWNLOAD_PROGRESS_INTERVAL:
                            last_emit = now
                            pct = int((received / total) * 100) if total else 0
                            self._set_state(
                                kind="downloading",
                                version=version,
                                progress=pct,
                                bytes=received,
                                total=total,
                            )

                # Final 100% before we move on to verification.
                self._set_state(
                    kind="downloading",
                    version=version,
                    progress=100 if total else 0,
                    bytes=received,
                    total=total or received,
                )

                if total and received < total:
                    raise IOError(f"truncated download: {received}/{total}")

                self._set_state(kind="verifying", version=version)
                if not self._verify_sha(new_path):
                    raise IOError("checksum mismatch")

                swap_bat = self._write_swap_script(new_path, INSTALLED_EXE)
                self._ready_swap_bat = swap_bat
                _update_log(f"staged update {version} ({received} bytes)")
                self._set_state(kind="ready", version=version, size=received)
                return {"ok": True, "swap_bat": str(swap_bat)}

            except Exception as e:
                last_error = str(e)
                _update_log(f"download attempt {attempt} failed: {e}")
                try:
                    if new_path.exists():
                        new_path.unlink()
                except Exception:
                    pass
                if attempt < self.DOWNLOAD_RETRIES:
                    time.sleep(2 * attempt)

        self._set_state(kind="error", error=last_error or "download failed")
        return {"ok": False, "error": last_error or "download failed"}

    def _verify_sha(self, new_path: Path) -> bool:
        sha_url = (self._latest or {}).get("sha256_url")
        if not sha_url:
            # No published checksum; accept the download as-is.
            return True
        try:
            req = urllib.request.Request(sha_url, headers={"User-Agent": UPDATE_USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                sha_text = resp.read().decode("utf-8", errors="replace").strip()
        except Exception as e:
            _update_log(f"verify: failed to fetch checksum: {e}")
            return True  # don't fail the update for a flaky checksum endpoint
        expected = sha_text.split()[0].lower() if sha_text else ""
        if not expected:
            return True
        h = hashlib.sha256()
        with open(new_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        actual = h.hexdigest().lower()
        ok = expected == actual
        if not ok:
            _update_log(f"verify: sha mismatch (expected {expected[:16]}..., got {actual[:16]}...)")
        return ok

    @staticmethod
    def _write_swap_script(new_exe: Path, target_exe: Path) -> Path:
        """Write a self-deleting batch that replaces the EXE once the parent quits.

        Improvements over v1:
         - Logs to %APPDATA%\\Tune\\update.log so failures are visible.
         - Uses the parent PID via wmic to wait for *that specific instance*
           to exit, rather than matching any Tune.exe (which falsely matches
           a relaunched copy of ourselves).
         - Retries the move with backoff, then falls back to copy + delete.
         - Always relaunches Tune.exe at the end, so the user is never left
           without a running app.
        """
        swap_bat = INSTALL_DIR / "_update.bat"
        log_path = UPDATE_LOG_PATH
        parent_pid = os.getpid()
        content = (
            "@echo off\r\n"
            "setlocal EnableDelayedExpansion\r\n"
            f'set "LOG={log_path}"\r\n'
            f'set "PARENT_PID={parent_pid}"\r\n'
            f'set "NEW_EXE={new_exe}"\r\n'
            f'set "TARGET_EXE={target_exe}"\r\n'
            f'set "INSTALL_DIR={INSTALL_DIR}"\r\n'
            'echo [swap] start pid=%PARENT_PID% >> "%LOG%"\r\n'
            "set TRIES=0\r\n"
            ":wait\r\n"
            'tasklist /fi "PID eq %PARENT_PID%" /nh 2>nul | find "%PARENT_PID%" >nul\r\n'
            "if errorlevel 1 goto swap\r\n"
            "set /a TRIES+=1\r\n"
            "if %TRIES% GEQ 90 goto force\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            "goto wait\r\n"
            ":force\r\n"
            'echo [swap] parent still alive after 90s, force-killing >> "%LOG%"\r\n'
            'taskkill /pid %PARENT_PID% /f >nul 2>&1\r\n'
            "timeout /t 2 /nobreak >nul\r\n"
            ":swap\r\n"
            "set MOVE_TRIES=0\r\n"
            ":swap_retry\r\n"
            'move /y "%NEW_EXE%" "%TARGET_EXE%" >nul 2>&1\r\n'
            "if not errorlevel 1 goto launch\r\n"
            "set /a MOVE_TRIES+=1\r\n"
            "if %MOVE_TRIES% GEQ 8 goto fallback\r\n"
            'echo [swap] move failed, retry %MOVE_TRIES% >> "%LOG%"\r\n'
            "timeout /t 1 /nobreak >nul\r\n"
            "goto swap_retry\r\n"
            ":fallback\r\n"
            'echo [swap] move kept failing, falling back to copy+del >> "%LOG%"\r\n'
            'copy /y "%NEW_EXE%" "%TARGET_EXE%" >nul 2>&1\r\n'
            "if errorlevel 1 (\r\n"
            '  echo [swap] copy fallback also failed; aborting >> "%LOG%"\r\n'
            '  start "" "%TARGET_EXE%"\r\n'
            "  goto cleanup\r\n"
            ")\r\n"
            'del /f /q "%NEW_EXE%" >nul 2>&1\r\n'
            ":launch\r\n"
            'echo [swap] swap successful, launching new build >> "%LOG%"\r\n'
            'start "" "%TARGET_EXE%"\r\n'
            ":cleanup\r\n"
            'del /f /q "%NEW_EXE%" >nul 2>&1\r\n'
            'echo [swap] done >> "%LOG%"\r\n'
            '(goto) 2>nul & del "%~f0"\r\n'
        )
        swap_bat.write_text(content, encoding="ascii")
        return swap_bat

    def trigger_swap_on_exit(self) -> bool:
        """Spawn the swap script. Caller must immediately exit so the EXE handle is released."""
        if not self.has_pending():
            _update_log("trigger_swap: no pending swap_bat")
            return False
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", str(self._ready_swap_bat)],
                cwd=str(INSTALL_DIR),
                creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                close_fds=True,
            )
            _update_log(f"trigger_swap: spawned {self._ready_swap_bat}")
            return True
        except Exception as e:
            _update_log(f"trigger_swap failed: {e}")
            return False


def make_tray_icon_image() -> Image.Image:
    logo_path = WEB_DIR / "logo-64.png"
    if logo_path.exists():
        try:
            return Image.open(logo_path).convert("RGBA")
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=DEFAULT_ACCENT)
    d.ellipse((22, 22, 42, 42), fill="white")
    return img


class Api:
    def __init__(self, app):
        self.app = app

    def ready(self):
        return {
            **self.app.settings,
            "autostart": autostart_enabled(),
            "version": APP_VERSION,
            "update": self.app._update_state,
            "updater_active": self.app.updater.is_active(),
            "homepage_url": HOMEPAGE_URL,
        }

    def check_for_updates(self):
        return self.app.check_for_updates()

    def get_update_state(self):
        return self.app.get_update_state()

    def install_update(self):
        return self.app.install_pending_update()

    def media_action(self, action):
        self.app.media_action(action)

    def media_action_seek(self, seconds):
        try:
            self.app.media_seek(int(seconds))
        except Exception:
            pass
        return True

    def save_setting(self, key, value):
        self.app.save_setting(key, value)
        return True

    def set_autostart(self, enabled):
        try:
            set_autostart(bool(enabled))
            return True
        except Exception:
            return False

    def toggle_mini(self):
        self.app.toggle_mini()

    def minimize(self):
        self.app.minimize()

    def close_window(self):
        self.app.close_button()

    def quit(self):
        self.app.quit_app()

    def open_url(self, url):
        try:
            webbrowser.open(str(url))
        except Exception:
            pass
        return True

    def resize_window(self, w, h, drag_left=False):
        try:
            self.app.resize_window(int(w), int(h), bool(drag_left))
        except Exception:
            pass
        return True


class AppController:
    def __init__(self):
        self.settings = load_settings()
        self.history = HistoryStore()
        self.lyrics_cache = LyricsCache()
        self.artwork_host = ArtworkHost()
        self.window: webview.Window | None = None
        self.is_mini = False
        self._lyrics_track_key = None
        self._tray_icon = None
        self.worker = PresenceWorker(self._on_worker_update, self.history, self.artwork_host)
        self.worker.set_discord_enabled(self.settings["show_on_discord"])
        self.updater = Updater(self._on_updater_status)
        self._update_state: dict = {"kind": "idle"}

    def _on_updater_status(self, status: dict):
        self._update_state = status
        self.push({"update": status})

    def start_updater(self):
        if self.settings.get("auto_check_updates", True):
            self.updater.start_periodic()

    def check_for_updates(self) -> dict:
        """Kick off a non-blocking check. Returns immediately."""
        if not self.updater.is_active():
            return {"ok": False, "error": "running from source — auto-update disabled"}
        if self.updater.has_pending():
            # Surface the pending state so the UI shows "Restart to install".
            self._on_updater_status({"kind": "ready", **(self.updater.latest() or {})})
            return {"ok": True, "kind": "ready"}
        ok = self.updater.check_async(auto_download=True)
        return {"ok": ok, "kind": "checking" if ok else "busy"}

    def get_update_state(self) -> dict:
        return {
            **self.updater.state(),
            "active": self.updater.is_active(),
            "pending": self.updater.has_pending(),
            "current": APP_VERSION,
        }

    def install_pending_update(self) -> bool:
        if not self.updater.has_pending():
            return False
        if not self.updater.trigger_swap_on_exit():
            return False
        # Give the swap script a moment to start before we release the EXE handle.
        threading.Timer(0.2, self.quit_app).start()
        return True

    def push(self, payload: dict):
        if not self.window:
            return
        try:
            self.window.evaluate_js(f"window.__app && window.__app.push({json.dumps(payload)})")
        except Exception:
            pass

    def _on_worker_update(self, update: dict):
        track = update.get("track")
        if track:
            track_key = f"{track.get('artist', '')}|{track.get('title', '')}"
            if track_key != self._lyrics_track_key:
                self._lyrics_track_key = track_key
                threading.Thread(target=self._fetch_and_push_lyrics, args=(track,), daemon=True).start()

        self.push({
            **update,
            "recents": self.history.recent_unique(8),
            "history": self.history.recent(80),
            "stats": self.history.today_stats(),
        })

    def _fetch_and_push_lyrics(self, track):
        result = self.lyrics_cache.get(
            track.get("artist") or "",
            track.get("title") or "",
            track.get("album") or "",
            track.get("duration") or 0,
        )
        track_key = f"{track.get('artist', '')}|{track.get('title', '')}"
        if track_key == self._lyrics_track_key:
            self.push({"lyrics_for": track_key, "lyrics": result or []})

    def media_action(self, action):
        session = self.worker.get_session()
        if not session:
            return
        try:
            if action == "toggle":
                session.try_toggle_play_pause_async()
            elif action == "next":
                session.try_skip_next_async()
            elif action == "prev":
                session.try_skip_previous_async()
            elif action == "shuffle_toggle":
                self._toggle_shuffle(session)
            elif action == "repeat_cycle":
                self._cycle_repeat(session)
        except Exception:
            pass
        # Wake the worker so the UI reflects the new state without waiting for the poll cycle.
        threading.Timer(0.08, self.worker.kick).start()
        threading.Timer(0.4, self.worker.kick).start()

    def media_seek(self, seconds: int):
        session = self.worker.get_session()
        if not session:
            return
        try:
            # SMTC TimeSpan is in 100ns ticks; pass an int via timedelta-equivalent.
            from datetime import timedelta
            session.try_change_playback_position_async(timedelta(seconds=max(0, int(seconds))))
        except Exception:
            pass
        threading.Timer(0.08, self.worker.kick).start()

    def _toggle_shuffle(self, session):
        try:
            current = session.get_playback_info().is_shuffle_active
        except Exception:
            current = None
        new_value = not bool(current) if current is not None else True
        try:
            session.try_change_shuffle_active_async(new_value)
        except Exception:
            pass

    def _cycle_repeat(self, session):
        # None -> List -> Track -> None (matches Apple Music's button order)
        next_mode = {0: 2, 2: 1, 1: 0}
        try:
            current = int(session.get_playback_info().auto_repeat_mode)
        except Exception:
            current = 0
        target = next_mode.get(current, 2)
        try:
            session.try_change_auto_repeat_mode_async(target)
        except Exception:
            pass

    def save_setting(self, key, value):
        self.settings[key] = value
        save_json(SETTINGS_PATH, self.settings)
        if key == "show_on_discord":
            self.worker.set_discord_enabled(bool(value))
        elif key == "always_on_top" and self.window:
            try:
                self.window.on_top = bool(value)
            except Exception:
                pass

    def resize_window(self, w, h, drag_left=False):
        if not self.window:
            return
        try:
            self.window.resize(w, h)
        except Exception:
            pass

    def toggle_mini(self):
        self.is_mini = not self.is_mini
        if self.window:
            try:
                self.window.resize(*( (380, 480) if self.is_mini else (560, 800) ))
            except Exception:
                pass
            self.window.evaluate_js(f"window.__app.setMini({'true' if self.is_mini else 'false'})")

    def minimize(self):
        if self.window:
            try:
                self.window.minimize()
            except Exception:
                pass

    def close_button(self):
        if self.settings.get("minimize_to_tray", True) and self.window:
            try:
                self.window.hide()
            except Exception:
                pass
        else:
            self.quit_app()

    def _find_app_hwnds(self):
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        pid = kernel32.GetCurrentProcessId()
        hwnds = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(hwnd, _lparam):
            wpid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid:
                hwnds.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        return hwnds

    def apply_window_icon(self):
        try:
            ico_path = BASE_DIR / "icon.ico"
            if not ico_path.exists():
                return False
            user32 = ctypes.windll.user32
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            big = user32.LoadImageW(0, str(ico_path), IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
            small = user32.LoadImageW(0, str(ico_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            GCLP_HICON = -14
            GCLP_HICONSM = -34
            for hwnd in self._find_app_hwnds():
                if big:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
                if small:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
                try:
                    if big:
                        user32.SetClassLongPtrW(hwnd, GCLP_HICON, big)
                    if small:
                        user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, small)
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def setup_tray(self):
        image = make_tray_icon_image()
        menu_items = [
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("Hide", self._tray_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Play/Pause", lambda: self.media_action("toggle")),
            pystray.MenuItem("Next", lambda: self.media_action("next")),
            pystray.MenuItem("Previous", lambda: self.media_action("prev")),
            pystray.Menu.SEPARATOR,
        ]
        if self.updater.is_active():
            menu_items.extend([
                pystray.MenuItem(
                    lambda item: self._tray_update_label(),
                    self._tray_update_action,
                ),
                pystray.Menu.SEPARATOR,
            ])
        menu_items.append(pystray.MenuItem("Quit", self._tray_quit))
        self._tray_icon = pystray.Icon(APP_NAME, image, APP_DISPLAY_NAME, pystray.Menu(*menu_items))
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _tray_update_label(self) -> str:
        s = self._update_state or {}
        kind = s.get("kind", "idle")
        v = s.get("version") or s.get("tag") or ""
        if kind == "ready":
            return f"Restart to install update v{v}".strip()
        if kind == "downloading":
            pct = s.get("progress")
            if isinstance(pct, int):
                return f"Downloading update... {pct}%"
            return "Downloading update..."
        if kind == "verifying":
            return "Verifying update..."
        if kind == "checking":
            return "Checking for updates..."
        if kind == "error":
            return "Update failed (click to retry)"
        if kind == "up_to_date":
            return "Up to date"
        return "Check for updates"

    def _tray_update_action(self, icon=None, item=None):
        kind = (self._update_state or {}).get("kind", "idle")
        if kind == "ready":
            self.install_pending_update()
            return
        if kind in ("downloading", "verifying", "checking"):
            return
        # Non-blocking: returns immediately, status updates flow via push().
        self.check_for_updates()

    def _tray_show(self, icon=None, item=None):
        if self.window:
            try:
                self.window.show()
                self.window.restore()
            except Exception:
                pass

    def _tray_hide(self, icon=None, item=None):
        if self.window:
            try:
                self.window.hide()
            except Exception:
                pass

    def _tray_quit(self, icon=None, item=None):
        self.quit_app()

    def quit_app(self):
        # If an update has been downloaded and verified, kick off the swap
        # script before we tear down so the new binary starts after exit.
        try:
            if self.updater.has_pending():
                self.updater.trigger_swap_on_exit()
        except Exception:
            pass
        try:
            self.worker.stop()
        except Exception:
            pass
        try:
            if self._tray_icon:
                self._tray_icon.stop()
        except Exception:
            pass
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass
        os._exit(0)


def main():
    app = AppController()
    api = Api(app)

    html_path = WEB_DIR / "index.html"
    app.window = webview.create_window(
        APP_DISPLAY_NAME,
        str(html_path),
        width=560,
        height=800,
        min_size=(380, 460),
        frameless=True,
        easy_drag=False,
        resizable=True,
        background_color="#050507",
        on_top=app.settings.get("always_on_top", False),
        js_api=api,
    )

    def on_loaded():
        # Window handle is not always available immediately after the JS loaded
        # event fires, so retry a few times across the first few seconds.
        for delay in (0.05, 0.3, 0.8, 1.5, 3.0):
            threading.Timer(delay, app.apply_window_icon).start()
        app.setup_tray()
        app.worker.start()
        app.start_updater()
        if app.settings.get("start_minimized") and app.window:
            try:
                app.window.hide()
            except Exception:
                pass

    app.window.events.loaded += on_loaded
    webview.start(debug=False)


if __name__ == "__main__":
    main()
