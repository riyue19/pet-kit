import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.event_bus import EventBus
from core.events import make_event
from core.logs import EventIdFilter, RingHandler, current_event, setup_logging
from core.redact import RedactingFilter, register_secret
from providers.base import ChatResult

TOKEN = "test-token"


def rq(ring, **kw):
    entries, _seq = ring.query(**kw)
    return entries


AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def ring():
    """A ring attached to the root logger the way setup_logging does (without touching files)."""
    r = RingHandler(size=50)
    r.addFilter(RedactingFilter())
    r.addFilter(EventIdFilter())
    root = logging.getLogger()
    prev = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(r)
    yield r
    root.removeHandler(r)
    root.setLevel(prev)


def test_ring_records_location_and_filters(ring):
    log = logging.getLogger("pet.test")
    log.info("hello 世界")
    log.warning("careful")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed here")

    entries = rq(ring)
    assert [e["msg"] for e in entries] == ["hello 世界", "careful", "failed here"]
    err = entries[-1]
    assert err["level"] == "ERROR" and err["logger"] == "pet.test"
    assert err["file"].endswith("test_logs.py") and err["func"] == "test_ring_records_location_and_filters"
    assert err["line"] > 0 and "ValueError: boom" in err["exc"]
    assert [e["msg"] for e in rq(ring, level="WARNING")] == ["careful", "failed here"]
    assert [e["msg"] for e in rq(ring, q="BOOM")] == ["failed here"]  # matches the stack, case-insensitive
    assert rq(ring, logger="other") == []
    assert rq(ring, after=entries[1]["seq"]) == [err]
    assert [e["msg"] for e in rq(ring, limit=1)] == ["failed here"]


def test_ring_redacts_secrets(ring):
    register_secret("sk-super-secret-value-123")
    log = logging.getLogger("pet.test")
    try:
        raise RuntimeError("auth sk-super-secret-value-123 rejected")
    except RuntimeError:
        log.exception("key was %s", "sk-super-secret-value-123")
    e = rq(ring)[-1]
    assert "sk-super" not in e["msg"] and "sk-super" not in e["exc"]


def test_ring_is_bounded(ring):
    log = logging.getLogger("pet.test")
    for i in range(80):
        log.info("n%d", i)
    entries = rq(ring, limit=1000)
    assert len(entries) == 50 and entries[-1]["msg"] == "n79" and ring.last_seq >= 80


def test_event_id_follows_handler_and_spawned_tasks(ring):
    bus = EventBus()
    log = logging.getLogger("pet.test")

    async def handler(event):
        log.info("in handler")

        async def child():
            log.info("in child task")

        await asyncio.create_task(child())

    async def run():
        bus.subscribe(handler)
        event = make_event("user.text", {"text": "hi"})
        bus.publish(event)
        await bus.drain()
        log.info("outside")
        return event.id

    eid = asyncio.run(run())
    by_msg = {e["msg"]: e["event_id"] for e in rq(ring)}
    assert by_msg["in handler"] == eid and by_msg["in child task"] == eid
    assert by_msg["outside"] is None
    assert current_event.get() is None


def test_bus_history_and_results():
    bus = EventBus(history=3)

    async def run():
        events = [make_event("user.text", {"text": str(i)}) for i in range(4)]
        for e in events:
            bus.publish(e)
        await bus.drain()
        bus.record_result(events[-1].id, {"output": {"text": "ok"}})
        return events

    events = asyncio.run(run())
    hist = bus.history()
    assert [h["payload"]["text"] for h in hist] == ["1", "2", "3"]
    assert hist[-1]["result"] == {"output": {"text": "ok"}} and hist[0]["result"] is None
    assert [h["payload"]["text"] for h in bus.history(after=hist[1]["seq"])] == ["3"]
    assert bus.history(type="voice.utterance") == []
    assert events[-1].id == hist[-1]["id"]


def test_failing_handler_is_logged_with_event_id(ring):
    bus = EventBus()

    async def bad(_event):
        raise KeyError("nope")

    async def run():
        bus.subscribe(bad)
        e = make_event("user.text", {"text": "x"})
        bus.publish(e)
        await bus.drain()
        return e.id

    eid = asyncio.run(run())
    err = [e for e in rq(ring, level="ERROR")][-1]
    assert err["event_id"] == eid and "KeyError" in err["exc"]
    assert bus.history()[-1]["result"] == {"error": "处理失败，详见日志"}


def test_setup_logging_writes_json_lines(tmp_path):
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    try:
        ring = setup_logging(tmp_path)
        logging.getLogger("pet.file").warning("写入文件")
        for h in root.handlers:
            h.flush()
        lines = (tmp_path / "backend.jsonl").read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[-1])
        assert entry["msg"] == "写入文件" and entry["level"] == "WARNING" and entry["file"].endswith("test_logs.py")
        assert rq(ring)[-1]["msg"] == "写入文件"
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        root.setLevel(saved[0])
        for h in saved[1]:
            root.addHandler(h)


# --- console routes -------------------------------------------------------------------------

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
PERSONA = """# 人设注释要保留
name: 小星
system_prompt: |
  第一行
  第二行
history_turns: 8
max_reply_chars: 80
"""


class EchoProvider:
    def __init__(self, profile, key):
        pass

    async def chat(self, messages, **_kw):
        return ChatResult(text='{"text": "收到", "expression": null, "motion": null}', model="m", latency_ms=1)

    async def aclose(self):
        pass


@pytest.fixture
def client(tmp_path, ring):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "persona.yaml").write_text(PERSONA, encoding="utf-8")
    app = create_app(
        config_path=tmp_path / "backend.yaml", token=TOKEN, provider_factory=EchoProvider, log_ring=ring, log_dir=tmp_path
    )
    with TestClient(app) as c:  # one event loop for the whole test, so background event tasks finish
        yield c


def test_routes_require_token(client):
    for path in ("/api/logs", "/api/events", "/api/persona"):
        assert client.get(path).status_code == 401


def test_logs_route(client):
    logging.getLogger("pet.route").error("route error")
    body = client.get("/api/logs", headers=AUTH, params={"level": "ERROR"}).json()
    assert body["entries"][-1]["msg"] == "route error" and body["log_dir"]
    again = client.get("/api/logs", headers=AUTH, params={"after": body["last_seq"], "level": "ERROR"}).json()
    assert again["entries"] == []
    assert client.get("/api/logs", headers=AUTH, params={"level": "LOUD"}).status_code == 422


def test_inject_event_runs_pipeline_and_records_result(client):
    r = client.post("/api/events", headers=AUTH, json={"type": "voice.utterance", "payload": {"text": "你好"}})
    eid = r.json()["id"]
    events = client.get("/api/events", headers=AUTH).json()
    mine = next(e for e in events["events"] if e["id"] == eid)
    assert mine["type"] == "voice.utterance" and mine["payload"] == {"text": "你好", "duration": 0.0}
    assert mine["result"]["output"]["text"] == "收到"
    assert "voice.utterance" in events["injectable"]
    assert {"screen.changed", "timer.idle", "user.text"} <= set(events["types"])
    logs = client.get("/api/logs", headers=AUTH, params={"event_id": eid}).json()["entries"]
    msgs = " ".join(e["msg"] for e in logs)
    assert "控制台注入事件" in msgs and "调用 LLM" in msgs and "LLM 回复" in msgs


@pytest.mark.parametrize(
    "body",
    [
        {"type": "screen.changed", "payload": {}},
        {"type": "user.text", "payload": {"text": ""}},
        {"type": "pet.interaction", "payload": {"kind": "click", "area": "tail"}},
    ],
)
def test_inject_rejects_bad_events(client, body):
    r = client.post("/api/events", headers=AUTH, json=body)
    assert r.status_code == 422 and r.json()["detail"]


def test_persona_roundtrip_keeps_comments(client, tmp_path):
    p = client.get("/api/persona", headers=AUTH).json()
    assert p["name"] == "小星" and p["system_prompt"] == "第一行\n第二行\n"
    p["name"] = "小月"
    p["system_prompt"] = "新的\n提示词"
    saved = client.put("/api/persona", headers=AUTH, json=p).json()
    assert saved["name"] == "小月"
    text = (tmp_path / "persona.yaml").read_text(encoding="utf-8")
    assert "# 人设注释要保留" in text and "system_prompt: |" in text
    assert client.get("/api/persona", headers=AUTH).json()["system_prompt"] == "新的\n提示词"
    bad = client.put("/api/persona", headers=AUTH, json={**p, "history_turns": 99})
    assert bad.status_code == 422 and "history_turns" in bad.json()["detail"]


def test_query_seq_matches_snapshot(ring):
    log = logging.getLogger("pet.test")
    log.info("a")
    entries, seq = ring.query()
    assert seq == entries[-1]["seq"]
    log.info("b")
    newer, seq2 = ring.query(after=seq)
    assert [e["msg"] for e in newer] == ["b"] and seq2 == newer[-1]["seq"]


def test_bad_log_call_never_raises(ring):
    logging.getLogger("pet.test").info("needs %s and %s", "only-one")  # mismatched args
    assert "only-one" in rq(ring)[-1]["msg"]


def test_emit_without_pet_skips_tts(client):
    hub = client.app.state.pet_hub
    calls = []

    async def fake_say(*a, **k):
        calls.append(a)

    hub.speaker.say = fake_say
    r = client.post("/api/events", headers=AUTH, json={"type": "user.text", "payload": {"text": "没人在听"}})
    events = client.get("/api/events", headers=AUTH).json()["events"]
    assert next(e for e in events if e["id"] == r.json()["id"])["result"]["output"]["text"] == "收到"
    assert calls == []


def test_persona_rejects_non_mapping_file(client, tmp_path):
    (tmp_path / "persona.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    r = client.put("/api/persona", headers=AUTH, json={"name": "x", "system_prompt": "y", "history_turns": 1, "max_reply_chars": 20})
    assert r.status_code in (422, 500) and "映射" in r.json()["detail"]
