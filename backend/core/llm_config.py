import io
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from core.memory_config import MemoryConfig
from core.net import validate_proxy
from core.camera_config import CameraConfig
from core.screen_config import EnvironmentConfig, ProactiveConfig, ScreenConfig
from core.voice_config import AsrConfig, TtsConfig

NAME_RE = re.compile(r"^[\w\-. ]{1,32}$")
RESERVED_EXTRA = frozenset({"model", "messages", "stream", "tools", "tool_choice", "functions", "n"})
DEFAULT_PORT = 8765


class ConfigError(Exception):
    pass


class LlmProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    base_url: str
    model: str = Field(min_length=1, max_length=200)
    # None means "don't send", leaving the provider default in effect.
    max_tokens: int | None = Field(default=1024, ge=1, le=1_000_000)
    temperature: float | None = Field(default=0.8, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    timeout: float = Field(default=60, gt=0, le=600)
    # True: request with stream=true and join the chunks (some relays only answer, or only stay within their timeout, when streaming). The reply is still handled whole.
    stream: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)
    # How this endpoint enables the model's own web search (relay-specific):
    #   off              no search
    #   google_function  tools=[{function: googleSearch}] — new-api style relays map it to Gemini Google Search
    #   openai_options   web_search_options={} — OpenAI search models / relays that translate it
    web_search: Literal["off", "google_function", "openai_options"] = "off"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not NAME_RE.match(v):
            raise ValueError("名称只能包含字母、数字、汉字、空格和 -_.，长度 1~32")
        return v

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("必须以 http:// 或 https:// 开头")
        return v

    @field_validator("extra")
    @classmethod
    def _extra(cls, v: dict[str, Any]) -> dict[str, Any]:
        bad = sorted(RESERVED_EXTRA & v.keys())
        if bad:
            raise ValueError(f"不能包含保留字段：{', '.join(bad)}")
        return v


Role = Literal["chat", "vision", "memory"]


class LlmRoles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat: str | None = None
    vision: str | None = None
    # Memory summarization; null = use the chat profile.
    memory: str | None = None


MAX_FALLBACKS = 2


class LlmFallbacks(BaseModel):
    """Backup profiles per role, in order: 2nd place, then 3rd place (at most MAX_FALLBACKS each)."""

    model_config = ConfigDict(extra="forbid")

    chat: list[str] = Field(default_factory=list, max_length=MAX_FALLBACKS)
    vision: list[str] = Field(default_factory=list, max_length=MAX_FALLBACKS)
    # Used when roles.memory is set; with roles.memory null the chat chain is used.
    memory: list[str] = Field(default_factory=list, max_length=MAX_FALLBACKS)


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[LlmProfile] = Field(default_factory=list)
    roles: LlmRoles = Field(default_factory=LlmRoles)
    fallbacks: LlmFallbacks = Field(default_factory=LlmFallbacks)
    # Let the chat model search the web (only for profiles whose web_search isn't "off").
    web_search: bool = True

    @model_validator(mode="after")
    def _refs(self) -> "LlmConfig":
        names = [p.name for p in self.profiles]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"配置名称重复：{', '.join(dup)}")
        for role in ("chat", "vision", "memory"):
            ref = getattr(self.roles, role)
            if ref is not None and ref not in names:
                raise ValueError(f"roles.{role} 引用了不存在的配置：{ref}")
            backups = getattr(self.fallbacks, role)
            for n in backups:
                if n not in names:
                    raise ValueError(f"fallbacks.{role} 引用了不存在的配置：{n}")
            if backups and ref is None and role != "memory":
                raise ValueError(f"fallbacks.{role}: 先指定主用配置（roles.{role}）才能设置备选")
            if len({*backups, *([ref] if ref else [])}) != len(backups) + (1 if ref else 0):
                raise ValueError(f"fallbacks.{role}: 备选不能重复，也不能与主用配置相同")
        return self

    def profile(self, name: str) -> LlmProfile | None:
        return next((p for p in self.profiles if p.name == name), None)

    def for_role(self, role: Role) -> LlmProfile | None:
        name = getattr(self.roles, role)
        return self.profile(name) if name else None

    def chain(self, role: Role) -> list[LlmProfile]:
        """Profiles to try for a role, best first: primary, then its fallbacks. Empty = role not configured.

        Memory summarization without its own profile follows the chat chain.
        """
        if role == "memory" and not self.roles.memory:
            role = "chat"
        primary = getattr(self.roles, role)
        if not primary:
            return []
        names = [primary, *getattr(self.fallbacks, role)]
        return [p for n in names if (p := self.profile(n)) is not None]


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: int = Field(default=DEFAULT_PORT, ge=1024, le=65535)


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxy: str = "direct"

    @field_validator("proxy")
    @classmethod
    def _proxy(cls, v: str) -> str:
        return validate_proxy(v)


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    server: ServerConfig = Field(default_factory=ServerConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    camera: CameraConfig = Field(default_factory=CameraConfig)


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def format_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        msg = err["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}")
    return "；".join(parts)


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def parse_backend_config(text: str) -> BackendConfig:
    try:
        doc = _yaml().load(text)
    except YAMLError as e:
        raise ConfigError(f"YAML 语法错误：{e}") from e
    if doc is None:
        return BackendConfig()
    if not isinstance(doc, dict):
        raise ConfigError("<root>: 必须是映射")
    try:
        return BackendConfig.model_validate(_plain(doc))
    except ValidationError as e:
        raise ConfigError(format_validation_error(e)) from e


def load_backend_config(path: Path) -> BackendConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return BackendConfig()
    return parse_backend_config(text)


def save_llm_config(path: Path, llm: LlmConfig) -> None:
    save_sections(path, {"llm": llm})


def save_sections(path: Path, sections: dict[str, BaseModel]) -> None:
    """Replaces only the given top-level sections; the rest of the file and its comments are kept."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    y = _yaml()
    try:
        doc = y.load(text)
    except YAMLError as e:
        raise ConfigError(f"YAML 语法错误：{e}") from e
    if doc is None:
        doc = y.load("{}")
    for name, model in sections.items():
        doc[name] = model.model_dump(mode="json")
    buf = io.StringIO()
    y.dump(doc, buf)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(buf.getvalue(), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
