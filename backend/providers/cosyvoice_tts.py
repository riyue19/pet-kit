"""Alibaba Cloud Model Studio CosyVoice, duplex WebSocket API, streamed as raw PCM.

Protocol (https://help.aliyun.com/zh/model-studio/cosyvoice-websocket-api): connect with a Bearer key, send
run-task, wait for task-started, send the text in continue-task, then finish-task. Audio arrives as binary
frames between JSON events; task-finished ends the stream, task-failed carries error_code / error_message.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import websockets
from websockets.exceptions import InvalidStatus, WebSocketException

from core import net
from core.redact import redact
from core.voice_config import COSYVOICE_EMOTIONS, TTS_SAMPLE_RATE, CosyVoiceConfig
from providers.base import ProviderError

log = logging.getLogger(__name__)

# MiniMax-style emotion names (what TtsConfig.emotions maps to) -> CosyVoice's.
EMOTION_ALIASES = {"calm": "neutral"}
ERRORS = {
    "InvalidApiKey": "鉴权失败，请检查 API Key",
    "Arrearage": "账户欠费",
    "Throttling": "触发限流，请稍后再试",
    "Throttling.RateQuota": "触发限流，请稍后再试",
    "Throttling.AllocationQuota": "额度已用完",
}
# Marks a failure caused by the request's parameters; Speaker retries once without the tone on it.
BAD_PARAMETER = "InvalidParameter"

Connect = Callable[..., Any]


def instruction_for(cfg: CosyVoiceConfig, emotion: str | None) -> str | None:
    if cfg.emotion_instruct and emotion:
        emotion = EMOTION_ALIASES.get(emotion, emotion)
        if emotion in COSYVOICE_EMOTIONS:
            return f"你说话的情感是{emotion}。"
    return cfg.instruction.strip() or None


def run_task(cfg: CosyVoiceConfig, task_id: str, emotion: str | None, language: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "text_type": "PlainText",
        "voice": cfg.voice,
        "format": "pcm",
        "sample_rate": TTS_SAMPLE_RATE,
        "volume": cfg.volume,
        "rate": cfg.rate,
        "pitch": cfg.pitch,
    }
    instruction = instruction_for(cfg, emotion)
    if instruction:
        params["instruction"] = instruction
    if language:
        params["language_hints"] = [language]
    return {
        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {
            "task_group": "audio",
            "task": "tts",
            "function": "SpeechSynthesizer",
            "model": cfg.model,
            "parameters": params,
            "input": {},
        },
    }


def _action(action: str, task_id: str, text: str | None = None) -> str:
    return json.dumps(
        {
            "header": {"action": action, "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {} if text is None else {"text": text}},
        },
        ensure_ascii=False,
    )


# The engine's code for "this voice does not belong to this model" (reported as InvalidParameter).
VOICE_MISMATCH = "418"
# The model the built-in (系统) voices suggested in the console belong to.
FALLBACK_MODEL = "cosyvoice-v3-flash"
# (model, voice) pairs the service rejected as mismatched: later sentences go straight to FALLBACK_MODEL.
_mismatched: set[tuple[str, str]] = set()


class VoiceMismatch(ProviderError):
    pass


def _failure(header: dict[str, Any], cfg: CosyVoiceConfig) -> ProviderError:
    code = str(header.get("error_code") or "")
    detail = str(header.get("error_message") or "")
    if VOICE_MISMATCH in detail:
        return VoiceMismatch(
            f"CosyVoice：音色「{cfg.voice}」不能用在模型 {cfg.model} 上（418）。"
            f"系统音色请选 {FALLBACK_MODEL}；复刻/设计音色要选创建它时用的模型。"[:300]
        )
    msg = ERRORS.get(code) or detail or "未知错误"
    return ProviderError(redact(f"CosyVoice 错误 {code}：{msg}"[:300]))


class CosyVoiceTts:
    sample_rate = TTS_SAMPLE_RATE

    def __init__(self, cfg: CosyVoiceConfig, api_key: str | None, *, connect: Connect | None = None) -> None:
        if not api_key:
            raise ProviderError("尚未填写阿里云百炼 API Key")
        self._cfg = cfg
        self._key = api_key
        self._connect = connect or websockets.connect

    async def stream(
        self, text: str, *, emotion: str | None = None, language: str | None = None
    ) -> AsyncIterator[bytes]:
        """A voice that does not belong to the chosen model (418) is retried once with FALLBACK_MODEL, where
        the built-in voices live, so a wrong model choice still speaks; the log says to fix the setting."""
        cfg = self._cfg
        pair = (cfg.model, cfg.voice)
        if pair in _mismatched:
            cfg = cfg.model_copy(update={"model": FALLBACK_MODEL})
        try:
            async for chunk in self._stream_once(cfg, text, emotion, language):
                yield chunk
        except VoiceMismatch:
            # Raised before any audio (the task fails on start).
            if cfg.model == FALLBACK_MODEL:
                raise
            log.warning(
                "CosyVoice：音色 %s 不属于模型 %s（418），已改用 %s 朗读；请在「语音」页把模型改成 %s",
                cfg.voice, cfg.model, FALLBACK_MODEL, FALLBACK_MODEL,
            )
            _mismatched.add(pair)
            async for chunk in self._stream_once(cfg.model_copy(update={"model": FALLBACK_MODEL}), text, emotion, language):
                yield chunk

    async def _stream_once(
        self, cfg: CosyVoiceConfig, text: str, emotion: str | None, language: str | None
    ) -> AsyncIterator[bytes]:
        task_id = uuid.uuid4().hex
        carry = b""
        try:
            async with self._connect(
                cfg.url,
                additional_headers={"Authorization": f"bearer {self._key}"},
                open_timeout=min(cfg.timeout, 10),
                proxy=net.ws_proxy(),
            ) as ws:
                await ws.send(json.dumps(run_task(cfg, task_id, emotion, language), ensure_ascii=False))
                started = False
                while True:
                    frame = await asyncio.wait_for(ws.recv(), cfg.timeout)
                    if isinstance(frame, bytes):
                        # Keep whole 16-bit samples per yielded frame.
                        chunk = carry + frame
                        cut = len(chunk) - len(chunk) % 2
                        carry = chunk[cut:]
                        if cut:
                            yield chunk[:cut]
                        continue
                    try:
                        header = json.loads(frame).get("header") or {}
                    except (ValueError, AttributeError):
                        continue
                    event = header.get("event")
                    if event == "task-started" and not started:
                        started = True
                        await ws.send(_action("continue-task", task_id, text))
                        await ws.send(_action("finish-task", task_id))
                    elif event == "task-finished":
                        return
                    elif event == "task-failed":
                        raise _failure(header, cfg)
        except ProviderError:
            raise
        except TimeoutError:
            raise ProviderError("CosyVoice 请求超时") from None
        except InvalidStatus as e:
            status = e.response.status_code
            if status in (401, 403):
                raise ProviderError(f"鉴权失败（{status}），请检查 API Key 和接口地址") from None
            raise ProviderError(f"CosyVoice HTTP {status}") from None
        except (WebSocketException, OSError) as e:
            raise ProviderError(redact(f"无法连接 CosyVoice：{type(e).__name__}")) from None

    async def aclose(self) -> None:
        return None
