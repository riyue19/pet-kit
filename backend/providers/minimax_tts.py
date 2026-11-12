"""MiniMax T2A v2 (https://platform.minimaxi.com/docs/api-reference/speech-t2a-http), streamed as raw PCM."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from core import net
from core.redact import redact
from core.voice_config import TTS_SAMPLE_RATE, MiniMaxConfig
from providers.base import ProviderError

log = logging.getLogger(__name__)

ERRORS = {
    1001: "请求超时",
    1002: "触发限流，请稍后再试",
    1004: "鉴权失败，请检查 API Key",
    1008: "账户余额不足",
    1039: "触发 TPM 限流，请稍后再试",
    1042: "文本中非法字符过多",
    2013: "参数有误（检查音色 ID / 模型）",
}


LANGUAGE_BOOST = {"ja": "Japanese", "en": "English", "zh": "Chinese"}


def build_request(
    cfg: MiniMaxConfig, text: str, emotion: str | None, language: str | None = None
) -> dict[str, Any]:
    voice: dict[str, Any] = {"voice_id": cfg.voice_id, "speed": cfg.speed, "vol": cfg.vol, "pitch": cfg.pitch}
    if emotion:
        voice["emotion"] = emotion
    return {
        "model": cfg.model,
        "text": text,
        "stream": True,
        # Without this the last chunk repeats the whole utterance.
        "stream_options": {"exclude_aggregated_audio": True},
        "voice_setting": voice,
        "audio_setting": {"sample_rate": TTS_SAMPLE_RATE, "format": "pcm", "channel": 1},
        "language_boost": LANGUAGE_BOOST.get(language or "", "auto"),
    }


def check_base_resp(data: dict[str, Any]) -> None:
    base = data.get("base_resp") or {}
    code = base.get("status_code", 0)
    if code:
        msg = ERRORS.get(code) or str(base.get("status_msg") or "未知错误")
        raise ProviderError(redact(f"MiniMax 错误 {code}：{msg}"))


def audio_from_event(line: str) -> bytes | None:
    """PCM bytes carried by one SSE line, or None for non-data lines / the final summary chunk."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    try:
        data = json.loads(line[5:])
    except ValueError:
        log.debug("跳过无法解析的 SSE 行：%s", redact(line[:200]))
        return None
    if not isinstance(data, dict):
        return None
    check_base_resp(data)
    payload = data.get("data") or {}
    hex_audio = payload.get("audio") if isinstance(payload, dict) else None
    return bytes.fromhex(hex_audio) if hex_audio else None


class MiniMaxTts:
    sample_rate = TTS_SAMPLE_RATE

    def __init__(
        self, cfg: MiniMaxConfig, api_key: str | None, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not api_key:
            raise ProviderError("尚未填写 MiniMax API Key")
        self._cfg = cfg
        self._client = net.make_client(
            base_url=cfg.base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(cfg.timeout, connect=10),
            transport=transport,
        )

    async def stream(
        self, text: str, *, emotion: str | None = None, language: str | None = None
    ) -> AsyncIterator[bytes]:
        carry = b""
        try:
            async with self._client.stream("POST", "/v1/t2a_v2", json=build_request(self._cfg, text, emotion, language)) as r:
                if r.status_code == 401:
                    raise ProviderError("鉴权失败（401），请检查 API Key")
                if r.status_code >= 400:
                    raise ProviderError(f"MiniMax HTTP {r.status_code}")
                if "text/event-stream" not in r.headers.get("content-type", ""):
                    # Errors come back as a plain JSON body with HTTP 200.
                    body = await r.aread()
                    log.warning("MiniMax 返回非流式响应（HTTP %s）：%s", r.status_code, redact(body[:300].decode("utf-8", "replace")))
                    try:
                        check_base_resp(json.loads(body))
                    except ValueError:
                        pass
                    raise ProviderError("MiniMax 返回了非流式响应")
                async for line in r.aiter_lines():
                    chunk = audio_from_event(line)
                    if not chunk:
                        continue
                    # Keep whole 16-bit samples per yielded frame.
                    chunk = carry + chunk
                    cut = len(chunk) - len(chunk) % 2
                    carry = chunk[cut:]
                    if cut:
                        yield chunk[:cut]
        except httpx.TimeoutException:
            raise ProviderError("MiniMax 请求超时") from None
        except httpx.HTTPError as e:
            raise ProviderError(redact(f"无法连接 MiniMax：{type(e).__name__}")) from None

    async def list_voices(self) -> list[dict[str, str]]:
        try:
            r = await self._client.post("/v1/get_voice", json={"voice_type": "all"})
        except httpx.HTTPError as e:
            raise ProviderError(redact(f"无法连接 MiniMax：{type(e).__name__}")) from None
        if r.status_code == 401:
            raise ProviderError("鉴权失败（401），请检查 API Key")
        try:
            data = r.json()
        except ValueError:
            raise ProviderError(f"MiniMax HTTP {r.status_code}") from None
        check_base_resp(data)
        voices = []
        for kind in ("voice_cloning", "voice_generation", "system_voice"):
            for v in data.get(kind) or []:
                vid = v.get("voice_id")
                if isinstance(vid, str) and vid:
                    voices.append({"voice_id": vid, "name": str(v.get("voice_name") or vid), "kind": kind})
        return voices

    async def aclose(self) -> None:
        await self._client.aclose()
