"""config/status.yaml: the pet's status bar (console 状态 page).

A stat is a number the LLM may nudge ("+0.3", "-1"); the program does the arithmetic and the clamping.
Re-read on every turn, so edits apply to the next reply. Plain text the user wrote for the model:
credentials never belong in it (CLAUDE.md §4.1).
"""

import math
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
AFFECTION = "affection"
AFFECTION_COLOR = "#ff7eb6"
MAX_STATS = 12
MAX_TIERS = 20
MAX_REASON_CHARS = 20
LIMIT = 1_000_000


def fmt(v: float) -> str:
    """12.0 -> "12", 0.30 -> "0.3"."""
    return f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else str(v)


class Tier(BaseModel):
    """From `min` (inclusive) up to the next tier's `min`: extra prompt text describing the pet's attitude."""

    model_config = ConfigDict(extra="forbid")

    min: float = Field(ge=-LIMIT, le=LIMIT)
    name: str = Field(min_length=1, max_length=12)
    prompt: str = Field(default="", max_length=500)

    @field_validator("name", "prompt")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class Stat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=12)
    # What the number means, and when it goes up or down (both are shown to the model).
    meaning: str = Field(default="", max_length=200)
    rule: str = Field(default="", max_length=300)
    min: float = Field(default=0, ge=-LIMIT, le=LIMIT)
    max: float = Field(default=100, ge=-LIMIT, le=LIMIT)
    initial: float = Field(default=0, ge=-LIMIT, le=LIMIT)
    # Largest change one reply can make; whatever the model asks for is cut down to this.
    max_up: float = Field(default=1, ge=0, le=LIMIT)
    max_down: float = Field(default=1, ge=0, le=LIMIT)
    # Total rise allowed per day; 0 = no limit.
    daily_up: float = Field(default=0, ge=0, le=LIMIT)
    color: str = "#8c6cff"
    tiers: list[Tier] = Field(default_factory=list, max_length=MAX_TIERS)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError("只能用小写字母、数字、下划线，字母开头，最多 24 个字符")
        return v

    @field_validator("name", "meaning", "rule")
    @classmethod
    def _one_line(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not COLOR_RE.match(v):
            raise ValueError("颜色要写成 #rrggbb")
        return v.lower()

    @model_validator(mode="after")
    def _check(self) -> "Stat":
        if not self.min < self.max:
            raise ValueError(f"{self.name}：最小值必须小于最大值")
        if not self.min <= self.initial <= self.max:
            raise ValueError(f"{self.name}：初始值要在最小值和最大值之间")
        mins = [t.min for t in self.tiers]
        if len(set(mins)) != len(mins):
            raise ValueError(f"{self.name}：有两个区间的下限相同")
        self.tiers.sort(key=lambda t: t.min)
        return self

    def clamp(self, v: float) -> float:
        if not math.isfinite(v):
            return self.initial
        return round(min(self.max, max(self.min, v)), 2)

    def tier_for(self, value: float) -> Tier | None:
        """The highest tier starting at or below `value`; below all of them, the lowest one."""
        if not self.tiers:
            return None
        found = self.tiers[0]
        for t in self.tiers:
            if t.min <= value:
                found = t
        return found


_AFFECTION_TIERS = (
    (-20, "厌恶", "你现在很讨厌主人：态度冷淡带刺，回话简短，不主动关心，也不撒娇；但不要完全不理人，主人认真道歉时可以稍微松动。"),
    (-10, "反感", "你对主人有些不满：说话冷淡、爱答不理，偶尔讽刺两句，不主动找话题。"),
    (0, "陌生", "你和主人还不熟：保持礼貌和一点距离感，不撒娇，不聊太私人的事，称呼和语气都比较客气。"),
    (10, "认识", "你和主人刚认识不久：愿意聊天，但还有些拘谨，偶尔开个小玩笑试探一下。"),
    (20, "熟悉", "你和主人已经熟悉：说话自然放松，会主动接话，偶尔关心一句。"),
    (30, "朋友", "你把主人当朋友：聊天随意，会吐槽、会开玩笑，也会分享自己的小想法。"),
    (40, "好友", "你和主人是好朋友：记得主人的习惯和喜好，会主动关心，被冷落时会小小抱怨。"),
    (50, "亲近", "你和主人很亲近：语气亲昵，偶尔撒娇，愿意说心里话，主人不开心时会认真安慰。"),
    (60, "信赖", "你非常信赖主人：会依赖主人、向主人求助，也会坦率表达想念和在意。"),
    (70, "依恋", "你很依恋主人：喜欢黏着主人，主人离开久了会想念，回来时明显开心，偶尔吃点小醋。"),
    (80, "倾心", "你对主人满心喜欢：说话温柔又直白，毫不掩饰偏爱，常常把主人放在第一位。"),
    (90, "挚爱", "主人是你最重要的人：无条件的信任和偏爱，默契十足，亲昵自然而不做作，会珍惜和主人在一起的每一刻。"),
)


def default_affection() -> Stat:
    return Stat(
        id=AFFECTION,
        name="好感度",
        meaning="你对主人的好感和亲密程度。",
        rule=(
            "普通闲聊不变。主人真诚的关心、夸奖、陪伴、道谢、记得你说过的事时小幅上升（+0.1 到 +0.5）；"
            "敷衍、冷落、嘲讽、辱骂、说话不算数时下降（-0.1 到 -2）。好感是慢慢积累的，一句话不会让你一下子喜欢上谁。"
        ),
        min=-20,
        max=100,
        initial=0,
        max_up=0.5,
        max_down=2,
        daily_up=5,
        color=AFFECTION_COLOR,
        tiers=[Tier(min=m, name=n, prompt=p) for m, n, p in _AFFECTION_TIERS],
    )


class StatusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Off: nothing is added to the prompt and no value changes.
    enabled: bool = False
    stats: list[Stat] = Field(default_factory=lambda: [default_affection()], max_length=MAX_STATS)

    @model_validator(mode="after")
    def _check(self) -> "StatusConfig":
        ids = [s.id for s in self.stats]
        if len(set(ids)) != len(ids):
            raise ValueError("有两个状态值的 id 相同")
        if AFFECTION not in ids:
            # 好感度 is built in: it can be edited but not removed.
            self.stats.insert(0, default_affection())
        return self

    def stat(self, stat_id: str) -> Stat | None:
        return next((s for s in self.stats if s.id == stat_id), None)


class StatusChange(BaseModel):
    """One change of one stat. The model supplies id / delta / reason; core.status fills in the rest."""

    id: str
    # Signed; after core.status applied it, the change that actually happened.
    delta: float
    reason: str = ""
    name: str = ""
    value: float = 0
    color: str = ""
    # Name of the tier the value moved into, when it crossed a boundary.
    tier: str | None = None
    # Where the new value sits in the stat's range, 0..1 (for the little bar in the pet window).
    ratio: float = 0
