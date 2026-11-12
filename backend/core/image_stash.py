"""Short-lived in-memory store for images the user sends to the pet.

A WebSocket message is capped at 64 KB, and events are logged and shown in the console, so the image itself
never travels in an event: the pet window uploads it over HTTP first and the event carries only the id.
Images live in RAM only (never written to disk, never logged) and are removed on first use or after TTL_SECONDS.
"""

import base64
import re
import secrets
import time

MAX_IMAGE_BYTES = 5 * 1024 * 1024
TTL_SECONDS = 180.0
MAX_ENTRIES = 4
DATA_URL_RE = re.compile(r"^data:image/(?:jpeg|png|webp|gif);base64,([A-Za-z0-9+/=]+)$")
ID_RE = re.compile(r"^img_[0-9a-f]{16}$")


class ImageStash:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, str]] = {}

    def _expire(self, now: float) -> None:
        for k in [k for k, (ts, _) in self._items.items() if now - ts > TTL_SECONDS]:
            del self._items[k]

    def put(self, data_url: str) -> str:
        """Stores a base64 image data URL; raises ValueError with a readable message if it isn't acceptable."""
        m = DATA_URL_RE.match(data_url)
        if m is None:
            raise ValueError("只支持 JPEG / PNG / WebP / GIF 图片")
        try:
            size = len(base64.b64decode(m.group(1), validate=True))
        except ValueError:
            raise ValueError("图片数据损坏") from None
        if size > MAX_IMAGE_BYTES:
            raise ValueError(f"图片太大（上限 {MAX_IMAGE_BYTES // (1024 * 1024)} MB）")
        now = time.monotonic()
        self._expire(now)
        while len(self._items) >= MAX_ENTRIES:
            del self._items[min(self._items, key=lambda k: self._items[k][0])]
        image_id = f"img_{secrets.token_hex(8)}"
        self._items[image_id] = (now, data_url)
        return image_id

    def take(self, image_id: str) -> str | None:
        """Returns the data URL and forgets it; None if unknown or expired."""
        self._expire(time.monotonic())
        item = self._items.pop(image_id, None)
        return item[1] if item else None
