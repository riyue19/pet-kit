"""恶魔轮盘赌: a gun loaded with live and blank rounds, two players, hidden items.

Public: both players' health, how many live / blank rounds are left, every shot and item use.
Hidden: the order of the rounds (from everyone), each player's items and what a magnifier showed (from the other).

A round ends when the gun is empty: the wheel decides again who goes first, both sides' items are thrown
away and three new ones dealt, health carries over. The match ends when someone's health reaches zero.
"""

import random
from typing import Any, Literal

from core.games.common import HEADER, JSON_RULE, SAY_RULE, GameError, Side, other

ITEM_NAMES = {"juice": "果汁", "dice": "骰子", "flash": "震爆弹", "lens": "放大镜", "driver": "螺丝刀"}
ITEMS = tuple(ITEM_NAMES)
DEAL = 3
CLASSIC_BULLETS = 6
CLASSIC_HP = 4
BULLET_RANGE = (2, 12)
HP_RANGE = (1, 9)
MAX_LOG = 80
WHO = {"player": "主人", "pet": "你"}

RULES = f"""{HEADER}
游戏是「恶魔轮盘赌」。规则：枪里有若干发子弹，真弹和假弹各剩几发双方都知道，但顺序谁都不知道。轮到谁，谁可以先用任意个道具，然后必须开一枪：朝对方开枪，无论真假都换对方行动；朝自己开枪，是假弹就继续由自己行动，是真弹就自己扣血并换对方行动。真弹扣 1 点生命，谁的生命先归零谁输。子弹打完算一轮结束：重新决定先手，双方手里的道具全部作废并各发 3 个新的，生命值保留。
道具：juice 果汁（回 1 点生命，满血不能用）；dice 骰子（猜大小，1~3 是小、4~6 是大，猜对回 1 点生命，猜错扣 1 点）；flash 震爆弹（你的下一枪如果是真弹，伤害变成 2）；lens 放大镜（偷看当前这一发是真是假，只有你知道）；driver 螺丝刀（卸掉当前这一发，卸下来的子弹双方都看得到）。
策略提示：真弹比例高就打对方，假弹比例高就打自己赚一次行动；道具留到下一轮会作废，该用就用；用放大镜看过就按看到的打。
你每次只做一步。{JSON_RULE}
开枪：{{"action": "shoot", "target": "self 或 opponent", "say": "…"}}
用道具：{{"action": "item", "item": "道具的英文名", "guess": "big 或 small（只有骰子要填）", "say": "…"}}
{SAY_RULE}放大镜看到的结果说不说、说真话还是假话由你决定。"""


class Roulette:
    key = "roulette"

    def __init__(
        self,
        *,
        mode: Literal["classic", "custom"] = "classic",
        bullets: int = CLASSIC_BULLETS,
        player_hp: int = CLASSIC_HP,
        pet_hp: int = CLASSIC_HP,
        use_llm: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        if mode == "classic":
            bullets, player_hp, pet_hp = CLASSIC_BULLETS, CLASSIC_HP, CLASSIC_HP
        if not BULLET_RANGE[0] <= bullets <= BULLET_RANGE[1]:
            raise GameError(f"子弹数量要在 {BULLET_RANGE[0]}~{BULLET_RANGE[1]} 之间")
        if not all(HP_RANGE[0] <= h <= HP_RANGE[1] for h in (player_hp, pet_hp)):
            raise GameError(f"生命值要在 {HP_RANGE[0]}~{HP_RANGE[1]} 之间")
        self.mode = mode
        self.bullets = bullets
        self.use_llm = use_llm
        self.rng = rng or random.Random()
        self.max_hp: dict[Side, int] = {"player": player_hp, "pet": pet_hp}
        self.hp: dict[Side, int] = dict(self.max_hp)
        self.items: dict[Side, list[str]] = {"player": [], "pet": []}
        # Rounds still in the gun, the next one first: 1 = live, 0 = blank. Never leaves this object whole.
        self.chamber: list[int] = []
        self.round = 0
        # The player to move armed a 震爆弹: their next shot does 2 if it is live.
        self.boost = False
        # What each side saw of the current round through a 放大镜 (None = hasn't looked).
        self.known: dict[Side, int | None] = {"player": None, "pet": None}
        self.winner: Side | None = None
        self.log: list[dict[str, Any]] = []
        self._seq = 0
        self.first: Side = "player"
        self.turn: Side = "player"
        self._reload()

    @property
    def result(self) -> str | None:
        return self.winner

    # --- moves -----------------------------------------------------------------------------

    def shoot(self, side: Side, target: Literal["self", "opponent"]) -> dict[str, Any]:
        self._check_turn(side)
        if target not in ("self", "opponent"):
            raise GameError("只能朝自己或对方开枪")
        live = self.chamber.pop(0)
        victim = side if target == "self" else other(side)
        damage = (2 if self.boost else 1) if live else 0
        self.boost = False
        self.known = {"player": None, "pet": None}
        self.hp[victim] = max(0, self.hp[victim] - damage)
        entry = self._note("shoot", side, target=target, live=bool(live), damage=damage)
        # Shooting yourself with a blank keeps the turn; anything else passes it.
        if not (target == "self" and not live):
            self.turn = other(side)
        self._settle()
        return entry

    def use(self, side: Side, item: str, guess: str | None = None) -> dict[str, Any]:
        self._check_turn(side)
        if item not in self.items[side]:
            raise GameError(f"没有这个道具：{ITEM_NAMES.get(item, '未知道具')}")
        data: dict[str, Any] = {}
        if item == "juice":
            if self.hp[side] >= self.max_hp[side]:
                raise GameError("生命值已满，不能喝果汁")
            self.hp[side] += 1
        elif item == "dice":
            if guess not in ("big", "small"):
                raise GameError("用骰子要先猜大还是小")
            roll = self.rng.randint(1, 6)
            ok = (roll >= 4) == (guess == "big")
            self.hp[side] = min(self.max_hp[side], self.hp[side] + 1) if ok else max(0, self.hp[side] - 1)
            data = {"guess": guess, "roll": roll, "ok": ok}
        elif item == "flash":
            if self.boost:
                raise GameError("震爆弹已经生效了，开枪之后才能再用")
            self.boost = True
        elif item == "lens":
            # The result is private: it goes to `known`, not into the public log.
            self.known[side] = self.chamber[0]
        elif item == "driver":
            data = {"live": bool(self.chamber.pop(0))}
            self.known = {"player": None, "pet": None}
        self.items[side].remove(item)
        entry = self._note("item", side, item=item, **data)
        self._settle()
        return entry

    def act(self, side: Side, move: dict[str, Any]) -> dict[str, Any]:
        """A move as the model writes it: {"action": "shoot", "target"} or {"action": "item", "item", "guess"}."""
        action = move.get("action")
        if action == "shoot":
            return self.shoot(side, move.get("target"))  # type: ignore[arg-type]
        if action == "item":
            return self.use(side, str(move.get("item")), move.get("guess"))
        raise GameError("看不懂这一步")

    # --- views -----------------------------------------------------------------------------

    def view(self) -> dict[str, Any]:
        """What the player may see. No bullet order, none of the pet's items, not what the pet peeked at."""
        return {
            "mode": self.mode,
            "bullets": self.bullets,
            "round": self.round,
            "first": self.first,
            "turn": self.turn,
            "winner": self.winner,
            "hp": dict(self.hp),
            "max_hp": dict(self.max_hp),
            "left": len(self.chamber),
            "live": sum(self.chamber),
            "blank": len(self.chamber) - sum(self.chamber),
            "items": list(self.items["player"]),
            "pet_items": len(self.items["pet"]),
            "peek": self.known["player"],
            "boost": self.boost,
            "use_llm": self.use_llm,
            "seq": self._seq,
            "log": list(self.log),
        }

    def situation(self) -> str:
        """The game as the pet sees it, for its prompt. None of the player's items, not what the player peeked at."""
        live = sum(self.chamber)
        mine = "、".join(f"{i}（{ITEM_NAMES[i]}）" for i in self.items["pet"]) or "没有"
        lines = [
            f"第 {self.round} 轮。你的生命 {self.hp['pet']}/{self.max_hp['pet']}，主人的生命 {self.hp['player']}/{self.max_hp['player']}。",
            f"枪里还剩 {len(self.chamber)} 发：真弹 {live} 发，假弹 {len(self.chamber) - live} 发。",
            f"你的道具：{mine}。主人还有 {len(self.items['player'])} 个道具（你看不到是什么）。",
        ]
        if self.known["pet"] is not None:
            lines.append(f"你用放大镜看过：当前这一发是{'真弹' if self.known['pet'] else '假弹'}。")
        if self.boost:
            lines.append("你的震爆弹已经生效：下一枪如果是真弹，伤害是 2。")
        recent = [t for t in (self._text(e) for e in self.log[-10:]) if t]
        if recent:
            lines.append("最近发生的事：\n" + "\n".join(f"- {t}" for t in recent))
        lines.append("现在轮到你，做一步。")
        return "\n".join(lines)

    def summary(self) -> str:
        return f"恶魔轮盘赌打了 {self.round} 轮，最后你的生命 {self.hp['pet']}，主人的生命 {self.hp['player']}"

    @staticmethod
    def _text(e: dict[str, Any]) -> str:
        who = WHO[e["side"]]
        if e["kind"] == "shoot":
            target = "自己" if e["target"] == "self" else ("你" if e["side"] == "player" else "主人")
            hit = f"真弹，扣 {e['damage']} 点生命" if e["live"] else "假弹"
            return f"{who}朝{target}开了一枪：{hit}。"
        if e["kind"] == "item":
            name = ITEM_NAMES[e["item"]]
            if e["item"] == "dice":
                return f"{who}用了{name}：猜{'大' if e['guess'] == 'big' else '小'}，掷出 {e['roll']}，{'猜对回 1 点' if e['ok'] else '猜错扣 1 点'}。"
            if e["item"] == "driver":
                return f"{who}用了{name}：卸掉一发{'真弹' if e['live'] else '假弹'}。"
            return f"{who}用了{name}。"
        if e["kind"] == "load":
            return f"第 {e['round']} 轮开始：真弹 {e['live']} 发，假弹 {e['blank']} 发；道具已重发，{who}先手。"
        return ""

    # --- internals -------------------------------------------------------------------------

    def _check_turn(self, side: Side) -> None:
        if self.winner is not None:
            raise GameError("这一局已经结束了")
        if self.turn != side:
            raise GameError("还没轮到你" if side == "player" else "还没轮到对方")

    def _note(self, kind: str, side: Side, **data: Any) -> dict[str, Any]:
        self._seq += 1
        entry = {"seq": self._seq, "kind": kind, "side": side, **data}
        self.log.append(entry)
        del self.log[:-MAX_LOG]
        return entry

    def _settle(self) -> None:
        for side in ("player", "pet"):
            if self.hp[side] <= 0:
                self.winner = other(side)
                self._note("win", self.winner)
                return
        if not self.chamber:
            self._reload()

    def _reload(self) -> None:
        """A new round: the wheel picks who goes first (1/2 each), old items are discarded, three new ones each."""
        live = self.rng.randint(1, self.bullets - 1)
        self.chamber = [1] * live + [0] * (self.bullets - live)
        self.rng.shuffle(self.chamber)
        self.round += 1
        self.boost = False
        self.known = {"player": None, "pet": None}
        self.first = self.rng.choice(("player", "pet"))
        self.turn = self.first
        for side in ("player", "pet"):
            self.items[side] = [self.rng.choice(ITEMS) for _ in range(DEAL)]
        # Which items were dealt stays with their owner; `side` is who goes first this round.
        self._note("load", self.first, live=live, blank=self.bullets - live, round=self.round)


def auto_move(game: Roulette, rng: random.Random) -> dict[str, Any]:
    """Built-in strategy for the pet (LLM off, failed, or it asked for an illegal move). Always legal."""
    items = game.items["pet"]
    left = len(game.chamber)
    known = game.known["pet"]
    p = float(known) if known is not None else sum(game.chamber) / left
    if "juice" in items and game.hp["pet"] < game.max_hp["pet"]:
        return {"action": "item", "item": "juice"}
    if "lens" in items and known is None and 0 < p < 1:
        return {"action": "item", "item": "lens"}
    if p >= 0.75 and "flash" in items and not game.boost:
        return {"action": "item", "item": "flash"}
    if 0.35 < p < 0.65 and "driver" in items and left > 1:
        return {"action": "item", "item": "driver"}
    if p == 0:
        return {"action": "shoot", "target": "self"}
    if p > 0.5 or (p == 0.5 and rng.random() < 0.5):
        return {"action": "shoot", "target": "opponent"}
    return {"action": "shoot", "target": "self"}
