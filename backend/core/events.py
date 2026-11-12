"""Event structure (CLAUDE.md §2). Every perception source becomes an Event on the bus."""

import itertools
import time
from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TEXT_CHARS = 500

EventType = Literal[
    "user.text",
    "pet.interaction",
    "voice.utterance",
    "screen.changed",
    "hotkey.pressed",
    "timer.idle",
    "timer.fishing_tick",
    "camera.presence",
]
EVENT_TYPES: tuple[str, ...] = get_args(EventType)


class Event(BaseModel):
    id: str
    type: EventType
    ts: float
    payload: dict[str, Any] = Field(default_factory=dict)


_seq = itertools.count(1)


def make_event(type: EventType, payload: dict[str, Any]) -> Event:
    return Event(id=f"evt_{datetime.now():%Y%m%d}_{next(_seq):06d}", type=type, ts=time.time(), payload=payload)


class UserTextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    # user.text only: an image parked via POST /api/pet/image (the bytes never travel in the event).
    image_id: str | None = Field(default=None, pattern=r"^img_[0-9a-f]{16}$")

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("内容为空")
        return v


class InteractionPayload(BaseModel):
    """pet.interaction: the user touched the pet window (click on head/body)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["click"]
    area: Literal["head", "body"]


class HotkeyPayload(BaseModel):
    """hotkey.pressed: a combo declared in config/hotkeys.yaml fired. Only the id travels, never key data."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")


# Event types the pet window may inject, with their payload schema.
CLIENT_EVENTS: dict[str, type[BaseModel]] = {
    "user.text": UserTextPayload,
    "pet.interaction": InteractionPayload,
    "hotkey.pressed": HotkeyPayload,
}


def client_event(type: str, payload: Any) -> Event:
    """Validates an event sent by the pet window. Raises ValueError with a readable message."""
    schema = CLIENT_EVENTS.get(type)
    if schema is None:
        raise ValueError(f"不支持的事件类型：{type}")
    return make_event(type, schema.model_validate(payload).model_dump(exclude_none=True))  # type: ignore[arg-type]


class VoiceUtterancePayload(UserTextPayload):
    """voice.utterance: recognized speech (produced by perception/asr.py, or injected from the console)."""

    duration: float = Field(default=0.0, ge=0, le=600)


# Event types the console may inject for testing (rule layer and LLM behave as for real input).
CONSOLE_EVENTS: dict[str, type[BaseModel]] = {**CLIENT_EVENTS, "voice.utterance": VoiceUtterancePayload}


def console_event(type: str, payload: Any) -> Event:
    schema = CONSOLE_EVENTS.get(type)
    if schema is None:
        raise ValueError(f"不支持注入的事件类型：{type}（可选：{'、'.join(CONSOLE_EVENTS)}）")
    return make_event(type, schema.model_validate(payload).model_dump(exclude_none=True))  # type: ignore[arg-type]
