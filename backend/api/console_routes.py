"""Console API for M4: logs (ring buffer), event stream + injection, persona."""

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from api.auth import require_token
from core.event_bus import EventBus
from core.events import CONSOLE_EVENTS, EVENT_TYPES, console_event
from core.llm_config import ConfigError, format_validation_error
from core.logs import RingHandler
from core.persona import Persona, load_yaml_model, save_yaml_model

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogEntry(BaseModel):
    seq: int
    ts: float
    level: str
    logger: str
    file: str
    line: int
    func: str
    msg: str
    event_id: str | None
    exc: str | None


class LogsOut(BaseModel):
    entries: list[LogEntry]
    # Pass as `after` next time to get only newer entries.
    last_seq: int
    # Where the JSON-lines files are (older history than the in-memory ring); null in tests.
    log_dir: str | None


class EventOut(BaseModel):
    seq: int
    id: str
    type: str
    ts: float
    payload: dict[str, Any]
    # {"output": {text, expression, motion}} or {"error": "..."}; null while pending / when nothing was emitted.
    result: dict[str, Any] | None


class EventsOut(BaseModel):
    events: list[EventOut]
    last_seq: int
    injectable: list[str]
    # Every event type there is (for the page's type filter).
    types: list[str]


class InjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    payload: dict[str, Any] = {}


class InjectOut(BaseModel):
    id: str


@router.get("/logs")
async def get_logs(
    request: Request,
    after: int = Query(0, ge=0),
    level: Level = "DEBUG",
    logger: str | None = Query(None, max_length=200),
    event_id: str | None = Query(None, max_length=64),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=1000),
) -> LogsOut:
    ring: RingHandler = request.app.state.log_ring
    entries, last_seq = ring.query(
        after=after, level=level, logger=logger or None, event_id=event_id or None, q=q or None, limit=limit
    )
    log_dir = request.app.state.log_dir
    return LogsOut(
        entries=[LogEntry(**e) for e in entries],
        last_seq=last_seq,
        log_dir=str(log_dir) if log_dir else None,
    )


@router.get("/events")
async def get_events(
    request: Request,
    after: int = Query(0, ge=0),
    type: str | None = Query(None, max_length=64),
    limit: int = Query(200, ge=1, le=500),
) -> EventsOut:
    bus: EventBus = request.app.state.bus
    # async: runs on the event loop, so nothing is published between these two lines.
    events = bus.history(after=after, type=type or None, limit=limit)
    last = bus.last_seq
    return EventsOut(events=[EventOut(**e) for e in events], last_seq=last, injectable=list(CONSOLE_EVENTS), types=list(EVENT_TYPES))


@router.post("/events")
async def inject_event(request: Request, body: InjectIn) -> InjectOut:
    try:
        event = console_event(body.type, body.payload)
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    log.info("控制台注入事件 %s %s", event.id, event.type, extra={"event_id": event.id})
    request.app.state.bus.publish(event)
    return InjectOut(id=event.id)


def _persona_path(request: Request):
    return request.app.state.config_path.parent / "persona.yaml"


@router.get("/persona")
def get_persona(request: Request) -> Persona:
    try:
        return load_yaml_model(_persona_path(request), Persona)
    except ConfigError as e:
        raise HTTPException(500, str(e)) from None


@router.put("/persona")
async def put_persona(request: Request, body: dict[str, Any]) -> Persona:
    try:
        persona = Persona.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None
    async with request.app.state.save_lock:
        try:
            await asyncio.to_thread(save_yaml_model, _persona_path(request), persona)
        except ConfigError as e:
            raise HTTPException(500, str(e)) from None
    log.info("人设已保存（%s）", persona.name)
    return persona
