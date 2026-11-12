import asyncio
import json
import random

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from core.agent import Agent, AgentOutput, Labels, ReactionRules, parse_output
from core.credentials import KeyStore
from core.events import client_event, make_event
from providers.base import ChatResult, ProviderError

TOKEN = "test-token"
LABELS = Labels(expressions=["happy", "sad"], motions=["wave"])
CONFIG = """server:
  port: 8765
llm:
  profiles:
    - name: main
      base_url: https://api.example.com/v1
      model: m
  roles:
    chat: main
"""


class ScriptedProvider:
    calls: list = []
    replies: list = []

    def __init__(self, profile, key):
        self.key = key

    async def chat(self, messages, **_kw):
        ScriptedProvider.calls.append((self.key, messages))
        reply = ScriptedProvider.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(text=reply, model="m", latency_ms=1)

    async def aclose(self):
        pass


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    ScriptedProvider.calls, ScriptedProvider.replies = [], []
    return tmp_path


def make_agent(config_dir):
    return Agent(
        config_path=config_dir / "backend.yaml",
        persona_path=config_dir / "persona.yaml",
        keys=KeyStore(),
        provider_factory=ScriptedProvider,
    )


# --- parse_output ---------------------------------------------------------------------------


def test_parse_json_reply():
    out = parse_output('{"text": "你好", "expression": "happy", "motion": "wave"}', LABELS)
    assert out == AgentOutput(text="你好", expression="happy", motion="wave")


def test_parse_strips_fences_and_drops_unknown_labels():
    out = parse_output('```json\n{"text": "嗯", "expression": "furious", "motion": 3}\n```', LABELS)
    assert out == AgentOutput(text="嗯")


def test_parse_plain_text_fallback():
    assert parse_output("就是一句话", LABELS) == AgentOutput(text="就是一句话")
    assert parse_output("前缀 {坏的 json}", LABELS).text == "前缀 {坏的 json}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"text": "第一行\n第二行", "expression": "happy", "motion": null}', AgentOutput(text="第一行\n第二行", expression="happy")),
        ('{"text": "你说"早安"了吗", "expression": "happy", "motion": null}', AgentOutput(text='你说"早安"了吗', expression="happy")),
        ('{"text": "嗨", "expression": "happy"}\n{"text": "还有", "expression": null}', AgentOutput(text="嗨", expression="happy")),
        ('好的：{"text": "嗨", "motion": "wave"} 以上', AgentOutput(text="嗨", motion="wave")),
        ('{"text": "说到一半", "expression": "hap', AgentOutput(text="说到一半")),
        ('{"text": "说到一', AgentOutput(text="说到一")),
        ("text: 你好呀\nexpression: happy\nmotion: wave", AgentOutput(text="你好呀", expression="happy", motion="wave")),
        ("text：你好\nexpression：sad", AgentOutput(text="你好", expression="sad")),
        ('{"text": "{\\"text\\": \\"套娃\\", \\"expression\\": \\"happy\\"}", "expression": null}', AgentOutput(text="套娃", expression="happy")),
        ('{"text": "嗨", "expression": "happy",}', AgentOutput(text="嗨", expression="happy")),
        ("{'text': '嗨', 'expression': 'happy'}", AgentOutput(text="嗨", expression="happy")),
        ('{"expression": "happy", "motion": "wave"}', AgentOutput(expression="happy", motion="wave")),
        ("注意：今天要下雨哦", AgentOutput(text="注意：今天要下雨哦")),
    ],
)
def test_parse_salvages_malformed_replies(raw, expected):
    assert parse_output(raw, LABELS) == expected


def test_parse_never_emits_tool_calls():
    out = parse_output('{"text": "x", "tool_calls": [{"name": "rm", "args": {}}]}', LABELS)
    assert out.tool_calls == []


# --- events ---------------------------------------------------------------------------------


def test_client_event_validation():
    e = client_event("user.text", {"text": "  hi  "})
    assert e.type == "user.text" and e.payload == {"text": "hi"} and e.id.startswith("evt_")
    for bad in [("user.text", {"text": "   "}), ("user.text", {"text": "x", "extra": 1}), ("timer.idle", {})]:
        with pytest.raises(ValueError):
            client_event(*bad)
    with pytest.raises(ValueError):
        client_event("pet.interaction", {"kind": "click", "area": "tail"})


# --- agent ----------------------------------------------------------------------------------


def test_agent_builds_prompt_and_keeps_history(config_dir, memory_keyring):
    KeyStore().set("main", "sk-secret-1234567890")
    ScriptedProvider.replies = ['{"text": "你好呀", "expression": "happy", "motion": null}', '{"text": "记得"}']
    agent = make_agent(config_dir)
    out = asyncio.run(agent.respond(make_event("user.text", {"text": "你好"}), LABELS))
    assert out == AgentOutput(text="你好呀", expression="happy")
    asyncio.run(agent.respond(make_event("user.text", {"text": "还记得吗"}), LABELS))

    key, messages = ScriptedProvider.calls[1]
    assert key == "sk-secret-1234567890"
    system = messages[0]["content"]
    assert "happy、sad" in system and "wave" in system
    assert "sk-secret" not in json.dumps(messages, ensure_ascii=False)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_agent_history_is_bounded(config_dir):
    (config_dir / "persona.yaml").write_text("history_turns: 1\n", encoding="utf-8")
    ScriptedProvider.replies = ["a", "b", "c"]
    agent = make_agent(config_dir)
    for t in ["1", "2", "3"]:
        asyncio.run(agent.respond(make_event("user.text", {"text": t}), LABELS))
    assert len(ScriptedProvider.calls[2][1]) == 1 + 2 + 1


def test_agent_reports_missing_role_and_provider_errors(config_dir):
    ScriptedProvider.replies = [ProviderError("鉴权失败（401），请检查 API Key")]
    agent = make_agent(config_dir)
    out = asyncio.run(agent.respond(make_event("user.text", {"text": "hi"}), LABELS))
    assert "鉴权失败" in out.text
    (config_dir / "backend.yaml").write_text("server:\n  port: 8765\n", encoding="utf-8")
    out = asyncio.run(agent.respond(make_event("user.text", {"text": "hi"}), LABELS))
    assert "控制台" in out.text and ScriptedProvider.calls.__len__() == 1


# --- reaction rules -------------------------------------------------------------------------

REACTIONS = """cooldown_seconds: 2
llm_cooldown_seconds: 100
interactions:
  head:
    lines: ["嘿嘿"]
    expressions: [happy, missing]
    motions: []
    llm_chance: 0.5
"""


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FixedRng(random.Random):
    def __init__(self, value):
        super().__init__(0)
        self.value = value

    def random(self):
        return self.value


def test_rules_canned_reaction_and_cooldown(tmp_path):
    (tmp_path / "r.yaml").write_text(REACTIONS, encoding="utf-8")
    clock = Clock()
    rules = ReactionRules(tmp_path / "r.yaml", rng=FixedRng(0.9), clock=clock)
    head = make_event("pet.interaction", {"kind": "click", "area": "head"})
    assert rules.decide(head, LABELS, llm_available=True) == AgentOutput(text="嘿嘿", expression="happy")
    clock.t += 1
    assert rules.decide(head, LABELS, llm_available=True) is None
    clock.t += 2
    body = make_event("pet.interaction", {"kind": "click", "area": "body"})
    assert rules.decide(body, LABELS, llm_available=True) is None


def test_rules_escalate_to_llm_with_its_own_cooldown(tmp_path):
    (tmp_path / "r.yaml").write_text(REACTIONS, encoding="utf-8")
    clock = Clock()
    rules = ReactionRules(tmp_path / "r.yaml", rng=FixedRng(0.1), clock=clock)
    head = make_event("pet.interaction", {"kind": "click", "area": "head"})
    assert rules.decide(head, LABELS, llm_available=False) != "llm"
    clock.t += 5
    assert rules.decide(head, LABELS, llm_available=True) == "llm"
    clock.t += 5
    assert isinstance(rules.decide(head, LABELS, llm_available=True), AgentOutput)


# --- websocket ------------------------------------------------------------------------------


@pytest.fixture
def ws_client(config_dir):
    app = create_app(config_path=config_dir / "backend.yaml", token=TOKEN, provider_factory=ScriptedProvider)
    return TestClient(app)


def hello(**labels):
    return {"type": "hello", "token": TOKEN, "labels": labels}


def handshake(ws, **labels):
    ws.send_json(hello(**labels))
    assert ws.receive_json() == {"type": "ready"}
    assert ws.receive_json() == {"type": "mic", "listen": False}


@pytest.mark.parametrize("first", [{"type": "hello", "token": "wrong"}, {"type": "event"}, ["x"]])
def test_ws_rejects_bad_hello(ws_client, first):
    with ws_client.websocket_connect("/ws/pet") as ws:
        ws.send_json(first)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_chat_roundtrip(ws_client):
    ScriptedProvider.replies = ['{"text": "在呢", "expression": "happy", "motion": null}']
    with ws_client.websocket_connect("/ws/pet") as ws:
        handshake(ws, expressions=["happy"], motions=[])
        ws.send_json({"type": "event", "event": {"type": "user.text", "payload": {"text": "在吗"}}})
        assert ws.receive_json() == {"type": "status", "thinking": True}
        assert ws.receive_json() == {"type": "status", "thinking": False}
        msg = ws.receive_json()
        assert msg["type"] == "output" and msg["event_id"].startswith("evt_")
        assert msg["output"] == {"text": "在呢", "speech": "", "expression": "happy", "motion": None, "sticker": None, "tool_calls": [], "status": []}


def test_ws_rejects_invalid_events(ws_client):
    with ws_client.websocket_connect("/ws/pet") as ws:
        handshake(ws)
        ws.send_json({"type": "event", "event": {"type": "hotkey.pressed", "payload": {}}})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "event", "event": {"type": "user.text", "payload": {"text": ""}}})
        assert ws.receive_json()["type"] == "error"
    assert ScriptedProvider.calls == []


def test_ws_interaction_uses_rules(ws_client, config_dir):
    (config_dir / "reactions.yaml").write_text(REACTIONS.replace("llm_chance: 0.5", "llm_chance: 0"), encoding="utf-8")
    with ws_client.websocket_connect("/ws/pet") as ws:
        handshake(ws, expressions=["happy"])
        ws.send_json({"type": "event", "event": {"type": "pet.interaction", "payload": {"kind": "click", "area": "head"}}})
        msg = ws.receive_json()
        assert msg["output"]["text"] == "嘿嘿" and msg["output"]["expression"] == "happy"
    assert ScriptedProvider.calls == []


def test_ws_binary_frame_closes_cleanly(ws_client):
    with ws_client.websocket_connect("/ws/pet") as ws:
        ws.send_bytes(b"\x00\x01")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401


def test_dispatcher_caps_pending_llm_calls():
    from core.dispatcher import Dispatcher

    release = asyncio.Event()
    emitted: list[AgentOutput] = []

    class SlowAgent:
        calls = 0

        def chat_configured(self):
            return True

        async def respond(self, event, labels, user_text=None):
            SlowAgent.calls += 1
            await release.wait()
            return AgentOutput(text="ok")

    async def emit(output, event):
        emitted.append(output)

    async def set_thinking(_):
        pass

    async def run():
        d = Dispatcher(agent=SlowAgent(), rules=None, labels=lambda: LABELS, emit=emit, set_thinking=set_thinking)
        tasks = [asyncio.create_task(d(make_event("user.text", {"text": str(i)}))) for i in range(4)]
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(run())
    assert SlowAgent.calls == 2
    assert sorted(o.text for o in emitted) == ["ok", "ok", "（等我先回完上一句～）", "（等我先回完上一句～）"]


def test_parse_speech_field():
    out = parse_output('{"text": "你好呀", "speech": "こんにちは", "expression": "happy", "motion": null}', LABELS)
    assert (out.text, out.speech, out.expression) == ("你好呀", "こんにちは", "happy")
    broken = parse_output('{"text": "你说"早"呀", "speech": "おはよう", "expression": "happy"', LABELS)
    assert (broken.text, broken.speech) == ('你说"早"呀', "おはよう")
    assert parse_output('{"text": "只有中文"}', LABELS).speech == ""
    # Real logs: the model splits the key across a newline, so strict JSON never parses.
    split = parse_output('{"text\n": "切，谁稀罕管你", "speech": "フン"}', LABELS)
    assert (split.text, split.speech) == ("切，谁稀罕管你", "フン")
    # Japanese-only reply still has something to show.
    assert parse_output('{"text": "", "speech": "フン"}', LABELS).text == "フン"


def test_format_instructions_speech_language():
    from core.agent import format_instructions
    from core.persona import Persona

    ja = format_instructions(Persona(), LABELS, "ja")
    assert '"speech"' in ja and "日语" in ja
    assert '"speech"' not in format_instructions(Persona(), LABELS, None)


def test_agent_web_search_flag_and_nudge(tmp_path):
    seen = []

    class P:
        def __init__(self, *_):
            pass

        async def chat(self, messages, *, web_search=False):
            seen.append((web_search, messages[0]["content"], messages[-1]["content"]))
            return ChatResult(text='{"text": "查到了"}', model="m", latency_ms=1, citations=2)

        async def aclose(self):
            pass

    cfg = CONFIG.replace("      model: m\n", "      model: m\n      web_search: google_function\n")
    (tmp_path / "backend.yaml").write_text(cfg, encoding="utf-8")
    agent = Agent(config_path=tmp_path / "backend.yaml", persona_path=tmp_path / "p.yaml", keys=KeyStore(), provider_factory=P)
    asyncio.run(agent.respond(make_event("user.text", {"text": "哪吒2最终票房多少钱"}), LABELS))
    asyncio.run(agent.respond(make_event("user.text", {"text": "你好呀"}), LABELS))
    (on1, sys1, user1), (on2, _, user2) = seen
    assert on1 and on2 and "联网搜索" in sys1
    assert "请先联网搜索" in user1 and "请先联网搜索" not in user2
    (tmp_path / "backend.yaml").write_text(cfg + "  web_search: false\n" if False else cfg.replace("  roles:", "  web_search: false\n  roles:"), encoding="utf-8")
    asyncio.run(agent.respond(make_event("user.text", {"text": "最新新闻"}), LABELS))
    assert seen[-1][0] is False and "请先联网搜索" not in seen[-1][2]


# --- built-in show_code / set_style --------------------------------------------------------


def _drain_until(ws, kind, limit=8):
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind:
            return msg, seen
    raise AssertionError(f"no {kind} message in {seen}")


def test_ws_show_code_reaches_the_pet(ws_client):
    reply = {
        "text": "写好啦，在代码窗口里",
        "tool_calls": [{"name": "show_code", "args": {"title": "问候", "language": "python", "code": "print('hi')\n"}}],
    }
    ScriptedProvider.replies = [json.dumps(reply, ensure_ascii=False)]
    with ws_client.websocket_connect("/ws/pet") as ws:
        handshake(ws)
        ws.send_json({"type": "event", "event": {"type": "user.text", "payload": {"text": "写个 hello"}}})
        msg, _ = _drain_until(ws, "code")
    assert msg == {"type": "code", "title": "问候", "language": "python", "code": "print('hi')\n"}
    # The tool is offered to the model on a spoken turn that mentions it (keyword mode), others are left out.
    system = ScriptedProvider.calls[0][1][0]["content"]
    assert "show_code：" in system and "set_style：" not in system


def test_ws_set_style_reaches_the_pet_and_bad_css_is_refused(ws_client):
    good = {"text": "改好了", "tool_calls": [{"name": "set_style", "args": {"css": "#bubble { --b-bg: pink; }"}}]}
    bad = {"text": "改好了", "tool_calls": [{"name": "set_style", "args": {"css": "@import url(http://x/y.css);"}}]}
    ScriptedProvider.replies = [json.dumps(good, ensure_ascii=False), json.dumps(bad, ensure_ascii=False)]
    with ws_client.websocket_connect("/ws/pet") as ws:
        handshake(ws)
        ws.send_json({"type": "event", "event": {"type": "user.text", "payload": {"text": "气泡改粉色"}}})
        msg, _ = _drain_until(ws, "set_css")
        assert msg == {"type": "set_css", "css": "#bubble { --b-bg: pink; }", "replace": False}
        ws.send_json({"type": "event", "event": {"type": "user.text", "payload": {"text": "再改一次"}}})
        # The second proposal is refused: no set_css, a short notice instead.
        refused = []
        for _ in range(8):
            m = ws.receive_json()
            refused.append(m)
            if m["type"] == "output" and "不允许" in m["output"]["text"]:
                break
        assert not any(m["type"] == "set_css" for m in refused)
        assert any(m["type"] == "output" and "不允许" in m["output"]["text"] for m in refused)


def test_parse_output_sticker_only_from_the_offered_names():
    labels = Labels(expressions=["happy"], motions=[], stickers=["猫猫震惊", "好耶"])
    out = parse_output('{"text": "啊？", "expression": null, "motion": null, "sticker": "猫猫震惊"}', labels)
    assert out.sticker == "猫猫震惊" and out.text == "啊？"
    assert parse_output('{"text": "啊？", "sticker": "不存在"}', labels).sticker is None
    assert parse_output('{"text": "啊？", "sticker": null}', labels).sticker is None
    assert parse_output('{"text": "啊？", "sticker": "好耶"}', LABELS).sticker is None  # the pet has no stickers
    # Broken JSON (unescaped quotes inside text): the sticker survives and its key never leaks into the bubble.
    broken = parse_output('{"text": "他说"好"了", "sticker": "好耶"', labels)
    assert broken.sticker == "好耶" and broken.text == '他说"好"了'


def test_parse_labels_keeps_only_usable_sticker_names():
    from api.pet_ws import parse_labels

    got = parse_labels({"expressions": ["happy"], "motions": [], "stickers": [" 好耶 ", "好耶", "a\"b", "", 5, "字" * 21, "x{y}"]})
    assert got.stickers == ["好耶"] and got.expressions == ["happy"]
    assert parse_labels({"expressions": ["happy"]}).stickers == []
