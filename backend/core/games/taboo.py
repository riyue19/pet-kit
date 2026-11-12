"""禁忌词: one side describes a word without saying it or its forbidden words; the other guesses.

Cooperative: guessed within ROUNDS exchanges = both win. Hidden: the word and its forbidden words from the
guesser. When the pet guesses, its prompt holds only the clues (never the word); when the pet describes,
the program checks its clue for the forbidden words before the player sees it.
"""

import random
import re
from typing import Any

from core.games.common import HEADER, JSON_RULE, GameError, Side

ROUNDS = 10
MAX_TEXT = 120

# (word, forbidden words). Everyday words; the forbidden ones are the obvious ways to describe it.
WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("火锅", ("涮", "辣", "锅", "四川", "毛肚")),
    ("奶茶", ("珍珠", "牛奶", "茶", "甜", "吸管")),
    ("月亮", ("晚上", "圆", "太阳", "嫦娥", "天上")),
    ("雨伞", ("下雨", "撑", "遮", "太阳", "雨")),
    ("手机", ("电话", "屏幕", "打", "充电", "微信")),
    ("熊猫", ("黑白", "竹子", "国宝", "四川", "胖")),
    ("冰箱", ("冷", "食物", "保鲜", "厨房", "冻")),
    ("地铁", ("站", "轨道", "车", "地下", "刷卡")),
    ("闹钟", ("起床", "早上", "响", "时间", "叫")),
    ("牙刷", ("牙膏", "刷", "牙", "早晚", "口腔")),
    ("西瓜", ("夏天", "红", "绿", "籽", "水果")),
    ("钢琴", ("弹", "黑白", "琴键", "音乐", "乐器")),
    ("医生", ("医院", "看病", "护士", "病人", "白大褂")),
    ("彩虹", ("七", "颜色", "雨", "天空", "桥")),
    ("电梯", ("楼", "上下", "按钮", "楼梯", "层")),
    ("足球", ("踢", "球", "脚", "世界杯", "射门")),
    ("眼镜", ("近视", "戴", "看", "镜片", "眼睛")),
    ("饺子", ("包", "馅", "过年", "煮", "面皮")),
    ("图书馆", ("书", "借", "安静", "阅读", "学习")),
    ("外卖", ("送", "骑手", "点", "吃", "订单")),
    ("口罩", ("戴", "脸", "病毒", "鼻子", "嘴")),
    ("沙发", ("坐", "客厅", "软", "躺", "家具")),
    ("蜡烛", ("火", "点", "生日", "光", "停电")),
    ("蜜蜂", ("蜂蜜", "花", "蜇", "飞", "嗡嗡")),
    ("台风", ("风", "雨", "夏天", "海", "登陆")),
    ("键盘", ("打字", "按", "电脑", "鼠标", "字母")),
    ("耳机", ("听", "音乐", "耳朵", "蓝牙", "戴")),
    ("圣诞节", ("老人", "礼物", "十二月", "树", "雪")),
    ("长城", ("北京", "墙", "万里", "古代", "爬")),
    ("咖啡", ("苦", "提神", "豆", "喝", "拿铁")),
    ("镜子", ("照", "反射", "脸", "玻璃", "看")),
    ("枕头", ("睡觉", "头", "床", "软", "垫")),
    ("快递", ("包裹", "送", "收", "网购", "驿站")),
    ("烟花", ("过年", "放", "爆炸", "天空", "火")),
    ("企鹅", ("南极", "冷", "鸟", "黑白", "冰")),
    ("自行车", ("骑", "轮子", "两个", "踩", "车")),
    ("蛋糕", ("生日", "奶油", "甜", "蜡烛", "切")),
    ("游泳", ("水", "泳池", "游", "夏天", "泳衣")),
    ("作业", ("老师", "写", "学校", "学生", "交")),
    ("红绿灯", ("路口", "红", "绿", "停", "交通")),
    ("猫", ("喵", "老鼠", "宠物", "抓", "铲屎")),
    ("影子", ("光", "黑", "跟着", "太阳", "身后")),
)

DESCRIBE_RULES = f"""{HEADER}
游戏是「禁忌词」猜词，你和主人是队友：你来描述一个词，主人来猜，{ROUNDS} 轮之内猜中就一起赢。
规则：描述里绝对不能出现目标词，也不能出现任何一个禁忌词（包含这些字眼的词也不行）；不能用拼音、英文翻译、谐音、拆字、“第一个字是…”这类办法绕过。
策略：每一轮换一个新的角度（用途、场景、感觉、外形、和它有关的人或事），不要重复上一轮的话；主人猜错时，针对他猜的词指出差别，把他往正确方向引。
{JSON_RULE}
{{"say": "你这一轮的描述"}}
描述不超过 40 个字，口语化，符合你的人设；只用文字，不要用 emoji、颜文字和括号动作描写。"""
GUESS_RULES = f"""{HEADER}
游戏是「禁忌词」猜词，你和主人是队友：主人在描述一个词（主人不能说出这个词，也不能说几个禁忌词），你来猜，{ROUNDS} 轮之内猜中就一起赢。你不知道答案，只能根据主人的描述推理。
策略：把主人每一轮的描述合在一起想；已经猜错的词不要再猜；答案通常是常见的名词或活动。
{JSON_RULE}
{{"guess": "你猜的词（只写这个词本身，不要加别的字）", "say": "猜的时候说的一句话"}}
say 不超过 30 个字，口语化，符合你的人设；只用文字，不要用 emoji、颜文字和括号动作描写。"""
FALLBACK_CLUES = ("这个词有 {n} 个字。", "我一时想不出不犯规的说法，你再大胆猜一个。", "换个思路想想，是很常见的东西。")

_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)


def norm(text: str) -> str:
    return _NOISE.sub("", text).lower()


class Taboo:
    key = "taboo"
    use_llm = True

    def __init__(self, *, describer: Side, rng: random.Random | None = None, entry: tuple[str, tuple[str, ...]] | None = None) -> None:
        rng = rng or random.Random()
        self.describer: Side = describer
        self.word, taboo = entry or rng.choice(WORDS)
        self.taboo = list(taboo)
        # The exchange being played, 1..ROUNDS.
        self.round = 1
        # {side, kind: "clue" | "guess", text, ok?}
        self.turns: list[dict[str, Any]] = []
        # "player" = guessed in time (a shared win, recorded as the player's); "draw" = not guessed.
        self.result: str | None = None

    def violation(self, text: str) -> str | None:
        """The forbidden word a clue contains, if any."""
        flat = norm(text)
        return next((w for w in (self.word, *self.taboo) if norm(w) in flat), None)

    def correct(self, text: str) -> bool:
        return norm(self.word) in norm(text)

    def check_open(self) -> None:
        if self.result is not None:
            raise GameError("这一局已经结束了")

    def clue(self, side: Side, text: str) -> None:
        self.turns.append({"side": side, "kind": "clue", "text": " ".join(text.split())[:MAX_TEXT]})

    def guess(self, side: Side, text: str) -> bool:
        """Records a guess and moves the game on. True when it was right."""
        ok = self.correct(text)
        self.turns.append({"side": side, "kind": "guess", "text": " ".join(text.split())[:MAX_TEXT], "ok": ok})
        if ok:
            self.result = "player"
        elif self.round >= ROUNDS:
            self.result = "draw"
        else:
            self.round += 1
        return ok

    def fallback_clue(self) -> str:
        return FALLBACK_CLUES[min(self.round - 1, len(FALLBACK_CLUES) - 1)].format(n=len(self.word))

    def view(self) -> dict[str, Any]:
        """What the player may see: the word and its forbidden words only when describing, or once it is over."""
        show = self.describer == "player" or self.result is not None
        return {
            "describer": self.describer,
            "round": self.round,
            "rounds": ROUNDS,
            "turns": [dict(t) for t in self.turns],
            "word": self.word if show else None,
            "taboo": list(self.taboo) if show else None,
            "word_chars": len(self.word),
            "result": self.result,
            "use_llm": True,
        }

    def _talk(self) -> str:
        lines = []
        for t in self.turns:
            who = "主人" if t["side"] == "player" else "你"
            what = "描述" if t["kind"] == "clue" else ("猜" if not t.get("ok") else "猜中")
            wrong = "（猜错了）" if t["kind"] == "guess" and not t.get("ok") else ""
            lines.append(f"- {who}{what}：{t['text']}{wrong}")
        return "\n".join(lines)

    def describe_situation(self) -> str:
        """For the pet as describer: holds the word (the pet's own secret)."""
        lines = [
            f"目标词：{self.word}",
            f"禁忌词（连同目标词本身都不能说）：{'、'.join(self.taboo)}",
            f"现在是第 {self.round} 轮（共 {ROUNDS} 轮）。",
        ]
        if self.turns:
            lines.append(f"到目前为止：\n{self._talk()}")
        lines.append("给出这一轮的描述。")
        return "\n".join(lines)

    def guess_situation(self) -> str:
        """For the pet as guesser: only what was said. Never the word or the forbidden words."""
        return f"现在是第 {self.round} 轮（共 {ROUNDS} 轮）。答案是 {len(self.word)} 个字。\n到目前为止：\n{self._talk()}\n你来猜。"

    def summary(self) -> str:
        return f"禁忌词猜词，要猜的词是「{self.word}」，{'猜中了' if self.result == 'player' else '没猜出来'}"
