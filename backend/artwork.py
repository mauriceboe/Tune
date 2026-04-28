"""Cover art accent extraction and catbox.moe upload cache."""

from __future__ import annotations

import collections
import colorsys
import hashlib
import json
import threading
import urllib.parse
import urllib.request
import uuid

from PIL import Image

from .config import APP_NAME, APP_VERSION, ARTWORK_CACHE_PATH, DEFAULT_ACCENT
from .storage import load_json, save_json


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


class ArtworkHost:
    """Uploads cover art to catbox.moe so Discord can show the exact image.

    Each unique cover (by SHA-256) is uploaded only once and the URL is cached
    on disk forever.
    """

    def __init__(self):
        self._lock = threading.Lock()
        data = load_json(ARTWORK_CACHE_PATH, {})
        self._cache = data if isinstance(data, dict) else {}

    def _save(self):
        save_json(ARTWORK_CACHE_PATH, self._cache)

    def upload(self, image_bytes: bytes, log=None):
        if not image_bytes:
            return None
        h = hashlib.sha256(image_bytes).hexdigest()
        with self._lock:
            cached = self._cache.get(h)
        if cached:
            if log:
                log(f"artwork: cache hit {h[:12]}... -> {cached}")
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
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                url = resp.read().decode("utf-8", errors="ignore").strip()
            if url.startswith("http"):
                with self._lock:
                    self._cache[h] = url
                    self._save()
                if log:
                    log(f"artwork: uploaded {h[:12]}... -> {url} ({len(image_bytes)} bytes)")
                return url
            if log:
                log(f"artwork: catbox returned non-URL: {url!r}")
        except Exception as e:
            if log:
                log(f"artwork: upload failed: {e!r}")
            return None
        return None
