"""Console API for 小游戏 → 挂机钓鱼 (core/games/fishing). Every fact comes from the program; the page only
shows it and sends commands (the same handler as typed `/钓鱼 …` commands and the pet's menu)."""

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.auth import require_token
from core.games.fishing import FishingGame, engine
from core.games.fishing.game import COMMANDS
from core.llm_config import ConfigError, format_validation_error

router = APIRouter(prefix="/api/fishing", dependencies=[Depends(require_token)])

SIMULATE_CASTS = 10000


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandIn(Strict):
    cmd: Literal[COMMANDS]  # type: ignore[valid-type]
    args: list[str] = Field(default_factory=list, max_length=4)
    # The page asked the user first (unlock, rod upgrade, sell all, release a rare fish).
    confirmed: bool = False


class SettingsIn(Strict):
    wait_min_sec: int | None = None
    wait_max_sec: int | None = None
    llm_budget_per_day: int | None = None
    animate_empty: bool | None = None
    bite_window_ms: int | None = None
    hud_animation: bool | None = None
    hud_placed: bool | None = None
    hud_scale: float | None = None


class HookIn(Strict):
    seq: int = Field(ge=0)
    clicked: bool = True


def _game(request: Request) -> FishingGame:
    return request.app.state.fishing


def _state(request: Request) -> dict[str, Any]:
    try:
        return _game(request).snapshot()
    except ConfigError as e:
        raise HTTPException(409, f"钓鱼配置有误：{e}") from None


@router.get("")
def get_fishing(request: Request) -> dict[str, Any]:
    return _state(request)


@router.post("/command")
async def post_command(request: Request, body: CommandIn) -> dict[str, Any]:
    result = await _game(request).command(body.cmd, [a[:40] for a in body.args], body.confirmed)
    return {"ok": result.ok, "text": result.text, "need_confirm": result.need_confirm, "state": _state(request)}


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    game = _game(request)
    try:
        game.set_settings(body.model_dump(exclude_none=True))
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None
    except ConfigError as e:
        raise HTTPException(409, f"钓鱼配置有误：{e}") from None
    await game.push_state()
    return _state(request)


@router.post("/hook")
async def post_hook(request: Request, body: HookIn) -> dict[str, Any]:
    """The console's fishing scene answers a bite window (same as a click on the pet's HUD)."""
    await _game(request).hook(body.seq, body.clicked)
    return _state(request)


@router.get("/hud")
def get_hud(request: Request, after: int | None = Query(None, ge=0)) -> dict[str, Any]:
    """For the console's fishing scene (polled): the HUD state, the open bite window if any, and the HUD
    timelines numbered above `after` (omit `after` on the first call to get only the current number)."""
    game = _game(request)
    feed, last = game.feed(after)
    return {"state": game.state_message(), "pending_seq": game.pending_seq(), "feed": feed, "last": last, "now": time.time()}


@router.post("/simulate")
async def post_simulate(request: Request) -> dict[str, Any]:
    """10 000 simulated casts with the current place, bait and rod: what to expect. Changes nothing."""
    game = _game(request)
    try:
        cfg = game.files.fish()
    except ConfigError as e:
        raise HTTPException(409, f"钓鱼配置有误：{e}") from None
    state = game.state_message()
    cfg = cfg.model_copy(update={"game": game.settings(cfg)})
    return await asyncio.to_thread(
        engine.simulate, cfg, SIMULATE_CASTS, location=state["location"], bait=state["bait"], rod_level=state["rod_level"]
    )
