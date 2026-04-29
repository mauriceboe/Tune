"""Synced-lyrics fetcher with multi-provider fallback chain.

Providers in order of preference:
  1. LRCLIB        — community-driven, MusicBrainz-aligned (primary)
  2. NetEase Music — large international catalogue, line-level lrc
  3. Lyricsify     — scraping fallback (LRC text dumps)
  4. Megalobiz     — scraping fallback (LRC files indexed via search)

Result shape (one of):
  - None (no lyrics found)
  - list of [start_seconds, text]                          — line-level
  - list of [start_seconds, text, [[w_start, w_text], …]]  — word-level karaoke

Frontend treats the optional 3rd element as the per-line word timeline.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request
from html import unescape
from typing import Optional

from .config import APP_NAME, APP_VERSION

UA = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) {APP_NAME}/{APP_VERSION}"

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, headers: dict | None = None, timeout: float = 4.0) -> bytes | None:
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def _fetch_post(url: str, body: dict, headers: dict | None = None, timeout: float = 4.0) -> bytes | None:
    h = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        h.update(headers)
    data = urllib.parse.urlencode(body).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def parse_lrc(lrc: str) -> list[list]:
    """Parse plain LRC into [[seconds, text], …]."""
    out: list[list] = []
    for raw in lrc.splitlines():
        for m in re.finditer(r"\[(\d+):(\d+)(?:[.:](\d+))?\]", raw):
            mm, ss, frac = m.group(1), m.group(2), m.group(3) or "0"
            t = int(mm) * 60 + int(ss) + int(frac) / (10 ** len(frac))
            text = re.sub(r"\[\d+:\d+(?:[.:]\d+)?\]", "", raw).strip()
            if text:
                out.append([t, text])
    out.sort(key=lambda x: x[0])
    return out


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def fetch_lrclib(artist: str, title: str, album: str, duration: int) -> Optional[list]:
    params = {
        "artist_name": artist,
        "track_name": title,
        "album_name": album or "",
        "duration": str(int(duration or 0)),
    }
    body = _fetch("https://lrclib.net/api/get?" + urllib.parse.urlencode(params))
    if body:
        try:
            data = json.loads(body)
            synced = data.get("syncedLyrics") or ""
            if synced:
                lines = parse_lrc(synced)
                if lines:
                    return lines
        except Exception:
            pass
    body = _fetch("https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": f"{artist} {title}"}))
    if body:
        try:
            for hit in (json.loads(body) or [])[:3]:
                synced = hit.get("syncedLyrics") or ""
                if synced:
                    lines = parse_lrc(synced)
                    if lines:
                        return lines
        except Exception:
            pass
    return None


# NetEase Music — legacy public API, no auth needed.
NETEASE_HEADERS = {
    "Referer": "https://music.163.com/",
    "Origin": "https://music.163.com",
}


def _netease_search(query: str) -> Optional[int]:
    body = _fetch_post(
        "https://music.163.com/api/search/get/web",
        {"s": query, "type": "1", "limit": "5", "offset": "0"},
        headers=NETEASE_HEADERS,
    )
    if not body:
        return None
    try:
        data = json.loads(body)
        songs = ((data.get("result") or {}).get("songs") or [])
        if songs:
            return int(songs[0].get("id"))
    except Exception:
        pass
    return None


def _netease_lyric(song_id: int) -> dict | None:
    body = _fetch(
        f"https://music.163.com/api/song/lyric?id={song_id}&lv=-1&kv=-1&tv=-1",
        headers=NETEASE_HEADERS,
    )
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def fetch_netease(artist: str, title: str, album: str, duration: int) -> Optional[list]:
    queries = [f"{artist} {title}", f"{title} {artist}", title]
    song_id: Optional[int] = None
    for q in queries:
        song_id = _netease_search(q)
        if song_id:
            break
    if not song_id:
        return None
    data = _netease_lyric(song_id)
    if not data:
        return None
    lrc_text = ((data.get("lrc") or {}).get("lyric")) or ""
    klyric_text = ((data.get("klyric") or {}).get("lyric")) or ""
    if klyric_text:
        word_lines = _parse_klyric(klyric_text)
        if word_lines:
            return word_lines
    if lrc_text:
        lines = parse_lrc(lrc_text)
        if lines:
            return lines
    return None


_KLYRIC_LINE_RE = re.compile(r"\[(\d+),(\d+)\](.*)")
_KLYRIC_WORD_RE = re.compile(r"\((\d+),(\d+),\d+\)([^()]*)")


def _parse_klyric(text: str) -> list[list]:
    """NetEase klyric format: [start_ms,duration_ms](w_offset_ms,w_dur_ms,?)word…"""
    lines: list[list] = []
    for raw in text.splitlines():
        m = _KLYRIC_LINE_RE.match(raw)
        if not m:
            continue
        line_start_ms = int(m.group(1))
        rest = m.group(3)
        words = []
        full_text = ""
        for wm in _KLYRIC_WORD_RE.finditer(rest):
            w_offset_ms = int(wm.group(1))
            w_text = wm.group(3)
            words.append([w_offset_ms / 1000.0, w_text])
            full_text += w_text
        if not full_text.strip():
            continue
        if words:
            lines.append([line_start_ms / 1000.0, full_text.strip(), words])
        else:
            lines.append([line_start_ms / 1000.0, full_text.strip()])
    lines.sort(key=lambda x: x[0])
    return lines


# Lyricsify scraping — uses their search page + LRC text on song page.
def fetch_lyricsify(artist: str, title: str, album: str, duration: int) -> Optional[list]:
    q = urllib.parse.quote(f"{artist} {title}")
    body = _fetch(f"https://www.lyricsify.com/search?q={q}")
    if not body:
        return None
    try:
        html = body.decode("utf-8", errors="ignore")
    except Exception:
        return None
    m = re.search(r'href="(/lyrics/[^"]+)"', html)
    if not m:
        return None
    page = _fetch(f"https://www.lyricsify.com{m.group(1)}")
    if not page:
        return None
    try:
        page_html = page.decode("utf-8", errors="ignore")
    except Exception:
        return None
    pre = re.search(r'<div[^>]*id=["\']lyrics_text["\'][^>]*>(.*?)</div>', page_html, re.S)
    if not pre:
        pre = re.search(r'<pre[^>]*>(.*?)</pre>', page_html, re.S)
    if not pre:
        return None
    lrc_text = unescape(re.sub(r"<[^>]+>", "", pre.group(1)))
    lines = parse_lrc(lrc_text)
    return lines or None


# Megalobiz scraping — community LRC repository.
def fetch_megalobiz(artist: str, title: str, album: str, duration: int) -> Optional[list]:
    q = urllib.parse.quote(f"{artist} {title}")
    body = _fetch(f"https://www.megalobiz.com/search/all?qry={q}&display=more")
    if not body:
        return None
    try:
        html = body.decode("utf-8", errors="ignore")
    except Exception:
        return None
    m = re.search(r'href="(/lrc/maker/[^"]+)"', html)
    if not m:
        return None
    page = _fetch(f"https://www.megalobiz.com{m.group(1)}")
    if not page:
        return None
    try:
        page_html = page.decode("utf-8", errors="ignore")
    except Exception:
        return None
    pre = re.search(r'<div[^>]*class=["\']entity_more_intro["\'][^>]*>(.*?)</div>', page_html, re.S)
    if not pre:
        pre = re.search(r'<span[^>]*class=["\']lyrics_details[^"\']*["\'][^>]*>(.*?)</span>', page_html, re.S)
    if not pre:
        return None
    lrc_text = unescape(re.sub(r"<[^>]+>", "", pre.group(1)))
    lines = parse_lrc(lrc_text)
    return lines or None


PROVIDER_CHAIN = [
    ("lrclib", fetch_lrclib),
    ("netease", fetch_netease),
    ("lyricsify", fetch_lyricsify),
    ("megalobiz", fetch_megalobiz),
]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class LyricsCache:
    """In-memory cache that walks the provider chain on miss."""

    def __init__(self, log=None):
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._log = log or (lambda *a, **k: None)

    def get(self, artist: str, title: str, album: str, duration: int):
        key = f"{artist}|{title}|{int(duration or 0)}".lower()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        # Walk the providers strictly in order — the higher-quality sources
        # (LRCLIB, NetEase) come first, so on a hit we never even hit the
        # scrapers. Worst case (4 misses, 4s each) ~16 s; cached after that.
        result = None
        for name, fn in PROVIDER_CHAIN:
            try:
                result = fn(artist, title, album, duration)
            except Exception as e:
                self._log(f"lyrics provider {name} crashed: {e!r}")
                continue
            if result:
                self._log(f"lyrics provider hit: {name} ({len(result)} lines)")
                break
        with self._lock:
            self._cache[key] = result
        return result
