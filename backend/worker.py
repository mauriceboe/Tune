"""SMTC poll loop + Discord Rich Presence mirror."""

from __future__ import annotations

import asyncio
import base64
import io
import threading
import time
import urllib.parse
from datetime import date

from PIL import Image
from pypresence import ActivityType, Presence, StatusDisplayType
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

from .artwork import ArtworkHost, dominant_color
from .config import DISCORD_CLIENT_ID, POLL_SECONDS
from .storage import HistoryStore

APPLE_MUSIC_AUMID_HINT = "applemusicwin"


class PresenceWorker:
    """Polls SMTC for the active Apple Music session and mirrors it to Discord.

    Pushes updates to a callback the daemon turns into JSON-RPC events
    consumed by the Tauri front-end.
    """

    def __init__(self, on_update, history: HistoryStore, artwork_host: ArtworkHost, log):
        self.on_update = on_update
        self.history = history
        self.artwork_host = artwork_host
        self._log = log
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._show_on_discord = True
        self._lock = threading.Lock()
        self._current_session = None
        self._session_lock = threading.Lock()
        self._last_recorded_key: str | None = None

    # ---- public ----

    def set_discord_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._show_on_discord = bool(enabled)

    def get_session(self):
        with self._session_lock:
            return self._current_session

    def kick(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # ---- SMTC reads ----

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
        try:
            v = int(mode)
        except Exception:
            return "none"
        return {0: "none", 1: "track", 2: "list"}.get(v, "none")

    @staticmethod
    def _decode_shuffle(value) -> bool:
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

    # ---- main loop ----

    def _wake_or_wait(self):
        self._wake.wait(POLL_SECONDS)
        self._wake.clear()

    def _run(self):
        self._log("worker: thread started")
        rpc = None
        rpc_next_retry = 0.0
        last_key: str | None = None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while not self._stop.is_set():
            try:
                with self._lock:
                    discord_on = self._show_on_discord

                if discord_on and rpc is None and time.time() >= rpc_next_retry:
                    try:
                        rpc = Presence(DISCORD_CLIENT_ID)
                        rpc.connect()
                        self._log("worker: Discord RPC connected")
                    except Exception as e:
                        self._log(f"worker: Discord RPC connect failed: {e!r}")
                        rpc = None
                        rpc_next_retry = time.time() + 60

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

                if discord_on and rpc and key != last_key:
                    discord_artwork = None
                    if track.get("thumb_bytes"):
                        discord_artwork = self.artwork_host.upload(
                            track["thumb_bytes"], log=self._log
                        )
                    large_image = discord_artwork or "apple_music"
                    self._log(
                        f"worker: pushing presence track={track.get('title')!r} "
                        f"artist={track.get('artist')!r} large_image={large_image!r}"
                    )
                    q = urllib.parse.quote(f"{track['artist']} {track['title']}")
                    payload = {
                        "name": "Apple Music",
                        "details": (track["title"] or "Unknown")[:128],
                        "state": (track["artist"] or "Unknown")[:128],
                        "large_image": large_image,
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
