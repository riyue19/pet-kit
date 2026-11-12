"""config/persona.yaml and config/reactions.yaml. Both are re-read on use so edits apply without restart."""

from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML, YAMLError

from core.llm_config import ConfigError, format_validation_error

DEFAULT_PROMPT = (
    "你是住在用户电脑桌面上的桌宠，性格活泼、有点小傲娇，说话简短口语化，像朋友一样聊天。"
    "不要自称 AI 或语言模型。"
)


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="桌宠", min_length=1, max_length=32)
    system_prompt: str = Field(default=DEFAULT_PROMPT, min_length=1, max_length=8000)
    # Past exchanges (user + reply pairs) sent with each request.
    history_turns: int = Field(default=8, ge=0, le=50)
    max_reply_chars: int = Field(default=80, ge=10, le=500)


class InteractionReaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)
    motions: list[str] = Field(default_factory=list)
    # Probability of letting the LLM improvise instead of a canned line.
    llm_chance: float = Field(default=0.0, ge=0, le=1)


class Reactions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cooldown_seconds: float = Field(default=1.5, ge=0, le=60)
    llm_cooldown_seconds: float = Field(default=30, ge=0, le=3600)
    interactions: dict[str, InteractionReaction] = Field(default_factory=dict)


M = TypeVar("M", bound=BaseModel)


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def load_yaml_model(path: Path, model: type[M]) -> M:
    """Missing file -> defaults; syntax or schema errors -> ConfigError."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return model()
    try:
        data = YAML(typ="safe").load(text) or {}
        return model.model_validate(_plain(data))
    except YAMLError as e:
        raise ConfigError(f"{path.name}: YAML 语法错误：{e}") from None
    except ValidationError as e:
        raise ConfigError(f"{path.name}: {format_validation_error(e)}") from None


def save_yaml_model(path: Path, model: BaseModel) -> None:
    """Writes the model's fields into the YAML file, keeping comments and unknown keys.

    Multi-line strings are written as `|` blocks so prompts stay readable in the file.
    """
    import io
    import os

    from ruamel.yaml.scalarstring import LiteralScalarString

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    try:
        doc = y.load(text)
    except YAMLError as e:
        raise ConfigError(f"{path.name}: YAML 语法错误：{e}") from None
    if doc is None:
        doc = y.load("{}")
    if not isinstance(doc, dict):
        raise ConfigError(f"{path.name}: 顶层必须是映射（key: value）")
    for key, value in model.model_dump(mode="json").items():
        doc[key] = LiteralScalarString(value) if isinstance(value, str) and "\n" in value else value
    buf = io.StringIO()
    y.dump(doc, buf)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(buf.getvalue(), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
