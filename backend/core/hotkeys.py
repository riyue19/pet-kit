"""config/hotkeys.yaml: global shortcuts (CLAUDE.md §4.2: only declared combos, keystrokes are never recorded).

The Electron main process registers `keys` with the OS and handles `builtin` actions locally. For the other
actions it sends hotkey.pressed {id}; this module resolves the id against the file (re-read every time, so
console edits apply at once) and decides what happens: a canned reaction (free) or an LLM prompt (cooldown).
"""

import logging
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from ruamel.yaml import YAML, YAMLError

from core.llm_config import format_validation_error

log = logging.getLogger(__name__)

ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
BUILTINS = ("toggle_visible", "toggle_mic", "toggle_camera", "stop_speaking", "open_console", "push_to_talk")
# llm presses cost money: a floor per hotkey, plus one shared window across all llm hotkeys.
MIN_LLM_COOLDOWN = 3.0
SHARED_LLM_COOLDOWN = 3.0


def _text(v: Any, limit: int) -> str:
    return "" if v is None else str(v).strip()[:limit]


def _texts(v: Any, max_items: int, limit: int) -> list[str]:
    # Same leniency as frontend/src/shared/hotkeys.ts validateHotkeys: non-strings (e.g. a YAML date) become
    # text, blanks are dropped, extras truncated. The two must agree or a file valid for one side breaks the other.
    if not isinstance(v, list):
        return []
    return [t for t in (_text(x, limit) for x in v) if t][:max_items]


class Hotkey(BaseModel):
    # Unknown keys are ignored (the frontend ignores them too).
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=ID_PATTERN)
    name: str = ""
    # Electron accelerator, e.g. "Ctrl+Alt+H". Validated and registered by the frontend.
    keys: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    action: Literal["builtin", "reaction", "llm"]
    builtin: str | None = None
    # reaction: one random line / expression / motion label (labels must exist in the model's mapping)
    lines: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)
    motions: list[str] = Field(default_factory=list)
    # llm: what the pet is told happened, e.g. "（主人按下了快捷键，想听你夸夸他）"
    prompt: str = ""
    # Presses within this window are ignored (llm presses cost money).
    cooldown_seconds: float = 2.0

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if isinstance(d.get("id"), str):
            d["id"] = d["id"].strip()
        d["name"] = _text(d.get("name"), 32)
        d["prompt"] = _text(d.get("prompt"), 500)
        d["lines"] = _texts(d.get("lines"), 50, 200)
        d["expressions"] = _texts(d.get("expressions"), 20, 32)
        d["motions"] = _texts(d.get("motions"), 20, 32)
        d["enabled"] = d.get("enabled") is not False
        if d.get("builtin") not in BUILTINS:
            d["builtin"] = None
        cd = d.get("cooldown_seconds")
        d["cooldown_seconds"] = cd if isinstance(cd, (int, float)) and not isinstance(cd, bool) and 0 <= cd <= 3600 else 2.0
        return d

    @model_validator(mode="after")
    def _shape(self) -> "Hotkey":
        if not self.keys.strip():
            raise ValueError(f"{self.id}: keys 不能为空")
        if self.action == "builtin" and self.builtin is None:
            raise ValueError(f"{self.id}: action 为 builtin 时必须填写 builtin（{'/'.join(BUILTINS)}）")
        if self.action == "llm" and not self.prompt.strip():
            raise ValueError(f"{self.id}: action 为 llm 时必须填写 prompt")
        if self.action == "llm":
            self.cooldown_seconds = max(self.cooldown_seconds, MIN_LLM_COOLDOWN)
        if self.action == "reaction" and not (self.lines or self.expressions or self.motions):
            raise ValueError(f"{self.id}: action 为 reaction 时至少要有一句台词、一个表情或一个动作")
        return self


class HotkeysConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hotkeys: list[Hotkey] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _unique(self) -> "HotkeysConfig":
        ids = [h.id for h in self.hotkeys]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"id 重复：{', '.join(dup)}")
        return self


HotkeyDecision = tuple[Literal["reaction", "llm"], Hotkey] | None


def load_hotkeys(path: Path) -> list[Hotkey]:
    """Valid entries of hotkeys.yaml; an invalid entry is skipped (and logged) instead of voiding the file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        doc = YAML(typ="safe").load(text) or {}
    except YAMLError as e:
        log.warning("hotkeys.yaml 语法错误，快捷键暂不可用：%s", e)
        return []
    items = doc.get("hotkeys") if isinstance(doc, dict) else None
    out: list[Hotkey] = []
    seen: set[str] = set()
    for i, raw in enumerate(items if isinstance(items, list) else []):
        try:
            hk = Hotkey.model_validate(raw)
        except ValidationError as e:
            log.warning("hotkeys.yaml 第 %d 项无效，已跳过：%s", i + 1, format_validation_error(e))
            continue
        if hk.id in seen:
            log.warning("hotkeys.yaml id 重复，已跳过：%s", hk.id)
            continue
        seen.add(hk.id)
        out.append(hk)
    return out


class HotkeyRules:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None) -> None:
        self._path = path
        self._clock = clock
        self._rng = rng or random.Random()
        self._last: dict[str, float] = {}
        self._last_llm = float("-inf")

    def decide(self, hotkey_id: str) -> HotkeyDecision:
        hk = next((h for h in load_hotkeys(self._path) if h.id == hotkey_id), None)
        if hk is None or not hk.enabled:
            log.info("未知或已停用的快捷键：%s", hotkey_id)
            return None
        if hk.action == "builtin":
            return None  # handled in the frontend; nothing for the backend to do
        now = self._clock()
        if now - self._last.get(hk.id, float("-inf")) < hk.cooldown_seconds:
            log.debug("快捷键 %s 冷却中，忽略", hk.id)
            return None
        if hk.action == "llm" and now - self._last_llm < SHARED_LLM_COOLDOWN:
            log.debug("刚触发过 LLM 快捷键，%s 忽略", hk.id)
            return None
        self._last[hk.id] = now
        if hk.action == "llm":
            self._last_llm = now
        return hk.action, hk

    def pick(self, items: list[str]) -> str | None:
        return self._rng.choice(items) if items else None
