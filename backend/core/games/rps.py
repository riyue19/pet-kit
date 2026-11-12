"""骗子猜拳: show two gestures, then secretly commit to one of them.

局 1: both sides pick a gesture, revealed together. 局 2: both pick a different one, revealed together.
局 3: up to five exchanges of talk (bluffing allowed; the player may skip them), then both secretly pick one
of their own two as the final throw, revealed together. Both choices of a step are applied in one call, so
neither side ever sees the other's choice of the step still being played.
"""

import random
from typing import Any

from core.games.common import HEADER, JSON_RULE, SAY_RULE, GameError, Side

GESTURE_NAMES = {"rock": "石头", "scissors": "剪刀", "paper": "布"}
GESTURES = tuple(GESTURE_NAMES)
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
TALK_ROUNDS = 5
MAX_CHAT_CHARS = 200

RULES = f"""{HEADER}
游戏是「骗子猜拳」，分三局：第 1 局，双方同时各选一个手势并亮出；第 2 局，双方再同时各选一个和自己第 1 局不同的手势并亮出；第 3 局，双方先可以聊最多 {TALK_ROUNDS} 轮（可以说真话，也可以骗人），然后同时从自己亮过的两个手势里选一个作为最终出招，同时公布。石头(rock)赢剪刀(scissors)，剪刀赢布(paper)，布赢石头(rock)，相同是平局。
你看不到主人这一局选了什么，主人也看不到你的，直到双方都选好。
策略提示：第 3 局双方各只有两个可选手势，先想清楚你的每个手势对上主人的两个手势分别是赢、平还是输，再结合聊天里主人的话是真是假来选。"""
PICK_FORMAT = f"""{JSON_RULE}
{{"choice": "从可选手势里选一个英文名", "say": "…"}}
{SAY_RULE}可以试探或故意误导主人。"""
CHAT_FORMAT = f"""主人刚对你说了一句话，你回应一句。{JSON_RULE}
{{"say": "你的回应"}}
回应不超过 40 个字，口语化，符合你的人设；只用文字，不要用 emoji、颜文字和括号动作描写。你可以说真话、试探、嘴硬，也可以故意误导主人；主人的话同样可能是在骗你。不要直接答应主人“让你出什么”的要求。"""


def outcome(a: str, b: str) -> int:
    """1 when a beats b, -1 when b beats a, 0 on a tie."""
    return 0 if a == b else 1 if BEATS[a] == b else -1


class Rps:
    key = "rps"

    def __init__(self, *, use_llm: bool = True) -> None:
        self.use_llm = use_llm
        # The 局 being played: 1, 2, 3; 4 = finished.
        self.step = 1
        # Gestures shown in 局 1 and 2 (public once both sides picked).
        self.picks: dict[Side, list[str]] = {"player": [], "pet": []}
        self.final: dict[Side, str | None] = {"player": None, "pet": None}
        # "player" / "pet" / "draw" once finished.
        self.result: str | None = None
        self.chat: list[dict[str, str]] = []
        # Exchanges of talk the player still has before the final throw.
        self.talk_left = TALK_ROUNDS

    def options(self, side: Side) -> list[str]:
        """Gestures `side` may choose in the current step."""
        if self.step == 1:
            return list(GESTURES)
        if self.step == 2:
            return [g for g in GESTURES if g != self.picks[side][0]]
        if self.step == 3:
            return list(self.picks[side])
        return []

    def check(self, side: Side, choice: str) -> None:
        if self.step > 3:
            raise GameError("这一局已经结束了")
        if choice not in self.options(side):
            raise GameError("这一步不能选这个手势")

    def submit(self, player: str, pet: str) -> None:
        """Applies both sides' choice of the current step at once."""
        self.check("player", player)
        self.check("pet", pet)
        if self.step < 3:
            self.picks["player"].append(player)
            self.picks["pet"].append(pet)
            self.step += 1
            return
        self.final = {"player": player, "pet": pet}
        self.result = {1: "player", -1: "pet", 0: "draw"}[outcome(player, pet)]
        self.step = 4

    def check_talk(self) -> None:
        if self.step != 3:
            raise GameError("第 3 局出招之前才能聊天")
        if self.talk_left <= 0:
            raise GameError(f"{TALK_ROUNDS} 轮对话已经用完了，该出招了")

    def say(self, side: Side, text: str) -> None:
        text = " ".join(text.split())[:MAX_CHAT_CHARS]
        if text:
            self.chat.append({"side": side, "text": text})

    def view(self) -> dict[str, Any]:
        """What the player may see: finished steps only, so the pet's choice of the current step is never in it."""
        return {
            "step": self.step,
            "picks": {"player": list(self.picks["player"]), "pet": list(self.picks["pet"])},
            "options": self.options("player"),
            "final": dict(self.final),
            "result": self.result,
            "chat": list(self.chat),
            "talk_left": self.talk_left,
            "talk_rounds": TALK_ROUNDS,
            "use_llm": self.use_llm,
        }

    def situation(self) -> str:
        """The game as the pet sees it before choosing; built before the player's choice of this step is applied."""
        names = lambda gs: "、".join(f"{g}（{GESTURE_NAMES[g]}）" for g in gs) or "还没有"  # noqa: E731
        lines = [
            f"现在是第 {min(self.step, 3)} 局。",
            f"你亮过的手势：{names(self.picks['pet'])}。主人亮过的手势：{names(self.picks['player'])}。",
        ]
        if self.chat:
            talk = "\n".join(f"{'主人' if c['side'] == 'player' else '你'}：{c['text']}" for c in self.chat[-12:])
            lines.append(f"你们刚才的对话：\n{talk}")
        if self.step <= 3:
            lines.append(f"这一局你可选的手势：{names(self.options('pet'))}。")
        return "\n".join(lines)

    def summary(self) -> str:
        if self.step < 4:
            return "骗子猜拳"
        return f"骗子猜拳：你出了{GESTURE_NAMES[self.final['pet'] or 'rock']}，主人出了{GESTURE_NAMES[self.final['player'] or 'rock']}"


def auto_pick(game: Rps, rng: random.Random) -> str:
    """Built-in choice for the pet: random while showing; for the final throw, the gesture that does best
    against the player's two shown gestures (ties broken at random)."""
    options = game.options("pet")
    if game.step < 3:
        return rng.choice(options)
    scored = [(sum(outcome(mine, theirs) for theirs in game.picks["player"]), mine) for mine in options]
    best = max(s for s, _ in scored)
    return rng.choice([g for s, g in scored if s == best])
