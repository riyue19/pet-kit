"""M7: the pet starts conversations on its own — idle chat, welcome back, time-of-day greetings.

One loop wakes every TICK_SECONDS and does only cheap checks (idle time, foreground title, the shared
proactive gate); when something qualifies it charges the gate and publishes a timer.idle event.
The vision model is never involved: topics come from the clock, the weather already in the prompt,
the window title and memory.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from core.events import Event, make_event
from core.llm_config import ConfigError, load_backend_config
from core.proactive import GateLimits, ProactiveGate
from core.screen_config import PrivacyConfig, ProactiveConfig
from perception.screen import Foreground, is_own

log = logging.getLogger(__name__)

TICK_SECONDS = 30
# "At the computer" = some keyboard/mouse input within this many seconds.
PRESENT_SECONDS = 120
# Welcome back only right after the return, not whenever the gate next opens.
WELCOME_WINDOW_SECONDS = 180
# A "day" for greetings starts at 05:00, so 23:30 and 01:00 are the same night.
DAY_START_HOURS = 5
STATE_FILE = "scheduler.json"

# key, start hour, end hour (may pass 24), what the model is told
GREETINGS = (
    ("morning", 5, 10, "今天第一次在电脑前"),
    ("lunch", 11, 13, "到午饭时间了"),
    ("dinner", 17, 19, "到晚饭时间了"),
    ("night", 23, 26, "已经很晚了，主人还没睡"),
)


def greeting_slot(hour: int) -> tuple[str, str] | None:
    for key, start, end, desc in GREETINGS:
        if start <= hour < end or (end > 24 and hour < end - 24):
            return key, desc
    return None


@dataclass
class Situation:
    kind: str  # idle | welcome_back | greeting (the idle action travels as kind "action", without a Situation)
    text: str  # what the model is told, without the （）
    slot: str | None = None  # greeting slot, for the once-a-day record


class Scheduler:
    def __init__(
        self,
        *,
        config_path: Path,
        privacy: Callable[[], PrivacyConfig],
        gate: ProactiveGate,
        publish: Callable[[Event], None],
        busy: Callable[[], str | None],
        idle: Callable[[], float | None],
        probe: Callable[[], Foreground | None],
        windows: Callable[[], list[tuple[str, str]]] = lambda: [],
        state_path: Path | None = None,
        tick_seconds: float = TICK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._config_path = config_path
        self._privacy = privacy
        self._gate = gate
        self._publish = publish
        self._busy = busy
        self._idle = idle
        self._probe = probe
        self._windows = windows
        self._state_path = state_path
        self._tick = tick_seconds
        self._clock = clock
        self._now = now
        self._started = clock()
        self._last_remark = float("-inf")  # last time the scheduler started a conversation
        self._last_action = float("-inf")  # last idle action
        self._away = False
        self._returned_at = float("-inf")
        self._greeted: dict[str, str] = {}  # slot -> greeting day (YYYY-MM-DD), saved once actually said
        self._claimed: dict[str, str] = {}  # slot -> day tried this run (a failed try isn't retried today)
        self.last: Situation | None = None
        # What was actually said, for the console status line (wall clock).
        self.last_spoken: tuple[float, str, str] | None = None
        self._load()

    def idle_seconds(self) -> float | None:
        try:
            return self._idle()
        except Exception:
            log.debug("读取系统空闲时间失败", exc_info=True)
            return None

    # --- once-a-day record (survives restarts) ----------------------------------------------

    def _load(self) -> None:
        if self._state_path is None:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        greeted = data.get("greeted") if isinstance(data, dict) else None
        if isinstance(greeted, dict):
            self._greeted = {str(k): str(v) for k, v in greeted.items()}

    def _save(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps({"greeted": self._greeted}), encoding="utf-8")
        except OSError:
            log.warning("无法保存问候记录", exc_info=True)

    def _day(self, now: datetime) -> str:
        return (now - timedelta(hours=DAY_START_HOURS)).strftime("%Y-%m-%d")

    # --- decision ------------------------------------------------------------------------------

    def _configs(self) -> tuple[ProactiveConfig, float, bool]:
        try:
            cfg = load_backend_config(self._config_path)
            return cfg.proactive, cfg.screen.quiet_after_user_seconds, cfg.llm.for_role("chat") is not None
        except ConfigError:
            return ProactiveConfig(), 45, False

    def spoken(self, payload: dict, text: str, at: float) -> None:
        """The dispatcher actually delivered a remark from this scheduler."""
        self.last_spoken = (at, str(payload.get("kind") or ""), text)
        slot = payload.get("slot")
        if isinstance(slot, str) and slot in self._claimed:
            self._greeted[slot] = self._claimed[slot]
            self._save()

    def decide(
        self, idle: float | None, fg: Foreground | None, windows: list[tuple[str, str]] | None = None
    ) -> Situation | None:
        """Runs on the event loop with no awaits, so check + charge can't interleave with a screen look."""
        cfg, quiet_after_user, chat_ok = self._configs()
        if not cfg.enabled or idle is None:
            return None
        now = self._clock()
        # Track presence even while busy, so a return isn't missed.
        if idle >= cfg.away_minutes * 60:
            self._away = True
            return None  # nobody there: say nothing
        if self._away and idle < PRESENT_SECONDS:
            self._away = False
            self._returned_at = now
        if idle >= PRESENT_SECONDS or not chat_ok:
            return None

        privacy = self._privacy()
        # Do-not-disturb fails closed: unknown foreground, fullscreen (slides, games, video — speaking aloud
        # is more intrusive than a screen comment's bubble), or any visible meeting/recording window.
        quiet = (
            fg is None
            or fg.fullscreen
            or windows is None
            or any(privacy.quiet(p, t) for p, t in [(fg.process, fg.title), *windows])
        )
        if fg is not None and (is_own(fg) or not fg.process or privacy.blocked(fg.process, fg.title)):
            fg = None  # a private window never becomes a topic (and its title never enters an event)
        cad = cfg.cadence
        why = self._gate.check(
            GateLimits(cad.min_gap_seconds, cad.max_per_hour, quiet_after_user),
            busy=self._busy(),
            quiet_app=quiet,
        )
        if why:
            log.debug("先不主动搭话：%s", why)
            return None

        situation = self._welcome_back(cfg, now, fg) or self._greeting(cfg, fg) or self._idle_chat(cfg, now, fg)
        if situation is None:
            return None
        self._gate.attempt()
        self._last_remark = now
        if situation.kind == "welcome_back":
            self._returned_at = float("-inf")
        if situation.slot:
            # Saved to disk only once it is actually said (spoken()); a failed try isn't repeated today.
            self._claimed[situation.slot] = self._day(self._now())
        self.last = situation
        return situation

    def _welcome_back(self, cfg: ProactiveConfig, now: float, fg: Foreground | None) -> Situation | None:
        if not cfg.welcome_back or now - self._returned_at > WELCOME_WINDOW_SECONDS:
            return None
        return Situation("welcome_back", self._context("主人离开电脑一阵子，刚回来", fg))

    def _greeting(self, cfg: ProactiveConfig, fg: Foreground | None) -> Situation | None:
        if not cfg.greetings:
            return None
        now = self._now()
        slot = greeting_slot(now.hour)
        day = self._day(now)
        if slot is None or day in (self._greeted.get(slot[0]), self._claimed.get(slot[0])):
            return None
        return Situation("greeting", self._context(slot[1], fg), slot=slot[0])

    def _idle_chat(self, cfg: ProactiveConfig, now: float, fg: Foreground | None) -> Situation | None:
        if not cfg.idle_chat:
            return None
        # Measured from the last thing either side said (or startup), so it doesn't fire right away.
        since = max(self._gate.last_user, self._last_remark, self._started)
        if now - since < cfg.idle_after_minutes * 60:
            return None
        if now - self._last_remark < cfg.cadence.idle_repeat_seconds:
            return None
        if self._gate.last_user > self._started:
            lead = f"主人在电脑前，但已经 {int((now - self._gate.last_user) // 60)} 分钟没和你说话了"
        else:
            lead = "主人在电脑前，但有一阵子没和你说话了"
        return Situation("idle", self._context(lead, fg))

    def idle_action(self) -> dict | None:
        """Payload of an idle action that is due now, else None. Rule layer only: no LLM, no gate budget,
        and keyboard/mouse activity is ignored — only engaging the pet resets the wait."""
        try:
            cfg = load_backend_config(self._config_path).proactive
        except ConfigError:
            return None
        if not cfg.idle_action or not (cfg.idle_action_motions or cfg.idle_action_expressions):
            return None
        now = self._clock()
        since = max(self._gate.last_user, self._last_action, self._started)
        if now - since < cfg.idle_action_minutes * 60 or self._busy():
            return None
        self._last_action = now
        return {"kind": "action", "motions": list(cfg.idle_action_motions), "expressions": list(cfg.idle_action_expressions)}

    @staticmethod
    def _context(lead: str, fg: Foreground | None) -> str:
        if fg is None:
            return lead
        title = f"「{fg.title[:60]}」" if fg.title else ""
        return f"{lead}；主人正在用 {fg.process}{title}"

    async def run(self) -> None:
        while True:
            try:
                idle = await asyncio.to_thread(self._idle)
                fg, windows = None, None
                if idle is not None and idle < PRESENT_SECONDS:
                    fg = await asyncio.to_thread(self._probe)
                    try:
                        windows = await asyncio.to_thread(self._windows)
                    except Exception:  # noqa: BLE001
                        log.exception("枚举窗口失败，按免打扰处理")  # windows=None -> decide() stays quiet
                situation = self.decide(idle, fg, windows)
                if situation is not None:
                    log.info("主动搭话（%s）：%s", situation.kind, situation.text)
                    payload = {"kind": situation.kind, "situation": situation.text, "slot": situation.slot}
                    self._publish(make_event("timer.idle", payload))
                else:
                    action = self.idle_action()
                    if action is not None:
                        log.info("空闲动作：很久没有互动了")
                        self._publish(make_event("timer.idle", action))
            except Exception:
                log.exception("主动对话检查失败")
            await asyncio.sleep(self._tick)
