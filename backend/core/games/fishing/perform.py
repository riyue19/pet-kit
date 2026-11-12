"""What the LLM is asked for in fishing (one spoken line, codex notes, bottle letters) and the checks on its
replies. The program decides every fact; a reply that changes one, or breaks the format, is thrown away and a
stock line is used instead."""

import re
from typing import Any

from core.games.common import clean_say

MAX_LINE_CHARS = 60
LLM_TIMEOUT_S = 6.0
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CONTACT = re.compile(r"https?|www\.|@|\d{7,}|[\w.-]+\.(?:com|cn|net|org)\b", re.IGNORECASE)

LINE_RULES = """【钓鱼演出规则】
你正在以当前人设演出一次钓鱼事件。事件事实由程序决定，你只负责用符合人设的口吻说一句话并选择表情/动作。

必须遵守：
1. 事实不可改：鱼名、尺寸、稀有度、是否首次/巨物/破纪录，只能照 payload 说，不得编造其他鱼名、数字、价格或奖励。
2. 篇幅：text 为 1 句，最多 2 句，总长不超过 40 个汉字；不要列表、不要 emoji、不要括号动作描写。
3. 结合 context（天气/时间/地点/饵）和 persona_traits，但每次只挑一个点，不要全堆上。
4. 不要重复 recent_lines 里已经说过的句子或相近的句式。
5. rarity 越高，情绪越强；common 不要过度兴奋。first_time=true 时可以提一下"第一次钓到"。
6. expression 和 motion 只能从 allowed 里选；拿不准或 allowed 为空就写 null。
7. tool_calls 必须为 []。你不能执行任何动作，也不能提议卖鱼、放生或花钱。
8. 只输出一个 JSON 对象，不要任何多余文字。

输出格式：
{"text":"...","expression":null,"motion":null,"tool_calls":[]}"""

STORY_RULES = """为钓鱼图鉴写一条"钓鱼笔记"。
要求：
1. 40–70 个汉字，平静克制的笔记口吻，像钓鱼人随手写的观察。
2. 写一个具体的小习性或小细节，不要百科式介绍，不要夸张。
3. 不要出现具体数字、价格、"传说中"之类的套话，不要 emoji。
只输出一个 JSON 对象：{"story":"正文"}，不要任何多余文字。"""

BOTTLE_RULES = """写几封"漂流瓶里的信"，每封由一个虚构的人写给陌生的拾取者。
要求：
1. 每封 30–90 个汉字，口吻各不相同（可以是出海的人、旅行者、在海边住的老人、写给未来的自己等）。
2. 内容轻松、温和或略带诗意；主题多样（日常小事、天气、一个小愿望、一个小笑话、一句祝福）。
3. 严禁：真实人物、品牌、网址、联系方式、地址、让读信人去做某事或转发、悲伤到沉重的内容、任何个人隐私。
4. 发信人署名用虚构的昵称（2–4 个字），不要用真实姓名。
只输出一个 JSON 对象：{"letters":[{"sender":"...","body":"..."}]}，不要任何多余文字。"""


def validate_line(move: Any, *, allowed_expressions: list[str], allowed_motions: list[str], numbers: set[float]) -> dict[str, Any] | None:
    """The checked {text, expression, motion}, or None when anything is off. `numbers`: the only numbers
    the text may contain (the fish's size, the counts of a summary)."""
    if not isinstance(move, dict):
        return None
    if move.get("tool_calls") not in (None, []):
        return None
    raw = move.get("text")
    if not isinstance(raw, str) or len(raw.strip()) > MAX_LINE_CHARS:
        return None
    text = clean_say(raw, MAX_LINE_CHARS)
    if not text:
        return None
    for found in _NUMBER.findall(text):
        if not any(abs(float(found) - n) < 1e-9 for n in numbers):
            return None
    out: dict[str, Any] = {"text": text}
    for key, allowed in (("expression", allowed_expressions), ("motion", allowed_motions)):
        value = move.get(key)
        if value in (None, ""):
            out[key] = None
        elif isinstance(value, str) and value in allowed:
            out[key] = value
        else:
            return None
    return out


def validate_story(move: Any) -> str | None:
    story = move.get("story") if isinstance(move, dict) else None
    if not isinstance(story, str):
        return None
    story = clean_say(story, 200)
    return story if 20 <= len(story) <= 120 and not _NUMBER.search(story) and not _CONTACT.search(story) else None


def valid_letter(sender: Any, body: Any) -> tuple[str, str] | None:
    if not isinstance(sender, str) or not isinstance(body, str):
        return None
    sender, body = clean_say(sender, 20), " ".join(body.split())
    if not 1 <= len(sender) <= 8 or not 20 <= len(body) <= 120:
        return None
    if _CONTACT.search(body) or _CONTACT.search(sender):
        return None
    return sender, body


def validate_letters(move: Any) -> list[tuple[str, str]]:
    """The letters of a batch that pass the checks (bad ones are dropped one by one)."""
    letters = move.get("letters") if isinstance(move, dict) else move
    if not isinstance(letters, list):
        return []
    out = []
    for item in letters:
        if isinstance(item, dict):
            ok = valid_letter(item.get("sender"), item.get("body"))
            if ok and ok not in out:
                out.append(ok)
    return out
