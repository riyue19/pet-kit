"""Console API for the 指令 page: the skill whitelist (config/skills.yaml) and installed programs to add to it."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from action.skills.config import BUILTIN_NAMES, SKILL_NAMES, SkillsConfig
from action.skills.installed import scan_start_menu
from api.auth import require_token
from core.llm_config import ConfigError
from core.persona import load_yaml_model, save_yaml_model

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skills", dependencies=[Depends(require_token)])


class SkillsOut(SkillsConfig):
    available: list[str] = list(SKILL_NAMES)
    # Built-in actions (no targets). One missing from `skills` is enabled, without confirmation.
    builtin: list[str] = list(BUILTIN_NAMES)


def _path(request: Request):
    return request.app.state.config_path.parent / "skills.yaml"


@router.get("")
def get_skills(request: Request) -> SkillsOut:
    try:
        cfg = load_yaml_model(_path(request), SkillsConfig)
    except ConfigError as e:
        raise HTTPException(422, str(e)) from None
    return SkillsOut(**cfg.model_dump())


@router.put("")
async def put_skills(request: Request, body: SkillsConfig) -> SkillsOut:
    async with request.app.state.save_lock:
        await asyncio.to_thread(save_yaml_model, _path(request), body)
    log.info("技能白名单已保存（%d 个技能）", len(body.skills))
    return SkillsOut(**body.model_dump())


@router.get("/installed")
async def installed(request: Request) -> list[dict[str, str]]:
    """Programs found in the Start Menu, for picking into open_app. Slow (~1 s): call on demand."""
    scan = getattr(request.app.state, "scan_programs", scan_start_menu)
    try:
        return await asyncio.to_thread(scan)
    except Exception as e:  # noqa: BLE001
        log.warning("扫描已安装程序失败：%s", type(e).__name__)
        raise HTTPException(500, "扫描已安装程序失败") from None
