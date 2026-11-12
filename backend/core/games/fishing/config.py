"""config/fish.yaml (numbers and content) and config/fishing_lines.yaml (stock lines). Re-read when changed."""

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.llm_config import ConfigError
from core.persona import load_yaml_model

log = logging.getLogger(__name__)

TEMPLATES = ("slim", "round", "flat", "eel", "whisker")
PATTERNS = ("plain", "stripes", "spots", "lateral", "scales", "belly")
# The eel's patterns follow its body curve; the others are not drawn for it.
EEL_PATTERNS = ("stripes", "spots", "belly", "lateral", "plain")
TIME_SLOTS = ("dawn", "day", "dusk", "night")
WEATHERS = ("sunny", "cloudy", "rain")
RARITIES = ("common", "uncommon", "rare", "legendary")
OUTCOMES = ("empty", "fish", "junk", "bottle")
TIME_NAMES = {"dawn": "清晨", "day": "白天", "dusk": "黄昏", "night": "夜晚"}
WEATHER_NAMES = {"sunny": "晴", "cloudy": "阴", "rain": "雨"}

Rarity = Literal["common", "uncommon", "rare", "legendary"]
TimeSlot = Literal["any", "dawn", "day", "dusk", "night"]
Weather = Literal["any", "sunny", "cloudy", "rain"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GameSettings(Strict):
    # A cast waits a random, real time between these two before anything bites (short waits are more common
    # than long ones). Never less than a few minutes: there is no way to get a fish at once.
    wait_min_sec: int = Field(default=300, ge=180, le=21600)
    wait_max_sec: int = Field(default=10800, ge=180, le=43200)
    offline_catchup_cap: int = Field(default=24, ge=0, le=200)
    offline_catchup_max_hours: float = Field(default=8, ge=0, le=72)
    pity_empty_streak: int = Field(default=4, ge=1, le=50)
    bite_window_ms: int = Field(default=3000, ge=1000, le=10000)
    llm_budget_per_day: int = Field(default=30, ge=0, le=500)
    animate_empty: bool = False
    weather_change_hours: float = Field(default=3, gt=0, le=48)
    # False = the light tier: the HUD shows still pictures only.
    hud_animation: bool = True
    # The small fishing window next to the pet: shown at all (false = put away), and its size.
    hud_placed: bool = True
    hud_scale: float = Field(default=1.0, ge=0.6, le=2.5)

    @model_validator(mode="after")
    def _wait_range(self) -> "GameSettings":
        if self.wait_min_sec > self.wait_max_sec:
            raise ValueError("最短等待不能大于最长等待")
        return self


class PersonaTraits(Strict):
    """How this pet fishes. Lives here (not in persona.yaml) so the persona file stays about speech."""

    patience: float = Field(default=0.5, ge=0, le=1)
    talkative: float = Field(default=0.5, ge=0, le=1)
    mood: str = Field(default="neutral", pattern=r"^[a-z_]{1,16}$")


class RarityCfg(Strict):
    base_weight: float = Field(gt=0)
    label: str = Field(min_length=1, max_length=8)
    first_bonus: int = Field(default=0, ge=0)
    price_mult: float = Field(default=1.0, gt=0)
    bite_window: bool = False


class Location(Strict):
    name: str = Field(min_length=1, max_length=12)
    unlock_cost: int = Field(default=0, ge=0)
    requires_rod_level: int = Field(default=1, ge=1)


class Bait(Strict):
    name: str = Field(min_length=1, max_length=12)
    cost: int = Field(default=0, ge=0)
    fish_weight_mult: float = Field(default=1.0, gt=0)
    rarity_mult: dict[Rarity, float] = Field(default_factory=dict)


class Rod(Strict):
    name: str = Field(min_length=1, max_length=12)
    cost: int = Field(default=0, ge=0)
    size_mult: float = Field(default=1.0, gt=0)
    rarity_mult: dict[Rarity, float] = Field(default_factory=dict)


class Economy(Strict):
    price_size_clamp: tuple[float, float] = (0.6, 2.0)
    trophy_price_mult: float = Field(default=1.5, gt=0)
    trophy_sd: float = Field(default=1.5, gt=0)
    release_affinity: float = Field(default=0.3, ge=0)
    release_affinity_daily_cap: float = Field(default=1.5, ge=0)
    luck_per_affinity_level: float = Field(default=0.02, ge=0)
    luck_cap: float = Field(default=0.10, ge=0)


class Size(Strict):
    mean: float = Field(gt=0)
    sd: float = Field(ge=0)
    min: float = Field(gt=0)
    max: float = Field(gt=0)


class Species(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: str = Field(min_length=1, max_length=12)
    rarity: Rarity
    template: str
    patterns: list[str] = Field(default_factory=list)
    locations: list[str] = Field(min_length=1)
    time: list[TimeSlot] = Field(default_factory=lambda: ["any"])
    weather: list[Weather] = Field(default_factory=lambda: ["any"])
    size_cm: Size
    base_price: int = Field(ge=0)
    weight: float = Field(default=10, gt=0)
    bait_bonus: dict[str, float] = Field(default_factory=dict)

    def at(self, location: str, time_slot: str, weather: str) -> bool:
        return (
            location in self.locations
            and ("any" in self.time or time_slot in self.time)
            and ("any" in self.weather or weather in self.weather)
        )


class Junk(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: str = Field(min_length=1, max_length=12)
    icon: str = ""
    sell_price: int = Field(default=1, ge=0)


class RewardRange(Strict):
    min: int = Field(default=2, ge=0)
    max: int = Field(default=8, ge=0)


class BottleCfg(Strict):
    icon: str = ""
    pool_min: int = Field(default=20, ge=0, le=500)
    batch_size: int = Field(default=10, ge=1, le=30)
    reward_coins: RewardRange = Field(default_factory=RewardRange)


class FishConfig(Strict):
    game: GameSettings = Field(default_factory=GameSettings)
    persona: PersonaTraits = Field(default_factory=PersonaTraits)
    outcome_weights: dict[Literal["empty", "fish", "junk", "bottle"], float] = Field(
        default_factory=lambda: {"empty": 50, "fish": 38, "junk": 8, "bottle": 4}
    )
    weather_weights: dict[Literal["sunny", "cloudy", "rain"], float] = Field(
        default_factory=lambda: {"sunny": 50, "cloudy": 30, "rain": 20}
    )
    rarity: dict[Rarity, RarityCfg] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    baits: dict[str, Bait] = Field(default_factory=dict)
    rods: dict[int, Rod] = Field(default_factory=dict)
    economy: Economy = Field(default_factory=Economy)
    species: list[Species] = Field(default_factory=list)
    junk: list[Junk] = Field(default_factory=list)
    bottle: BottleCfg = Field(default_factory=BottleCfg)

    @model_validator(mode="after")
    def _check(self) -> "FishConfig":
        errors: list[str] = []
        if any(w < 0 for w in self.outcome_weights.values()) or self.outcome_weights.get("fish", 0) <= 0:
            errors.append("outcome_weights：权重不能为负，且 fish 必须大于 0")
        if sum(self.weather_weights.values()) <= 0:
            errors.append("weather_weights：至少要有一种天气的权重大于 0")
        if "none" not in self.baits:
            errors.append("baits：必须有 none（无饵）")
        if 1 not in self.rods or sorted(self.rods) != list(range(1, len(self.rods) + 1)):
            errors.append("rods：等级必须从 1 开始连续编号")
        free = [k for k, loc in self.locations.items() if loc.unlock_cost == 0]
        if not free:
            errors.append("locations：至少要有一个 unlock_cost 为 0 的地点")
        seen: set[str] = set()
        for i, s in enumerate(self.species):
            where = f"species 第 {i + 1} 条（{s.id} {s.name}）"
            if s.id in seen:
                errors.append(f"{where}：id 重复")
            seen.add(s.id)
            if s.rarity not in self.rarity:
                errors.append(f"{where}：稀有度 {s.rarity} 没有在 rarity 里定义")
            if s.template not in TEMPLATES:
                errors.append(f"{where}：没有 {s.template} 这个鱼形模板（可用：{'、'.join(TEMPLATES)}）")
            for p in s.patterns:
                if p not in PATTERNS:
                    errors.append(f"{where}：没有 {p} 这个花纹（可用：{'、'.join(PATTERNS)}）")
            for loc in s.locations:
                if loc not in self.locations:
                    errors.append(f"{where}：地点 {loc} 没有在 locations 里定义")
            for b in s.bait_bonus:
                if b not in self.baits:
                    errors.append(f"{where}：饵 {b} 没有在 baits 里定义")
            if not s.size_cm.min <= s.size_cm.mean <= s.size_cm.max:
                errors.append(f"{where}：size_cm 需要满足 min ≤ mean ≤ max")
        if len({j.id for j in self.junk}) != len(self.junk):
            errors.append("junk：id 重复")
        if errors:
            raise ValueError("；".join(errors))
        return self

    def warnings(self) -> list[str]:
        out = []
        for s in self.species:
            bad = [p for p in s.patterns if s.template == "eel" and p not in EEL_PATTERNS]
            if bad:
                out.append(f"{s.name}（{s.id}）：eel 模板不支持花纹 {'、'.join(bad)}，会被忽略")
        return out

    def species_by_id(self, sid: str | None) -> Species | None:
        return next((s for s in self.species if s.id == sid), None)

    def species_by_name(self, name: str) -> Species | None:
        return next((s for s in self.species if s.name == name or s.id == name), None)

    def junk_by_id(self, jid: str | None) -> Junk | None:
        return next((j for j in self.junk if j.id == jid), None)

    def find(self, table: dict, name: str) -> str | None:
        """Key of a location / bait by key or display name."""
        for key, item in table.items():
            if name in (str(key), item.name):
                return key
        return None

    def start_location(self) -> str:
        return next(k for k, loc in self.locations.items() if loc.unlock_cost == 0)


MoodLines = dict[str, list[str]]


class Lines(BaseModel):
    """outcome -> category -> mood -> lines; missing parts fall back (see pick_group)."""

    model_config = ConfigDict(extra="ignore")

    catch: dict[str, MoodLines] = Field(default_factory=dict)
    escape: MoodLines = Field(default_factory=dict)
    empty: MoodLines = Field(default_factory=dict)
    junk: MoodLines = Field(default_factory=dict)
    bottle: MoodLines = Field(default_factory=dict)
    bite: MoodLines = Field(default_factory=dict)
    offline_summary: MoodLines = Field(default_factory=dict)


BUILTIN_LINES = {
    "catch": ["钓到一条{fish}，{size}厘米。"],
    "escape": ["啊，跑了……"],
    "empty": ["……什么都没有。"],
    "streak": ["连着空军了。"],
    "junk": ["钓到了{fish}。"],
    "bottle": ["有个漂流瓶！"],
    "bite": ["有动静！"],
    "offline_summary": ["这段时间钓了{n}竿，收获{fish_count}条鱼。"],
}


def pick_group(lines: Lines, kind: str, category: str | None, mood: str) -> list[str]:
    """Candidate lines for an outcome. `kind`: catch / escape / empty / streak / junk / bottle / bite /
    offline_summary; `category`: the rarity for catch."""
    if kind == "catch":
        moods = lines.catch.get(category or "common") or lines.catch.get("common") or {}
    elif kind == "streak":
        return lines.empty.get("streak") or BUILTIN_LINES["streak"]
    else:
        moods = getattr(lines, kind, {}) or {}
    return moods.get(mood) or moods.get("neutral") or BUILTIN_LINES[kind]


class FishFiles:
    """Both files, cached by mtime. A broken edit keeps the last good version (and logs why)."""

    def __init__(self, config_dir: Path) -> None:
        self._fish_path = config_dir / "fish.yaml"
        self._lines_path = config_dir / "fishing_lines.yaml"
        self._fish: FishConfig | None = None
        self._lines: Lines | None = None
        self._stamps: dict[Path, float | None] = {}
        self.error: str | None = None

    def _changed(self, path: Path) -> bool:
        try:
            stamp: float | None = path.stat().st_mtime
        except OSError:
            stamp = None
        if path in self._stamps and self._stamps[path] == stamp:
            return False
        self._stamps[path] = stamp
        return True

    def fish(self) -> FishConfig:
        """Raises ConfigError when the file is broken and there is no earlier good version."""
        if self._changed(self._fish_path) or self._fish is None:
            try:
                if not self._fish_path.is_file():
                    raise ConfigError("fish.yaml: 找不到文件")
                cfg = load_yaml_model(self._fish_path, FishConfig)
                if not cfg.species:
                    raise ConfigError("fish.yaml: 没有任何物种（文件缺失或 species 为空）")
                for w in cfg.warnings():
                    log.warning("fish.yaml：%s", w)
                self._fish, self.error = cfg, None
            except ConfigError as e:
                self.error = str(e)
                log.error("钓鱼配置有误：%s", e)
                if self._fish is None:
                    raise
        return self._fish

    def lines(self) -> Lines:
        if self._changed(self._lines_path) or self._lines is None:
            try:
                self._lines = load_yaml_model(self._lines_path, Lines)
            except ConfigError as e:
                log.error("钓鱼台词有误，使用内置台词：%s", e)
                self._lines = self._lines or Lines()
        return self._lines
