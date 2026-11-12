"""`screen` / `environment` sections of config/backend.yaml and config/privacy.yaml."""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


@dataclass(frozen=True)
class Cadence:
    """How chatty screen comments are. All limits apply together."""

    dwell_seconds: float  # stay on new content this long before the pet takes a look
    per_content_seconds: float  # the same app+title is looked at again at most this often
    min_gap_seconds: float  # between any two proactive remarks
    max_per_hour: int
    # Re-look at unchanged-title content only when the picture changed this much (0..1); None = never.
    revisit_change: float | None


CADENCES: dict[str, Cadence] = {
    "quiet": Cadence(dwell_seconds=60, per_content_seconds=1800, min_gap_seconds=600, max_per_hour=2, revisit_change=None),
    "normal": Cadence(dwell_seconds=30, per_content_seconds=600, min_gap_seconds=180, max_per_hour=6, revisit_change=0.35),
    "chatty": Cadence(dwell_seconds=10, per_content_seconds=180, min_gap_seconds=60, max_per_hour=20, revisit_change=0.25),
}


class ScreenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Master switch for screen perception (tier 1 polling included).
    enabled: bool = False
    # quiet / normal / chatty: see CADENCES.
    mode: Literal["quiet", "normal", "chatty"] = "normal"
    # Comment on fullscreen apps (games, videos). CLAUDE.md §4.4 lists fullscreen as do-not-disturb; this is
    # the user's explicit opt-in to game/video commentary. Meetings and screen recording stay silent regardless.
    allow_fullscreen: bool = True
    # Seconds after the user last talked/typed/clicked during which the pet doesn't butt in.
    quiet_after_user_seconds: float = Field(default=45, ge=0, le=600)
    poll_seconds: float = Field(default=2, ge=0.5, le=30)

    @property
    def cadence(self) -> Cadence:
        return CADENCES[self.mode]


@dataclass(frozen=True)
class ChatCadence:
    """How often the pet starts a conversation on its own (idle chat / welcome back / greetings)."""

    min_gap_seconds: float  # between any two proactive remarks (shared with screen comments)
    max_per_hour: int  # proactive remarks per hour, all kinds together
    idle_repeat_seconds: float  # after one idle chat, wait at least this long before the next


CHAT_CADENCES: dict[str, ChatCadence] = {
    "quiet": ChatCadence(min_gap_seconds=900, max_per_hour=2, idle_repeat_seconds=3600),
    "normal": ChatCadence(min_gap_seconds=300, max_per_hour=4, idle_repeat_seconds=1800),
    "chatty": ChatCadence(min_gap_seconds=120, max_per_hour=8, idle_repeat_seconds=900),
}


_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ProactiveConfig(BaseModel):
    """`proactive` section: the pet starting conversations by itself (M7)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: Literal["quiet", "normal", "chatty"] = "normal"
    # Idle chat: the user is at the computer but hasn't talked to the pet for this long.
    idle_chat: bool = True
    idle_after_minutes: float = Field(default=20, ge=3, le=240)
    # Welcome back: no keyboard/mouse input for this long, then input again.
    welcome_back: bool = True
    away_minutes: float = Field(default=10, ge=3, le=240)
    # Time-of-day greetings (morning / lunch / dinner / late night), each at most once a day.
    greetings: bool = True
    # Idle action (rule layer only, no LLM, works with `enabled` off): this long after the user last engaged
    # the pet (text / voice / click / hotkey) — keyboard and mouse activity don't count — play one of these
    # labels. Labels the current model lacks are skipped, so the list survives a model change.
    idle_action: bool = False
    idle_action_minutes: float = Field(default=20, ge=1, le=240)
    idle_action_motions: list[str] = Field(default_factory=list, max_length=16)
    idle_action_expressions: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("idle_action_motions", "idle_action_expressions")
    @classmethod
    def _labels(cls, v: list[str]) -> list[str]:
        bad = sorted(x for x in v if not _LABEL_RE.fullmatch(x))
        if bad:
            raise ValueError(f"标签只能是小写字母开头的 a-z0-9_，最长 32：{', '.join(bad)}")
        return list(dict.fromkeys(v))

    @property
    def cadence(self) -> ChatCadence:
        return CHAT_CADENCES[self.mode]


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # City for weather, e.g. 杭州 / 北京 朝阳; empty = no weather. Looked up with Open-Meteo (no key, no IP lookup).
    city: str = Field(default="", max_length=40)
    weather: bool = True

    @field_validator("city")
    @classmethod
    def _city(cls, v: str) -> str:
        return v.strip()


DEFAULT_BLOCK_PROCESSES = [
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "dashlane.exe", "enpass.exe",
    "alipay.exe", "支付宝.exe", "cmb.exe", "icbc.exe", "ccb.exe", "abchina.exe", "boc.exe",
    "tdxw.exe", "hexin.exe", "eastmoney.exe", "thsfin.exe",
]
DEFAULT_BLOCK_TITLES = [
    "银行", "网银", "支付", "付款", "收银台", "钱包", "证券", "股票交易", "密码", "password",
    "验证码", "身份证", "医保", "社保", "病历", "隐私", "无痕", "InPrivate", "Incognito",
]
DEFAULT_QUIET_PROCESSES = [
    "wemeetapp.exe", "zoom.exe", "teams.exe", "ms-teams.exe", "webex.exe", "lark meeting.exe",
    "obs64.exe", "obs32.exe", "bdcam.exe", "camtasia.exe", "livehime.exe", "douyin_live.exe",
]
DEFAULT_QUIET_TITLES = ["腾讯会议", "会议中", "飞书会议", "钉钉会议", "正在共享", "共享屏幕", "直播中"]


class PrivacyConfig(BaseModel):
    """config/privacy.yaml. Matching is case-insensitive; titles match by substring."""

    model_config = ConfigDict(extra="forbid")

    # Never captured, never commented on, title not stored in events.
    block_processes: list[str] = Field(default_factory=lambda: list(DEFAULT_BLOCK_PROCESSES), max_length=500)
    block_titles: list[str] = Field(default_factory=lambda: list(DEFAULT_BLOCK_TITLES), max_length=500)
    # While these are in the foreground (meetings, screen recording, streaming) the pet stays silent.
    quiet_processes: list[str] = Field(default_factory=lambda: list(DEFAULT_QUIET_PROCESSES), max_length=500)
    quiet_titles: list[str] = Field(default_factory=lambda: list(DEFAULT_QUIET_TITLES), max_length=500)

    @field_validator("block_processes", "block_titles", "quiet_processes", "quiet_titles")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return [x.strip()[:100] for x in v if isinstance(x, str) and x.strip()]

    def _match(self, process: str, title: str, procs: list[str], titles: list[str]) -> bool:
        p, t = process.lower(), title.lower()
        return p in {x.lower() for x in procs} or any(k.lower() in t for k in titles)

    def blocked(self, process: str, title: str) -> bool:
        return self._match(process, title, self.block_processes, self.block_titles)

    def quiet(self, process: str, title: str) -> bool:
        return self._match(process, title, self.quiet_processes, self.quiet_titles)
