"""screen.changed -> (gate) -> screenshot -> vision LLM -> maybe a remark.

The screenshot is taken only here, only after every cheap check passed, kept in memory, sent once and dropped.
At most one look is in flight; each look is charged to the proactive budget before it is made.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.agent import Agent, AgentOutput, Labels
from core.events import Event
from core.llm_config import ConfigError, load_backend_config
from core.persona import load_yaml_model
from core.proactive import GateLimits, ProactiveGate
from core.screen_config import PrivacyConfig, ScreenConfig
from perception.screen import Foreground, capture_jpeg, foreground, is_own, visible_windows

log = logging.getLogger(__name__)


class PrivacyFile:
    """config/privacy.yaml, re-read when it changes (polled every couple of seconds by the watcher)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self._cfg = PrivacyConfig()

    def __call__(self) -> PrivacyConfig:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return PrivacyConfig()  # defaults are already a sensible blacklist
        if mtime != self._mtime:
            try:
                self._cfg = load_yaml_model(self.path, PrivacyConfig)
            except ConfigError as e:
                # Keep the last good lists (or defaults): a typo must not open up the blacklist.
                log.warning("privacy.yaml 有误，继续使用上一次的设置：%s", e)
            self._mtime = mtime
        return self._cfg


def blocked(privacy: PrivacyConfig, process: str, title: str) -> bool:
    # Fail closed: a process whose name can't be read (protected / anti-cheat / security software) is private.
    return not process or privacy.blocked(process, title)


@dataclass
class LastComment:
    at: float  # wall clock
    app: str
    text: str


class ScreenCommenter:
    def __init__(
        self,
        *,
        agent: Agent,
        gate: ProactiveGate,
        config_path: Path,
        privacy: Callable[[], PrivacyConfig],
        labels: Callable[[], Labels],
        busy: Callable[[], str | None],
        probe: Callable[[], Foreground | None] = foreground,
        capture: Callable[..., bytes | None] = capture_jpeg,
        windows: Callable[[], list[tuple[str, str]]] = visible_windows,
    ) -> None:
        self._agent = agent
        self._gate = gate
        self._config_path = config_path
        self._privacy = privacy
        self._labels = labels
        self._busy = busy
        self._probe = probe
        self._capture = capture
        self._windows = windows
        self._inflight = False
        self.last: LastComment | None = None

    @property
    def inflight(self) -> bool:
        return self._inflight

    def _configs(self):
        try:
            cfg = load_backend_config(self._config_path)
            return cfg.screen, cfg.llm.roles.vision
        except ConfigError:
            return ScreenConfig(), None

    def _quiet(self, privacy: PrivacyConfig, fg: Foreground) -> bool:
        # Meetings / recording / streaming usually aren't the foreground window: check every visible one.
        if privacy.quiet(fg.process, fg.title):
            return True
        try:
            return any(privacy.quiet(p, t) for p, t in self._windows())
        except Exception:  # noqa: BLE001
            log.exception("枚举窗口失败，按免打扰处理")
            return True

    async def handle(self, event: Event, *, force: bool = False) -> AgentOutput | None:
        """`force` (console "看一眼") skips only the cadence limits and the "same content" check —
        never the master switch, the blacklist, do-not-disturb or "busy"."""
        cfg, vision = self._configs()
        if not cfg.enabled:
            return None
        if not vision:
            log.info("没有指定识图用的 LLM，不看屏幕")
            return None
        if self._inflight:
            log.debug("上一眼还没看完，跳过")
            return None
        # Claim the slot before the first await, so concurrent events can't both pass the gate.
        self._inflight = True
        try:
            return await self._look(event, cfg, force)
        finally:
            self._inflight = False

    async def _look(self, event: Event, cfg: ScreenConfig, force: bool) -> AgentOutput | None:
        fg = await asyncio.to_thread(self._probe)
        if fg is None or is_own(fg):
            if fg is not None:
                log.info("前台是桌宠自己的窗口，不看")
            return None
        privacy = self._privacy()
        if blocked(privacy, fg.process, fg.title):
            log.info("隐私窗口，不截图")
            return None
        p = event.payload
        if not force and (fg.process != p.get("app") or fg.title[:200] != p.get("title")):
            log.debug("画面已经切走了，跳过")
            return None
        if fg.fullscreen and not cfg.allow_fullscreen:
            log.info("全屏中，按设置不打扰")
            return None
        quiet = await asyncio.to_thread(self._quiet, privacy, fg)
        cad = cfg.cadence
        limits = GateLimits(cad.min_gap_seconds, cad.max_per_hour, cfg.quiet_after_user_seconds)
        if force:
            # Manual test: only DND and busy apply.
            why = "免打扰：会议 / 录屏 / 直播中" if quiet else self._busy()
        else:
            why = self._gate.check(limits, busy=self._busy(), quiet_app=quiet)
        if why:
            log.info("先不看屏幕：%s", why)
            return None
        self._gate.attempt()  # charged now: the vision call costs money whether or not the pet speaks
        jpeg = await asyncio.to_thread(self._capture, fg, lambda proc, title: blocked(privacy, proc, title))
        if jpeg is None:
            log.info("截图取消（窗口切走了，或上方有隐私窗口）")
            return None
        seen = f"主人正在用 {fg.process}" + (f"：{fg.title}" if fg.title else "") + ("（全屏）" if fg.fullscreen else "")
        log.info("看屏幕：%s（截图 %d KB）", fg.process, len(jpeg) // 1024)
        user_before = self._gate.last_user
        turn = await self._agent.respond_screen(event, self._labels(), image_jpeg=jpeg, seen=seen)
        del jpeg
        out = turn.output
        if turn.failed:
            self._gate.failed()
            return None
        if self._gate.last_user != user_before:
            log.info("主人在我看屏幕时开口了，这句吐槽不说了")
            return None
        if out.text:
            await turn.commit()
            self._gate.spoke()
            self.last = LastComment(at=time.time(), app=fg.process, text=out.text)
        else:
            log.info("看了一眼，没什么好说的")
        return out
