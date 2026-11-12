import asyncio
import random

import pytest

from core.agent import AgentOutput, Labels
from core.dispatcher import Dispatcher
from core.events import client_event, make_event
from core.hotkeys import Hotkey, HotkeyRules, HotkeysConfig

YAML = """hotkeys:
  - id: greet
    keys: Ctrl+Alt+W
    action: reaction
    lines: [嗨]
    expressions: [happy, unknown]
    motions: [wave]
    cooldown_seconds: 5
  - id: praise
    keys: Ctrl+Alt+H
    action: llm
    prompt: （主人想听你夸夸他）
    cooldown_seconds: 10
  - id: mic
    keys: Ctrl+Alt+M
    action: builtin
    builtin: toggle_mic
  - id: off
    keys: Ctrl+Alt+O
    enabled: false
    action: reaction
    lines: [x]
"""


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def rules(tmp_path):
    (tmp_path / "hotkeys.yaml").write_text(YAML, encoding="utf-8")
    clock = Clock()
    return HotkeyRules(tmp_path / "hotkeys.yaml", clock=clock, rng=random.Random(0)), clock, tmp_path


def test_decide_kinds_and_cooldown(rules):
    r, clock, _ = rules
    kind, hk = r.decide("greet")
    assert kind == "reaction" and hk.lines == ["嗨"]
    assert r.decide("greet") is None  # cooldown
    clock.t += 5
    assert r.decide("greet")[0] == "reaction"
    assert r.decide("praise")[0] == "llm"
    assert r.decide("mic") is None  # builtin is the frontend's job
    assert r.decide("off") is None and r.decide("nope") is None


def test_bad_file_is_ignored(rules):
    r, _, tmp_path = rules
    (tmp_path / "hotkeys.yaml").write_text("hotkeys:\n  - id: x\n    keys: A\n    action: llm\n", encoding="utf-8")
    assert r.decide("x") is None  # prompt missing -> whole file rejected, logged


@pytest.mark.parametrize(
    "item",
    [
        {"id": "a", "keys": "Ctrl+A", "action": "builtin"},
        {"id": "a", "keys": "Ctrl+A", "action": "llm", "prompt": " "},
        {"id": "a", "keys": "Ctrl+A", "action": "reaction"},
        {"id": "Bad", "keys": "Ctrl+A", "action": "reaction", "lines": ["x"]},
    ],
)
def test_hotkey_validation(item):
    with pytest.raises(ValueError):
        Hotkey.model_validate(item)


def test_duplicate_ids():
    item = {"id": "a", "keys": "Ctrl+A", "action": "reaction", "lines": ["x"]}
    with pytest.raises(ValueError):
        HotkeysConfig.model_validate({"hotkeys": [item, item]})


def test_hotkey_event_payload_is_only_an_id():
    assert client_event("hotkey.pressed", {"id": "greet"}).payload == {"id": "greet"}
    with pytest.raises(ValueError):
        client_event("hotkey.pressed", {"id": "greet", "keys": "Ctrl+Alt+W"})


class FakeAgent:
    def __init__(self):
        self.calls = []

    def chat_configured(self):
        return True

    async def respond(self, event, labels, user_text=None):
        self.calls.append((event.type, user_text))
        return AgentOutput(text="你真棒")


def run_dispatch(rules, hotkey_id):
    r, _, _ = rules
    agent, emitted = FakeAgent(), []

    async def emit(output, event):
        emitted.append(output)

    async def set_thinking(_):
        pass

    d = Dispatcher(
        agent=agent,
        rules=None,
        labels=lambda: Labels(expressions=["happy"], motions=["wave"]),
        emit=emit,
        set_thinking=set_thinking,
        hotkeys=r,
    )
    asyncio.run(d(make_event("hotkey.pressed", {"id": hotkey_id})))
    return agent, emitted


def test_dispatch_reaction_uses_only_known_labels(rules):
    agent, emitted = run_dispatch(rules, "greet")
    assert agent.calls == []
    assert emitted == [AgentOutput(text="嗨", expression="happy", motion="wave")]


def test_dispatch_llm_passes_prompt(rules):
    agent, emitted = run_dispatch(rules, "praise")
    assert agent.calls == [("hotkey.pressed", "（主人想听你夸夸他）")]
    assert emitted[0].text == "你真棒"


def test_one_bad_entry_does_not_disable_the_rest(rules):
    r, _, tmp_path = rules
    (tmp_path / "hotkeys.yaml").write_text(
        """hotkeys:
  - {id: broken, keys: Ctrl+Alt+B, action: llm}
  - {id: greet, keys: Ctrl+Alt+W, action: reaction, lines: [2026-01-01, 嗨], note: ignored, cooldown: 9}
""",
        encoding="utf-8",
    )
    kind, hk = r.decide("greet")
    assert kind == "reaction" and hk.lines == ["2026-01-01", "嗨"]  # YAML date coerced to text
    assert r.decide("broken") is None


def test_llm_cooldown_floor_and_shared_window(rules):
    r, clock, tmp_path = rules
    (tmp_path / "hotkeys.yaml").write_text(
        """hotkeys:
  - {id: a, keys: Ctrl+Alt+A, action: llm, prompt: p, cooldown_seconds: 0}
  - {id: b, keys: Ctrl+Alt+B, action: llm, prompt: q, cooldown_seconds: 0}
""",
        encoding="utf-8",
    )
    assert r.decide("a")[0] == "llm"
    clock.t += 1
    assert r.decide("a") is None  # floor of 3 s despite cooldown 0
    assert r.decide("b") is None  # shared llm window
    clock.t += 3
    assert r.decide("b")[0] == "llm"


def test_stop_drops_replies_already_in_flight(tmp_path):
    import time as _time

    from fastapi.testclient import TestClient

    from api.app import create_app
    from providers.base import ChatResult

    (tmp_path / "backend.yaml").write_text(
        "llm:\n  profiles:\n    - {name: main, base_url: https://x.example/v1, model: m}\n  roles: {chat: main}\n",
        encoding="utf-8",
    )

    class Slow:
        def __init__(self, *_):
            pass

        async def chat(self, _messages, **_kw):
            await asyncio.sleep(0.2)
            return ChatResult(text='{"text": "长篇大论"}', model="m", latency_ms=1)

        async def aclose(self):
            pass

    app = create_app(config_path=tmp_path / "backend.yaml", token="t", provider_factory=Slow)
    auth = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        eid = client.post("/api/events", headers=auth, json={"type": "user.text", "payload": {"text": "说吧"}}).json()["id"]
        app.state.pet_hub.stopped_at = _time.time()
        _time.sleep(0.4)
        ev = next(e for e in client.get("/api/events", headers=auth).json()["events"] if e["id"] == eid)
    assert ev["result"] == {"error": "已被「停止说话」丢弃"}
