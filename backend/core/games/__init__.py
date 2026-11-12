"""Mini games played against the pet from the console's 小游戏 page.

The program owns everything a player could cheat with: hidden items, dice and cards, the bullet order, random
numbers, whose turn it is, the score. The LLM only chooses a move (validated here; an illegal or missing one
falls back to a built-in strategy) and says a line. Its prompt never contains what the user keeps hidden, and
the console never receives what the pet keeps hidden.

What is said during a game is never stored: not in the chat history, not in memory. Only the tally of results
is kept (GameHall.records), and one line about it goes into the pet's ordinary prompt.
"""

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Callable

from core.games.auction import Auction
from core.games.brain import GameBrain
from core.games.common import GameError, Side, other
from core.games.liar_dice import LiarDice
from core.games.roulette import Roulette
from core.games.rps import Rps
from core.games.taboo import Taboo

__all__ = ["GAME_NAMES", "Auction", "GameBrain", "GameError", "GameHall", "LiarDice", "Roulette", "Rps", "Side", "Taboo", "other"]

log = logging.getLogger(__name__)

GAME_NAMES = {"roulette": "恶魔轮盘赌", "rps": "骗子猜拳", "dice": "吹牛骰", "auction": "拍卖数字牌", "taboo": "禁忌词"}
STATE_FILE = "games.json"
MAX_RECENT = 20
# An invitation from the pet counts for the game started within this long.
INVITE_VALID_SECONDS = 900
# The pet invites on its own at most this often.
INVITE_EVERY_SECONDS = 1800


class GameHall:
    """The games in progress (memory only; a backend restart ends them), the settings and the tally."""

    def __init__(
        self,
        brain: GameBrain,
        rng: random.Random | None = None,
        state_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.brain = brain
        self.rng = rng or random.Random()
        self._state_path = state_path
        self._clock = clock
        self.games: dict[str, Any] = {k: None for k in GAME_NAMES}
        # voice: the pet's game lines are also spoken (TTS). invite: the pet may propose a game.
        self.settings: dict[str, bool] = {"voice": False, "invite": True}
        # Results per game from the player's side: player = the player won (禁忌词: guessed), pet, draw.
        self.records: dict[str, dict[str, int]] = {k: {"player": 0, "pet": 0, "draw": 0} for k in GAME_NAMES}
        # Newest last: {ts, game, result}.
        self.recent: list[dict[str, Any]] = []
        # The pet's pending invitation: {game, ts, by_pet}.
        self.invited: dict[str, Any] | None = None
        self.last_invite = float("-inf")
        # One move at a time: a pet move must not interleave with a player move.
        self.lock = asyncio.Lock()
        self._load()

    # --- matches ---------------------------------------------------------------------------

    def start(self, key: str, game: Any) -> None:
        inv = self.pending_invite()
        # Started on the pet's own invitation (a win is then worth more affection).
        game.invited = bool(inv and inv["game"] == key and inv["by_pet"])
        if inv and inv["game"] == key:
            self.invited = None
        # Set once the match is over: {say, reward}; also marks the result as recorded.
        game.end = None
        self.games[key] = game

    def playing(self) -> bool:
        return any(g is not None and g.result is None for g in self.games.values())

    def record(self, game: Any) -> None:
        self.records[game.key][game.result] += 1
        self.recent.append({"ts": self._clock(), "game": game.key, "result": game.result})
        del self.recent[:-MAX_RECENT]
        self._save()

    # --- invitations -----------------------------------------------------------------------

    def pending_invite(self) -> dict[str, Any] | None:
        if self.invited and self._clock() - self.invited["ts"] > INVITE_VALID_SECONDS:
            self.invited = None
        return self.invited

    def can_invite(self, *, unprompted: bool = False) -> bool:
        """May the pet propose a game now? `unprompted`: on its own initiative (rate limited)."""
        if not self.settings["invite"] or self.playing():
            return False
        return not unprompted or self._clock() - self.last_invite >= INVITE_EVERY_SECONDS

    def invite(self, key: str, *, by_pet: bool) -> None:
        self.invited = {"game": key, "ts": self._clock(), "by_pet": by_pet}
        if by_pet:
            self.last_invite = self._clock()

    # --- what the pet remembers ------------------------------------------------------------

    def record_line(self) -> str | None:
        """One line for the pet's ordinary prompt: the tally, nothing that was said."""
        parts = []
        for key, name in GAME_NAMES.items():
            r = self.records[key]
            if not sum(r.values()):
                continue
            if key == "taboo":
                parts.append(f"{name} 猜中 {r['player']} 次、没猜中 {r['draw']} 次")
            else:
                parts.append(f"{name} 你赢 {r['pet']} 局、主人赢 {r['player']} 局、平 {r['draw']} 局")
        if not parts:
            return None
        line = "你和主人玩过的小游戏战绩：" + "；".join(parts) + "。"
        if self.recent:
            last = self.recent[-1]
            how = {"player": "主人赢了", "pet": "你赢了", "draw": "打平了"}[last["result"]]
            if last["game"] == "taboo":
                how = "猜中了" if last["result"] == "player" else "没猜出来"
            line += f"最近一局是{GAME_NAMES[last['game']]}，{how}。"
        return line + "（只在聊到游戏时提起，不要每句话都说。）"

    # --- persistence -----------------------------------------------------------------------

    def set_settings(self, **changes: bool | None) -> None:
        for k, v in changes.items():
            if k in self.settings and isinstance(v, bool):
                self.settings[k] = v
        self._save()

    def _load(self) -> None:
        if self._state_path is None:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("小游戏战绩文件读不了，从零开始：%s", e)
            return
        if not isinstance(data, dict):
            return
        for k, v in (data.get("settings") or {}).items() if isinstance(data.get("settings"), dict) else ():
            if k in self.settings and isinstance(v, bool):
                self.settings[k] = v
        records = data.get("records")
        if isinstance(records, dict):
            for key, r in records.items():
                if key in self.records and isinstance(r, dict):
                    for side in self.records[key]:
                        if isinstance(r.get(side), int) and not isinstance(r.get(side), bool) and r[side] >= 0:
                            self.records[key][side] = r[side]
        recent = data.get("recent")
        if isinstance(recent, list):
            self.recent = [
                x
                for x in recent[-MAX_RECENT:]
                if isinstance(x, dict) and x.get("game") in GAME_NAMES and x.get("result") in ("player", "pet", "draw")
            ]

    def _save(self) -> None:
        if self._state_path is None:
            return
        data = {"settings": self.settings, "records": self.records, "recent": self.recent}
        tmp = self._state_path.with_suffix(".json.tmp")
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            log.exception("保存小游戏战绩失败")
        finally:
            tmp.unlink(missing_ok=True)
