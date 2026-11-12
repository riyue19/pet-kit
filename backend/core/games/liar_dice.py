"""吹牛骰: five dice each under a cup, bids on how many of a face are on the whole table.

Hidden: the other side's dice until someone calls. Public: how many dice each side has left, every bid.
A bid is "at least `count` dice show `face`" over both cups. The next player raises (a larger count, or the
same count with a higher face) or calls. On a call the dice are shown: if the table holds at least `count`
of `face` the bidder was right and the caller loses a die, otherwise the bidder loses one. Whoever loses a
die bids first in the next round; the first to lose all five loses the match.
"""

import random
from typing import Any

from core.games.common import HEADER, JSON_RULE, SAY_RULE, GameError, Side, as_int, other

START_DICE = 5

RULES = f"""{HEADER}
游戏是「吹牛骰」。双方各有若干颗骰子扣在杯子下，只能看到自己的。轮流报数：“我认为场上一共至少有 n 个 x 点”（场上 = 双方所有骰子，x 是 1~6，没有万能点）。轮到你时二选一：
- 加码：报一个更大的数（n 更大，x 随意；或者 n 不变但 x 更大）；
- 喊开：认为对方上一次报数是吹牛。开盅后数出场上 x 点的总数：总数达到或超过 n，报数的人赢，喊开的人掉一颗骰子；总数不够，报数的人掉一颗骰子。
掉骰子的人下一轮先报数，先掉光 {START_DICE} 颗的人输。
策略提示：先数自己杯里每个点数有几颗；对方每颗骰子是某个点数的概率是 1/6，所以对方 k 颗骰子里平均有 k/6 颗是你想要的点数。报数明显超过“你自己有的 + 对方平均有的 + 1”时就该喊开；自己报数可以略微吹一点，但别吹到一开就输。
{JSON_RULE}
加码：{{"action": "bid", "count": 数量, "face": 点数, "say": "…"}}
喊开：{{"action": "call", "say": "…"}}
{SAY_RULE}可以虚张声势，也可以装没底气；不要说出你杯子里真实的骰子。"""


def bid_text(b: dict[str, Any]) -> str:
    return f"{b['count']} 个 {b['face']} 点"


class LiarDice:
    key = "dice"

    def __init__(self, *, use_llm: bool = True, rng: random.Random | None = None) -> None:
        self.use_llm = use_llm
        self.rng = rng or random.Random()
        self.dice: dict[Side, int] = {"player": START_DICE, "pet": START_DICE}
        self.hands: dict[Side, list[int]] = {"player": [], "pet": []}
        self.round = 1
        # The wheel: who bids first in round 1.
        self.first: Side = self.rng.choice(("player", "pet"))
        self.turn: Side = self.first
        # Bids of the current round, oldest first: {side, count, face, say?}.
        self.bids: list[dict[str, Any]] = []
        # bid: bidding; reveal: the cups are up (waiting for next_round); over: the match ended.
        self.phase = "bid"
        self.reveal: dict[str, Any] | None = None
        self.winner: Side | None = None
        # One line per finished round: {round, bid, caller, total, loser}.
        self.history: list[dict[str, Any]] = []
        self._roll()

    @property
    def result(self) -> str | None:
        return self.winner

    @property
    def total(self) -> int:
        return self.dice["player"] + self.dice["pet"]

    def _roll(self) -> None:
        for side in ("player", "pet"):
            self.hands[side] = sorted(self.rng.randint(1, 6) for _ in range(self.dice[side]))

    def _check_turn(self, side: Side) -> None:
        if self.phase == "over":
            raise GameError("这一局已经结束了")
        if self.phase == "reveal":
            raise GameError("先开始下一轮")
        if self.turn != side:
            raise GameError("还没轮到你" if side == "player" else "还没轮到对方")

    def legal_bid(self, count: int, face: int) -> bool:
        if not (1 <= face <= 6 and 1 <= count <= self.total):
            return False
        if not self.bids:
            return True
        last = self.bids[-1]
        return count > last["count"] or (count == last["count"] and face > last["face"])

    def can_raise(self) -> bool:
        if not self.bids:
            return True
        last = self.bids[-1]
        return last["count"] < self.total or last["face"] < 6

    def bid(self, side: Side, count: int, face: int) -> dict[str, Any]:
        self._check_turn(side)
        if not self.legal_bid(count, face):
            if not (1 <= face <= 6):
                raise GameError("点数只能是 1~6")
            if not (1 <= count <= self.total):
                raise GameError(f"数量要在 1~{self.total} 之间（场上一共 {self.total} 颗骰子）")
            raise GameError(f"要比上一次报的 {bid_text(self.bids[-1])} 更大：数量更多，或者数量相同但点数更大")
        entry = {"side": side, "count": count, "face": face}
        self.bids.append(entry)
        self.turn = other(side)
        return entry

    def call(self, side: Side) -> dict[str, Any]:
        self._check_turn(side)
        if not self.bids:
            raise GameError("还没有人报数，不能喊开")
        last = self.bids[-1]
        total = sum(1 for d in self.hands["player"] + self.hands["pet"] if d == last["face"])
        bidder: Side = last["side"]
        loser: Side = side if total >= last["count"] else bidder
        self.dice[loser] -= 1
        self.reveal = {
            "hands": {"player": list(self.hands["player"]), "pet": list(self.hands["pet"])},
            "bid": dict(last),
            "caller": side,
            "total": total,
            "loser": loser,
        }
        self.history.append({"round": self.round, "bid": dict(last), "caller": side, "total": total, "loser": loser})
        if self.dice[loser] == 0:
            self.phase, self.winner = "over", other(loser)
        else:
            self.phase = "reveal"
        return self.reveal

    def next_round(self) -> None:
        if self.phase != "reveal" or self.reveal is None:
            raise GameError("现在不能开始下一轮")
        self.turn = self.reveal["loser"]
        self.round += 1
        self.bids, self.reveal, self.phase = [], None, "bid"
        self._roll()

    def act(self, side: Side, move: dict[str, Any]) -> dict[str, Any]:
        """A move as the model writes it."""
        if move.get("action") == "call":
            return self.call(side)
        if move.get("action") == "bid":
            count, face = as_int(move.get("count")), as_int(move.get("face"))
            if count is None or face is None:
                raise GameError("报数要填数量和点数")
            return self.bid(side, count, face)
        raise GameError("看不懂这一步")

    def view(self) -> dict[str, Any]:
        """What the player may see: the pet's dice only once the cups are up."""
        return {
            "round": self.round,
            "phase": self.phase,
            "first": self.first,
            "turn": self.turn,
            "dice": dict(self.dice),
            "start_dice": START_DICE,
            "hand": list(self.hands["player"]),
            "bids": [dict(b) for b in self.bids],
            "can_call": bool(self.bids),
            "can_raise": self.can_raise(),
            "total": self.total if self.phase == "bid" else len(self.hands["player"]) + len(self.hands["pet"]),
            "reveal": self.reveal,
            "winner": self.winner,
            "history": list(self.history),
            "use_llm": self.use_llm,
        }

    def situation(self) -> str:
        """The round as the pet sees it: its own dice, never the player's."""
        mine = "、".join(str(d) for d in self.hands["pet"])
        lines = [
            f"第 {self.round} 轮。你有 {self.dice['pet']} 颗骰子，主人有 {self.dice['player']} 颗，场上一共 {self.total} 颗。",
            f"你杯子里的骰子：{mine}。主人的你看不到。",
        ]
        if self.bids:
            said = "\n".join(f"- {'主人' if b['side'] == 'player' else '你'}：至少 {bid_text(b)}" for b in self.bids)
            lines.append(f"这一轮的报数：\n{said}")
            lines.append(f"现在轮到你：加码（必须比 {bid_text(self.bids[-1])} 更大，数量最多 {self.total}）或者喊开。")
            if not self.can_raise():
                lines.append("已经报到最大了，你只能喊开。")
        else:
            lines.append(f"这一轮由你先报数（数量 1~{self.total}，点数 1~6）。")
        return "\n".join(lines)

    def summary(self) -> str:
        return f"吹牛骰玩了 {self.round} 轮，最后你剩 {self.dice['pet']} 颗骰子，主人剩 {self.dice['player']} 颗"


def auto_move(game: LiarDice, rng: random.Random) -> dict[str, Any]:
    """Built-in strategy: count its own dice, expect a sixth of the player's to match. Always legal."""
    mine = game.hands["pet"]
    theirs = game.dice["player"]

    def expected(face: int) -> float:
        return mine.count(face) + theirs / 6

    best = max(range(1, 7), key=lambda f: (mine.count(f), f))
    if not game.bids:
        return {"action": "bid", "count": max(1, round(expected(best))), "face": best}
    last = game.bids[-1]
    if not game.can_raise() or last["count"] > expected(last["face"]) + 0.6 + rng.random():
        return {"action": "call"}
    count = last["count"] if best > last["face"] else last["count"] + 1
    if count > game.total or count > expected(best) + 1.5:
        # Raising on its best face would be a worse lie than the bid on the table probably is.
        cheap = next(((last["count"], f) for f in range(last["face"] + 1, 7) if mine.count(f)), None)
        if cheap and cheap[0] <= expected(cheap[1]) + 1.5:
            return {"action": "bid", "count": cheap[0], "face": cheap[1]}
        return {"action": "call"}
    return {"action": "bid", "count": count, "face": best}
