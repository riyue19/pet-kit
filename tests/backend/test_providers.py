import logging

from core.credentials import KeyStore
from core.llm_config import LlmProfile
from core.redact import MASK, RedactingFilter, redact
from providers.base import user_text
from providers.openai_compat import build_request


def test_build_request_omits_nulls_and_routes_extra_to_body():
    p = LlmProfile(
        name="a",
        base_url="http://x",
        model="m",
        max_tokens=None,
        temperature=0.5,
        top_p=0.9,
        extra={"seed": 3, "response_format": {"type": "json_object"}},
    )
    req = build_request(p, [user_text("hi")])
    assert req["model"] == "m"
    assert req["messages"] == [{"role": "user", "content": "hi"}]
    assert req["temperature"] == 0.5 and req["top_p"] == 0.9
    assert "max_tokens" not in req and "presence_penalty" not in req
    assert req["extra_body"] == {"seed": 3, "response_format": {"type": "json_object"}}


def test_build_request_without_extra_has_no_extra_body():
    p = LlmProfile(name="a", base_url="http://x", model="m")
    assert "extra_body" not in build_request(p, [])


def test_redact_known_keys_and_patterns():
    KeyStore().set("p", "custom-secret-value-123")
    text = redact("key=custom-secret-value-123 other=sk-abcdefghijklmnop Authorization: Bearer abc.def-ghi_jkl")
    assert "custom-secret-value-123" not in text
    assert "sk-abcdefghijklmnop" not in text
    assert "abc.def-ghi_jkl" not in text
    assert text.count(MASK) == 3


def test_log_filter_redacts_formatted_args():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token %s", ("sk-1234567890abcdef",), None)
    RedactingFilter().filter(record)
    assert "sk-1234567890abcdef" not in record.getMessage()


def test_key_status_hint_and_remap(memory_keyring):
    keys = KeyStore()
    assert keys.status("a") == {"set": False, "hint": None}
    keys.set("a", "  sk-test-1234567890wxyz  ")
    assert keys.status("a") == {"set": True, "hint": "…wxyz"}
    keys.set("short", "abcd1234")
    assert keys.status("short") == {"set": True, "hint": None}
    assert keys.remap({"b": "a", "short": "short"}, {"a", "short"}) == []
    assert keys.get("a") is None and keys.get("b") == "sk-test-1234567890wxyz"
    assert keys.get("short") == "abcd1234"
    keys.delete("missing")


def test_build_request_web_search_variants():
    from core.llm_config import LlmProfile
    from providers.openai_compat import GOOGLE_SEARCH_TOOL, build_request

    msgs = [{"role": "user", "content": "hi"}]
    g = LlmProfile(name="g", base_url="https://x.example/v1", model="m", web_search="google_function", extra={"a": 1})
    assert build_request(g, msgs, web_search=True)["tools"] == [GOOGLE_SEARCH_TOOL]
    assert "tools" not in build_request(g, msgs)  # only when asked (screen looks never search)
    o = LlmProfile(name="o", base_url="https://x.example/v1", model="m", web_search="openai_options")
    assert build_request(o, msgs, web_search=True)["extra_body"] == {"web_search_options": {}}
    off = LlmProfile(name="f", base_url="https://x.example/v1", model="m")
    req = build_request(off, msgs, web_search=True)
    assert "tools" not in req and "extra_body" not in req


def test_build_request_stream_flag():
    from core.llm_config import LlmProfile
    from providers.openai_compat import build_request

    msgs = [{"role": "user", "content": "hi"}]
    off = LlmProfile(name="a", base_url="https://x.example/v1", model="m")
    on = LlmProfile(name="b", base_url="https://x.example/v1", model="m", stream=True)
    assert "stream" not in build_request(off, msgs)
    assert build_request(on, msgs)["stream"] is True


def _stream_provider(chunks, fail_after=None):
    from types import SimpleNamespace as NS

    from core.llm_config import LlmProfile
    from providers.openai_compat import OpenAICompatProvider

    async def gen():
        for i, c in enumerate(chunks):
            if fail_after is not None and i == fail_after:
                raise RuntimeError("boom")
            yield c

    async def create(**kwargs):
        assert kwargs["stream"] is True
        return gen()

    p = OpenAICompatProvider(LlmProfile(name="s", base_url="https://x.example/v1", model="m", stream=True), "k")
    p._client = NS(chat=NS(completions=NS(create=create)))
    return p


def _chunk(content, model=None):
    from types import SimpleNamespace as NS

    return NS(model=model, choices=[NS(delta=NS(content=content, annotations=None, model_extra=None))])


def test_stream_chat_joins_chunks():
    import asyncio
    from types import SimpleNamespace as NS

    chunks = [
        NS(model="", choices=[]),
        _chunk("你", "real-model"),
        _chunk(None),
        _chunk("好"),
        _chunk("！"),
    ]
    r = asyncio.run(_stream_provider(chunks).chat([{"role": "user", "content": "hi"}]))
    assert r.text == "你好！" and r.model == "real-model"


def test_stream_chat_error_becomes_provider_error():
    import asyncio

    import pytest
    from providers.base import ProviderError

    p = _stream_provider([_chunk("a"), _chunk("b")], fail_after=1)
    with pytest.raises(ProviderError):
        asyncio.run(p.chat([{"role": "user", "content": "hi"}]))
