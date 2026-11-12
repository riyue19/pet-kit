import re
from typing import Literal

Side = Literal["player", "pet"]
MAX_SAY_CHARS = 60

# Emoji and pictographs (not CJK, not ordinary punctuation). Game lines are plain text.
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U0001f1e6-\U0001f1ff☀-➿⬀-⯿←-⇿‍️⃣〰〽㊗㊙]"
)

# Shared by every game's prompt. Keeping the model inside the game matters more than anything else here.
HEADER = (
    "【小游戏】你正在和主人玩一局小游戏。这只是游戏：按规则认真玩、尽力去赢，同时保持你的人设和说话方式。"
    "不要跳出游戏聊别的，不要复述规则，不要替主人做决定，也不要声称知道你看不到的信息。"
    "游戏里说的话不会被记住，所以不要提“记下来”之类的话。"
)
SAY_RULE = (
    "say 是你这一步对主人说的一句话：不超过 30 个字，口语化，符合你的人设；只用文字，"
    "不要用 emoji、颜文字和括号动作描写。不想说就写空字符串。"
)
JSON_RULE = "只输出一个 JSON 对象，不要输出任何其它内容（不要解释，不要代码块）。"


class GameError(Exception):
    """An illegal move; the message is shown to the player."""


def other(side: Side) -> Side:
    return "pet" if side == "player" else "player"


def clean_say(raw: object, limit: int = MAX_SAY_CHARS) -> str:
    """The pet's spoken line from a model reply: one line, no emoji, bounded."""
    if not isinstance(raw, str):
        return ""
    return " ".join(_EMOJI.sub("", raw).split())[:limit]


def as_int(raw: object) -> int | None:
    """A whole number as models write it (3, "3", 3.0); None otherwise."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw.lstrip("-").isdigit():
            return None
        return int(raw)
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return raw if isinstance(raw, int) else None
