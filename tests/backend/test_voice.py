import asyncio
import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

import api.pet_ws as pet_ws
from action.tts import Speaker, speakable
from api.app import create_app
from core.llm_config import load_backend_config, save_sections
from core.voice import VoiceState, is_echo
from core.voice_config import AsrConfig, MiniMaxConfig, TtsConfig
from perception.asr import AsrEngine, VoiceListener, meaningful
from providers.base import ChatResult, ProviderError
from providers.minimax_tts import MiniMaxTts, audio_from_event, build_request

TOKEN = "test-token"

# --- text helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("（歪头）好呀！", "好呀！"),
        ("嗯*眨眼*，(laughs)知道啦【小声】", "嗯，知道啦"),
        ("（后端未连接：在 backend 目录运行 uv run python -m api）", ""),
        ("……", ""),
        ("", ""),
    ],
)
def test_speakable(text, spoken):
    assert speakable(text) == spoken


def test_is_echo():
    spoken = "今天天气真不错，我们一起去公园散步吧。"
    assert is_echo("我们一起去公园散步", spoken)
    assert is_echo("今天天气真不错", spoken)
    assert not is_echo("帮我打开音乐", spoken)
    assert not is_echo("嗯", "")


def test_meaningful():
    assert meaningful("你好") and meaningful("ok")
    assert not meaningful("。") and not meaningful("  ")


def test_emotion_mapping_and_validation():
    cfg = TtsConfig(emotions={"confused": "surprised"})
    assert cfg.emotion_for("happy") == "happy"
    assert cfg.emotion_for("confused") == "surprised"
    assert cfg.emotion_for("blush") is None and cfg.emotion_for(None) is None
    with pytest.raises(ValueError):
        TtsConfig(emotions={"Bad-Label": "happy"})
    with pytest.raises(ValueError):
        TtsConfig(emotions={"ok": "giggle"})
    with pytest.raises(ValueError):
        MiniMaxConfig(base_url="http://api.minimax.cn")


def test_save_sections_keeps_other_sections(tmp_path):
    path = tmp_path / "backend.yaml"
    path.write_text("# head comment\nserver:\n  port: 9000 # keep me\nllm:\n  profiles: []\n", encoding="utf-8")
    save_sections(path, {"asr": AsrConfig(listen=True), "tts": TtsConfig(enabled=False)})
    text = path.read_text(encoding="utf-8")
    assert "# head comment" in text and "# keep me" in text
    cfg = load_backend_config(path)
    assert cfg.server.port == 9000 and cfg.asr.listen is True and cfg.tts.enabled is False


# --- MiniMax --------------------------------------------------------------------------------


def test_minimax_request_shape():
    body = build_request(MiniMaxConfig(voice_id="v1"), "你好", "happy")
    assert body["stream"] is True and body["stream_options"] == {"exclude_aggregated_audio": True}
    assert body["voice_setting"]["voice_id"] == "v1" and body["voice_setting"]["emotion"] == "happy"
    assert body["audio_setting"] == {"sample_rate": 24000, "format": "pcm", "channel": 1}
    assert "emotion" not in build_request(MiniMaxConfig(), "x", None)["voice_setting"]


def test_audio_from_event():
    ok = {"data": {"audio": "0102", "status": 1}, "base_resp": {"status_code": 0}}
    assert audio_from_event("data: " + json.dumps(ok)) == b"\x01\x02"
    assert audio_from_event("event: ping") is None
    assert audio_from_event('data: {"data": {"audio": "", "status": 2}}') is None
    bad = {"base_resp": {"status_code": 1004, "status_msg": "auth"}}
    with pytest.raises(ProviderError, match="鉴权"):
        audio_from_event("data: " + json.dumps(bad))


def _sse(*hex_chunks: str) -> bytes:
    lines = [
        "data: " + json.dumps({"data": {"audio": h, "status": 1}, "base_resp": {"status_code": 0}}) + "\n\n"
        for h in hex_chunks
    ]
    lines.append("data: " + json.dumps({"data": {"audio": "", "status": 2}, "base_resp": {"status_code": 0}}) + "\n\n")
    return "".join(lines).encode()


async def _collect(tts: MiniMaxTts) -> list[bytes]:
    try:
        return [c async for c in tts.stream("你好")]
    finally:
        await tts.aclose()


def test_minimax_stream_keeps_whole_samples():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse("010203", "0405", "06"))

    tts = MiniMaxTts(MiniMaxConfig(), "sk-test", transport=httpx.MockTransport(handler))
    chunks = asyncio.run(_collect(tts))
    assert all(len(c) % 2 == 0 for c in chunks)
    assert b"".join(chunks) == bytes([1, 2, 3, 4, 5, 6])
    assert seen["auth"] == "Bearer sk-test" and seen["body"]["text"] == "你好"


def test_minimax_json_error_body():
    def handler(_request):
        return httpx.Response(200, json={"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}})

    tts = MiniMaxTts(MiniMaxConfig(), "sk-test", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="余额"):
        asyncio.run(_collect(tts))


def test_minimax_requires_key():
    with pytest.raises(ProviderError):
        MiniMaxTts(MiniMaxConfig(), None)


# --- Speaker --------------------------------------------------------------------------------


class FakeTts:
    sample_rate = 24000
    gate: asyncio.Event | None = None
    fail: str | None = None
    calls: list = []

    def __init__(self, cfg, key):
        if key is None:
            raise ProviderError("尚未填写 MiniMax API Key")

    async def stream(self, text, *, emotion=None, language=None):
        FakeTts.calls.append((text, emotion))
        if FakeTts.fail:
            raise ProviderError(FakeTts.fail)
        yield b"\x00\x01" * 4
        if FakeTts.gate is not None:
            await FakeTts.gate.wait()
        yield b"\x00\x02" * 4

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def reset_fake_tts():
    FakeTts.gate, FakeTts.fail, FakeTts.calls = None, None, []


def make_speaker(sent, *, enabled=True, key="k"):
    async def send_json(m):
        sent.append(m)

    async def send_bytes(b):
        sent.append(b)

    return Speaker(
        config=lambda: TtsConfig(enabled=enabled),
        api_key=lambda _provider: key,
        factory=FakeTts,
        send_json=send_json,
        send_bytes=send_bytes,
    )


def test_speaker_streams_and_maps_emotion():
    sent: list = []

    async def run():
        sp = make_speaker(sent)
        task = await sp.speak("（笑）好呀", "happy")
        return await task

    result = asyncio.run(run())
    assert result.ok and result.first_audio_ms is not None
    assert sent[0] == {"type": "audio_start", "id": 1, "sample_rate": 24000}
    assert sent[1:3] == [b"\x00\x01" * 4, b"\x00\x02" * 4]
    assert sent[3] == {"type": "audio_end", "id": 1}
    assert FakeTts.calls == [("好呀", "happy")]


def test_speaker_skips_unspeakable_and_disabled():
    async def run():
        assert await make_speaker([]).speak("（系统提示）") is None
        assert await make_speaker([], enabled=False).speak("你好") is None

    asyncio.run(run())


def test_speaker_interrupt_and_replace():
    sent: list = []

    async def run():
        FakeTts.gate = asyncio.Event()
        sp = make_speaker(sent)
        first = await sp.speak("第一句")
        for _ in range(200):  # wait until the first utterance is actually streaming
            if b"\x00\x01" * 4 in sent:
                break
            await asyncio.sleep(0.005)
        assert sp.active
        second = await sp.speak("第二句")  # cancels the first
        r1 = await asyncio.gather(first, return_exceptions=True)
        FakeTts.gate.set()
        r2 = await second
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert isinstance(r1[0], asyncio.CancelledError) and r2.ok
    ends = [m for m in sent if isinstance(m, dict) and m["type"] == "audio_end"]
    assert ends[0] == {"type": "audio_end", "id": 1, "error": "interrupted"}
    assert ends[1] == {"type": "audio_end", "id": 2}


def test_speaker_reports_provider_errors():
    sent: list = []
    FakeTts.fail = "MiniMax 错误 1004：鉴权失败，请检查 API Key"

    async def run():
        return await (await make_speaker(sent).speak("你好"))

    result = asyncio.run(run())
    assert not result.ok and "1004" in result.error
    assert sent[-1]["type"] == "audio_end" and "1004" in sent[-1]["error"]


def test_speaker_without_key_sends_nothing():
    sent: list = []

    async def run():
        return await (await make_speaker(sent, key=None).speak("你好"))

    result = asyncio.run(run())
    assert not result.ok and "Key" in result.error and sent == []


# --- VoiceListener --------------------------------------------------------------------------

WIN = 512


class FakeVad:
    """Speech = any window with amplitude > 0.1; a segment ends after 2 quiet windows."""

    window = WIN

    def __init__(self, *_args, **_kwargs):
        self._speech: list[np.ndarray] = []
        self._quiet = 0
        self._segments: list[np.ndarray] = []
        self._speaking = False

    def accept(self, samples):
        if np.abs(samples).max() > 0.1:
            self._speech.append(samples)
            self._speaking, self._quiet = True, 0
        elif self._speaking:
            self._quiet += 1
            if self._quiet >= 2:
                self._segments.append(np.concatenate(self._speech))
                self._speech, self._speaking = [], False

    def speaking(self):
        return self._speaking

    def pop_segments(self):
        out, self._segments = self._segments, []
        return out

    def reset(self):
        self.__init__()


class FakeRecognizer:
    def __init__(self, text="你好呀"):
        self.text = text

    def transcribe(self, samples, sample_rate):
        assert sample_rate == 16000 and samples.dtype == np.float32
        return self.text


def pcm(seconds: float, loud: bool) -> bytes:
    n = int(16000 * seconds)
    return (np.full(n, 8000 if loud else 0, dtype="<i2")).tobytes()


def run_listener(frames, *, playing=False, text="你好呀", barge=0.4):
    log: list = []

    async def on_speech(active):
        log.append(("speech", active))

    async def on_barge_in():
        log.append(("barge",))

    async def on_utterance(t, d, manual=False):
        log.append(("utt", t, d))

    async def run():
        lis = VoiceListener(
            vad=FakeVad(),
            engine=AsrEngine(lambda: FakeRecognizer(text)),
            barge_in_seconds=lambda: barge,
            playing=lambda: playing,
            on_speech=on_speech,
            on_barge_in=on_barge_in,
            on_utterance=on_utterance,
        )
        for f in frames:
            await lis.feed(f)
        for _ in range(50):
            if any(e[0] == "utt" for e in log) or text == "。":
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        await lis.aclose()

    asyncio.run(run())
    return log


def test_listener_recognizes_an_utterance():
    log = run_listener([pcm(0.5, True), pcm(0.2, False)])
    assert log[0] == ("speech", True) and log[1] == ("barge",)
    assert ("speech", False) in log
    utt = [e for e in log if e[0] == "utt"]
    assert len(utt) == 1 and utt[0][1] == "你好呀" and 0.4 < utt[0][2] < 0.6


def test_listener_barge_in_needs_sustained_speech_while_playing():
    short = run_listener([pcm(0.2, True), pcm(0.2, False)], playing=True, barge=0.4)
    assert ("barge",) not in short
    long = run_listener([pcm(0.6, True), pcm(0.2, False)], playing=True, barge=0.4)
    assert ("barge",) in long


def test_listener_drops_meaningless_text():
    log = run_listener([pcm(0.5, True), pcm(0.2, False)], text="。")
    assert not [e for e in log if e[0] == "utt"]


def test_listener_handles_odd_byte_frames():
    data = pcm(0.5, True) + pcm(0.2, False)
    frames = [data[i : i + 333] for i in range(0, len(data), 333)]
    assert [e for e in run_listener(frames) if e[0] == "utt"]


# --- WebSocket + routes ---------------------------------------------------------------------

CONFIG = """server:
  port: 8765
llm:
  profiles:
    - name: main
      base_url: https://api.example.com/v1
      model: m
  roles:
    chat: main
asr:
  listen: {listen}
  model_dir: {model_dir}
  short_min_seconds: 0
"""


class ReplyProvider:
    calls: list = []

    def __init__(self, profile, key):
        pass

    async def chat(self, messages, **_kw):
        ReplyProvider.calls.append(messages[-1]["content"])
        return ChatResult(text='{"text": "收到啦", "expression": "happy", "motion": null}', model="m", latency_ms=1)

    async def aclose(self):
        pass


def make_client(tmp_path, monkeypatch, *, listen: bool, with_models: bool = True):
    model_dir = tmp_path / "asr"
    if with_models:
        (model_dir / "sense-voice").mkdir(parents=True)
        for f in ("sense-voice/model.int8.onnx", "sense-voice/tokens.txt", "silero_vad.onnx"):
            (model_dir / f).write_bytes(b"x")
    path = tmp_path / "backend.yaml"
    path.write_text(CONFIG.format(listen=str(listen).lower(), model_dir=model_dir.as_posix()), encoding="utf-8")
    monkeypatch.setattr(pet_ws, "SileroVad", FakeVad)
    voice = VoiceState(path)
    voice.engine = AsrEngine(lambda: FakeRecognizer("在吗"))
    voice.keys.set("minimax", "sk-test-key-123456")
    ReplyProvider.calls = []
    app = create_app(config_path=path, token=TOKEN, provider_factory=ReplyProvider, voice=voice, tts_factory=FakeTts)
    return TestClient(app), voice


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_ws_voice_roundtrip(tmp_path, monkeypatch):
    client, _voice = make_client(tmp_path, monkeypatch, listen=True)
    with client.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {"expressions": ["happy"]}})
        assert ws.receive_json() == {"type": "ready"}
        assert ws.receive_json() == {"type": "mic", "listen": True}
        ws.send_bytes(pcm(0.5, True))
        ws.send_bytes(pcm(0.2, False))
        got: list = []
        while not any(isinstance(m, dict) and m.get("type") == "audio_end" for m in got):
            frame = ws.receive()
            got.append(json.loads(frame["text"]) if frame.get("text") is not None else frame["bytes"])
    types = [m["type"] if isinstance(m, dict) else "bytes" for m in got]
    assert types[:2] == ["hearing", "hearing"] or "hearing" in types
    assert {"type": "heard", "text": "在吗"} in got
    outputs = [m for m in got if isinstance(m, dict) and m["type"] == "output"]
    assert outputs[0]["output"]["text"] == "收到啦"
    assert types.index("output") < types.index("audio_start") < types.index("bytes") < types.index("audio_end")
    assert ReplyProvider.calls == ["在吗"]
    assert FakeTts.calls == [("收到啦", "happy")]


def test_ws_ignores_audio_when_not_listening(tmp_path, monkeypatch):
    client, voice = make_client(tmp_path, monkeypatch, listen=False)
    with client.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {}})
        ws.receive_json()
        assert ws.receive_json() == {"type": "mic", "listen": False}
        ws.send_bytes(pcm(0.5, True) + pcm(0.2, False))
        ws.send_json({"type": "playback", "playing": True})
        ws.send_json({"type": "nope"})
        assert ws.receive_json()["type"] == "error"  # proves the earlier frames were consumed silently
        assert voice.playing is True
    assert ReplyProvider.calls == []


def test_listen_requires_models(tmp_path, monkeypatch):
    client, voice = make_client(tmp_path, monkeypatch, listen=True, with_models=False)
    assert voice.listening is False
    state = client.get("/api/voice", headers=auth()).json()
    assert state["asr"]["listen"] is True and state["models_ready"] is False


def test_voice_routes(tmp_path, monkeypatch):
    client, voice = make_client(tmp_path, monkeypatch, listen=False)
    assert client.get("/api/voice").status_code == 401
    state = client.get("/api/voice", headers=auth()).json()
    assert state["key"] == {"set": True, "hint": "…3456"} and "happy" in state["emotions"]
    assert "sk-test" not in json.dumps(state)

    body = {"asr": {**state["asr"], "listen": True}, "tts": {**state["tts"], "emotions": {"blush": "happy"}}}
    saved = client.put("/api/voice", headers=auth(), json=body).json()
    assert saved["asr"]["listen"] is True and saved["tts"]["emotions"] == {"blush": "happy"}
    assert voice.listening is True
    assert load_backend_config(tmp_path / "backend.yaml").llm.roles.chat == "main"

    bad = {"asr": state["asr"], "tts": {**state["tts"], "emotions": {"blush": "giggle"}}}
    r = client.put("/api/voice", headers=auth(), json=bad)
    assert r.status_code == 422 and "emotions" in r.json()["detail"]

    r = client.post("/api/voice/test", headers=auth(), json={})
    assert r.json() == {"ok": False, "first_audio_ms": None, "error": "桌宠未连接，无法试听"}

    assert client.delete("/api/voice/key", headers=auth()).json() == {"set": False, "hint": None}
    r = client.put("/api/voice/key", headers=auth(), json={"api_key": "has space"})
    assert r.status_code == 422
    assert client.put("/api/voice/key", headers=auth(), json={"api_key": "sk-abcdefghijkl"}).json()["set"] is True


def test_voice_test_route_speaks_through_pet(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch, listen=False)
    with client.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {}})
        ws.receive_json(), ws.receive_json()
        r = client.post("/api/voice/test", headers=auth(), json={"text": "试一下", "expression": "sad"})
        assert r.json()["ok"] is True
        assert ws.receive_json()["type"] == "audio_start"
    assert FakeTts.calls == [("试一下", "sad")]


def test_concurrent_speak_leaves_a_single_stream():
    sent: list = []

    async def run():
        FakeTts.gate = asyncio.Event()
        sp = make_speaker(sent)
        await sp.speak("第零句")
        # Two speakers racing while the current one is being cancelled.
        a, b = await asyncio.gather(sp.speak("第一句"), sp.speak("第二句"))
        live = [t for t in (a, b) if not t.done()]
        await asyncio.sleep(0.05)
        await sp.interrupt()
        FakeTts.gate.set()
        await asyncio.gather(a, b, return_exceptions=True)
        return live, sp.active

    live, active = asyncio.run(run())
    assert len(live) == 1 and not active
    starts = [m["id"] for m in sent if isinstance(m, dict) and m["type"] == "audio_start"]
    ends = [m["id"] for m in sent if isinstance(m, dict) and m["type"] == "audio_end"]
    assert sorted(starts) == sorted(ends)  # every started stream was closed


def test_utterance_dropped_after_listening_turned_off(tmp_path, monkeypatch):
    client, voice = make_client(tmp_path, monkeypatch, listen=True)

    class SlowRecognizer(FakeRecognizer):
        def transcribe(self, samples, sample_rate):
            voice.asr = voice.asr.model_copy(update={"listen": False})  # user switches off mid-recognition
            return "在吗"

    voice.engine = AsrEngine(lambda: SlowRecognizer())
    with client.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {}})
        ws.receive_json(), ws.receive_json()
        ws.send_bytes(pcm(0.5, True) + pcm(0.2, False))
        ws.send_json({"type": "nope"})
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "error":
                break
        import time

        time.sleep(0.2)
    assert not any(m["type"] == "heard" for m in msgs)
    assert ReplyProvider.calls == []


def make_queue_speaker(log, *, queue=True):
    async def send_json(m):
        log.append(("json", m["type"], m.get("id")))

    async def send_bytes(_b):
        pass

    async def wait_idle(seconds):
        log.append(("wait_idle", round(seconds, 1)))

    return Speaker(
        config=lambda: TtsConfig(),
        api_key=lambda _provider: "k",
        factory=FakeTts,
        send_json=send_json,
        send_bytes=send_bytes,
        queue_mode=lambda: queue,
        wait_idle=wait_idle,
    )


def announcer(log, name):
    async def announce():
        log.append(("show", name))

    return announce


def test_queue_mode_finishes_current_reply_first():
    log: list = []

    async def run():
        FakeTts.gate = asyncio.Event()
        sp = make_queue_speaker(log)
        await sp.say("第一句", None, announcer(log, "A"))
        for _ in range(200):
            if ("json", "audio_start", 1) in log:
                break
            await asyncio.sleep(0.005)
        await sp.say("第二句", None, announcer(log, "B"))
        await asyncio.sleep(0.05)
        assert ("show", "B") not in log  # B waits while A is still streaming
        FakeTts.gate.set()
        for _ in range(200):
            if not sp.busy:
                break
            await asyncio.sleep(0.005)

    asyncio.run(run())
    shows = [e for e in log if e[0] in ("show", "wait_idle") or e[1] in ("audio_start", "audio_end")]
    assert shows == [
        ("show", "A"),
        ("json", "audio_start", 1),
        ("json", "audio_end", 1),
        ("wait_idle", 2.0),  # 16 bytes of 24 kHz audio + grace
        ("show", "B"),
        ("json", "audio_start", 2),
        ("json", "audio_end", 2),
        ("wait_idle", 2.0),
    ]
    assert [c[0] for c in FakeTts.calls] == ["第一句", "第二句"]


def test_queue_mode_shows_unspeakable_output_immediately():
    log: list = []

    async def run():
        FakeTts.gate = asyncio.Event()
        sp = make_queue_speaker(log)
        await sp.say("第一句", None, announcer(log, "A"))
        await sp.say("（等我先回完上一句～）", None, announcer(log, "notice"))
        assert ("show", "notice") in log
        await sp.interrupt()
        assert not sp.busy

    asyncio.run(run())


def test_immediate_mode_cuts_current_reply():
    log: list = []

    async def run():
        FakeTts.gate = asyncio.Event()
        sp = make_queue_speaker(log, queue=False)
        await sp.say("第一句", None, announcer(log, "A"))
        for _ in range(200):
            if ("json", "audio_start", 1) in log:
                break
            await asyncio.sleep(0.005)
        await sp.say("第二句", None, announcer(log, "B"))
        FakeTts.gate.set()
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert ("show", "B") in log
    assert log.index(("json", "audio_end", 1)) < log.index(("json", "audio_start", 2))


@pytest.mark.parametrize("barge", [False, True])
def test_hub_barge_in_respects_setting(tmp_path, monkeypatch, barge):
    _client, voice = make_client(tmp_path, monkeypatch, listen=True)
    voice.asr = voice.asr.model_copy(update={"barge_in": barge})
    hub = _client.app.state.pet_hub
    calls = []

    async def fake_interrupt():
        calls.append(1)

    hub.interrupt = fake_interrupt
    asyncio.run(hub.barge_in())
    assert calls == ([1] if barge else [])


def test_wait_playback_done(tmp_path, monkeypatch):
    _client, voice = make_client(tmp_path, monkeypatch, listen=False)

    async def run():
        voice.set_playing(True)
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, voice.set_playing, False)
        t = loop.time()
        await voice.wait_playback_done(5)
        waited = loop.time() - t
        t = loop.time()
        await voice.wait_playback_done(0.2)  # never reports playing: gives up after the report window
        return waited, loop.time() - t

    waited, gave_up = asyncio.run(run())
    assert 0.05 < waited < 1 and gave_up < 1


def test_minimax_language_boost():
    assert build_request(MiniMaxConfig(), "こんにちは", None, "ja")["language_boost"] == "Japanese"
    assert build_request(MiniMaxConfig(), "你好", None)["language_boost"] == "auto"


def test_hub_speaks_speech_but_shows_text(tmp_path, monkeypatch):
    from core.agent import AgentOutput
    from core.events import make_event

    client, _ = make_client(tmp_path, monkeypatch, listen=False)
    hub = client.app.state.pet_hub
    said, shown = [], []

    async def fake_say(text, expression, announce):
        said.append(text)
        await announce()

    async def fake_broadcast(msg):
        shown.append(msg)

    hub.speaker.say = fake_say
    hub.broadcast = fake_broadcast
    hub.clients = {object()}
    asyncio.run(hub.emit(AgentOutput(text="你好呀", speech="こんにちは"), make_event("user.text", {"text": "hi"})))
    assert said == ["こんにちは"] and shown[0]["output"]["text"] == "你好呀"


@pytest.mark.parametrize(
    ("text", "ok"),
    [("我。", False), ("嗯。", False), ("嗯嗯", False), ("啊啊啊！", False), ("お。", False), ("好的", True), ("在吗", True), ("嗯，好", True)],
)
def test_worth_sending(text, ok):
    from perception.asr import worth_sending

    assert worth_sending(text, 2) is ok


@pytest.mark.parametrize(
    ("text", "duration", "ok"),
    [
        ("不是。", 0.39, False),  # real logs: stray fragments
        ("放开。", 0.45, False),
        ("不是。", 1.2, True),  # said deliberately
        ("这个我。", 0.8, False),  # real logs: a fragment that triggered a reply
        ("帮我打开原神。", 0.45, True),  # long text is never dropped for duration
        ("在吗", 0.3, True),  # check off
    ],
)
def test_worth_sending_short_blips(text, duration, ok):
    from perception.asr import worth_sending

    off = text == "在吗"
    assert worth_sending(text, 2, duration, 0.0 if off else 1.0) is ok


class PickyTts(FakeTts):
    """Rejects every tone with 2013 (like speech-2.8 and whisper) but speaks without one."""

    async def stream(self, text, *, emotion=None, language=None):
        FakeTts.calls.append((text, emotion))
        if emotion == "whisper":
            raise ProviderError("MiniMax 错误 2013：参数有误（检查音色 ID / 模型）")
        yield b"\x00\x01" * 4


def make_picky_speaker(sent):
    async def send_json(m):
        sent.append(m)

    async def send_bytes(b):
        sent.append(b)

    return Speaker(
        config=lambda: TtsConfig(enabled=True, emotions={"shy": "whisper"}),
        api_key=lambda _provider: "k",
        factory=PickyTts,
        send_json=send_json,
        send_bytes=send_bytes,
    )


def test_unsupported_tone_falls_back_to_default_voice_and_is_remembered():
    sent: list = []

    async def run():
        sp = make_picky_speaker(sent)
        first = await (await sp.speak("你好呀", "shy"))
        second = await (await sp.speak("再见啦", "shy"))
        return first, second

    first, second = asyncio.run(run())
    assert first.ok and second.ok
    # First line: tried whisper, then retried without; second line goes straight to the default voice.
    assert FakeTts.calls == [("你好呀", "whisper"), ("你好呀", None), ("再见啦", None)]
    assert not any(isinstance(m, dict) and m.get("error") for m in sent)


# --- drop_reason / keystate / push-to-talk / heard log ----------------------------------------


def test_drop_reason_options():
    from perception.asr import drop_reason

    assert drop_reason("嗯嗯", 2) is not None
    assert drop_reason("嗯嗯", 2, drop_fillers=False) is None
    assert drop_reason("字幕。", 2, ignore_phrases=["字幕"]) is not None
    assert drop_reason("这个我要", 2, 0.5, 1.0, short_chars=3) is None
    assert drop_reason("这个我要", 2, 0.5, 1.0) is not None


def test_combo_keys():
    from perception.keystate import combo_keys

    assert combo_keys("Ctrl+Alt+Space") == [(0x11,), (0x12,), (0x20,)]
    assert combo_keys("F9") == [(0x78,)]
    assert combo_keys("Super+A") == [(0x5B, 0x5C), (0x41,)]
    assert combo_keys("Ctrl+不存在") is None


def run_hold(hold_seconds, *, loud=False):
    log: list = []

    async def noop(*_a):
        pass

    async def on_utterance(t, d, manual=False):
        log.append((t, d, manual))

    async def run():
        lis = VoiceListener(
            vad=FakeVad(),
            engine=AsrEngine(lambda: FakeRecognizer("在吗")),
            barge_in_seconds=lambda: 0.4,
            playing=lambda: False,
            on_speech=noop,
            on_barge_in=noop,
            on_utterance=on_utterance,
        )
        await lis.hold_begin()
        await lis.feed(pcm(hold_seconds, loud))
        await lis.hold_end()
        for _ in range(50):
            if log:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        await lis.aclose()

    asyncio.run(run())
    return log


@pytest.mark.parametrize("loud", [False, True])
def test_hold_sends_whole_recording(loud):
    log = run_hold(1.0, loud=loud)
    assert len(log) == 1
    text, duration, manual = log[0]
    assert text == "在吗" and manual is True and 0.95 < duration < 1.05


def test_hold_too_short_is_ignored():
    assert run_hold(0.1) == []


PTT_YAML = """hotkeys:
  - id: ptt
    name: 按住说话
    keys: Ctrl+Alt+Space
    enabled: true
    action: builtin
    builtin: push_to_talk
"""


def test_ws_push_to_talk(tmp_path, monkeypatch):
    import time

    (tmp_path / "hotkeys.yaml").write_text(PTT_YAML, encoding="utf-8")
    client, _voice = make_client(tmp_path, monkeypatch, listen=False)
    held = {"v": True}
    client.app.state.pet_hub.key_down = lambda combo: held["v"]
    monkeypatch.setattr(pet_ws, "PTT_TAIL_S", 0.01)
    monkeypatch.setattr(pet_ws, "PTT_POLL_S", 0.005)
    got: list = []
    with client.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {}})
        assert ws.receive_json() == {"type": "ready"}
        assert ws.receive_json() == {"type": "mic", "listen": False}
        ws.send_json({"type": "ptt", "id": "ptt"})
        while True:
            m = ws.receive_json()
            got.append(m)
            if m == {"type": "mic", "listen": True}:
                break
        ws.send_bytes(pcm(1.0, False))
        time.sleep(0.2)  # let the server consume the audio before the key is released
        held["v"] = False
        while True:
            m = ws.receive_json()
            got.append(m)
            if m["type"] == "output":
                break
    assert {"type": "heard", "text": "在吗"} in got
    assert {"type": "mic", "listen": False} in got
    assert ReplyProvider.calls == ["在吗"]


def test_ws_ptt_unknown_id_does_not_record(tmp_path, monkeypatch):
    (tmp_path / "hotkeys.yaml").write_text(PTT_YAML, encoding="utf-8")
    client, _voice = make_client(tmp_path, monkeypatch, listen=False)
    client.app.state.pet_hub.key_down = lambda combo: True
    with client.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {}})
        ws.receive_json(), ws.receive_json()
        ws.send_json({"type": "ptt", "id": "greet"})
        ws.send_json({"type": "nope"})
        assert ws.receive_json()["type"] == "error"


def test_heard_route(tmp_path, monkeypatch):
    client, _voice = make_client(tmp_path, monkeypatch, listen=True)
    assert client.get("/api/voice/heard").status_code == 401
    with client.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {"expressions": ["happy"]}})
        ws.receive_json(), ws.receive_json()
        ws.send_bytes(pcm(0.5, True))
        ws.send_bytes(pcm(0.2, False))
        got: list = []
        while not any(isinstance(m, dict) and m.get("type") == "audio_end" for m in got):
            frame = ws.receive()
            got.append(json.loads(frame["text"]) if frame.get("text") is not None else frame["bytes"])
    items = client.get("/api/voice/heard", headers=auth()).json()
    assert items[0]["text"] == "在吗" and items[0]["sent"] is True
    assert items[0]["manual"] is False and items[0]["reason"] is None
