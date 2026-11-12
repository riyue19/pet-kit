"""CosyVoice (WebSocket) and OpenAI-compatible (HTTP) TTS providers, against fakes: no network."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from core.voice_config import CosyVoiceConfig, OpenAITtsConfig, TtsConfig
from providers import cosyvoice_tts
from providers.base import ProviderError
from providers.cosyvoice_tts import CosyVoiceTts, instruction_for, run_task
from providers.factory import make_tts_provider
from providers.minimax_tts import MiniMaxTts
from providers.openai_tts import OpenAICompatTts, build_request, strip_wav_header


async def collect(tts, **kw) -> list[bytes]:
    return [chunk async for chunk in tts.stream("你好", **kw)]


# --- config ---------------------------------------------------------------------------------


def test_provider_selection_and_keys():
    assert isinstance(make_tts_provider(TtsConfig(), "k"), MiniMaxTts)
    assert isinstance(make_tts_provider(TtsConfig(provider="cosyvoice"), "k"), CosyVoiceTts)
    assert isinstance(make_tts_provider(TtsConfig(provider="openai"), "k"), OpenAICompatTts)
    with pytest.raises(ValidationError):
        TtsConfig(provider="nope")


def test_urls_are_validated():
    with pytest.raises(ValidationError):
        CosyVoiceConfig(url="ws://dashscope.aliyuncs.com/api-ws/v1/inference")
    with pytest.raises(ValidationError):
        OpenAITtsConfig(base_url="http://example.com/v1")
    assert OpenAITtsConfig(base_url="http://127.0.0.1:9880/v1/").base_url == "http://127.0.0.1:9880/v1"
    with pytest.raises(ValidationError):
        OpenAITtsConfig(extra={"input": "x"})


def test_pin_endpoints_keeps_saved_addresses():
    saved = TtsConfig()
    form = TtsConfig(
        provider="openai",
        openai=OpenAITtsConfig(base_url="https://evil.example/v1", voice="nova"),
        cosyvoice=CosyVoiceConfig(url="wss://evil.example/x"),
    )
    pinned = form.pin_endpoints(saved)
    assert pinned.openai.base_url == saved.openai.base_url and pinned.openai.voice == "nova"
    assert pinned.cosyvoice.url == saved.cosyvoice.url
    assert pinned.provider == "openai"


# --- CosyVoice ------------------------------------------------------------------------------


def event(name: str, **header) -> str:
    return json.dumps({"header": {"event": name, **header}, "payload": {}})


class FakeWs:
    def __init__(self, script):
        self.script = list(script)
        self.sent: list[dict] = []

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        if not self.script:
            await asyncio.sleep(3600)
        return self.script.pop(0)


def fake_connect(ws, seen=None):
    class Ctx:
        async def __aenter__(self):
            return ws

        async def __aexit__(self, *exc):
            return False

    def connect(url, **kw):
        if seen is not None:
            seen.update(url=url, **kw)
        return Ctx()

    return connect


def test_cosyvoice_request_shape():
    cfg = CosyVoiceConfig(voice="longanhuan", emotion_instruct=True)
    params = run_task(cfg, "t1", "happy", "ja")["payload"]["parameters"]
    assert params["format"] == "pcm" and params["sample_rate"] == 24000 and params["voice"] == "longanhuan"
    assert params["instruction"] == "你说话的情感是happy。" and params["language_hints"] == ["ja"]
    assert instruction_for(cfg, "calm") == "你说话的情感是neutral。"
    # Emotions CosyVoice doesn't know, or the switch off: the fixed instruction (or none).
    assert instruction_for(cfg, "whisper") is None
    plain = run_task(CosyVoiceConfig(instruction="请用四川话表达。"), "t", "happy", None)["payload"]["parameters"]
    assert plain["instruction"] == "请用四川话表达。" and "language_hints" not in plain


def test_cosyvoice_stream_sends_text_after_task_started():
    ws = FakeWs([event("task-started"), b"\x01\x02\x03", event("result-generated"), b"\x04", event("task-finished")])
    seen: dict = {}
    tts = CosyVoiceTts(CosyVoiceConfig(), "sk-test", connect=fake_connect(ws, seen))
    chunks = asyncio.run(collect(tts))
    # Whole 16-bit samples per frame: the odd byte is carried over.
    assert chunks == [b"\x01\x02", b"\x03\x04"]
    assert [m["header"]["action"] for m in ws.sent] == ["run-task", "continue-task", "finish-task"]
    assert ws.sent[1]["payload"]["input"] == {"text": "你好"}
    assert len({m["header"]["task_id"] for m in ws.sent}) == 1
    assert seen["additional_headers"] == {"Authorization": "bearer sk-test"}
    assert seen["url"].startswith("wss://")


def test_cosyvoice_task_failed_and_timeout():
    ws = FakeWs([event("task-started"), event("task-failed", error_code="InvalidParameter", error_message="bad voice")])
    tts = CosyVoiceTts(CosyVoiceConfig(), "k", connect=fake_connect(ws))
    with pytest.raises(ProviderError, match="InvalidParameter"):
        asyncio.run(collect(tts))

    # 418 = the voice does not belong to the model: retried once with the model the built-in voices are for,
    # and later sentences skip the failing attempt.
    cosyvoice_tts._mismatched.clear()
    bad = event("task-failed", error_code="InvalidParameter", error_message="[cosyvoice:]Engine return error code: 418")
    first, second, third = FakeWs([bad]), FakeWs([event("task-started"), b"\x01\x02", event("task-finished")]), FakeWs([event("task-started"), b"\x03\x04", event("task-finished")])
    queue = [first, second, third]
    cfg = CosyVoiceConfig(model="cosyvoice-v3.5-flash", voice="longanyang")
    tts = CosyVoiceTts(cfg, "k", connect=lambda url, **kw: fake_connect(queue.pop(0))(url, **kw))
    assert asyncio.run(collect(tts)) == [b"\x01\x02"]
    assert first.sent[0]["payload"]["model"] == "cosyvoice-v3.5-flash"
    assert second.sent[0]["payload"]["model"] == "cosyvoice-v3-flash" and second.sent[0]["payload"]["parameters"]["voice"] == "longanyang"
    assert asyncio.run(collect(tts)) == [b"\x03\x04"] and third.sent[0]["payload"]["model"] == "cosyvoice-v3-flash"
    # Already on that model: a readable error, without the marker that makes Speaker drop the tone and retry.
    cosyvoice_tts._mismatched.clear()
    with pytest.raises(ProviderError, match="418") as err:
        asyncio.run(collect(CosyVoiceTts(CosyVoiceConfig(voice="nope"), "k", connect=fake_connect(FakeWs([bad])))))
    assert "InvalidParameter" not in str(err.value) and "nope" in str(err.value)

    silent = CosyVoiceTts(CosyVoiceConfig(timeout=0.05), "k", connect=fake_connect(FakeWs([])))
    with pytest.raises(ProviderError, match="超时"):
        asyncio.run(collect(silent))

    with pytest.raises(ProviderError):
        CosyVoiceTts(CosyVoiceConfig(), None)


# --- OpenAI-compatible ----------------------------------------------------------------------


def test_openai_request_and_pcm_stream():
    got: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        got["url"] = str(request.url)
        got["auth"] = request.headers.get("authorization")
        got["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"\x01\x02\x03\x04\x05", headers={"content-type": "audio/pcm"})

    cfg = OpenAITtsConfig(base_url="https://tts.example/v1", voice="nova", extra={"sample_rate": 24000})
    tts = OpenAICompatTts(cfg, "sk-x", transport=httpx.MockTransport(handler))
    assert b"".join(asyncio.run(collect(tts))) == b"\x01\x02\x03\x04"
    assert got["url"] == "https://tts.example/v1/audio/speech" and got["auth"] == "Bearer sk-x"
    assert got["body"] == {"sample_rate": 24000, "model": cfg.model, "input": "你好", "voice": "nova", "response_format": "pcm", "speed": 1.0}
    assert build_request(cfg, "x")["input"] == "x"
    assert tts.sample_rate == 24000


def test_openai_wav_header_is_skipped():
    wav = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x10\x00\x00\x00" + b"\x00" * 16 + b"data\x04\x00\x00\x00" + b"\x0a\x0b\x0c\x0d"
    assert strip_wav_header(wav) == b"\x0a\x0b\x0c\x0d"
    assert strip_wav_header(wav[:20]) is None
    assert strip_wav_header(b"\x01\x02") == b"\x01\x02"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    cfg = OpenAITtsConfig(response_format="wav", sample_rate=32000)
    tts = OpenAICompatTts(cfg, "k", transport=httpx.MockTransport(handler))
    assert b"".join(asyncio.run(collect(tts))) == b"\x0a\x0b\x0c\x0d" and tts.sample_rate == 32000


def test_openai_errors_and_key_rules():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "nope"})

    tts = OpenAICompatTts(OpenAITtsConfig(), "k", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="鉴权失败"):
        asyncio.run(collect(tts))

    def json_ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "model not found"})

    tts = OpenAICompatTts(OpenAITtsConfig(), "k", transport=httpx.MockTransport(json_ok))
    with pytest.raises(ProviderError, match="没有返回音频"):
        asyncio.run(collect(tts))

    with pytest.raises(ProviderError):
        OpenAICompatTts(OpenAITtsConfig(), None)
    # A local server needs no key.
    OpenAICompatTts(OpenAITtsConfig(base_url="http://127.0.0.1:9880/v1"), None)
