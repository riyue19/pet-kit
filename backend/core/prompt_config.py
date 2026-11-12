"""config/prompt.yaml: the user-editable parts of the system prompt (console 提示词 page).

Re-read on every turn, so edits apply to the next reply. Everything here is plain text the user wrote for
the model; credentials never belong in it (CLAUDE.md §4.1).
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_KNOWLEDGE = 50
# Knowledge goes into every matching prompt; cap it so the cost can't creep up unnoticed.
MAX_KNOWLEDGE_CHARS = 4000
MAX_NOTE_CHARS = 100
MAX_OVERRIDE_CHARS = 4000
OVERRIDE_KEY_RE = re.compile(r"^[a-z][a-z_]{0,31}$")
# Actions that have no name on the 指令 page to match against: words that bring their description into the
# prompt (skills_mode "keyword"). An empty list = always offered.
DEFAULT_TOOL_KEYWORDS: dict[str, list[str]] = {
    "play_music": ["播放", "放首", "放一首", "来首", "来一首", "首歌", "放歌", "听歌", "音乐"],
    "show_code": ["代码", "脚本", "命令", "程序", "css", "写个", "写一个"],
    "set_style": ["样式", "外观", "颜色", "圆角", "气泡", "输入框", "皮肤", "主题", "css"],
    "look_camera": ["看看我", "看我", "看到我", "看得到", "看得见", "看见我", "我现在", "摄像头"],
    "invite_game": ["游戏", "玩", "无聊", "猜拳", "骰", "轮盘", "来一局", "来一把"],
}


class KnowledgeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=2000)
    enabled: bool = True
    # always: in every prompt. keyword: only when the user's words contain one of `keywords`.
    mode: Literal["always", "keyword"] = "always"
    keywords: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title", "content")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空")
        return v

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, v: list[str]) -> list[str]:
        out = [k.strip()[:30] for k in v if k.strip()]
        return list(dict.fromkeys(out))

    def matches(self, text: str) -> bool:
        if not self.enabled:
            return False
        if self.mode == "always":
            return True
        low = text.lower()
        return any(k.lower() in low for k in self.keywords)


def _notes(v: dict[str, str]) -> dict[str, str]:
    bad = sorted(k for k in v if not LABEL_RE.match(k))
    if bad:
        raise ValueError(f"标签格式不正确：{', '.join(bad)}")
    return {k: " ".join(t.split())[:MAX_NOTE_CHARS] for k, t in v.items() if t.strip()}


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Order of the blocks of the system prompt (keys of core.agent.SECTIONS); blocks not listed keep their
    # built-in place. Empty = the built-in order.
    order: list[str] = Field(default_factory=list, max_length=40)
    # Extra rules, sent as their own block (by default after the output format), e.g. "不要用颜文字".
    format_rules: str = Field(default="", max_length=2000)
    knowledge: list[KnowledgeEntry] = Field(default_factory=list, max_length=MAX_KNOWLEDGE)
    # Label -> when to use it; shown to the model next to the label. Labels the current model lacks are kept.
    expression_notes: dict[str, str] = Field(default_factory=dict)
    motion_notes: dict[str, str] = Field(default_factory=dict)
    # Extra words that mean "this needs a web search" (on top of the built-in list in core/agent.py).
    search_keywords: list[str] = Field(default_factory=list, max_length=50)
    # Replacement text for built-in prompt pieces, by template key (see core/agent.py TEMPLATES).
    # A missing or blank entry means the built-in text.
    overrides: dict[str, str] = Field(default_factory=dict)
    # keyword: a command's description is sent only when the user's words contain its name from the 指令 page
    # (or one of tool_keywords for actions without such a name). always: every usable command, every turn.
    skills_mode: Literal["keyword", "always"] = "keyword"
    tool_keywords: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_TOOL_KEYWORDS.items()})
    # A question that needs a web search, while the primary chat profile can't search: let a profile from
    # the fallback chain that can search answer this one line.
    search_use_fallback: bool = True
    # tts.speech_language is ja/en and the model left out the spoken line (or wrote it in the wrong
    # language): make one small extra LLM call to translate it instead of reading the Chinese text aloud.
    speech_repair: bool = True

    @field_validator("order")
    @classmethod
    def _order(cls, v: list[str]) -> list[str]:
        bad = sorted(k for k in v if not OVERRIDE_KEY_RE.match(k))
        if bad:
            raise ValueError(f"名称不正确：{', '.join(bad)}")
        return list(dict.fromkeys(v))

    @field_validator("format_rules")
    @classmethod
    def _rules(cls, v: str) -> str:
        return v.strip()

    @field_validator("expression_notes", "motion_notes")
    @classmethod
    def _label_notes(cls, v: dict[str, str]) -> dict[str, str]:
        return _notes(v)

    @field_validator("overrides")
    @classmethod
    def _overrides(cls, v: dict[str, str]) -> dict[str, str]:
        bad = sorted(k for k in v if not OVERRIDE_KEY_RE.match(k))
        if bad:
            raise ValueError(f"名称不正确：{', '.join(bad)}")
        long = sorted(k for k, t in v.items() if len(t) > MAX_OVERRIDE_CHARS)
        if long:
            raise ValueError(f"超过 {MAX_OVERRIDE_CHARS} 字：{', '.join(long)}")
        return {k: t.strip() for k, t in v.items() if t.strip()}

    @field_validator("tool_keywords")
    @classmethod
    def _tool_keywords(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        bad = sorted(k for k in v if not OVERRIDE_KEY_RE.match(k))
        if bad:
            raise ValueError(f"名称不正确：{', '.join(bad)}")
        return {k: list(dict.fromkeys(w.strip()[:20] for w in ws if w.strip()))[:30] for k, ws in v.items()}

    def tool_wanted(self, name: str, text: str) -> bool:
        """Should the built-in action `name` be described for this user line?"""
        if self.skills_mode == "always":
            return True
        # An action added after the file was saved has no entry yet: use its built-in words, not "always".
        words = self.tool_keywords.get(name, DEFAULT_TOOL_KEYWORDS.get(name, []))
        low = text.lower()
        return not words or any(w.lower() in low for w in words)

    @field_validator("search_keywords")
    @classmethod
    def _search(cls, v: list[str]) -> list[str]:
        out = [k.strip()[:20] for k in v if k.strip()]
        return list(dict.fromkeys(out))

    def knowledge_for(self, text: str) -> list[KnowledgeEntry]:
        """Entries to include for this user line, within the character budget (file order)."""
        out, used = [], 0
        for e in self.knowledge:
            if not e.matches(text):
                continue
            if used + len(e.content) > MAX_KNOWLEDGE_CHARS:
                break
            out.append(e)
            used += len(e.content)
        return out
