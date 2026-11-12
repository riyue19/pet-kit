import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.app import create_app
from core import llm_chain
from core.agent import Agent, Labels, parse_output
from core.credentials import KeyStore
from core.events import make_event
from core.status import StatusService
from core.status_config import Stat, StatusChange, StatusConfig, Tier, default_affection
from providers.base import ChatResult

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
REPLY = '{"text": "嘿嘿", "status": [{"id": "affection", "delta": "+0.3", "reason": "被夸了"}]}'


class ScriptedProvider:
    calls: list = []
    replies: list = []

    def __init__(self, profile, key):
        self.key = key

    async def chat(self, messages, **_kw):
        ScriptedProvider.calls.append((self.key, messages))
        return ChatResult(text=ScriptedProvider.replies.pop(0), model="m", latency_ms=1)

    async def aclose(self):
        pass


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    ScriptedProvider.calls, ScriptedProvider.replies = [], []
    llm_chain.reset_cooldowns()
    return tmp_path


def make_service(tmp_path, enabled=True, today=None):
    (tmp_path / "status.yaml").write_text(f"enabled: {'true' if enabled else 'false'}\n", encoding="utf-8")
    kw = {"today": today} if today else {}
    return StatusService(tmp_path / "status.yaml", tmp_path / "status.json", **kw)


def make_agent(config_dir, service):
    return Agent(
        config_path=config_dir / "backend.yaml",
        persona_path=config_dir / "persona.yaml",
        keys=KeyStore(),
        provider_factory=ScriptedProvider,
        status=service,
    )


def say(agent, text="你好"):
    return asyncio.run(agent.respond(make_event("user.text", {"text": text}), LABELS))


def last_system():
    return ScriptedProvider.calls[-1][1][0]["content"]


def ch(delta, id="affection", reason=""):
    return StatusChange(id=id, delta=delta, reason=reason)


# --- config ---------------------------------------------------------------------------------


def test_defaults():
    cfg = StatusConfig()
    assert cfg.enabled is False
    assert [s.id for s in cfg.stats] == ["affection"]
    a = cfg.stats[0]
    assert a.color == "#ff7eb6"
    assert len(a.tiers) == 12
    assert sum(t.min < 0 for t in a.tiers) == 2
    assert sum(t.min >= 0 for t in a.tiers) == 10


def test_affection_added_first():
    cfg = StatusConfig(stats=[Stat(id="mood", name="心情")])
    assert [s.id for s in cfg.stats] == ["affection", "mood"]


def test_invalid_configs():
    with pytest.raises(ValidationError):
        StatusConfig(stats=[Stat(id="a", name="x"), Stat(id="a", name="y")])
    with pytest.raises(ValidationError):
        Stat(id="a", name="x", min=5, max=5)
    with pytest.raises(ValidationError):
        Stat(id="a", name="x", min=10, max=5, initial=7)
    with pytest.raises(ValidationError):
        Stat(id="a", name="x", initial=101)
    with pytest.raises(ValidationError):
        Stat(id="a", name="x", color="red")
    with pytest.raises(ValidationError):
        Stat(id="a", name="x", color="#12345")
    for bad in ["A", "1a", "a-b", "", "a" * 25]:
        with pytest.raises(ValidationError):
            Stat(id=bad, name="x")
    with pytest.raises(ValidationError):
        Stat(id="a", name="x", tiers=[Tier(min=1, name="p"), Tier(min=1, name="q")])


def test_tiers_sorted():
    s = Stat(id="a", name="x", tiers=[Tier(min=10, name="hi"), Tier(min=-5, name="lo"), Tier(min=0, name="mid")])
    assert [t.name for t in s.tiers] == ["lo", "mid", "hi"]


def test_tier_for():
    s = Stat(id="a", name="x", min=-10, max=100, tiers=[Tier(min=0, name="mid"), Tier(min=10, name="hi"), Tier(min=-5, name="lo")])
    assert s.tier_for(5).name == "mid"
    assert s.tier_for(10).name == "hi"
    assert s.tier_for(99).name == "hi"
    assert s.tier_for(-8).name == "lo"
    assert Stat(id="a", name="x").tier_for(5) is None


# --- parse_output ---------------------------------------------------------------------------


def parse(status, ids=("affection",)):
    import json

    return parse_output(json.dumps({"text": "hi", "status": status}), LABELS, status_ids=ids)


def test_parse_empty_ids():
    assert parse([{"id": "affection", "delta": "+1"}], ids=()).status == []


def test_parse_deltas():
    cases = [("+0.3", 0.3), ("-1", -1.0), (0.5, 0.5), ("＋2", 2.0)]
    for raw, want in cases:
        out = parse([{"id": "affection", "delta": raw}])
        assert [c.delta for c in out.status] == [want], raw


def test_parse_drops_bad_entries():
    for raw in [0, "abc", True]:
        assert parse([{"id": "affection", "delta": raw}]).status == []
    assert parse([{"id": "affection"}]).status == []
    assert parse([{"id": "nope", "delta": "+1"}]).status == []


def test_parse_first_per_id_and_reason():
    out = parse([{"id": "affection", "delta": "+1"}, {"id": "affection", "delta": "-1"}])
    assert [c.delta for c in out.status] == [1.0]
    long = parse([{"id": "affection", "delta": "+1", "reason": "x" * 30}]).status[0]
    assert len(long.reason) == 20
    spaced = parse([{"id": "affection", "delta": "+1", "reason": "  你好   \n 世界  "}]).status[0]
    assert spaced.reason == "你好 世界"


def test_parse_status_not_list():
    assert parse("nope").status == []
    assert parse({"id": "affection", "delta": "+1"}).status == []


# --- StatusService --------------------------------------------------------------------------


def test_disabled(tmp_path):
    svc = make_service(tmp_path, enabled=False)
    assert svc.prompt() is None
    assert svc.apply([ch(1)]) == []
    assert svc.view(svc.config())["stats"][0]["value"] == 0


def test_clipping(tmp_path):
    svc = make_service(tmp_path)
    assert svc.apply([ch(5)])[0].delta == 0.5
    out = svc.apply([ch(-10)])
    assert out[0].delta == -2
    assert out[0].value == -1.5


def test_daily_limit(tmp_path):
    day = ["2026-01-01"]
    svc = make_service(tmp_path, today=lambda: day[0])
    for _ in range(10):
        assert svc.apply([ch(0.5)])[0].delta == 0.5
    assert svc.apply([ch(0.5)]) == []
    assert svc.view(svc.config())["stats"][0]["value"] == 5
    # falling is not limited
    assert svc.apply([ch(-1)])[0].value == 4
    day[0] = "2026-01-02"
    assert svc.apply([ch(0.5)])[0].value == 4.5


def test_clamped_range_and_negative(tmp_path):
    svc = make_service(tmp_path)
    svc.set_value("affection", -19)
    out = svc.apply([ch(-10)])
    assert out[0].value == -20 and out[0].delta == -1
    assert svc.apply([ch(-1)]) == []
    svc.set_value("affection", 1000)
    assert svc.apply([ch(0.5)]) == []
    assert svc.view(svc.config())["stats"][0]["value"] == 100


def test_change_fields_and_tier(tmp_path):
    svc = make_service(tmp_path)
    c = svc.apply([ch(0.5, reason="r")])[0]
    assert c.name == "好感度" and c.value == 0.5 and c.color == "#ff7eb6" and c.reason == "r"
    assert c.tier is None
    a = default_affection()
    assert c.ratio == pytest.approx((c.value - a.min) / (a.max - a.min), abs=1e-4)
    svc.set_value("affection", 9.8)
    c2 = svc.apply([ch(0.5)])[0]
    assert c2.tier == "认识"


def test_persistence(tmp_path):
    svc = make_service(tmp_path)
    svc.apply([ch(0.5)])
    again = StatusService(tmp_path / "status.yaml", tmp_path / "status.json")
    assert again.view(again.config())["stats"][0]["value"] == 0.5


def test_broken_yaml(tmp_path):
    svc = make_service(tmp_path)
    (tmp_path / "status.yaml").write_text("stats: 3\n", encoding="utf-8")
    assert svc.prompt() is None
    assert svc.apply([ch(0.5)]) == []


def test_prompt(tmp_path):
    svc = make_service(tmp_path)
    p = svc.prompt()
    assert "affection" in p.ids
    assert "affection" in p.stats and "当前 0" in p.stats
    assert "陌生" in p.attitude
    svc.set_value("affection", 35)
    assert "朋友" in svc.prompt().attitude


# --- Agent ----------------------------------------------------------------------------------


def test_agent_enabled(config_dir):
    svc = make_service(config_dir)
    ScriptedProvider.replies = [REPLY]
    out = say(make_agent(config_dir, svc))
    sys = last_system()
    assert "【状态值】" in sys and "【你现在对主人的态度】" in sys and '"status"' in sys
    assert len(out.status) == 1
    c = out.status[0]
    # The agent only passes on what the model asked for; api.app's emit applies it once the reply goes out.
    assert c.delta == 0.3 and c.reason == "被夸了"
    assert svc.view(svc.config())["stats"][0]["value"] == 0
    assert svc.apply(out.status)[0].value == 0.3


def test_agent_disabled(config_dir):
    svc = make_service(config_dir, enabled=False)
    ScriptedProvider.replies = [REPLY]
    out = say(make_agent(config_dir, svc))
    assert "【状态值】" not in last_system()
    assert out.status == []


def test_agent_proactive(config_dir):
    svc = make_service(config_dir)
    ScriptedProvider.replies = [REPLY]
    agent = make_agent(config_dir, svc)
    turn = asyncio.run(agent.respond_proactive(make_event("timer.idle", {}), LABELS, situation="主人很久没说话"))
    sys = last_system()
    assert "【你现在对主人的态度】" in sys and "【状态值】" not in sys
    assert turn.output.status == []
    assert svc.view(svc.config())["stats"][0]["value"] == 0


# --- routes ---------------------------------------------------------------------------------


@pytest.fixture
def client(config_dir):
    return TestClient(create_app(config_path=config_dir / "backend.yaml", token=TOKEN, provider_factory=ScriptedProvider))


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_get_status(client):
    assert client.get("/api/status").status_code == 401
    r = client.get("/api/status", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert {"config", "stats", "recent"} <= set(body)
    assert body["stats"][0] == {"id": "affection", "value": 0, "tier": "陌生", "gained_today": 0}


def test_put_status(client, config_dir):
    body = {"enabled": True, "stats": [{"id": "mood", "name": "心情"}]}
    r = client.put("/api/status", headers=auth(), json=body)
    assert r.status_code == 200
    assert (config_dir / "status.yaml").exists()
    got = client.get("/api/status", headers=auth()).json()
    assert got["config"]["enabled"] is True
    assert [s["id"] for s in got["config"]["stats"]] == ["affection", "mood"]
    bad = {"enabled": True, "stats": [{"id": "mood", "name": "心情", "min": 10, "max": 5}]}
    assert client.put("/api/status", headers=auth(), json=bad).status_code == 422


def test_post_value(client):
    r = client.post("/api/status/value", headers=auth(), json={"id": "affection", "value": 35})
    assert r.status_code == 200
    body = r.json()
    assert body["stats"][0]["value"] == 35 and body["stats"][0]["tier"] == "朋友"
    assert body["recent"][0]["reason"] == "在控制台手动修改"
    r = client.post("/api/status/value", headers=auth(), json={"id": "affection", "value": None})
    assert r.json()["stats"][0]["value"] == 0
    r = client.post("/api/status/value", headers=auth(), json={"id": "nope", "value": 1})
    assert r.status_code == 404
    r = client.post("/api/status/value", headers=auth(), json={"id": "affection", "value": 500})
    assert r.json()["stats"][0]["value"] == 100


# --- applied when the reply goes out (api.app emit) -----------------------------------------


def _chat(ws, text="你真好"):
    ws.send_json({"type": "hello", "token": TOKEN, "labels": {"expressions": [], "motions": []}})
    assert ws.receive_json() == {"type": "ready"}
    assert ws.receive_json() == {"type": "mic", "listen": False}
    ws.send_json({"type": "event", "event": {"type": "user.text", "payload": {"text": text}}})
    assert ws.receive_json() == {"type": "status", "thinking": True}
    assert ws.receive_json() == {"type": "status", "thinking": False}


def _enabled_client(config_dir):
    (config_dir / "status.yaml").write_text("enabled: true\n", encoding="utf-8")
    ScriptedProvider.replies = [REPLY]
    return TestClient(create_app(config_path=config_dir / "backend.yaml", token=TOKEN, provider_factory=ScriptedProvider))


def test_ws_reply_applies_and_sends_change(config_dir):
    c = _enabled_client(config_dir)
    with c.websocket_connect("/ws/pet") as ws:
        _chat(ws)
        msg = ws.receive_json()
        assert msg["type"] == "output"
        (change,) = msg["output"]["status"]
        assert change["id"] == "affection" and change["delta"] == 0.3 and change["value"] == 0.3
        assert change["name"] == "好感度" and change["reason"] == "被夸了" and change["color"] == "#ff7eb6"
        assert c.get("/api/status", headers=auth()).json()["stats"][0]["value"] == 0.3


def test_manual_value_is_shown_on_the_pet(config_dir):
    c = _enabled_client(config_dir)
    with c.websocket_connect("/ws/pet") as ws:
        ws.send_json({"type": "hello", "token": TOKEN, "labels": {"expressions": [], "motions": []}})
        assert ws.receive_json() == {"type": "ready"}
        assert ws.receive_json() == {"type": "mic", "listen": False}
        assert c.post("/api/status/value", headers=auth(), json={"id": "affection", "value": 10.2}).status_code == 200
        msg = ws.receive_json()
        (change,) = msg["output"]["status"]
        assert msg["type"] == "output" and msg["output"]["text"] == ""
        assert change["delta"] == 10.2 and change["tier"] == "认识" and change["reason"] == "在控制台手动修改"


def test_stopped_reply_does_not_change_values(config_dir):
    c = _enabled_client(config_dir)
    c.app.state.pet_hub.stopped_at = float("inf")  # as if 停止说话 was pressed while the reply was generated
    with c.websocket_connect("/ws/pet") as ws:
        _chat(ws)
        body = c.get("/api/status", headers=auth()).json()
        assert body["stats"][0]["value"] == 0 and body["recent"] == []
