"""config/skills.yaml: the whitelist of what the LLM may do (CLAUDE.md §4.6). Re-read on use.

The LLM only ever names a skill and picks a target *id* (or gives a search phrase); paths, URLs and
arguments come from this file, so it cannot make the pet run anything that is not listed here.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SKILL_NAMES = ("open_app", "play_music", "open_game")
# Built-in actions: they start nothing, they only show something on the pet side (action/builtin.py) or ask
# the camera / the games for something. They have no targets; only `enabled` and `confirm` apply.
# Not listed in the file = enabled (they need no setup), still under the master switch.
BUILTIN_NAMES = ("show_code", "set_style", "look_camera", "invite_game")
BUILTIN_TITLES = {
    "show_code": "打开代码窗口",
    "set_style": "修改桌宠外观",
    "look_camera": "用摄像头看一眼",
    "invite_game": "邀请你玩小游戏",
}
TARGET_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# Replaced with the keyring secret named by Target.credential when launching.
SECRET_PLACEHOLDER = "{secret}"


class Target(BaseModel):
    """One program, music app or game the skill may start."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=32)
    # Start a program (absolute path or a name on PATH), never through a shell. Or start a URL scheme.
    exe: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    # play_music: URL opened for a search phrase; {query} is URL-encoded. Without it the app is just opened.
    search_url: str | None = None
    # Username in Windows Credential Manager under service "pet-kit/skill". Its value replaces "{secret}"
    # in `args`. Only skill code reads it; it never reaches the LLM, logs or events.
    credential: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _one_launcher(self) -> "Target":
        if bool(self.exe) == bool(self.url):
            raise ValueError("exe 和 url 必须二选一")
        if self.search_url and "{query}" not in self.search_url:
            raise ValueError("search_url 里要有 {query}")
        if self.credential and not any(SECRET_PLACEHOLDER in a for a in self.args):
            raise ValueError(f"设置了 credential，args 里就要有 {SECRET_PLACEHOLDER}")
        return self


class SkillConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # True: a dialog on the pet must be accepted before the skill runs.
    confirm: bool = False
    targets: dict[str, Target] = Field(default_factory=dict)

    @field_validator("targets")
    @classmethod
    def _ids(cls, v: dict[str, Target]) -> dict[str, Target]:
        for key in v:
            if not TARGET_ID.fullmatch(key):
                raise ValueError(f"目标名 {key!r} 只能用小写字母、数字、下划线，且以字母开头")
        return v


class SkillsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Master switch: off = the LLM is not told about any skill and nothing runs.
    enabled: bool = True
    confirm_timeout_seconds: float = Field(default=30, ge=5, le=300)
    skills: dict[str, SkillConfig] = Field(default_factory=dict)

    @field_validator("skills")
    @classmethod
    def _known(cls, v: dict[str, SkillConfig]) -> dict[str, SkillConfig]:
        for name, skill in v.items():
            if name in BUILTIN_NAMES:
                if skill.targets:
                    raise ValueError(f"{name} 是内置动作，没有 targets")
            elif name not in SKILL_NAMES:
                raise ValueError(f"不是内置技能：{name}（可选：{'、'.join(SKILL_NAMES + BUILTIN_NAMES)}）")
        return v
