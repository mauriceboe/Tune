"""Synced-lyrics fetcher hitting LRCLIB."""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request

from .config import APP_NAME, APP_VERSION


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


class LyricsCache:
    """In-memory LRC cache with two LRCLIB endpoints (exact + search)."""

    def __init__(self):
        self._cache: dict = {}
        self._lock = threading.Lock()

    def get(self, artist: str, title: str, album: str, duration: int):
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
