"""Console API for the 状态 page: config/status.yaml and the current values."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.auth import require_token
from core.agent import AgentOutput
from core.llm_config import ConfigError, format_validation_error
from core.persona import save_yaml_model
from core.status import StatusService
from core.status_config import LIMIT, MAX_KEEP_HISTORY, MAX_STATS, MAX_TIERS, StatusConfig

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/status", dependencies=[Depends(require_token)])


class StatusOut(BaseModel):
    config: StatusConfig
    # Same order as config.stats: [{id, value, tier, gained_today}].
    stats: list[dict[str, Any]]
    # Newest first, only the newest few: [{ts, id, name, delta, value, reason, tier}]. Page with /history.
    recent: list[dict[str, Any]]
    # How many records are kept in total.
    recent_total: int
    # Limits of the form, so the page doesn't keep its own copy.
    max_stats: int = MAX_STATS
    max_tiers: int = MAX_TIERS
    max_keep_history: int = MAX_KEEP_HISTORY


class HistoryOut(BaseModel):
    # Newest first.
    items: list[dict[str, Any]]
    total: int


class ValueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=24)
    # null = back to the stat's initial value.
    value: float | None = Field(default=None, ge=-LIMIT, le=LIMIT)


def _service(request: Request) -> StatusService:
    return request.app.state.status


def _out(service: StatusService, config: StatusConfig) -> StatusOut:
    return StatusOut(config=config, **service.view(config))


@router.get("")
def get_status(request: Request) -> StatusOut:
    service = _service(request)
    try:
        return _out(service, service.config())
    except ConfigError as e:
        raise HTTPException(500, str(e)) from None


@router.put("")
async def put_status(request: Request, body: dict[str, Any]) -> StatusOut:
    try:
        config = StatusConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None
    service = _service(request)
    async with request.app.state.save_lock:
        try:
            await asyncio.to_thread(save_yaml_model, service.config_path, config)
        except ConfigError as e:
            raise HTTPException(500, str(e)) from None
    # A smaller keep_history applies at once, not only at the next change.
    service.trim(config.keep_history)
    log.info("状态栏设置已保存（%s，%d 项）", "开启" if config.enabled else "关闭", len(config.stats))
    return _out(service, config)


@router.get("/history")
def get_history(
    request: Request, offset: int = Query(0, ge=0, le=10_000_000), limit: int = Query(20, ge=1, le=200)
) -> HistoryOut:
    items, total = _service(request).history(offset, limit)
    return HistoryOut(items=items, total=total)


@router.post("/value")
async def set_value(request: Request, body: ValueIn) -> StatusOut:
    service = _service(request)
    try:
        if not service.set_value(body.id, body.value):
            raise HTTPException(404, "没有这个状态值")
        out = _out(service, service.config())
    except ConfigError as e:
        raise HTTPException(500, str(e)) from None
    change = service.last_manual
    if change is not None:
        # Show the same pop next to the pet's head as for a change made by the model (also a way to try the look).
        await request.app.state.pet_hub.broadcast(
            {"type": "output", "event_id": None, "output": AgentOutput(status=[change]).model_dump()}
        )
    return out
