"""Shared paths and constants for the Tune backend daemon."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Tune"
APP_VERSION = "2.1.1"
DISCORD_CLIENT_ID = os.environ.get("TUNE_DISCORD_CLIENT_ID", "861702238472241162")
GITHUB_REPO = "mauriceboe/Tune"

POLL_SECONDS = 1
DEFAULT_ACCENT = "#fc3c44"

SETTINGS_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = SETTINGS_DIR / "settings.json"
HISTORY_PATH = SETTINGS_DIR / "history.json"
ARTWORK_CACHE_PATH = SETTINGS_DIR / "artwork_cache.json"
LOG_PATH = SETTINGS_DIR / "backend.log"

DEFAULT_SETTINGS = {
    "show_on_discord": True,
    "minimize_to_tray": True,
    "start_minimized": False,
    "always_on_top": False,
    "auto_check_updates": True,
}
