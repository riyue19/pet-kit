import time
from typing import Any

import openai
from openai import AsyncOpenAI

from core import net
from core.llm_config import LlmProfile
from core.redact import redact
from providers.base import ChatResult, Message, ProviderError

_OPTIONAL_PARAMS = ("max_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty")
NO_KEY_PLACEHOLDER = "EMPTY"


GOOGLE_SEARCH_TOOL = {
    "type": "function",
    "function": {"name": "googleSearch", "description": "", "parameters": {"type": "object", "properties": {}}},
}


def build_request(profile: LlmProfile, messages: list[Message], *, web_search: bool = False) -> dict[str, Any]:
    """Keyword arguments for chat.completions.create; `extra` goes through extra_body untouched."""
    kwargs: dict[str, Any] = {"model": profile.model, "messages": messages}
    for name in _OPTIONAL_PARAMS:
        value = getattr(profile, name)
        if value is not None:
            kwargs[name] = value
    if profile.stream:
        kwargs["stream"] = True
    extra_body = dict(profile.extra)
    if web_search and profile.web_search == "google_function":
        kwargs["tools"] = [GOOGLE_SEARCH_TOOL]
    elif web_search and profile.web_search == "openai_options":
        extra_body["web_search_options"] = {}
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def _describe(e: Exception) -> str:
    if isinstance(e, openai.APITimeoutError):
        return "请求超时"
    if isinstance(e, openai.APIConnectionError):
        return "无法连接到服务地址"
    if isinstance(e, openai.AuthenticationError):
        return "鉴权失败（401），请检查 API Key"
    if isinstance(e, openai.APIStatusError):
        return f"HTTP {e.status_code}：{e.message}"
    return f"{type(e).__name__}: {e}"


class OpenAICompatProvider:
    """Any endpoint speaking the OpenAI Chat Completions protocol (OpenAI, DeepSeek, Qwen, Ollama, ...)."""

    def __init__(self, profile: LlmProfile, api_key: str | None) -> None:
        self._profile = profile
        self._client = AsyncOpenAI(
            base_url=profile.base_url,
            api_key=api_key or NO_KEY_PLACEHOLDER,
            timeout=profile.timeout,
            max_retries=0,
            http_client=openai.DefaultAsyncHttpxClient(**net.client_kwargs()),
        )

    async def chat(self, messages: list[Message], *, web_search: bool = False) -> ChatResult:
        start = time.perf_counter()
        if self._profile.stream:
            return await self._chat_stream(messages, web_search, start)
        try:
            resp = await self._client.chat.completions.create(
                **build_request(self._profile, messages, web_search=web_search)
            )
        except Exception as e:
            raise ProviderError(redact(_describe(e))) from None
        latency_ms = round((time.perf_counter() - start) * 1000)
        if not resp.choices:
            raise ProviderError("响应中没有 choices")
        msg = resp.choices[0].message
        text = msg.content or ""
        # Relays report grounding sources as OpenAI-style annotations (url citations).
        annotations = getattr(msg, "annotations", None) or (getattr(msg, "model_extra", None) or {}).get("annotations") or []
        return ChatResult(text=text, model=resp.model, latency_ms=latency_ms, citations=len(annotations))

    async def _chat_stream(self, messages: list[Message], web_search: bool, start: float) -> ChatResult:
        parts: list[str] = []
        model: str | None = None
        citations = 0
        saw_choices = False
        try:
            stream = await self._client.chat.completions.create(
                **build_request(self._profile, messages, web_search=web_search)
            )
            async for chunk in stream:
                if not model and getattr(chunk, "model", None):
                    model = chunk.model
                if not chunk.choices:
                    continue
                saw_choices = True
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    parts.append(delta.content)
                annotations = (
                    getattr(delta, "annotations", None)
                    or (getattr(delta, "model_extra", None) or {}).get("annotations")
                    or []
                )
                citations += len(annotations)
        except Exception as e:
            raise ProviderError(redact(_describe(e))) from None
        latency_ms = round((time.perf_counter() - start) * 1000)
        text = "".join(parts)
        if not saw_choices and not text:
            raise ProviderError("响应中没有 choices")
        return ChatResult(text=text, model=model or self._profile.model, latency_ms=latency_ms, citations=citations)

    async def aclose(self) -> None:
        await self._client.close()
