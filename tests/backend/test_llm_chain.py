import asyncio
import functools

import pytest

from core import llm_chain
from core.credentials import KeyStore
from core.llm_chain import chat_with_fallback
from core.llm_config import ConfigError, LlmProfile, parse_backend_config
from providers.base import ChatResult, ProviderError


def profile(name: str, web_search: str = "off") -> LlmProfile:
    return LlmProfile(name=name, base_url="https://x.example/v1", model=f"m-{name}", web_search=web_search)


class Script:
    """Per-profile scripted behaviour: name -> list of replies/exceptions."""

    def __init__(self, plan: dict):
        self.plan = plan
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, prof, key):
        script = self

        class P:
            async def chat(self, messages, *, web_search=False):
                script.calls.append((prof.name, web_search))
                r = script.plan[prof.name]
                if isinstance(r, Exception):
                    raise r
                return ChatResult(text=r, model=prof.model, latency_ms=1)

            async def aclose(self):
                pass

        return P()


def sync(fn):
    @functools.wraps(fn)
    def run_it():
        asyncio.run(fn())

    return run_it


@pytest.fixture(autouse=True)
def _reset():
    llm_chain.reset_cooldowns()


async def run(chain, plan, **kw):
    s = Script(plan)
    out = await chat_with_fallback(chain, KeyStore(), s, [], **kw)
    return out, s


@sync
async def test_primary_success_does_not_touch_backups():
    (res, used), s = await run([profile("a"), profile("b")], {"a": "A", "b": "B"})
    assert (res.text, used.name) == ("A", "a") and [c[0] for c in s.calls] == ["a"]


@sync
async def test_falls_through_to_second_then_third():
    chain = [profile("a"), profile("b"), profile("c")]
    (res, used), s = await run(chain, {"a": ProviderError("x"), "b": RuntimeError("boom"), "c": "C"})
    assert (res.text, used.name) == ("C", "c") and [c[0] for c in s.calls] == ["a", "b", "c"]


@sync
async def test_all_fail_raises_last_error():
    chain = [profile("a"), profile("b")]
    with pytest.raises(ProviderError, match="second"):
        await run(chain, {"a": ProviderError("first"), "b": ProviderError("second")})


@sync
async def test_failed_primary_goes_to_back_during_cooldown():
    chain = [profile("a"), profile("b")]
    s = Script({"a": ProviderError("x"), "b": "B"})
    await chat_with_fallback(chain, KeyStore(), s, [])
    s.calls.clear()
    await chat_with_fallback(chain, KeyStore(), s, [])
    assert [c[0] for c in s.calls] == ["b"]  # a is cooling down, b answers first


@sync
async def test_cooling_profiles_are_still_tried_last():
    chain = [profile("a"), profile("b")]
    s = Script({"a": ProviderError("x"), "b": ProviderError("y")})
    with pytest.raises(ProviderError):
        await chat_with_fallback(chain, KeyStore(), s, [])
    s.calls.clear()
    s.plan["a"] = "A"
    res, used = await chat_with_fallback(chain, KeyStore(), s, [])
    # Both are cooling down, so the configured order applies and nothing is skipped outright.
    assert used.name == "a" and [c[0] for c in s.calls] == ["a"]


@sync
async def test_web_search_only_for_profiles_that_support_it():
    chain = [profile("a", "google_function"), profile("b", "off")]
    s = Script({"a": ProviderError("x"), "b": "B"})
    await chat_with_fallback(chain, KeyStore(), s, [], web_search=True)
    assert s.calls == [("a", True), ("b", False)]


@sync
async def test_empty_chain_is_an_error():
    with pytest.raises(ProviderError):
        await chat_with_fallback([], KeyStore(), Script({}), [])


# --- config ---------------------------------------------------------------------------------

BASE = """llm:
  profiles:
    - {name: a, base_url: "https://x.example/v1", model: m}
    - {name: b, base_url: "https://x.example/v1", model: m}
    - {name: c, base_url: "https://x.example/v1", model: m}
    - {name: d, base_url: "https://x.example/v1", model: m}
  roles: {chat: a, memory: null}
"""


def test_chain_order_and_memory_follows_chat():
    cfg = parse_backend_config(BASE + "  fallbacks: {chat: [c, b]}\n").llm
    assert [p.name for p in cfg.chain("chat")] == ["a", "c", "b"]
    assert [p.name for p in cfg.chain("memory")] == ["a", "c", "b"]
    assert cfg.chain("vision") == []


def test_memory_with_own_profile_uses_own_fallbacks():
    cfg = parse_backend_config(BASE.replace("memory: null", "memory: d") + "  fallbacks: {chat: [b], memory: [c]}\n").llm
    assert [p.name for p in cfg.chain("memory")] == ["d", "c"]


@pytest.mark.parametrize(
    ("extra", "msg"),
    [
        ("  fallbacks: {chat: [b, c, d]}\n", "fallbacks.chat"),
        ("  fallbacks: {chat: [zzz]}\n", "不存在"),
        ("  fallbacks: {chat: [a]}\n", "不能"),
        ("  fallbacks: {chat: [b, b]}\n", "不能"),
        ("  fallbacks: {vision: [b]}\n", "主用"),
    ],
)
def test_fallback_validation(extra, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_backend_config(BASE + extra)
