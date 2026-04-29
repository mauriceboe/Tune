"""Tune backend daemon — JSON-RPC over stdin/stdout.

Spawned by the Tauri host as a sidecar. Each line on stdin is a JSON message:

    Request:  {"id": 1, "method": "media_action", "params": {"action": "toggle"}}
    Response: {"id": 1, "result": ...}
              {"id": 1, "error": "..."}
    Event:    {"event": "track_update", "data": {...}}

The daemon owns the SMTC poller, Discord RPC, history, lyrics fetch and
artwork cache. The host owns the window, tray, and process lifecycle.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from datetime import datetime
from typing import Any, Callable

# When the parent (Tauri) creates the stdout pipe, Python guesses the encoding
# from the system default (often cp1252 on Windows). Track titles, artist
# names and lyrics regularly contain non-Latin1 characters, which get encoded
# as invalid byte sequences and crash the Rust reader's UTF-8 LineReader.
# Force UTF-8 on both ends and replace anything that can't round-trip.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from .artwork import ArtworkHost
from .config import APP_NAME, APP_VERSION, GITHUB_REPO, LOG_PATH
from .lyrics import LyricsCache
from .storage import HistoryStore, load_settings, save_settings
from .worker import PresenceWorker

_log_lock = threading.Lock()


def log(msg: str) -> None:
    """Append a timestamped line to %APPDATA%\\Tune\\backend.log."""
    with _log_lock:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass


_stdout_lock = threading.Lock()


def emit(message: dict) -> None:
    """Write a single JSON line to stdout (atomic w.r.t. concurrent writers)."""
    with _stdout_lock:
        try:
            sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


class Daemon:
    def __init__(self):
        self.settings = load_settings()
        self.history = HistoryStore()
        self.lyrics_cache = LyricsCache(log=log)
        self.artwork_host = ArtworkHost()
        self._lyrics_track_key: str | None = None
        self.worker = PresenceWorker(
            on_update=self._on_worker_update,
            history=self.history,
            artwork_host=self.artwork_host,
            log=log,
        )
        self.worker.set_discord_enabled(self.settings.get("show_on_discord", True))

    # ---- bootstrap ----

    def start(self) -> None:
        self.worker.start()
        log(f"daemon: started (v{APP_VERSION})")
        emit({"event": "ready", "data": {
            "version": APP_VERSION,
            "settings": self.settings,
            "github_repo": GITHUB_REPO,
        }})

    # ---- worker callback ----

    def _on_worker_update(self, update: dict) -> None:
        track = update.get("track")
        if track:
            track_key = f"{track.get('artist', '')}|{track.get('title', '')}"
            if track_key != self._lyrics_track_key:
                self._lyrics_track_key = track_key
                threading.Thread(
                    target=self._fetch_and_emit_lyrics,
                    args=(track,),
                    daemon=True,
                ).start()
        emit({
            "event": "tick",
            "data": {
                **update,
                "recents": self.worker.enrich_recents_with_covers(self.history.recent_unique(8)),
                "history": self.history.recent(80),
                "stats": self.history.today_stats(),
            },
        })

    def _fetch_and_emit_lyrics(self, track: dict) -> None:
        result = self.lyrics_cache.get(
            track.get("artist") or "",
            track.get("title") or "",
            track.get("album") or "",
            track.get("duration") or 0,
        )
        track_key = f"{track.get('artist', '')}|{track.get('title', '')}"
        if track_key == self._lyrics_track_key:
            emit({"event": "lyrics", "data": {"key": track_key, "lines": result or []}})

    # ---- RPC methods ----

    def rpc_get_state(self, **_: Any) -> dict:
        return {
            "version": APP_VERSION,
            "settings": self.settings,
        }

    def rpc_save_setting(self, key: str, value: Any) -> bool:
        self.settings[key] = value
        save_settings(self.settings)
        if key == "show_on_discord":
            self.worker.set_discord_enabled(bool(value))
        return True

    def rpc_media_action(self, action: str) -> bool:
        session = self.worker.get_session()
        if not session:
            return False
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
            else:
                return False
        except Exception as e:
            log(f"media_action {action!r} failed: {e!r}")
            return False
        threading.Timer(0.08, self.worker.kick).start()
        threading.Timer(0.4, self.worker.kick).start()
        return True

    def rpc_media_seek(self, seconds: int) -> bool:
        session = self.worker.get_session()
        if not session:
            return False
        # winsdk's TryChangePlaybackPositionAsync expects a Windows TimeSpan,
        # which crosses the FFI boundary as an integer count of 100-nanosecond
        # ticks (10_000_000 per second). A datetime.timedelta is rejected with
        # "object cannot be interpreted as an integer".
        ticks = max(0, int(seconds)) * 10_000_000
        try:
            session.try_change_playback_position_async(ticks)
        except Exception as e:
            log(f"media_seek failed: {e!r}")
            return False
        threading.Timer(0.08, self.worker.kick).start()
        return True

    def rpc_recents(self, n: int = 8) -> list:
        return self.worker.enrich_recents_with_covers(self.history.recent_unique(int(n)))

    def rpc_history(self, n: int = 80) -> list:
        return self.history.recent(int(n))

    def rpc_stats(self) -> dict:
        return self.history.today_stats()

    def rpc_shutdown(self) -> bool:
        self.worker.stop()
        log("daemon: shutdown requested")
        return True

    # ---- shuffle/repeat helpers (mirror old AppController) ----

    def _toggle_shuffle(self, session) -> None:
        try:
            current = session.get_playback_info().is_shuffle_active
        except Exception:
            current = None
        new_value = not bool(current) if current is not None else True
        try:
            session.try_change_shuffle_active_async(new_value)
        except Exception:
            pass

    def _cycle_repeat(self, session) -> None:
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

    # ---- dispatch ----

    def dispatch(self, request: dict) -> dict:
        method = request.get("method") or ""
        params = request.get("params") or {}
        handler: Callable | None = getattr(self, f"rpc_{method}", None)
        if handler is None:
            return {"id": request.get("id"), "error": f"unknown method: {method}"}
        try:
            result = handler(**params) if isinstance(params, dict) else handler(*params)
        except TypeError as e:
            return {"id": request.get("id"), "error": f"bad params for {method}: {e}"}
        except Exception as e:
            log(f"rpc {method} crashed: {e!r}\n{traceback.format_exc()}")
            return {"id": request.get("id"), "error": str(e)}
        return {"id": request.get("id"), "result": result}


def main() -> int:
    log(f"daemon: boot pid={__import__('os').getpid()} v{APP_VERSION}")
    daemon = Daemon()
    daemon.start()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                log(f"daemon: bad JSON: {e!r}")
                continue
            response = daemon.dispatch(request)
            emit(response)
    except KeyboardInterrupt:
        pass
    log(f"daemon: stdin closed, shutting down")
    daemon.worker.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
