"""Builds the idle-fishing game (core/games/fishing) and connects it to the pet: the bubble and voice, the
do-not-disturb rules, the 主动对话 switch and the affection value. Split out of api/app.py.

The game itself is settled by code; it speaks rarely and stays silent while the user must not be disturbed.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

from api.pet_ws import PetHub
from core.agent import AgentOutput
from core.events import Event
from core.games import GameBrain
from core.games.fishing import DB_FILE, FishFiles, FishingGame, Store
from core.llm_config import ConfigError, load_backend_config
from core.proactive import ProactiveGate
from core.screen_comment import PrivacyFile
from core.status import StatusService
from core.status_config import AFFECTION
from perception.screen import foreground, visible_windows


def build_fishing(
    *,
    config_path: Path,
    state_dir: Path | None,
    hub: PetHub,
    brain: GameBrain,
    status: StatusService,
    privacy: PrivacyFile,
    gate: ProactiveGate,
    busy: Callable[[], str | None],
    publish: Callable[[Event], None],
) -> FishingGame:
    """`busy`: why the pet can't speak right now (thinking, talking, not connected), or None."""

    async def quiet() -> bool:
        """Fails closed, like the scheduler: no pet window, fullscreen, unknown foreground, a meeting."""
        if not hub.clients:
            return True
        try:
            fg = await asyncio.to_thread(foreground)
            windows = await asyncio.to_thread(visible_windows)
        except Exception:  # noqa: BLE001
            return True
        p = privacy()
        return fg is None or fg.fullscreen or any(p.quiet(proc, title) for proc, title in [(fg.process, fg.title), *windows])

    async def say(text: str, expression: str | None, motion: str | None, voiced: bool) -> None:
        out = AgentOutput(text=text, expression=expression, motion=motion)

        async def announce() -> None:
            await hub.broadcast({"type": "output", "event_id": None, "output": out.model_dump()})

        # Never over the pet's own speech or the user's: then the line is only shown.
        if voiced and hub.speaker and hub.clients and not busy() and not hub.hearing:
            gate.spoke()
            await hub.speaker.say(text, expression, announce)
        else:
            await announce()

    def may_speak() -> bool:
        """Fishing lines are unasked speech: they follow the 主动对话 switch (also turned off by 关闭全部感知)."""
        try:
            return load_backend_config(config_path).proactive.enabled
        except ConfigError:
            return False

    def affinity_level() -> int:
        try:
            cfg = status.config()
            stat = cfg.stat(AFFECTION) if cfg.enabled else None
        except ConfigError:
            stat = None
        return max(0, int(status.value(stat) // 10)) if stat is not None else 0

    async def reward(amount: float, reason: str) -> None:
        change = status.grant(AFFECTION, amount, reason)
        if change is not None:
            await hub.broadcast({"type": "output", "event_id": None, "output": AgentOutput(status=[change]).model_dump()})

    return FishingGame(
        files=FishFiles(config_path.parent),
        store=Store(state_dir / DB_FILE if state_dir else None),
        brain=brain,
        send=hub.broadcast,
        say=say,
        quiet=quiet,
        busy=lambda: busy() is not None or hub.hearing,
        may_speak=may_speak,
        labels=lambda: hub.labels,
        affinity_level=affinity_level,
        reward=reward,
        publish=publish,
    )
