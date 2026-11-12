"""camera.presence(arrived) -> (gate) -> one webcam frame -> vision LLM -> maybe a greeting.

The frame is taken only here, after every cheap check passed, kept in memory, sent once and dropped.
`left` events and short absences never reach the vision model. At most one look is in flight.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.agent import Agent, AgentOutput, Labels
from core.camera_config import CameraConfig
from core.events import Event
from core.llm_config import ConfigError, load_backend_config
from core.proactive import GateLimits, ProactiveGate
from core.screen_config import PrivacyConfig, ProactiveConfig

log = logging.getLogger(__name__)


@dataclass
class LastGreeting:
    at: float  # wall clock
    text: str


def away_phrase(seconds: float) -> str:
    if seconds < 90:
        return "刚离开一小会儿"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"离开了大约 {minutes} 分钟"
    hours = seconds / 3600
    return f"离开了大约 {hours:.1f} 小时"


class CameraReactor:
    def __init__(
        self,
        *,
        agent: Agent,
        gate: ProactiveGate,
        config_path: Path,
        privacy: Callable[[], PrivacyConfig],
        labels: Callable[[], Labels],
        busy: Callable[[], str | None],
        snapshot: Callable[[], bytes | None],
        windows: Callable[[], list[tuple[str, str]]],
    ) -> None:
        self._agent = agent
        self._gate = gate
        self._config_path = config_path
        self._privacy = privacy
        self._labels = labels
        self._busy = busy
        self._snapshot = snapshot
        self._windows = windows
        self._inflight = False
        self.last: LastGreeting | None = None

    @property
    def inflight(self) -> bool:
        return self._inflight

    def _configs(self) -> tuple[CameraConfig, ProactiveConfig, float, str | None]:
        try:
            cfg = load_backend_config(self._config_path)
            return cfg.camera, cfg.proactive, cfg.screen.quiet_after_user_seconds, cfg.llm.roles.vision
        except ConfigError:
            return CameraConfig(), ProactiveConfig(), 45.0, None

    def _quiet(self) -> bool:
        privacy = self._privacy()
        try:
            return any(privacy.quiet(p, t) for p, t in self._windows())
        except Exception:  # noqa: BLE001
            log.exception("枚举窗口失败，按免打扰处理")
            return True

    async def handle(self, event: Event, *, force: bool = False) -> AgentOutput | None:
        """`force` (console test) skips only the "greet on return / away long enough" rules and the cadence
        limits — never the master switch, do-not-disturb or "busy"."""
        cam, proactive, quiet_after, vision = self._configs()
        if not cam.enabled or event.payload.get("state") != "arrived":
            return None
        away = float(event.payload.get("away_seconds") or 0)
        if not force and (not cam.greet_on_return or not proactive.enabled or away < cam.greet_min_away_minutes * 60):
            return None
        if not vision:
            log.info("没有指定识图用的 LLM，不看摄像头")
            return None
        if self._inflight:
            return None
        self._inflight = True  # claimed before the first await
        try:
            return await self._look(event, cam, proactive, quiet_after, away, force)
        finally:
            self._inflight = False

    async def _look_asked(self, event: Event) -> AgentOutput | None:
        """The user asked the pet to look at them (chat tool call `look_camera`). They are talking to the pet, so
        no cadence / "user just spoke" limits and no proactive budget; do-not-disturb still applies, and the look
        waits until the pet has finished speaking its "let me see" line."""
        if await asyncio.to_thread(self._quiet):
            return AgentOutput(text="（现在在开会/录屏，摄像头先让出来了，没法看）")
        for _ in range(60):  # up to ~30 s
            if not self._busy():
                break
            await asyncio.sleep(0.5)
        jpeg = await asyncio.to_thread(self._snapshot)
        if jpeg is None:
            log.info("摄像头现在拿不到画面，没法看")
            return AgentOutput(text="（摄像头现在拿不到画面）")
        log.info("按主人要求看摄像头（画面 %d KB）", len(jpeg) // 1024)
        turn = await self._agent.respond_camera(event, self._labels(), image_jpeg=jpeg, seen="主人让你看看他", ask=True)
        del jpeg
        if turn.failed or not turn.output.text:
            return AgentOutput(text="（这次没看清，再让我看一次吧）")
        await turn.commit()
        self.last = LastGreeting(at=time.time(), text=turn.output.text)
        return turn.output

    async def _look(
        self, event: Event, cam: CameraConfig, proactive: ProactiveConfig, quiet_after: float, away: float, force: bool
    ) -> AgentOutput | None:
        ask = event.payload.get("reason") == "ask"
        if ask:
            return await self._look_asked(event)
        quiet = await asyncio.to_thread(self._quiet)
        if force:
            why = "免打扰：会议 / 录屏 / 直播中" if quiet else self._busy()
        else:
            cad = proactive.cadence  # shares the pet's own-initiative budget with idle chat / screen comments
            why = self._gate.check(GateLimits(cad.min_gap_seconds, cad.max_per_hour, quiet_after), busy=self._busy(), quiet_app=quiet)
        if why:
            log.info("先不看摄像头：%s", why)
            return None
        jpeg = await asyncio.to_thread(self._snapshot)  # free; only the model call costs
        if jpeg is None:
            log.info("摄像头现在拿不到画面，不打招呼")
            return None
        self._gate.attempt()
        seen = f"主人回到了电脑前，{away_phrase(away)}"
        log.info("看摄像头：%s（画面 %d KB）", seen, len(jpeg) // 1024)
        user_before = self._gate.last_user
        turn = await self._agent.respond_camera(event, self._labels(), image_jpeg=jpeg, seen=seen)
        del jpeg
        if turn.failed:
            self._gate.failed()
            return None
        if self._gate.last_user != user_before:
            log.info("主人在我看摄像头时开口了，这句招呼不说了")
            return None
        out = turn.output
        if out.text:
            await turn.commit()
            self._gate.spoke()
            self.last = LastGreeting(at=time.time(), text=out.text)
        else:
            log.info("看了一眼，没什么好说的")
        return out
