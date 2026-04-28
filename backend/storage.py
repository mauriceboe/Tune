"""Settings and history persistence."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_SETTINGS,
    HISTORY_PATH,
    SETTINGS_DIR,
    SETTINGS_PATH,
)


def load_json(path: Path, default):
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data: Any):
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_settings() -> dict:
    return {**DEFAULT_SETTINGS, **load_json(SETTINGS_PATH, {})}


def save_settings(settings: dict) -> None:
    save_json(SETTINGS_PATH, settings)


class HistoryStore:
    """Append-only log of every distinct play, kept on disk as JSON."""

    def __init__(self):
        self._lock = threading.Lock()
        self.entries: list[dict] = load_json(HISTORY_PATH, [])

    def add(self, entry: dict) -> None:
        with self._lock:
            self.entries.append(entry)
            try:
                save_json(HISTORY_PATH, self.entries)
            except Exception:
                pass

    def recent_unique(self, n: int) -> list[dict]:
        with self._lock:
            out: list[dict] = []
            seen: set[str] = set()
            for e in reversed(self.entries):
                key = f"{e.get('artist', '')}|{e.get('title', '')}".lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(e)
                if len(out) >= n:
                    break
            return out

    def recent(self, n: int = 80) -> list[dict]:
        with self._lock:
            return list(reversed(self.entries[-n:]))

    def today_stats(self) -> dict:
        today = date.today().isoformat()
        with self._lock:
            todays = [e for e in self.entries if e.get("date") == today]
        total = sum(int(e.get("listened") or 0) for e in todays)
        artist_counts = Counter(e.get("artist", "") for e in todays if e.get("artist"))
        top_artist, _ = (artist_counts.most_common(1)[0] if artist_counts else ("", 0))
        return {
            "total_seconds": total,
            "tracks": len(todays),
            "top_artist": top_artist,
        }
