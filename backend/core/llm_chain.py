"""Runs one chat call down a role's profile chain (primary, 2nd, 3rd) until one answers."""

import asyncio
import logging
import time

from core.credentials import KeyStore
from core.llm_config import LlmProfile
from providers.base import ChatResult, Message, ProviderError
from providers.factory import TextProviderFactory

log = logging.getLogger("llm.chain")

# After a profile fails it goes to the back of the line for this long, so a dead primary
# doesn't cost a full timeout on every message. It is still tried last (never skipped outright).
COOLDOWN_SECONDS = 60.0

# Keyed by (role, profile name): a model that can't do images shouldn't be demoted for chat.
_failed_at: dict[tuple[str, str], float] = {}


def _order(chain: list[LlmProfile], now: float, role: str) -> list[LlmProfile]:
    healthy = [p for p in chain if now - _failed_at.get((role, p.name), -COOLDOWN_SECONDS) >= COOLDOWN_SECONDS]
    cooling = [p for p in chain if p not in healthy]
    return healthy + cooling


def reset_cooldowns() -> None:
    _failed_at.clear()


async def chat_with_fallback(
    chain: list[LlmProfile],
    keys: KeyStore,
    factory: TextProviderFactory,
    messages: list[Message],
    *,
    web_search: bool = False,
    role: str = "chat",
    log_history: int | None = None,
) -> tuple[ChatResult, LlmProfile]:
    """Returns the result and the profile that produced it; raises the last error if every profile fails.

    `web_search` is a wish: it is only passed to profiles whose own web_search isn't "off".
    """
    if not chain:
        raise ProviderError("没有可用的 LLM 配置")
    last: Exception | None = None
    ordered = _order(chain, time.monotonic(), role)
    for i, profile in enumerate(ordered):
        try:
            key = await asyncio.to_thread(keys.get, profile.name)
            provider = factory(profile, key)
        except Exception as e:  # noqa: BLE001
            log.exception("无法创建 LLM 客户端（配置 %s）", profile.name)
            _failed_at[(role, profile.name)] = time.monotonic()
            last = ProviderError("无法创建 LLM 客户端，请检查配置") if not isinstance(e, ProviderError) else e
            continue
        log.info(
            "调用 LLM（%s）：配置 %s，模型 %s%s%s",
            role,
            profile.name,
            profile.model,
            f"，附带历史 {log_history} 条" if log_history is not None else "",
            f"（备选 {i}）" if i else "",
        )
        try:
            result = await provider.chat(messages, web_search=web_search and profile.web_search != "off")
        except Exception as e:  # noqa: BLE001
            if isinstance(e, ProviderError):
                log.warning("LLM 调用失败（配置 %s）：%s", profile.name, e)
                last = e
            else:
                log.exception("LLM 调用失败（配置 %s）", profile.name)
                last = ProviderError("LLM 调用失败")
            _failed_at[(role, profile.name)] = time.monotonic()
            if i + 1 < len(ordered):
                log.warning("切换到备选配置 %s", ordered[i + 1].name)
            continue
        finally:
            try:
                await provider.aclose()
            except Exception:  # noqa: BLE001
                log.warning("关闭 LLM 客户端失败（配置 %s）", profile.name, exc_info=True)
        _failed_at.pop((role, profile.name), None)
        return result, profile
    assert last is not None
    raise last
