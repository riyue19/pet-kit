"""Turns foreground-window activity into screen.changed events — rarely, and without any VLM call.

Tier 1 (title/process) runs every poll; tier 2 (thumbnail hash) only for content already looked at, once its
cooldown has passed, to tell whether the picture changed enough to be worth another look. The VLM (tier 3)
is the dispatcher's business, and only after the proactive gate says yes.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from core.events import Event, make_event
from core.screen_config import PrivacyConfig, ScreenConfig
from perception.screen import Foreground, foreground, hash_distance, is_own, thumbnail_hash

log = logging.getLogger(__name__)

TITLE_MAX = 200
HASH_EVERY_SECONDS = 10
FORGET_AFTER_SECONDS = 2 * 3600


@dataclass
class _Candidate:
    fg: Foreground
    since: float
    announced: bool = False


class ScreenWatcher:
    def __init__(
        self,
        *,
        config: Callable[[], ScreenConfig],
        privacy: Callable[[], PrivacyConfig],
        publish: Callable[[Event], None],
        probe: Callable[[], Foreground | None] = foreground,
        hasher: Callable[[Foreground], np.ndarray | None] = thumbnail_hash,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._privacy = privacy
        self._publish = publish
        self._probe = probe
        self._hasher = hasher
        self._clock = clock
        self._cand: _Candidate | None = None
        self._looked: dict[str, float] = {}  # content key -> when it was last announced
        self._base_hash: dict[str, np.ndarray | None] = {}
        self._last_hash_at = float("-inf")
        # Last foreground that wasn't the pet/console, for the console's status display.
        self.last_seen: Foreground | None = None

    async def run(self) -> None:
        while True:
            cfg = self._config()
            try:
                if cfg.enabled:
                    fg = await asyncio.to_thread(self._probe)
                    await self.step(fg, cfg, self._privacy())
                else:
                    self._cand = None
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("屏幕感知出错")
            await asyncio.sleep(cfg.poll_seconds)

    def _forget_old(self, now: float) -> None:
        for k in [k for k, t in self._looked.items() if now - t > FORGET_AFTER_SECONDS]:
            self._looked.pop(k, None)
            self._base_hash.pop(k, None)

    def _announce(self, fg: Foreground, reason: str, now: float) -> None:
        self._looked[fg.key] = now
        self._publish(
            make_event(
                "screen.changed",
                {"app": fg.process, "title": fg.title[:TITLE_MAX], "fullscreen": fg.fullscreen, "reason": reason},
            )
        )

    async def step(self, fg: Foreground | None, cfg: ScreenConfig, privacy: PrivacyConfig) -> None:
        now = self._clock()
        self._forget_old(now)
        if fg is None or is_own(fg):
            return  # keep the current candidate: glancing at the pet doesn't reset "what the user is doing"
        if not fg.process or privacy.blocked(fg.process, fg.title):
            # Private (or unidentifiable) window: never announced, and not shown as "last seen" either.
            self._cand = None
            return
        self.last_seen = fg
        cad = cfg.cadence
        if self._cand is None or self._cand.fg.key != fg.key:
            self._cand = _Candidate(fg=fg, since=now)
            return
        self._cand.fg = fg  # rect / fullscreen may change while the title stays
        last = self._looked.get(fg.key)
        if not self._cand.announced:
            if now - self._cand.since >= cad.dwell_seconds and (last is None or now - last >= cad.per_content_seconds):
                self._cand.announced = True
                self._announce(fg, "new", now)
                self._base_hash[fg.key] = await asyncio.to_thread(self._hasher, fg)
            return
        # Same content, already looked at: only a big visual change after the cooldown earns another look.
        if cad.revisit_change is None or last is None or now - last < cad.per_content_seconds:
            return
        if now - self._last_hash_at < HASH_EVERY_SECONDS:
            return
        self._last_hash_at = now
        h = await asyncio.to_thread(self._hasher, fg)
        if hash_distance(h, self._base_hash.get(fg.key)) >= cad.revisit_change:
            self._base_hash[fg.key] = h
            self._announce(fg, "changed", now)
