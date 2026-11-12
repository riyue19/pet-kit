"""Console API for the 语音 page: asr/tts config, the MiniMax key (OS credential store), voice list, test."""

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.auth import require_token
from core.credentials import MAX_KEY_LENGTH
from core.llm_config import format_validation_error, save_sections
from typing import Literal

from core.voice import VoiceState
from core.voice_config import COSYVOICE_MODELS, TTS_EMOTIONS, TTS_MODELS, TTS_PROVIDERS, AsrConfig, TtsConfig
from providers.base import ProviderError
from providers.minimax_tts import MiniMaxTts

TEST_TEXT = "你好呀，我是你的桌宠，以后请多指教！"
VOICES_CACHE_SECONDS = 600
FETCH_COMMAND = "uv run python scripts/fetch_models.py"

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", dependencies=[Depends(require_token)])


Provider = Literal[TTS_PROVIDERS]  # type: ignore[valid-type]


class KeyStatus(BaseModel):
    set: bool
    hint: str | None


class VoiceStateOut(BaseModel):
    asr: AsrConfig
    tts: TtsConfig
    # Whether the local ASR model files exist; listening stays off until they do.
    models_ready: bool
    fetch_command: str
    # Key of the saved provider, and of every provider (each has its own).
    key: KeyStatus
    keys: dict[str, KeyStatus]
    emotions: list[str]
    models: list[str]
    cosyvoice_models: list[str]


class VoiceSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asr: dict
    tts: dict


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(max_length=MAX_KEY_LENGTH)
    # Whose key; the saved provider when omitted.
    provider: Provider | None = None


class TestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=200)
    # Unsaved settings from the form; the saved config is used when omitted.
    tts: dict | None = None
    expression: str | None = None


class TestOut(BaseModel):
    ok: bool
    first_audio_ms: int | None = None
    error: str | None = None


class VoiceOut(BaseModel):
    voice_id: str
    name: str
    kind: str


def _voice(request: Request) -> VoiceState:
    return request.app.state.voice


async def _state(request: Request) -> VoiceStateOut:
    v = _voice(request)

    def statuses() -> dict[str, KeyStatus]:
        return {p: KeyStatus(**v.keys.status(p)) for p in TTS_PROVIDERS}

    keys = await asyncio.to_thread(statuses)
    return VoiceStateOut(
        asr=v.asr,
        tts=v.tts,
        models_ready=v.models_ready,
        fetch_command=FETCH_COMMAND,
        key=keys[v.tts.provider],
        keys=keys,
        emotions=list(TTS_EMOTIONS),
        models=list(TTS_MODELS),
        cosyvoice_models=list(COSYVOICE_MODELS),
    )


def _validate(asr: dict | None, tts: dict | None) -> tuple[AsrConfig | None, TtsConfig | None]:
    try:
        return (
            AsrConfig.model_validate(asr) if asr is not None else None,
            TtsConfig.model_validate(tts) if tts is not None else None,
        )
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None


@router.get("")
async def get_voice(request: Request) -> VoiceStateOut:
    _voice(request).reload()
    return await _state(request)


@router.put("")
async def put_voice(request: Request, body: VoiceSaveIn) -> VoiceStateOut:
    asr, tts = _validate(body.asr, body.tts)
    assert asr is not None and tts is not None
    async with request.app.state.save_lock:
        await asyncio.to_thread(save_sections, request.app.state.config_path, {"asr": asr, "tts": tts})
        v = _voice(request)
        v.reload()
    hub = request.app.state.pet_hub
    await hub.push_mic()
    v.warm_up()
    if not v.tts.enabled:
        await hub.interrupt()
    return await _state(request)


@router.get("/heard")
def get_heard(request: Request) -> list[dict]:
    """What the recognizer heard lately (newest first) and why each was sent or dropped. Memory only."""
    return list(reversed(request.app.state.pet_hub.heard_log))


@router.put("/key")
async def put_key(request: Request, body: KeyIn) -> KeyStatus:
    keys = _voice(request).keys
    name = body.provider or _voice(request).tts.provider
    async with request.app.state.save_lock:
        try:
            await asyncio.to_thread(keys.set, name, body.api_key)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
    request.app.state.voices_cache = None
    return KeyStatus(**await asyncio.to_thread(keys.status, name))


@router.delete("/key")
async def delete_key(request: Request, provider: Provider | None = None) -> KeyStatus:
    async with request.app.state.save_lock:
        await asyncio.to_thread(_voice(request).keys.delete, provider or _voice(request).tts.provider)
    request.app.state.voices_cache = None
    return KeyStatus(set=False, hint=None)


@router.post("/test")
async def test_voice(request: Request, body: TestIn) -> TestOut:
    """Speaks a sample through the pet (real playback path, so lip sync is exercised too)."""
    hub = request.app.state.pet_hub
    if not hub.clients:
        return TestOut(ok=False, error="桌宠未连接，无法试听")
    _, tts = _validate(None, body.tts)
    saved = _voice(request).tts
    # The stored key only ever goes to the saved endpoint, never to an unsaved URL typed into the form.
    cfg = (tts or saved).pin_endpoints(saved).model_copy(update={"enabled": True})
    task = await hub.speaker.speak(body.text or TEST_TEXT, body.expression, config=cfg)
    if task is None:
        return TestOut(ok=False, error="没有可朗读的文字")
    (result,) = await asyncio.gather(task, return_exceptions=True)
    if isinstance(result, BaseException):
        if not isinstance(result, asyncio.CancelledError):
            log.error("试听异常", exc_info=result)
        return TestOut(ok=False, error="试听被打断（有新的语音或你开口说话了）")
    return TestOut(ok=result.ok, first_audio_ms=result.first_audio_ms, error=result.error)


@router.get("/voices")
async def list_voices(request: Request) -> list[VoiceOut]:
    cache = getattr(request.app.state, "voices_cache", None)
    if cache and time.monotonic() - cache[0] < VOICES_CACHE_SECONDS:
        return cache[1]
    v = _voice(request)
    # Only MiniMax has a voice listing API; the other providers take a typed voice name.
    key = await asyncio.to_thread(v.api_key, "minimax")
    try:
        client = MiniMaxTts(v.tts.minimax, key)
    except ProviderError as e:
        log.warning("获取 MiniMax 音色列表失败：%s", e)
        raise HTTPException(400, str(e)) from None
    try:
        voices = [VoiceOut(**x) for x in await client.list_voices()]
    except ProviderError as e:
        log.warning("获取 MiniMax 音色列表失败：%s", e)
        raise HTTPException(502, str(e)) from None
    finally:
        await client.aclose()
    request.app.state.voices_cache = (time.monotonic(), voices)
    return voices
