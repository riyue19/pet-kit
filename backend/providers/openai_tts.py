"""Any OpenAI-compatible text-to-speech service: POST {base_url}/audio/speech, body streamed as PCM.

Covers OpenAI itself, relays, SiliconFlow and local servers that copy the endpoint. The response must be
16-bit mono PCM at the configured sample rate (optionally wrapped in a WAV header, which is skipped).
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from core import net
from core.redact import redact
from core.voice_config import OpenAITtsConfig
from providers.base import ProviderError

log = logging.getLogger(__name__)

# A WAV header is normally 44 bytes; allow for extra chunks (LIST etc.) before "data".
WAV_HEADER_SCAN = 4096


def build_request(cfg: OpenAITtsConfig, text: str) -> dict[str, Any]:
    return {
        **cfg.extra,
        "model": cfg.model,
        "input": text,
        "voice": cfg.voice,
        "response_format": cfg.response_format,
        "speed": cfg.speed,
    }


def strip_wav_header(head: bytes) -> bytes | None:
    """PCM that follows the header in `head`; None while more bytes are needed to find the data chunk."""
    if not head.startswith(b"RIFF"):
        return head  # not a WAV after all: treat as raw PCM
    at = head.find(b"data", 12)
    if at < 0 or len(head) < at + 8:
        return head[WAV_HEADER_SCAN:] if len(head) > WAV_HEADER_SCAN + 8 else None
    return head[at + 8 :]


class OpenAICompatTts:
    def __init__(
        self, cfg: OpenAITtsConfig, api_key: str | None, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._cfg = cfg
        self.sample_rate = cfg.sample_rate
        headers = {"Content-Type": "application/json"}
        # Local servers usually need no key.
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif not cfg.base_url.startswith("http://"):
            raise ProviderError("尚未填写语音服务的 API Key")
        self._client = net.make_client(
            base_url=cfg.base_url,
            headers=headers,
            timeout=httpx.Timeout(cfg.timeout, connect=10),
            transport=transport,
        )

    async def stream(
        self, text: str, *, emotion: str | None = None, language: str | None = None
    ) -> AsyncIterator[bytes]:
        carry = b""
        head: bytes | None = b"" if self._cfg.response_format == "wav" else None
        try:
            async with self._client.stream("POST", "/audio/speech", json=build_request(self._cfg, text)) as r:
                if r.status_code in (401, 403):
                    raise ProviderError(f"鉴权失败（{r.status_code}），请检查 API Key")
                if r.status_code >= 400:
                    body = (await r.aread())[:300].decode("utf-8", "replace")
                    log.warning("语音服务返回 HTTP %s：%s", r.status_code, redact(body))
                    raise ProviderError(redact(f"语音服务 HTTP {r.status_code}：{body[:120]}"))
                if "json" in r.headers.get("content-type", ""):
                    body = (await r.aread())[:300].decode("utf-8", "replace")
                    raise ProviderError(redact(f"语音服务没有返回音频：{body[:120]}"))
                async for chunk in r.aiter_bytes():
                    if head is not None:
                        head += chunk
                        pcm = strip_wav_header(head)
                        if pcm is None:
                            continue
                        chunk, head = pcm, None
                    # Keep whole 16-bit samples per yielded frame.
                    chunk = carry + chunk
                    cut = len(chunk) - len(chunk) % 2
                    carry = chunk[cut:]
                    if cut:
                        yield chunk[:cut]
        except httpx.TimeoutException:
            raise ProviderError("语音服务请求超时") from None
        except httpx.HTTPError as e:
            raise ProviderError(redact(f"无法连接语音服务：{type(e).__name__}")) from None

    async def aclose(self) -> None:
        await self._client.aclose()
