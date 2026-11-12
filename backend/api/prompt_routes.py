"""Console API for the 提示词 page: config/prompt.yaml, a preview of the assembled prompt, the last one sent."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.auth import require_token
from core.llm_config import ConfigError, format_validation_error
from core.persona import load_yaml_model, save_yaml_model
from core.agent import SECTIONS, TEMPLATES, section_order
from core.prompt_config import DEFAULT_TOOL_KEYWORDS, MAX_KNOWLEDGE_CHARS, PromptConfig

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/prompt", dependencies=[Depends(require_token)])

PREVIEW_TEXT = "你好呀"
TOOL_TITLES = {"play_music": "播放音乐", "show_code": "写代码（代码窗口）", "set_style": "改桌宠外观", "look_camera": "看看我（摄像头）", "invite_game": "邀请玩小游戏"}


class PromptOut(BaseModel):
    config: PromptConfig
    # Labels of the model the pet currently shows (empty while the pet window is not connected).
    expressions: list[str]
    motions: list[str]
    pet_connected: bool
    max_knowledge_chars: int
    # Every block config.overrides may replace: [{key, title, default, placeholders: [{name, desc}], required}].
    templates: list[dict[str, Any]]
    # Every block the system prompt can contain, in the order config.order gives:
    # [{key, title, condition, always, templates: [template key], source}].
    sections: list[dict[str, Any]]
    # Built-in order of the block keys (what an empty config.order means).
    default_order: list[str]
    # Names of the usable commands on the 指令 page (they trigger their command in keyword mode).
    skill_labels: list[str]
    # Actions triggered by config.tool_keywords: [{name, title, defaults}].
    tools: list[dict[str, Any]]
    # The last prompt actually sent to the LLM since the backend started; null if none yet.
    last: dict[str, Any] | None


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=500)
    # Unsaved settings to preview with (the page's form); null = the saved ones.
    config: dict[str, Any] | None = None


def _path(request: Request):
    return request.app.state.config_path.parent / "prompt.yaml"


def _out(request: Request, config: PromptConfig) -> PromptOut:
    hub = request.app.state.pet_hub
    return PromptOut(
        config=config,
        expressions=hub.labels.expressions,
        motions=hub.labels.motions,
        pet_connected=bool(hub.clients),
        max_knowledge_chars=MAX_KNOWLEDGE_CHARS,
        templates=[
            {
                "key": k,
                "title": t.title,
                "default": t.default,
                "placeholders": [{"name": n, "desc": d} for n, d in t.placeholders.items()],
                "required": list(t.required),
            }
            for k, t in TEMPLATES.items()
        ],
        sections=[
            {
                "key": k,
                "title": SECTIONS[k].title,
                "condition": SECTIONS[k].condition,
                "always": SECTIONS[k].always,
                "templates": list(SECTIONS[k].templates),
                "source": SECTIONS[k].source,
            }
            for k in section_order(config.order)
        ],
        default_order=list(SECTIONS),
        skill_labels=request.app.state.skills.labels(),
        tools=[{"name": n, "title": TOOL_TITLES[n], "defaults": DEFAULT_TOOL_KEYWORDS[n]} for n in TOOL_TITLES],
        last=request.app.state.agent.last_prompt,
    )


@router.get("")
def get_prompt(request: Request) -> PromptOut:
    try:
        return _out(request, load_yaml_model(_path(request), PromptConfig))
    except ConfigError as e:
        raise HTTPException(500, str(e)) from None


@router.put("")
async def put_prompt(request: Request, body: dict[str, Any]) -> PromptOut:
    try:
        config = PromptConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None
    unknown = sorted(set(config.overrides) - TEMPLATES.keys())
    if unknown:
        raise HTTPException(422, f"overrides: 没有这个内置提示词：{', '.join(unknown)}")
    unknown = sorted(set(config.order) - SECTIONS.keys())
    if unknown:
        raise HTTPException(422, f"order: 没有这个段落：{', '.join(unknown)}")
    async with request.app.state.save_lock:
        try:
            await asyncio.to_thread(save_yaml_model, _path(request), config)
        except ConfigError as e:
            raise HTTPException(500, str(e)) from None
    log.info("提示词设置已保存（知识库 %d 条）", len(config.knowledge))
    return _out(request, config)


@router.post("/preview")
async def preview_prompt(request: Request, body: PreviewIn) -> dict[str, Any]:
    """The prompt that would be sent if the user said this now (saved settings). Nothing is sent to the LLM."""
    pcfg = None
    if body.config is not None:
        try:
            pcfg = PromptConfig.model_validate(body.config)
        except ValidationError as e:
            raise HTTPException(422, format_validation_error(e)) from None
    try:
        return await request.app.state.agent.preview(
            body.text.strip() or PREVIEW_TEXT, request.app.state.pet_hub.labels, pcfg
        )
    except ConfigError as e:
        raise HTTPException(500, str(e)) from None
