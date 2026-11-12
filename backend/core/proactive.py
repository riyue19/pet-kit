"""Gate for everything the pet does on its own (CLAUDE.md §4.4): cooldown, hourly cap, do-not-disturb.

The budget counts *attempts* (every screen look = one vision call, whether the pet then speaks or not),
because the call is what costs money. A failed attempt (provider error) also backs off for a while.
Screen comments and the M7 scheduler (idle chat, welcome back, greetings) share the same budget.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

FAILURE_BACKOFF_SECONDS = 300


@dataclass
class GateLimits:
    min_gap_seconds: float
    max_per_hour: int
    quiet_after_user_seconds: float


class ProactiveGate:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._attempts: deque[float] = deque()
        self._spoken: deque[float] = deque()
        self._last_user = float("-inf")
        self._backoff_until = float("-inf")

    @property
    def last_user(self) -> float:
        return self._last_user

    def user_active(self) -> None:
        """Call on every user input (text, voice, click, hotkey)."""
        self._last_user = self._clock()

    def _trim(self, now: float) -> None:
        for q in (self._attempts, self._spoken):
            while q and now - q[0] > 3600:
                q.popleft()

    def check(self, limits: GateLimits, *, busy: str | None, quiet_app: bool) -> str | None:
        """None if the pet may act now, otherwise the reason it may not. `busy` is a reason or None."""
        now = self._clock()
        self._trim(now)
        if quiet_app:
            return "免打扰：会议 / 录屏 / 直播中"
        if busy:
            return busy
        if now < self._backoff_until:
            return "上次调用失败，稍后再试"
        if now - self._last_user < limits.quiet_after_user_seconds:
            return "主人刚刚在和我说话"
        if self._attempts and now - self._attempts[-1] < limits.min_gap_seconds:
            return "离上次主动行动太近"
        if len(self._attempts) >= limits.max_per_hour:
            return "本小时主动行动次数已满"
        return None

    def attempt(self) -> None:
        """Record a paid proactive call right before making it (check + attempt run without awaiting between)."""
        self._attempts.append(self._clock())

    def failed(self) -> None:
        self._backoff_until = self._clock() + FAILURE_BACKOFF_SECONDS

    def spoke(self) -> None:
        self._spoken.append(self._clock())

    @property
    def spoken_last_hour(self) -> int:
        now = self._clock()
        return sum(1 for t in self._spoken if now - t <= 3600)

    @property
    def attempts_last_hour(self) -> int:
        now = self._clock()
        return sum(1 for t in self._attempts if now - t <= 3600)
