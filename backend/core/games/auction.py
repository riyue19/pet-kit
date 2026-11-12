"""拍卖数字牌: three suits of A..K (1..13). One suit each as bidding cards, the third is the prizes.

Each round a prize card is turned up (worth its face value); both sides secretly play one card, shown
together; the higher card takes the prize, both played cards are gone. On a tie the prize stays on the table
and goes to whoever wins the next round. First to WIN_SCORE points wins; after 13 rounds the higher score wins.
Hidden: only the order of the prize deck and the card the other side is playing this round.
"""

import random
from typing import Any

from core.games.common import HEADER, JSON_RULE, SAY_RULE, GameError, Side, as_int

CARDS = tuple(range(1, 14))
WIN_SCORE = 45
FACES = {1: "A", 11: "J", 12: "Q", 13: "K"}

RULES = f"""{HEADER}
游戏是「拍卖数字牌」。你和主人各有一套 A~K 共 13 张出价牌（A=1，J=11，Q=12，K=13），另有一套 13 张奖励牌洗乱后一张张翻开，牌面多少就值多少分。每一轮：翻开一张奖励牌，双方同时暗出一张出价牌，同时亮出，谁的牌大谁拿走桌上的奖励分；打出去的牌双方都作废。出的牌一样大时没人得分，这张奖励牌留在桌上，和下一张一起归下一轮的赢家。先拿到 {WIN_SCORE} 分的人赢；13 轮打完还没人到 {WIN_SCORE} 分就比分数。
你看不到主人这一轮出了什么，主人也看不到你的。主人手里还剩哪些牌是公开信息。
策略提示：大牌要留给值钱的奖励；奖励分很低时可以丢小牌放掉；看主人剩下的牌，如果你能用刚好比主人最大牌还大的牌稳拿，就不用浪费更大的。
{JSON_RULE}
{{"card": 你要出的牌的点数（1~13 的整数，必须是你手里有的）, "say": "…"}}
{SAY_RULE}不要说出你这一轮出的是什么牌。"""


def face(card: int) -> str:
    return FACES.get(card, str(card))


class Auction:
    key = "auction"

    def __init__(self, *, use_llm: bool = True, rng: random.Random | None = None) -> None:
        self.use_llm = use_llm
        self.rng = rng or random.Random()
        self.hands: dict[Side, list[int]] = {"player": list(CARDS), "pet": list(CARDS)}
        # Prize cards not turned up yet (hidden from both sides).
        self.deck = list(CARDS)
        self.rng.shuffle(self.deck)
        # Prize cards on the table this round (more than one after a tie).
        self.pot: list[int] = [self.deck.pop()]
        self.scores: dict[Side, int] = {"player": 0, "pet": 0}
        self.round = 1
        # Finished rounds: {round, prizes, player, pet, winner: side | None}.
        self.history: list[dict[str, Any]] = []
        self.result: str | None = None

    def check(self, side: Side, card: int) -> None:
        if self.result is not None:
            raise GameError("这一局已经结束了")
        if card not in self.hands[side]:
            raise GameError("手里没有这张牌")

    def submit(self, player: int, pet: int) -> dict[str, Any]:
        """Applies both sides' card of this round at once."""
        self.check("player", player)
        self.check("pet", pet)
        self.hands["player"].remove(player)
        self.hands["pet"].remove(pet)
        winner: Side | None = "player" if player > pet else "pet" if pet > player else None
        entry = {"round": self.round, "prizes": list(self.pot), "player": player, "pet": pet, "winner": winner}
        self.history.append(entry)
        if winner is not None:
            self.scores[winner] += sum(self.pot)
            self.pot = []
        lead = max(self.scores.values())
        if lead >= WIN_SCORE or not self.deck:
            a, b = self.scores["player"], self.scores["pet"]
            self.result = "player" if a > b else "pet" if b > a else "draw"
        else:
            self.pot.append(self.deck.pop())
            self.round += 1
        return entry

    def view(self) -> dict[str, Any]:
        """What the player may see: the pet's remaining cards follow from the public history, the deck order never."""
        return {
            "round": self.round,
            "rounds": len(CARDS),
            "win_score": WIN_SCORE,
            "pot": list(self.pot),
            "hand": list(self.hands["player"]),
            "pet_hand": list(self.hands["pet"]),
            "scores": dict(self.scores),
            "left": len(self.deck),
            "history": list(self.history),
            "result": self.result,
            "use_llm": self.use_llm,
        }

    def situation(self) -> str:
        """The round as the pet sees it; built before the player's card of this round is applied."""
        cards = lambda cs: "、".join(f"{c}" for c in cs) or "没有"  # noqa: E731
        lines = [
            f"第 {self.round} 轮（共 {len(CARDS)} 轮）。你 {self.scores['pet']} 分，主人 {self.scores['player']} 分，先到 {WIN_SCORE} 分赢。",
            f"桌上的奖励：{cards(self.pot)}，共 {sum(self.pot)} 分。还没翻开的奖励牌有 {len(self.deck)} 张。",
            f"你手里的牌（点数）：{cards(self.hands['pet'])}。",
            f"主人手里还剩的牌（点数）：{cards(self.hands['player'])}。",
        ]
        if self.history:
            last = self.history[-1]
            who = {"player": "主人拿走", "pet": "你拿走", None: "打平，留在桌上"}[last["winner"]]
            lines.append(f"上一轮：奖励 {cards(last['prizes'])}，你出 {last['pet']}，主人出 {last['player']}，{who}。")
        lines.append("现在出一张牌。")
        return "\n".join(lines)

    def summary(self) -> str:
        return f"拍卖数字牌打了 {len(self.history)} 轮，你 {self.scores['pet']} 分，主人 {self.scores['player']} 分"


def model_card(move: dict[str, Any]) -> int | None:
    """The card in a model reply: 1..13 or A/J/Q/K."""
    raw = move.get("card")
    if isinstance(raw, str):
        named = {v: k for k, v in FACES.items()}.get(raw.strip().upper())
        if named:
            return named
    return as_int(raw)


def auto_bid(game: Auction, rng: random.Random) -> int:
    """Built-in strategy: spend about what the prize is worth, never more than needed to beat the player's best."""
    hand = game.hands["pet"]
    worth = min(13, sum(game.pot)) + rng.choice((-1, 0, 0, 1))
    card = min(hand, key=lambda c: (abs(c - worth), c))
    theirs = max(game.hands["player"])
    beats_all = [c for c in hand if c > theirs]
    if beats_all and card > min(beats_all):
        card = min(beats_all)
    return card
