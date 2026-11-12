"""Idle fishing: the pet keeps a line in the water in the background.

Each cast waits a random, real time (minutes to hours, settings wait_min_sec..wait_max_sec) before anything
happens; when that time is up the cast is settled and the line goes out again. There is no way to get a fish
at once: the wait is drawn when the line is cast and kept in the database (cast_ts / cast_due), so restarting
the program neither shortens nor repeats it.

The program owns every fact (what bites, how big, coins, the bag, the codex). A cast is decided in one go by
engine.resolve_cast and written in one transaction; the HUD animation and the pet's line only play it back.
The LLM is asked for a line only on the events worth one (rare fish, a first catch, a bottle, a summary),
inside a daily budget, and its reply is checked (perform.py); anything else uses the stock lines.

While the user must not be disturbed (fullscreen, a meeting, no pet window) casts are still settled, silently,
and are told as one summary afterwards. The same goes for the casts made up for after the program was closed.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.events import Event, make_event
from core.games.fishing import engine, perform
from core.games.fishing.config import (
    TIME_NAMES,
    WEATHER_NAMES,
    FishConfig,
    FishFiles,
    GameSettings,
    Species,
    pick_group,
)
from core.games.fishing.engine import CastResult, CastState
from core.games.fishing.store import Store
from core.llm_config import ConfigError

log = logging.getLogger(__name__)

SEED_FILE = Path(__file__).with_name("bottles_seed.json")
UNKNOWN_FISH = "未知鱼"
SPECIAL = ("rare", "legendary")
STEP_MS = {"cast": 600, "bite": 900, "reel": 800, "result": 3200, "escape": 700, "empty": 800}
# A cast that came due this long ago was missed (program closed / asleep): it is settled silently instead.
LATE_S = 120.0
# Re-publishing the tick for the same due cast is held back this long (the bus delivers it asynchronously).
REPUBLISH_S = 10.0
BOTTLE_CARD_MS = 7000
DIGEST_CARD_MS = 6000
# How long after the bite window closes the backend stops waiting for the pet window's answer.
HOOK_GRACE_S = 2.0
POLL_S = 2.0
CHORE_EVERY_S = 900
MAX_DIGEST = 200
SETTING_KEYS = (
    "wait_min_sec", "wait_max_sec", "llm_budget_per_day", "animate_empty", "bite_window_ms",
    "hud_animation", "hud_placed", "hud_scale",
)
# Commands the pet window's menu may send; everything that spends or sells needs the console or a typed command.
MENU_COMMANDS = ("start", "stop")

_COMMAND = re.compile(r"^\s*[/／]\s*(?:钓鱼|fish)(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_CONFIRM = ("确认", "confirm", "yes")
ALIASES = {
    "开始": "start", "停止": "stop", "暂停": "stop", "状态": "status", "背包": "bag", "地点": "place",
    "解锁": "unlock", "饵": "bait", "商店": "shop", "买": "buy", "升级竿": "rod", "卖": "sell",
    "放生": "release", "图鉴": "codex", "间隔": "interval", "等待": "interval", "帮助": "help", "?": "help", "？": "help",
}
COMMANDS = ("start", "stop", "status", "bag", "place", "unlock", "bait", "shop", "buy", "rod", "sell", "release", "codex", "interval", "help")
HELP = (
    "钓鱼命令：/钓鱼 开始｜停止｜状态｜背包｜商店｜图鉴 [鱼名]｜地点 <名>｜解锁 <名>｜饵 <名>｜买 <饵> [数量]｜"
    "升级竿｜卖 <鱼名｜全部普通｜全部>｜放生 <鱼名> [最大]｜等待 <最短分钟> <最长分钟>"
)


@dataclass
class CmdResult:
    ok: bool
    text: str
    need_confirm: bool = False


def parse_command(text: str) -> tuple[str, list[str], bool] | None:
    """A typed fishing command -> (command, arguments, confirmed); None when the text is ordinary chat."""
    m = _COMMAND.match(text)
    if m is None:
        return None
    tokens = (m.group(1) or "").split()
    confirmed = bool(tokens) and tokens[-1].lower() in _CONFIRM
    if confirmed:
        tokens.pop()
    if not tokens:
        return "status", [], confirmed
    head = tokens[0].lower()
    return ALIASES.get(head, head), tokens[1:], confirmed


async def _never_quiet() -> bool:
    return False


async def _no_send(_msg: dict) -> None:
    return None


async def _no_say(_text: str, _expression: str | None, _motion: str | None, _voice: bool) -> None:
    return None


class FishingGame:
    id = "fishing"
    # A background game: it does not pause the pet's own chatter.
    background = True

    def __init__(
        self,
        *,
        files: FishFiles,
        store: Store,
        brain: Any = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
        send: Callable[[dict], Awaitable[None]] = _no_send,
        say: Callable[[str, str | None, str | None, bool], Awaitable[None]] = _no_say,
        quiet: Callable[[], Awaitable[bool]] = _never_quiet,
        busy: Callable[[], bool] = lambda: False,
        may_speak: Callable[[], bool] = lambda: True,
        labels: Callable[[], Any] = lambda: None,
        affinity_level: Callable[[], int] = lambda: 0,
        reward: Callable[[float, str], Awaitable[None]] | None = None,
        publish: Callable[[Event], None] | None = None,
    ) -> None:
        self.files = files
        self.store = store
        self.brain = brain
        self.rng = rng or random.Random()
        self._clock = clock
        self._send = send
        self._say = say
        self._quiet = quiet
        self._busy = busy
        # Whether the pet may speak unasked (the 主动对话 switch). Replies to commands are not affected.
        self._may_speak = may_speak
        self._labels = labels
        self._affinity = affinity_level
        self._reward = reward
        self._publish = publish
        self._lock = asyncio.Lock()
        # The open bite window: {seq, result, timer}.
        self._pending: dict[str, Any] | None = None
        self._recent_lines: deque[str] = deque(maxlen=5)
        # The latest HUD timelines, numbered, for the console's fishing scene (it polls; the pet gets them pushed).
        self._feed: deque[tuple[int, dict]] = deque(maxlen=30)
        self._feed_n = 0
        self._tasks: set[asyncio.Task] = set()
        self._last_pub = float("-inf")
        self._last_sky: tuple[str, str] | None = None
        self._last_chore = self._clock()
        self._seed_bottles()

    # --- state -----------------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.store.get("enabled", False))

    def settings(self, cfg: FishConfig | None = None) -> GameSettings:
        """fish.yaml's `game` section with the console's changes on top."""
        base = (cfg or self.files.fish()).game
        over = self.store.get("settings", {})
        if not isinstance(over, dict) or not over:
            return base
        try:
            return GameSettings.model_validate({**base.model_dump(), **{k: v for k, v in over.items() if k in SETTING_KEYS}})
        except ValidationError:
            return base

    def set_settings(self, changes: dict[str, Any]) -> GameSettings:
        """Raises ValidationError on a value out of range."""
        cfg = self.files.fish()
        # Keys of older versions (tick_interval_sec) are dropped here.
        over = {k: v for k, v in dict(self.store.get("settings", {}) or {}).items() if k in SETTING_KEYS}
        over.update({k: v for k, v in changes.items() if k in SETTING_KEYS and v is not None})
        GameSettings.model_validate({**cfg.game.model_dump(), **over})
        self.store.set("settings", over)
        return self.settings(cfg)

    def _unlocked(self, cfg: FishConfig) -> list[str]:
        have = self.store.get("unlocked_locations", [])
        free = [k for k, loc in cfg.locations.items() if loc.unlock_cost == 0]
        return [k for k in cfg.locations if k in free or k in (have if isinstance(have, list) else [])]

    def _location(self, cfg: FishConfig) -> str:
        loc = self.store.get("location")
        return loc if loc in self._unlocked(cfg) else cfg.start_location()

    def _rod(self, cfg: FishConfig) -> int:
        level = self.store.get("rod_level", 1)
        return level if isinstance(level, int) and level in cfg.rods else 1

    def _bait(self, cfg: FishConfig) -> str:
        bait = self.store.get("bait", "none")
        if bait != "none" and (bait not in cfg.baits or self.store.bait_qty(bait) <= 0):
            return "none"
        return bait

    def _weather(self, cfg: FishConfig, now: float) -> str:
        weather, since = self.store.get("weather"), self.store.get("weather_ts", 0)
        if weather not in WEATHER_NAMES or now - float(since or 0) >= self.settings(cfg).weather_change_hours * 3600 or now < float(since or 0):
            weather = engine.draw_weather(cfg, self.rng)
            with self.store.tx():
                self.store.set("weather", weather)
                self.store.set("weather_ts", now)
        return weather

    def _name(self, cfg: FishConfig, species_id: str | None) -> str:
        sp = cfg.species_by_id(species_id)
        return sp.name if sp else UNKNOWN_FISH

    # --- the loop --------------------------------------------------------------------------------

    def _due(self) -> float | None:
        """When the line in the water gets its bite (Unix seconds); None = no line is out."""
        due = self.store.get("cast_due")
        return float(due) if isinstance(due, (int, float)) and not isinstance(due, bool) else None

    def cast_ts(self) -> float | None:
        """When the line in the water was cast; None = no line is out."""
        ts = self.store.get("cast_ts")
        if not self.enabled or self._due() is None or isinstance(ts, bool) or not isinstance(ts, (int, float)):
            return None
        return float(ts)

    async def _cast(self, cfg: FishConfig, now: float, *, animate: bool) -> None:
        """Puts the line out: draws how long this cast waits and stores it. Call with the lock held."""
        settings = self.settings(cfg)
        wait = engine.draw_wait(self.rng, settings.wait_min_sec, settings.wait_max_sec)
        with self.store.tx():
            self.store.set("cast_ts", now)
            self.store.set("cast_due", now + wait)
        if animate:
            seq = int(self.store.get("cast_seq", 0) or 0) + 1
            await self._anim(seq, "cast", "cast", [{"state": "cast", "dur": STEP_MS["cast"]}], settings)
        await self.push_state()

    async def run(self) -> None:
        """Publishes timer.fishing_tick when the cast is due; settles missed casts silently; tells summaries."""
        while True:
            try:
                if self.enabled and self._pending is None:
                    now, due = self._clock(), self._due()
                    if due is None:
                        # Fishing is on but no line is out (first run after an update, or a crash mid-cast).
                        quiet = await self._quiet()
                        async with self._lock:
                            if self.enabled and self._due() is None and self._pending is None:
                                await self._cast(self.files.fish(), now, animate=not quiet)
                    elif now - due >= LATE_S:
                        await self.catch_up()
                    elif now >= due and self._publish is not None and now - self._last_pub >= REPUBLISH_S:
                        self._last_pub = now
                        self._publish(make_event("timer.fishing_tick", {}))
                if self.enabled:
                    # The scene's sky follows the time of day and the weather between casts too.
                    now = self._clock()
                    sky = (engine.time_slot(now), self._weather(self.files.fish(), now))
                    if sky != self._last_sky:
                        self._last_sky = sky
                        await self.push_state()
                await self.flush_digest()
                if self.enabled and self._clock() - self._last_chore >= CHORE_EVERY_S and not self._busy():
                    self._last_chore = self._clock()
                    await self.chores()
            except ConfigError:
                pass  # logged by FishFiles; fixed by editing fish.yaml
            except Exception:
                log.exception("钓鱼调度出错")
            await asyncio.sleep(POLL_S)

    async def on_event(self, event: Event) -> None:
        """Event bus handler."""
        if event.type == "timer.fishing_tick":
            await self.tick()

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if self._pending and self._pending.get("timer"):
            self._pending["timer"].cancel()
        self.store.close()

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- one cast --------------------------------------------------------------------------------

    def _resolve(self, cfg: FishConfig, now: float) -> CastResult:
        state = CastState(
            seq=int(self.store.get("cast_seq", 0) or 0) + 1,
            location=self._location(cfg),
            bait=self._bait(cfg),
            rod_level=self._rod(cfg),
            empty_streak=int(self.store.get("empty_streak", 0) or 0),
            weather=self._weather(cfg, now),
            luck=engine.luck_for(cfg, self._affinity()),
        )
        return engine.resolve_cast(state, cfg, self.rng, now)

    def _land(self, cfg: FishConfig, result: CastResult) -> dict[str, Any]:
        """Puts the fish in the bag and the codex. Call inside a transaction."""
        assert result.species_id is not None and result.size_cm is not None
        self.store.add_fish(result.species_id, result.size_cm, result.trophy, result.ts, result.ctx["location"])
        first, record = self.store.record_catch(result.species_id, result.size_cm, result.ts)
        sp = cfg.species_by_id(result.species_id)
        bonus = cfg.rarity[sp.rarity].first_bonus if first and sp and sp.rarity in cfg.rarity else 0
        if bonus:
            self.store.add_coins(bonus)
        return {"first": first, "record": record, "coins": bonus}

    def _settle(self, cfg: FishConfig, result: CastResult, landed: bool | None) -> dict[str, Any]:
        """Writes one cast in one transaction. `landed`: True caught, False got away, None = the bite window
        is open (logged as got away until the window says otherwise, so a crash never invents a fish)."""
        info: dict[str, Any] = {}
        with self.store.tx():
            self.store.set("cast_seq", result.seq)
            self.store.set("last_tick_ts", result.ts)
            # The line is out of the water with this cast (same transaction: a crash never settles it twice).
            self.store.set("cast_ts", None)
            self.store.set("cast_due", None)
            bait = result.ctx["bait"]
            if bait != "none" and self.store.add_bait(bait, -1) <= 0:
                self.store.set("bait", "none")
            streak = int(self.store.get("empty_streak", 0) or 0) + 1 if result.outcome == "empty" else 0
            self.store.set("empty_streak", streak)
            info["streak"] = streak
            self.store.log_cast(result, escaped=result.outcome == "fish" and landed is not True)
            if result.outcome == "junk" and result.junk_id:
                self.store.add_junk(result.junk_id)
                junk = cfg.junk_by_id(result.junk_id)
                info["coins"] = junk.sell_price if junk else 0
                self.store.add_coins(info["coins"])
            elif result.outcome == "bottle":
                reward = cfg.bottle.reward_coins
                info["coins"] = self.rng.randint(min(reward.min, reward.max), max(reward.min, reward.max))
                self.store.add_coins(info["coins"])
                info["letter"] = self.store.take_bottle(result.ts)
            elif result.outcome == "fish" and landed:
                info.update(self._land(cfg, result))
        return info

    def _happening(self, cfg: FishConfig, result: CastResult, info: dict[str, Any], landed: bool) -> dict[str, Any]:
        """What happened on a settled cast, in the shape the animation, the line and the summary need."""
        sp = cfg.species_by_id(result.species_id)
        outcome = result.outcome if result.outcome != "fish" else ("catch" if landed else "escape")
        junk = cfg.junk_by_id(result.junk_id)
        return {
            "seq": result.seq,
            "outcome": outcome,
            "species": sp,
            "species_id": result.species_id,
            "size_cm": result.size_cm,
            "trophy": result.trophy,
            "first": bool(info.get("first")),
            "record": bool(info.get("record")),
            "junk_id": result.junk_id,
            "junk_name": junk.name if junk else "杂物",
            "letter": info.get("letter"),
            "coins": info.get("coins", 0),
            "streak": info.get("streak", 0),
            "ctx": result.ctx,
        }

    async def tick(self, *, force: bool = False) -> dict[str, Any] | None:
        """Settles the cast whose wait is over, then puts the line out again. `force` (tests only): settle now,
        whatever the wait and the switch say. Returns what happened, or None when nothing was settled (off,
        the wait is not over, a bite window is open, broken config)."""
        async with self._lock:
            try:
                cfg = self.files.fish()
            except ConfigError:
                return None
            if (not self.enabled and not force) or self._pending is not None:
                return None
            now = self._clock()
            if not force:
                due = self._due()
                if due is None or now < due:
                    return None
            quiet = await self._quiet()
            result = self._resolve(cfg, now)
            settings = self.settings(cfg)
            if result.needs_window and not quiet:
                self._settle(cfg, result, None)
                steps = [{"state": "bite", "dur": settings.bite_window_ms, "window": True}]
                total = settings.bite_window_ms / 1000 + HOOK_GRACE_S
                self._pending = {"seq": result.seq, "result": result, "timer": self._spawn(self._hook_timeout(result.seq, total))}
                await self._anim(result.seq, "until_bite", "pending", steps, settings)
                self._spawn(self._say_line(cfg, self._pool(cfg, "bite", None, {}), None, None))
                return {"seq": result.seq, "outcome": "pending"}
            landed = result.outcome == "fish" and (not result.needs_window or self.rng.random() < engine.hook_chance(cfg, result.ctx["rod_level"]))
            happening = self._happening(cfg, result, self._settle(cfg, result, landed), landed)
            await self._show(cfg, happening, quiet, tug=True)
            if self.enabled:
                await self._cast(cfg, now, animate=not quiet)
            return {"seq": result.seq, "outcome": happening["outcome"]}

    async def _hook_timeout(self, seq: int, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.hook(seq, False)

    async def hook(self, seq: int, clicked: bool) -> None:
        """The bite window's answer from the pet window: clicked = landed for sure; otherwise the pet tries
        on its own. Also called when no answer came in time (as not clicked)."""
        async with self._lock:
            pending = self._pending
            if pending is None or pending["seq"] != seq:
                return
            self._pending = None
            timer = pending.get("timer")
            if timer is not None and timer is not asyncio.current_task():
                timer.cancel()
            try:
                cfg = self.files.fish()
            except ConfigError:
                return
            result: CastResult = pending["result"]
            landed = clicked is True or self.rng.random() < engine.hook_chance(cfg, result.ctx["rod_level"])
            info: dict[str, Any] = {"streak": 0}
            if landed:
                with self.store.tx():
                    self.store.set_escaped(seq, False)
                    info.update(self._land(cfg, result))
            await self._show(cfg, self._happening(cfg, result, info, landed), False)
            if self.enabled:
                await self._cast(cfg, self._clock(), animate=True)

    # --- showing and talking ---------------------------------------------------------------------

    async def _anim(self, seq: int, phase: str, outcome: str, steps: list[dict], settings: GameSettings) -> None:
        msg = {"type": "fishing.anim", "seq": seq, "phase": phase, "outcome": outcome, "steps": steps, "lite": not settings.hud_animation}
        self._feed_n += 1
        self._feed.append((self._feed_n, msg))
        await self._send(msg)

    def feed(self, after: int | None) -> tuple[list[dict[str, Any]], int]:
        """HUD timelines numbered above `after` (None = none, just the current number), and the latest number."""
        items = [] if after is None else [{"n": n, "msg": m} for n, m in self._feed if n > after]
        return items, self._feed_n

    def _card(self, cfg: FishConfig, h: dict[str, Any]) -> dict[str, Any] | None:
        if h["outcome"] == "catch":
            sp: Species | None = h["species"]
            rarity = sp.rarity if sp else "common"
            return {
                "kind": "fish",
                "name": sp.name if sp else UNKNOWN_FISH,
                "template": sp.template if sp else "slim",
                "patterns": list(sp.patterns) if sp else [],
                "size_cm": h["size_cm"],
                "rarity": rarity,
                "rarity_label": cfg.rarity[rarity].label if rarity in cfg.rarity else "",
                "first": h["first"],
                "trophy": h["trophy"],
                "record": h["record"],
            }
        if h["outcome"] == "junk":
            return {"kind": "junk", "name": h["junk_name"], "junk_id": h["junk_id"]}
        if h["outcome"] == "bottle":
            letter = h["letter"] or {}
            return {"kind": "bottle", "name": "漂流瓶", "sender": letter.get("sender", ""), "body": letter.get("body", ""), "coins": h["coins"]}
        return None

    async def _show(self, cfg: FishConfig, h: dict[str, Any], quiet: bool, *, tug: bool = False) -> None:
        """Plays how a cast ended (the line has been in the water since it was cast): HUD animation plus
        (maybe) a line; while quiet it only goes to the summary. `tug`: the float goes under first (a cast
        that had no bite window)."""
        if h["outcome"] == "catch" and h["first"]:
            self._spawn(self._write_story(cfg, h["species_id"]))
        if quiet:
            self._digest_add(h)
            return
        settings = self.settings(cfg)
        card = self._card(cfg, h)
        steps: list[dict[str, Any]] = []
        if tug and h["outcome"] != "empty":
            steps.append({"state": "bite", "dur": STEP_MS["bite"]})
        if h["outcome"] == "escape":
            steps.append({"state": "escape", "dur": STEP_MS["escape"]})
        elif card:
            dur = BOTTLE_CARD_MS if card["kind"] == "bottle" else STEP_MS["result"] + (800 if card.get("rarity") in SPECIAL or card.get("first") else 0)
            steps += [{"state": "reel", "dur": STEP_MS["reel"]}, {"state": "result", "dur": dur, "card": card}]
        elif settings.animate_empty:
            steps.append({"state": "empty", "dur": STEP_MS["empty"]})
        else:
            steps.append({"state": "empty", "dur": 500, "minimal": True})
        await self._anim(h["seq"], "resolve", h["outcome"], steps, settings)
        await self.push_state()
        self._spawn(self._speak(cfg, h))

    def _pool(self, cfg: FishConfig, kind: str, category: str | None, values: dict[str, Any]) -> str:
        """A stock line, avoiding the last few said."""
        group = pick_group(self.files.lines(), kind, category, cfg.persona.mood)
        fresh = [line for line in group if line not in self._recent_lines] or group
        try:
            return self.rng.choice(fresh).format_map({"fish": "", "size": "", "place": "", "n": "", "fish_count": "", **values})
        except (KeyError, IndexError, ValueError):
            return ""

    def _values(self, cfg: FishConfig, h: dict[str, Any]) -> dict[str, Any]:
        loc = cfg.locations.get(h["ctx"].get("location", ""))
        size = h.get("size_cm")
        return {
            "fish": h["species"].name if h.get("species") else (h["junk_name"] if h["outcome"] == "junk" else UNKNOWN_FISH),
            "size": f"{size:g}" if isinstance(size, (int, float)) else "",
            "place": loc.name if loc else "",
        }

    def _spend(self, cfg: FishConfig) -> bool:
        """Takes one LLM call out of today's budget; False when none is left (or no LLM is set up)."""
        if self.brain is None or not self.brain.available():
            return False
        today = datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")
        used = int(self.store.get("llm_calls_today", 0) or 0) if self.store.get("llm_calls_date") == today else 0
        if used >= self.settings(cfg).llm_budget_per_day:
            return False
        with self.store.tx():
            self.store.set("llm_calls_date", today)
            self.store.set("llm_calls_today", used + 1)
        return True

    def llm_used_today(self) -> int:
        today = datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")
        return int(self.store.get("llm_calls_today", 0) or 0) if self.store.get("llm_calls_date") == today else 0

    async def _ask(self, rules: str, situation: str) -> Any:
        try:
            return await asyncio.wait_for(self.brain.ask(rules, situation), perform.LLM_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.info("钓鱼：LLM 超时，改用台词池")
            return None

    async def _llm_line(self, cfg: FishConfig, payload: dict[str, Any], numbers: set[float]) -> dict[str, Any] | None:
        # No line will be said, so no call is paid for.
        if not self._may_speak() or not self._spend(cfg):
            return None
        labels = self._labels()
        expressions = list(getattr(labels, "expressions", []) or [])
        motions = list(getattr(labels, "motions", []) or [])
        payload = {
            "event": "game.fishing.result",
            **payload,
            "persona_traits": {"patience": cfg.persona.patience, "talkative": cfg.persona.talkative},
            "recent_lines": list(self._recent_lines),
            "allowed": {"expressions": expressions, "motions": motions},
        }
        move = await self._ask(perform.LINE_RULES, json.dumps(payload, ensure_ascii=False))
        if move is None:
            return None
        line = perform.validate_line(move, allowed_expressions=expressions, allowed_motions=motions, numbers=numbers)
        if line is None:
            log.warning("fishing.llm_rejected：模型的钓鱼台词不合规，改用台词池")
        return line

    async def _say_line(self, cfg: FishConfig, text: str, expression: str | None, motion: str | None) -> None:
        # Every unasked line goes through here: silent while 主动对话 is off (the HUD still shows what happened).
        if not text or not self._may_speak():
            return
        self._recent_lines.append(text)
        await self._say(text, expression, motion, True)

    def _context(self, cfg: FishConfig, ctx: dict[str, Any]) -> dict[str, str]:
        loc = cfg.locations.get(ctx.get("location", ""))
        bait = cfg.baits.get(ctx.get("bait", "none"))
        return {
            "location": loc.name if loc else "",
            "weather": WEATHER_NAMES.get(ctx.get("weather", ""), ""),
            "time_slot": TIME_NAMES.get(ctx.get("time_slot", ""), ""),
            "bait": bait.name if bait else "",
            "mood": cfg.persona.mood,
        }

    async def _speak(self, cfg: FishConfig, h: dict[str, Any]) -> None:
        """The pet's line for a settled cast. Mostly silence; the LLM only for what is worth it."""
        try:
            outcome, sp = h["outcome"], h.get("species")
            rarity = sp.rarity if sp else "common"
            values = self._values(cfg, h)
            chatty = self.rng.random() < cfg.persona.talkative * 0.6 and not self._busy()
            worth_llm = (
                (outcome == "catch" and (rarity in SPECIAL or h["first"] or h["trophy"] or h["record"]))
                or outcome in ("escape", "bottle")
                or (outcome == "empty" and h["streak"] >= 6 and h["streak"] % 6 == 0)
            )
            if worth_llm:
                payload: dict[str, Any] = {"outcome": "empty_streak" if outcome == "empty" else outcome, "context": self._context(cfg, h["ctx"]), "streak": {"empty": h["streak"]}}
                numbers: set[float] = set()
                if sp is not None and h["size_cm"] is not None:
                    payload["fish"] = {
                        "id": sp.id, "name": sp.name, "rarity": sp.rarity, "size_cm": h["size_cm"],
                        "trophy": h["trophy"], "first_time": h["first"], "new_record": h["record"],
                    }
                    numbers = {float(h["size_cm"])}
                if outcome == "bottle" and h["letter"]:
                    payload["letter"] = {"sender": h["letter"]["sender"], "body": h["letter"]["body"]}
                if outcome == "empty":
                    numbers = {float(h["streak"])}
                line = await self._llm_line(cfg, payload, numbers)
                if line is not None:
                    await self._say_line(cfg, line["text"], line["expression"], line["motion"])
                    return
            if outcome == "catch":
                text = self._pool(cfg, "catch", rarity, values) if worth_llm or chatty else ""
            elif outcome == "escape":
                text = self._pool(cfg, "escape", None, values)
            elif outcome == "bottle":
                text = self._pool(cfg, "bottle", None, values)
            elif outcome == "junk":
                text = self._pool(cfg, "junk", None, values) if chatty else ""
            else:
                text = self._pool(cfg, "streak", None, values) if h["streak"] >= 3 and h["streak"] % 3 == 0 and not self._busy() else ""
            await self._say_line(cfg, text, None, None)
        except Exception:
            log.exception("钓鱼台词出错")

    # --- summary of silent casts -----------------------------------------------------------------

    def _digest_add(self, h: dict[str, Any]) -> None:
        digest = self.store.get("digest", [])
        digest = digest if isinstance(digest, list) else []
        sp = h.get("species")
        digest.append({
            "outcome": h["outcome"], "species_id": h.get("species_id"), "size_cm": h.get("size_cm"),
            "first": h["first"], "special": bool(sp and sp.rarity in SPECIAL) or h["trophy"],
        })
        self.store.set("digest", digest[-MAX_DIGEST:])

    async def flush_digest(self) -> bool:
        """Tells the casts settled in silence as one summary, once the user may be disturbed again."""
        digest = self.store.get("digest", [])
        if not isinstance(digest, list) or not digest or self._pending is not None or self._busy():
            return False
        if await self._quiet():
            return False
        async with self._lock:
            cfg = self.files.fish()
            self.store.set("digest", [])
            caught = [d for d in digest if d.get("outcome") == "catch"]
            notable = [d for d in caught if d.get("special") or d.get("first")]
            names = list(dict.fromkeys(self._name(cfg, d.get("species_id")) for d in notable))
            n, fish_count = len(digest), len(caught)
            bottles = sum(1 for d in digest if d.get("outcome") == "bottle")
            card = {"kind": "digest", "n": n, "fish_count": fish_count, "bottles": bottles, "highlights": names[:3], "more": max(0, fish_count - len(names[:3]))}
            settings = self.settings(cfg)
            seq = int(self.store.get("cast_seq", 0) or 0)
            await self._anim(seq, "digest", "digest", [{"state": "result", "dur": DIGEST_CARD_MS, "card": card}], settings)
            await self.push_state()
        payload = {
            "outcome": "offline_summary",
            "summary": {"casts": n, "fish_count": fish_count, "highlights": names[:3], "bottles": bottles},
            "context": self._context(cfg, {"location": self._location(cfg)}),
        }
        line = await self._llm_line(cfg, payload, {float(n), float(fish_count), float(bottles)})
        if line is not None:
            await self._say_line(cfg, line["text"], line["expression"], line["motion"])
        else:
            await self._say_line(cfg, self._pool(cfg, "offline_summary", None, {"n": n, "fish_count": fish_count}), None, None)
        return True

    async def catch_up(self) -> int:
        """Settles, silently, the casts whose wait ran out while the program was closed (or asleep): each at
        the time it was due, with a new random wait after it, so the time away brings no more fish than the
        same time spent watching. Returns how many."""
        if not self.enabled or self._pending is not None:
            return 0
        async with self._lock:
            cfg = self.files.fish()
            settings = self.settings(cfg)
            now = self._clock()
            due = self._due()
            if due is None or now - due < LATE_S:
                return 0
            # Only the last offline_catchup_max_hours count.
            due = max(due, now - settings.offline_catchup_max_hours * 3600)
            cast_ts, n = due, 0
            while due <= now and n < settings.offline_catchup_cap:
                # Oldest first, so time of day and weather move along.
                result = self._resolve(cfg, due)
                landed = result.outcome == "fish" and (not result.needs_window or self.rng.random() < engine.hook_chance(cfg, result.ctx["rod_level"]))
                h = self._happening(cfg, result, self._settle(cfg, result, landed), landed)
                self._digest_add(h)
                if h["outcome"] == "catch" and h["first"]:
                    self._spawn(self._write_story(cfg, h["species_id"]))
                n += 1
                cast_ts = due
                due += engine.draw_wait(self.rng, settings.wait_min_sec, settings.wait_max_sec)
            if due <= now:  # the cap was reached: the next line goes out now
                cast_ts, due = now, now + engine.draw_wait(self.rng, settings.wait_min_sec, settings.wait_max_sec)
            with self.store.tx():
                self.store.set("cast_ts", cast_ts)
                self.store.set("cast_due", due)
            if n:
                log.info("钓鱼：补算了 %d 竿", n)
            await self.push_state()
            return n

    # --- codex notes and bottle letters ----------------------------------------------------------

    def _seed_bottles(self) -> None:
        if self.store.bottle_counts()[0]:
            return
        try:
            seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("读取内置漂流瓶信件失败")
            return
        letters = [ok for x in seed if isinstance(x, dict) and (ok := perform.valid_letter(x.get("sender"), x.get("body")))]
        self.store.add_bottles(letters, self._clock())

    async def _write_story(self, cfg: FishConfig, species_id: str | None) -> None:
        """A short codex note for a newly found species, written once and kept."""
        try:
            sp = cfg.species_by_id(species_id)
            entry = self.store.codex_entry(species_id or "")
            if sp is None or entry is None or entry.get("story") or not self._spend(cfg):
                return
            places = "、".join(cfg.locations[k].name for k in sp.locations if k in cfg.locations)
            times = "全天" if "any" in sp.time else "、".join(TIME_NAMES[t] for t in sp.time)
            label = cfg.rarity[sp.rarity].label if sp.rarity in cfg.rarity else ""
            story = perform.validate_story(await self._ask(perform.STORY_RULES, f"物种：{sp.name}（{label}），常见于{places}，出没时间：{times}。"))
            if story:
                self.store.set_story(sp.id, story, self._clock())
        except Exception:
            log.exception("写钓鱼笔记出错")

    async def chores(self) -> None:
        """Quiet upkeep, at most one LLM call: a missing codex note, or a new batch of bottle letters."""
        cfg = self.files.fish()
        for sid, entry in self.store.codex().items():
            if not entry.get("story") and cfg.species_by_id(sid) is not None:
                await self._write_story(cfg, sid)
                return
        if self.store.bottle_counts()[1] < cfg.bottle.pool_min and self._spend(cfg):
            move = await self._ask(perform.BOTTLE_RULES, f"写 {cfg.bottle.batch_size} 封。")
            letters = perform.validate_letters(move)[: cfg.bottle.batch_size]
            if letters:
                self.store.add_bottles(letters, self._clock())
                log.info("钓鱼：新写了 %d 封漂流瓶信件", len(letters))

    # --- commands --------------------------------------------------------------------------------

    async def handle_text(self, text: str) -> bool:
        """A typed `/钓鱼 …` command: runs it and shows the reply. False = ordinary chat, not consumed."""
        parsed = parse_command(text)
        if parsed is None:
            return False
        result = await self.command(*parsed)
        await self._say(result.text, None, None, False)
        return True

    async def command(self, cmd: str, args: list[str] | None = None, confirmed: bool = False) -> CmdResult:
        """One handler for typed commands, the console and the pet's menu."""
        args = [str(a) for a in (args or [])]
        if cmd not in COMMANDS:
            return CmdResult(False, f"没有「{cmd}」这个钓鱼命令。{HELP}")
        try:
            cfg = self.files.fish()
        except ConfigError as e:
            return CmdResult(False, f"钓鱼配置有误：{e}")
        async with self._lock:
            was_on = self.enabled
            result = getattr(self, f"_cmd_{cmd}")(cfg, args, confirmed)
            if cmd == "start" and not was_on and self.enabled:
                await self._cast(cfg, self._clock(), animate=True)
        if isinstance(result, tuple):  # (result, affection to hand out)
            result, amount = result
            if amount and self._reward is not None:
                try:
                    await self._reward(amount, "放生了钓到的鱼")
                except Exception:
                    log.exception("放生奖励好感度失败")
        await self.push_state()
        return result

    def _cmd_help(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        return CmdResult(True, HELP)

    def _cmd_start(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        if self.enabled:
            return CmdResult(True, "已经在钓鱼了。")
        s = self.settings(cfg)
        with self.store.tx():
            self.store.set("enabled", True)
            self.store.set("cast_ts", None)
            self.store.set("cast_due", None)  # command() puts the line out
        self._last_pub = float("-inf")
        return CmdResult(
            True,
            f"抛竿：在「{cfg.locations[self._location(cfg)].name}」。要等上一阵才会有动静"
            f"（每一竿 {self._span(s.wait_min_sec)} 到 {self._span(s.wait_max_sec)} 不等）。",
        )

    @staticmethod
    def _span(seconds: float) -> str:
        minutes = max(1, round(seconds / 60))
        if minutes < 60:
            return f"{minutes} 分钟"
        hours, rest = divmod(minutes, 60)
        return f"{hours} 小时" + (f" {rest} 分钟" if rest else "")

    def _cmd_stop(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        with self.store.tx():
            self.store.set("enabled", False)
            # The line comes in empty: whatever would have bitten is not owed later.
            self.store.set("cast_ts", None)
            self.store.set("cast_due", None)
        return CmdResult(True, "收竿，先不钓了（背包和图鉴都留着）。")

    def _cmd_status(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        bait = self._bait(cfg)
        bait_text = cfg.baits[bait].name + (f"×{self.store.bait_qty(bait)}" if bait != "none" else "")
        parts = [
            f"在「{cfg.locations[self._location(cfg)].name}」钓鱼" if self.enabled else "钓鱼已暂停",
            f"天气：{WEATHER_NAMES[self._weather(cfg, self._clock())]}",
            f"饵：{bait_text}",
            f"竿：{cfg.rods[self._rod(cfg)].name}",
            f"金币 {int(self.store.get('coins', 0) or 0)}",
        ]
        since = self.cast_ts()
        if since is not None:
            parts.append(f"这一竿已经等了{self._span(max(0.0, self._clock() - since))}")
        return CmdResult(True, " · ".join(parts))

    def _cmd_bag(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        fish = self.store.inventory()
        if not fish:
            text = "背包里还没有鱼。"
        else:
            counts: dict[str, int] = {}
            for f in fish:
                name = self._name(cfg, f["species_id"])
                counts[name] = counts.get(name, 0) + 1
            text = f"背包 {len(fish)} 条：" + "、".join(f"{k}×{v}" for k, v in counts.items())
        stock = self.store.bait_stock()
        if stock:
            text += " ｜ 饵：" + "、".join(f"{cfg.baits[k].name}×{v}" for k, v in stock.items() if k in cfg.baits)
        return CmdResult(True, text)

    def _cmd_shop(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        baits = "、".join(f"{b.name} {b.cost} 金币" for k, b in cfg.baits.items() if k != "none")
        level = self._rod(cfg)
        nxt = cfg.rods.get(level + 1)
        rod = f"升级到{nxt.name} {nxt.cost} 金币" if nxt else "鱼竿已是最高级"
        return CmdResult(True, f"饵：{baits} ｜ {rod} ｜ 金币 {int(self.store.get('coins', 0) or 0)}")

    def _cmd_place(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        key = cfg.find(cfg.locations, args[0]) if args else None
        if key is None:
            return CmdResult(False, "去哪里钓？可选：" + "、".join(loc.name for loc in cfg.locations.values()))
        loc = cfg.locations[key]
        if key not in self._unlocked(cfg):
            return CmdResult(False, f"「{loc.name}」还没解锁：需要 {loc.unlock_cost} 金币" + (f"和 {cfg.rods[loc.requires_rod_level].name}" if loc.requires_rod_level > 1 and loc.requires_rod_level in cfg.rods else "") + f"。用 /钓鱼 解锁 {loc.name}")
        self.store.set("location", key)
        return CmdResult(True, f"换到「{loc.name}」钓鱼。")

    def _cmd_unlock(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        key = cfg.find(cfg.locations, args[0]) if args else None
        if key is None:
            return CmdResult(False, "要解锁哪里？可选：" + "、".join(loc.name for loc in cfg.locations.values()))
        loc = cfg.locations[key]
        if key in self._unlocked(cfg):
            return CmdResult(True, f"「{loc.name}」已经解锁了。")
        if self._rod(cfg) < loc.requires_rod_level:
            need = cfg.rods.get(loc.requires_rod_level)
            return CmdResult(False, f"「{loc.name}」需要先把鱼竿升级到{need.name if need else loc.requires_rod_level}。")
        coins = int(self.store.get("coins", 0) or 0)
        if coins < loc.unlock_cost:
            return CmdResult(False, f"金币不够：解锁「{loc.name}」要 {loc.unlock_cost}，现在只有 {coins}。")
        if not confirmed:
            return CmdResult(False, f"解锁「{loc.name}」要花 {loc.unlock_cost} 金币。确定的话：/钓鱼 解锁 {loc.name} 确认", need_confirm=True)
        with self.store.tx():
            self.store.add_coins(-loc.unlock_cost)
            self.store.set("unlocked_locations", [*[k for k in self._unlocked(cfg)], key])
        return CmdResult(True, f"解锁了「{loc.name}」！用 /钓鱼 地点 {loc.name} 过去。")

    def _cmd_bait(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        key = cfg.find(cfg.baits, args[0]) if args else None
        if key is None:
            return CmdResult(False, "用哪种饵？可选：" + "、".join(b.name for b in cfg.baits.values()))
        if key != "none" and self.store.bait_qty(key) <= 0:
            return CmdResult(False, f"没有{cfg.baits[key].name}了，先 /钓鱼 买 {cfg.baits[key].name}")
        self.store.set("bait", key)
        return CmdResult(True, f"换成{cfg.baits[key].name}。")

    def _cmd_buy(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        key = cfg.find(cfg.baits, args[0]) if args else None
        if key is None or key == "none":
            return CmdResult(False, "买哪种饵？可选：" + "、".join(b.name for k, b in cfg.baits.items() if k != "none"))
        try:
            qty = int(args[1]) if len(args) > 1 else 1
        except ValueError:
            qty = 0
        if not 1 <= qty <= 999:
            return CmdResult(False, "数量要在 1 到 999 之间。")
        cost = cfg.baits[key].cost * qty
        coins = int(self.store.get("coins", 0) or 0)
        if coins < cost:
            return CmdResult(False, f"金币不够：{qty} 个{cfg.baits[key].name}要 {cost}，现在只有 {coins}。")
        with self.store.tx():
            self.store.add_coins(-cost)
            total = self.store.add_bait(key, qty)
        return CmdResult(True, f"买了 {qty} 个{cfg.baits[key].name}（共 {total} 个），花了 {cost} 金币。")

    def _cmd_rod(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        level = self._rod(cfg)
        nxt = cfg.rods.get(level + 1)
        if nxt is None:
            return CmdResult(True, f"鱼竿已经是最高级的{cfg.rods[level].name}了。")
        coins = int(self.store.get("coins", 0) or 0)
        if coins < nxt.cost:
            return CmdResult(False, f"金币不够：升级到{nxt.name}要 {nxt.cost}，现在只有 {coins}。")
        if not confirmed:
            return CmdResult(False, f"升级到{nxt.name}要花 {nxt.cost} 金币。确定的话：/钓鱼 升级竿 确认", need_confirm=True)
        with self.store.tx():
            self.store.add_coins(-nxt.cost)
            self.store.set("rod_level", level + 1)
        return CmdResult(True, f"鱼竿升级成{nxt.name}了！")

    def _price(self, cfg: FishConfig, fish: dict[str, Any]) -> int:
        sp = cfg.species_by_id(fish["species_id"])
        return engine.price(cfg, sp, fish["size_cm"], bool(fish["trophy"])) if sp else 1

    def _pick(self, cfg: FishConfig, args: list[str]) -> tuple[dict[str, Any] | None, str]:
        """The bag fish a `<鱼名> [最大]` / `#<id>` argument means: the smallest of that species by default."""
        if not args:
            return None, "要哪条鱼？写鱼名，例如：鲤鱼（默认最小的一条，加「最大」选最大的）"
        fish = self.store.inventory()
        if args[0].startswith("#") and args[0][1:].isdigit():
            found = next((f for f in fish if f["id"] == int(args[0][1:])), None)
            return found, "" if found else "背包里没有这条鱼。"
        same = [f for f in fish if self._name(cfg, f["species_id"]) == args[0] or f["species_id"] == args[0]]
        if not same:
            return None, f"背包里没有{args[0]}。"
        biggest = len(args) > 1 and args[1] in ("最大", "max")
        return (max if biggest else min)(same, key=lambda f: f["size_cm"]), ""

    def _cmd_sell(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        fish = self.store.inventory()
        if args and args[0] in ("全部", "all", "全部普通", "common"):
            everything = args[0] in ("全部", "all")
            chosen = [f for f in fish if everything or (sp := cfg.species_by_id(f["species_id"])) is None or sp.rarity not in SPECIAL]
            if not chosen:
                return CmdResult(False, "没有可以卖的鱼。")
            total = sum(self._price(cfg, f) for f in chosen)
            if everything and not confirmed:
                return CmdResult(False, f"要把背包里全部 {len(chosen)} 条鱼（包括稀有的）卖掉，共 {total} 金币。确定的话：/钓鱼 卖 全部 确认", need_confirm=True)
        else:
            one, why = self._pick(cfg, args)
            if one is None:
                return CmdResult(False, why)
            chosen, total = [one], self._price(cfg, one)
        with self.store.tx():
            self.store.remove_fish([f["id"] for f in chosen])
            coins = self.store.add_coins(total)
        what = f"{self._name(cfg, chosen[0]['species_id'])}（{chosen[0]['size_cm']:g} 厘米）" if len(chosen) == 1 else f"{len(chosen)} 条鱼"
        return CmdResult(True, f"卖掉了{what}，得到 {total} 金币（现在 {coins}）。")

    def _cmd_release(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult | tuple[CmdResult, float]:
        one, why = self._pick(cfg, args)
        if one is None:
            return CmdResult(False, why)
        sp = cfg.species_by_id(one["species_id"])
        name = self._name(cfg, one["species_id"])
        if sp is not None and sp.rarity in SPECIAL and not confirmed:
            return CmdResult(False, f"{name}是{cfg.rarity[sp.rarity].label}鱼，确定放生？/钓鱼 放生 {name} 确认", need_confirm=True)
        today = datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")
        given = float(self.store.get("release_given", 0) or 0) if self.store.get("release_date") == today else 0.0
        amount = round(max(0.0, min(cfg.economy.release_affinity, cfg.economy.release_affinity_daily_cap - given)), 2)
        with self.store.tx():
            self.store.remove_fish([one["id"]])
            self.store.add_released(one["species_id"])
            self.store.set("release_date", today)
            self.store.set("release_given", given + amount)
        return CmdResult(True, f"把{name}（{one['size_cm']:g} 厘米）放回水里了。"), amount

    def _cmd_codex(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        codex = self.store.codex()
        known = [s for s in cfg.species if s.id in codex]
        if not args:
            return CmdResult(True, f"图鉴 {len(known)}/{len(cfg.species)}：" + ("、".join(s.name for s in known) if known else "还没有发现任何鱼") + "。")
        sp = cfg.species_by_name(args[0])
        if sp is None or sp.id not in codex:
            return CmdResult(False, f"图鉴里还没有{args[0]}。")
        e = codex[sp.id]
        return CmdResult(True, f"{sp.name}（{cfg.rarity[sp.rarity].label}）：钓到 {e['count']} 条，最大 {e['max_size']:g} 厘米。" + (e.get("story") or f"关于{sp.name}的笔记还没写完。"))

    def _cmd_interval(self, cfg: FishConfig, args: list[str], confirmed: bool) -> CmdResult:
        """`等待 <最短分钟> <最长分钟>`: how long a cast waits. Applies from the next cast."""
        usage = "写最短和最长等待的分钟数（最短不少于 3 分钟，最长不超过 720 分钟），例如：/钓鱼 等待 5 180"
        try:
            lo, hi = float(args[0]), float(args[1])
        except (IndexError, ValueError):
            return CmdResult(False, usage)
        try:
            self.set_settings({"wait_min_sec": int(lo * 60), "wait_max_sec": int(hi * 60)})
        except ValidationError:
            return CmdResult(False, usage)
        return CmdResult(True, f"之后每一竿等 {self._span(lo * 60)} 到 {self._span(hi * 60)}（已经抛出去的这一竿不变）。")

    # --- views -----------------------------------------------------------------------------------

    def state_message(self) -> dict[str, Any]:
        """For the fishing HUD (pet window and the console's fishing scene). When the bite comes is not sent:
        nobody gets to know it in advance."""
        try:
            cfg = self.files.fish()
        except ConfigError:
            return {"type": "fishing.state", "enabled": False, "lite": False, "placed": True, "scale": 1.0, "cast_ts": None}
        settings = self.settings(cfg)
        return {
            "type": "fishing.state",
            "enabled": self.enabled,
            "lite": not settings.hud_animation,
            "placed": settings.hud_placed,
            "scale": settings.hud_scale,
            "location": self._location(cfg),
            "bait": self._bait(cfg),
            "rod_level": self._rod(cfg),
            "coins": int(self.store.get("coins", 0) or 0),
            "cast_ts": self.cast_ts(),
            # What the scene's sky shows.
            "time_slot": engine.time_slot(self._clock()),
            "weather": self._weather(cfg, self._clock()),
        }

    def pending_seq(self) -> int | None:
        """The cast whose bite window is open now, if any."""
        return self._pending["seq"] if self._pending else None

    async def push_state(self) -> None:
        await self._send(self.state_message())

    def snapshot(self) -> dict[str, Any]:
        """Everything the console's fishing page shows. Undiscovered species give away only a silhouette's
        shape and where to look."""
        cfg = self.files.fish()
        now = self._clock()
        settings = self.settings(cfg)
        codex = self.store.codex()
        unlocked = self._unlocked(cfg)
        species = []
        for s in cfg.species:
            e = codex.get(s.id)
            item: dict[str, Any] = {
                "id": s.id,
                "found": e is not None,
                "template": s.template,
                "locations": [cfg.locations[k].name for k in s.locations if k in cfg.locations],
                "location_ids": list(s.locations),
            }
            if e is not None:
                count = max(1, int(e["count"]))
                item.update(
                    name=s.name, rarity=s.rarity, rarity_label=cfg.rarity[s.rarity].label, patterns=list(s.patterns),
                    time=["全天"] if "any" in s.time else [TIME_NAMES[t] for t in s.time],
                    weather=["不限"] if "any" in s.weather else [WEATHER_NAMES[w] for w in s.weather],
                    baits=[cfg.baits[b].name for b, mult in s.bait_bonus.items() if mult > 1 and b in cfg.baits],
                    count=e["count"], max_size=e["max_size"], min_size=e["min_size"] if e["min_size"] is not None else e["max_size"],
                    avg_size=round(e["total_size"] / count, 1) if e["total_size"] else e["max_size"],
                    first_ts=e["first_ts"], released=e["released"], story=e.get("story"),
                    trophy=engine.is_trophy(cfg, s, e["max_size"]),
                )
            species.append(item)
        inventory = []
        for f in self.store.inventory():
            sp = cfg.species_by_id(f["species_id"])
            inventory.append({
                "id": f["id"], "species_id": f["species_id"], "name": sp.name if sp else UNKNOWN_FISH,
                "rarity": sp.rarity if sp else "common", "rarity_label": cfg.rarity[sp.rarity].label if sp else "",
                "template": sp.template if sp else "slim", "patterns": list(sp.patterns) if sp else [],
                "size_cm": f["size_cm"], "trophy": bool(f["trophy"]), "caught_ts": f["caught_ts"],
                "location": cfg.locations[f["location"]].name if f["location"] in cfg.locations else "", "price": self._price(cfg, f),
            })
        log_rows = []
        for r in self.store.recent_log(40):
            junk = cfg.junk_by_id(r["junk_id"])
            log_rows.append({
                "seq": r["seq"], "ts": r["ts"], "outcome": "escape" if r["outcome"] == "fish" and r["escaped"] else r["outcome"],
                "name": self._name(cfg, r["species_id"]) if r["species_id"] else (junk.name if junk else None),
                "size_cm": r["size_cm"], "location": cfg.locations[r["location"]].name if r["location"] in cfg.locations else "",
                "weather": WEATHER_NAMES.get(r["weather"] or "", ""), "time_slot": TIME_NAMES.get(r["time_slot"] or "", ""),
            })
        level = self._rod(cfg)
        nxt = cfg.rods.get(level + 1)
        junk_stats = self.store.junk_stats()
        return {
            "enabled": self.enabled,
            "error": self.files.error,
            "llm_available": bool(self.brain is not None and self.brain.available()),
            "coins": int(self.store.get("coins", 0) or 0),
            "location": self._location(cfg),
            "bait": self._bait(cfg),
            "rod_level": level,
            "rod_name": cfg.rods[level].name,
            "next_rod": {"name": nxt.name, "cost": nxt.cost} if nxt else None,
            "weather": WEATHER_NAMES[self._weather(cfg, now)],
            "time_slot": TIME_NAMES[engine.time_slot(now)],
            # When the line in the water was cast (null = no line out, or a bite window is open).
            "cast_ts": self.cast_ts(),
            "now": now,
            "empty_streak": int(self.store.get("empty_streak", 0) or 0),
            "hook_chance": round(engine.hook_chance(cfg, level), 2),
            "settings": {k: getattr(settings, k) for k in SETTING_KEYS},
            "llm_used_today": self.llm_used_today(),
            "locations": [
                {"id": k, "name": loc.name, "unlock_cost": loc.unlock_cost, "requires_rod": cfg.rods[loc.requires_rod_level].name if loc.requires_rod_level in cfg.rods else "", "requires_rod_level": loc.requires_rod_level, "unlocked": k in unlocked}
                for k, loc in cfg.locations.items()
            ],
            "baits": [{"id": k, "name": b.name, "cost": b.cost, "qty": self.store.bait_qty(k) if k != "none" else None} for k, b in cfg.baits.items()],
            "inventory": inventory,
            "species": species,
            "found": sum(1 for s in species if s["found"]),
            "junk": [{"id": j.id, "name": j.name, "count": junk_stats.get(j.id, 0)} for j in cfg.junk],
            "bottles": [{"sender": b["sender"], "body": b["body"], "read_ts": b["read_ts"]} for b in self.store.read_bottles(30)],
            "log": log_rows,
            "totals": self.store.totals(),
        }

    def record_line(self) -> str | None:
        """One line for the pet's ordinary prompt, so it can answer "钓到什么了" truthfully."""
        try:
            cfg = self.files.fish()
        except ConfigError:
            return None
        totals = self.store.totals()
        if not totals["casts"]:
            return None
        found = len([s for s in cfg.species if s.id in self.store.codex()])
        state = f"你正在「{cfg.locations[self._location(cfg)].name}」挂机钓鱼" if self.enabled else "你之前挂机钓过鱼（现在收竿了）"
        return (
            f"{state}：一共抛了 {totals['casts']} 竿，钓到 {totals['fish']} 条，图鉴 {found}/{len(cfg.species)}，"
            f"背包里有 {len(self.store.inventory())} 条鱼，金币 {int(self.store.get('coins', 0) or 0)}。"
            "（只在聊到钓鱼时提起；卖鱼、放生、买东西只有主人自己能操作，你不能替主人做。）"
        )
