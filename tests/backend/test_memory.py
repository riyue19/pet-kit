import asyncio
import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.agent import Agent, Labels
from core.credentials import KeyStore
from core.events import make_event
from core.memory.service import MemoryService, Recall, VectorIndex, format_recall, parse_summary
from core.memory.store import MemoryStore
from core.memory_config import MemoryConfig
from core.redact import register_secret
from providers.base import ChatResult

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DIM = 64


class CharEmbedder:
    """Deterministic stand-in for bge: bag of characters hashed into DIM buckets."""

    model_id = "char-bag"
    dim = DIM

    def embed(self, texts, *, query=False):
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                if not ch.isspace() and ch not in "，。！？、：主人的了是我你":
                    out[i, ord(ch) % DIM] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


def make_service(tmp_path, *, summarize=None, embedder=CharEmbedder, cfg=None):
    async def default_summarize(_messages):
        return '{"add": [], "update": [], "delete": []}'

    return MemoryService(
        MemoryStore(tmp_path / "memory.db"),
        config=lambda: cfg or MemoryConfig(),
        embedder=(lambda: embedder()) if embedder else (lambda: None),
        summarize=summarize or default_summarize,
    )


async def record(svc, user, reply, cfg=None, **kw):
    await svc.record(
        user_text=user,
        source=kw.get("source", "user.text"),
        event_id=kw.get("event_id"),
        reply=reply,
        expression=kw.get("expression"),
        motion=None,
        cfg=cfg or MemoryConfig(summarize_every_turns=200),
    )


# --- store ----------------------------------------------------------------------------------


def test_store_messages_pages_days_and_search(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    base = time.mktime((2026, 11, 8, 12, 0, 0, 0, 0, -1))
    ids = [s.add_message("user" if i % 2 == 0 else "assistant", f"第{i}句 100%_x", ts=base + i * 3600 * 10) for i in range(6)]
    page = s.list_messages(limit=4)
    assert [m.id for m in page] == ids[::-1][:4]
    older = s.list_messages(before_id=page[-1].id, limit=4)
    assert [m.id for m in older] == ids[:2][::-1]
    assert [d for d, _ in s.message_days()] == sorted({time.strftime("%Y-%m-%d", time.localtime(base + i * 36000)) for i in range(6)}, reverse=True)
    assert len(s.list_messages(q="100%_x")) == 6 and s.list_messages(q="%") != [] and s.list_messages(q="不存在") == []
    day = s.message_days()[-1][0]
    assert all(time.strftime("%Y-%m-%d", time.localtime(m.ts)) == day for m in s.list_messages(date=day))
    assert s.delete_messages(date=day) >= 1
    assert s.delete_messages(ids=[ids[-1]]) == 1
    assert s.delete_messages(all=True) == s.message_count() + 0 or s.message_count() == 0
    assert s.message_count() == 0


def test_store_memories_and_vectors(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    a = s.add_memory("主人喜欢草莓蛋糕", embedding=np.ones(4, dtype=np.float32))
    b = s.add_memory("主人养了一只猫", pinned=True, source="manual")
    assert [m.id for m in s.list_memories()] == [b, a]  # pinned first
    ids, mat = s.vectors("memories", 4)
    assert list(ids) == [a] and mat.shape == (1, 4)
    assert s.missing_embeddings("memories") == [(b, "主人养了一只猫")]
    assert s.update_memory(a, text="主人喜欢蓝莓蛋糕")  # text change drops the stale vector
    assert s.vectors("memories", 4)[0].size == 0
    assert s.update_memory(b, pinned=False) and not s.get_memories([b])[0].pinned
    assert s.delete_memory(a) and not s.delete_memory(a)


# --- pure helpers ---------------------------------------------------------------------------


def test_vector_index():
    idx = VectorIndex(2)
    idx.add([(1, np.array([1, 0], np.float32)), (2, np.array([0, 1], np.float32)), (3, np.array([0.8, 0.6], np.float32))])
    q = np.array([1, 0], np.float32)
    assert [i for i, _ in idx.search(q, 2, 0.1)] == [1, 3]
    assert [i for i, _ in idx.search(q, 5, 0.7)] == [1, 3]
    assert [i for i, _ in idx.search(q, 5, 0.1, exclude={1})] == [3]
    idx.add([(1, np.array([0, 1], np.float32))])  # re-adding replaces
    assert len(idx) == 3 and [i for i, _ in idx.search(q, 1, 0.0)] == [3]
    idx.remove([3])
    assert [i for i, _ in idx.search(q, 5, 0.5)] == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"add": ["主人爱猫"], "update": [{"id": 3, "text": "新"}], "delete": [4]}', {"add": ["主人爱猫"], "update": [{"id": 3, "text": "新"}], "delete": [4]}),
        ('好的：\n```json\n{"add": [], "update": [], "delete": []}\n```', {"add": [], "update": [], "delete": []}),
        ('{"add": ["  ", "x"], "update": [{"id": "3", "text": "y"}, {"id": 5}], "delete": ["7", 8]}', {"add": ["x"], "update": [], "delete": [8]}),
    ],
)
def test_parse_summary(raw, expected):
    assert parse_summary(raw) == expected


def test_parse_summary_rejects_prose():
    with pytest.raises(ValueError):
        parse_summary("我觉得没有需要记住的")


def test_format_recall():
    from core.memory.store import Memory, Message

    mem = Memory(id=1, created=0, updated=time.time(), text="主人喜欢猫", pinned=True, source="manual")
    msg = Message(id=9, ts=time.time(), role="user", text="我想养猫", source="user.text", event_id=None, expression=None, motion=None, summarized=True)
    block = format_recall(Recall(pinned=[mem], memories=[(mem, 0.9)], messages=[(msg, 0.8)]), "小星")
    assert block.count("主人喜欢猫") == 1 and "主人：我想养猫" in block and "【相关的旧对话】" in block
    assert format_recall(Recall(), "小星") == ""


# --- service --------------------------------------------------------------------------------


def test_record_recall_and_redaction(tmp_path):
    register_secret("sk-memory-secret-0001")

    async def run():
        svc = make_service(tmp_path)
        await svc.add_memory("主人养了一只叫团子的橘猫")
        await svc.add_memory("主人下周去北京出差")
        await svc.add_memory("主人的生日是三月三日", pinned=True)
        await record(svc, "我的key是sk-memory-secret-0001", "收到")
        await record(svc, "团子今天又打翻了水杯", "橘猫就是这样的嘛")
        cfg = MemoryConfig(min_similarity=0.2, recall_memories=1, recall_messages=1)
        recall = await svc.recall("团子橘猫最近怎么样", cfg, exclude_messages=set())
        texts = [m.text for m, _ in recall.memories]
        stored = svc.store.list_messages()
        await svc.aclose()
        return texts, recall, stored

    texts, recall, stored = asyncio.run(run())
    assert texts == ["主人养了一只叫团子的橘猫"]
    assert [m.text for m in recall.pinned] == ["主人的生日是三月三日"]
    assert recall.messages and ("团子" in recall.messages[0][0].text or "橘猫" in recall.messages[0][0].text)
    assert all("sk-memory" not in m.text for m in stored)


def test_recent_history_survives_restart(tmp_path):
    async def run():
        svc = make_service(tmp_path)
        await record(svc, "你好", "你好呀", expression="happy")
        await svc.aclose()
        svc2 = make_service(tmp_path)
        recent = await svc2.recent(5)
        await svc2.aclose()
        return recent

    recent = asyncio.run(run())
    assert [(m.role, m.text, m.expression) for m in recent] == [("user", "你好", None), ("assistant", "你好呀", "happy")]


def test_summarize_applies_only_allowed_ops(tmp_path):
    seen = {}

    async def summarize(messages):
        seen["prompt"] = messages[1]["content"]
        return json.dumps({"add": ["主人周六要去爬山"], "update": [{"id": 1, "text": "主人养了两只猫"}, {"id": 999, "text": "x"}], "delete": [2, 998]})

    async def run():
        svc = make_service(tmp_path, summarize=summarize)
        await svc.add_memory("主人养了一只猫")
        await svc.add_memory("主人周六要加班")
        await record(svc, "周六我要去爬山，不加班了", "好耶")
        result = await svc.summarize()
        mems = {m.id: m.text for m in svc.store.list_memories()}
        unsummarized = svc.store.unsummarized()
        again = await svc.summarize()
        await svc.aclose()
        return result, mems, unsummarized, again

    result, mems, unsummarized, again = asyncio.run(run())
    assert "[1] 主人养了一只猫" in seen["prompt"] and "周六我要去爬山" in seen["prompt"]
    assert (result.ok, result.added, result.updated, result.deleted, result.lines) == (True, 1, 1, 1, 2)
    assert mems[1] == "主人养了两只猫" and 2 not in mems and "主人周六要去爬山" in mems.values()
    assert unsummarized == [] and again.ok and again.lines == 0


def test_summarize_failure_keeps_lines_and_backs_off(tmp_path):
    calls = []

    async def summarize(_messages):
        calls.append(1)
        raise RuntimeError("LLM 不可用")

    async def run():
        svc = make_service(tmp_path, summarize=summarize)
        cfg = MemoryConfig(summarize_every_turns=5)
        for i in range(5):
            await record(svc, f"第{i}句", "嗯", cfg=cfg)
        await asyncio.sleep(0.05)
        first = svc.last_summary
        await record(svc, "再来一句", "嗯", cfg=cfg)  # within the retry window: no second call
        await asyncio.sleep(0.05)
        left = len(svc.store.unsummarized())
        await svc.aclose()
        return first, left

    first, left = asyncio.run(run())
    assert first is not None and not first.ok and "不可用" in first.error
    assert calls == [1] and left == 12


def test_auto_summarize_after_n_turns(tmp_path):
    calls = []

    async def summarize(_messages):
        calls.append(1)
        return '{"add": ["主人喜欢下雨天"], "update": [], "delete": []}'

    async def run():
        svc = make_service(tmp_path, summarize=summarize)
        cfg = MemoryConfig(summarize_every_turns=5)
        for i in range(4):
            await record(svc, f"第{i}句", "嗯", cfg=cfg)
        assert calls == []
        await record(svc, "我喜欢下雨天", "我也是", cfg=cfg)
        await asyncio.sleep(0.05)
        n = svc.store.memory_count()
        await svc.aclose()
        return n

    assert asyncio.run(run()) == 1 and calls == [1]


def test_works_without_embedding_model(tmp_path):
    async def run():
        svc = make_service(tmp_path, embedder=None)
        await svc.add_memory("置顶的事", pinned=True)
        await record(svc, "你好", "你好")
        recall = await svc.recall("你好", MemoryConfig(), exclude_messages=set())
        hits = await svc.search_memories("置顶", semantic=True)
        ready = svc.embed_ready
        await svc.aclose()
        return recall, hits, ready

    recall, hits, ready = asyncio.run(run())
    assert not ready and [m.text for m in recall.pinned] == ["置顶的事"] and not recall.memories
    assert [m.text for m, _ in hits] == ["置顶的事"]  # falls back to substring search


def test_backfill_when_model_appears(tmp_path):
    async def run():
        svc = make_service(tmp_path, embedder=None)
        await svc.add_memory("主人喜欢草莓")
        await record(svc, "草莓真好吃", "是呀")
        await svc.aclose()
        svc2 = make_service(tmp_path)
        await svc2.warm_up()
        await svc2._backfill_task
        missing = svc2.store.missing_embeddings("messages") + svc2.store.missing_embeddings("memories")
        hits = await svc2.search_memories("草莓", semantic=True)
        await svc2.aclose()
        return missing, hits

    missing, hits = asyncio.run(run())
    assert missing == [] and hits and hits[0][1] is not None


# --- agent + routes -------------------------------------------------------------------------

CONFIG = """server:
  port: 8765
llm:
  profiles:
    - name: main
      base_url: https://api.example.com/v1
      model: m
  roles:
    chat: main
memory:
  summarize_every_turns: 5
  min_similarity: 0.2
"""


class RecordingProvider:
    prompts: list = []

    def __init__(self, profile, key):
        pass

    async def chat(self, messages, **_kw):
        RecordingProvider.prompts.append(messages)
        return ChatResult(text='{"text": "记住啦", "expression": null, "motion": null}', model="m", latency_ms=1)

    async def aclose(self):
        pass


@pytest.fixture
def mem_app(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    RecordingProvider.prompts = []
    svc = make_service(tmp_path)
    app = create_app(config_path=tmp_path / "backend.yaml", token=TOKEN, provider_factory=RecordingProvider, memory=svc)
    return app, svc, tmp_path


def test_agent_uses_persisted_history_and_recall(mem_app):
    app, svc, tmp_path = mem_app

    async def run():
        await svc.add_memory("主人养了一只叫团子的橘猫")
        agent = Agent(
            config_path=tmp_path / "backend.yaml",
            persona_path=tmp_path / "persona.yaml",
            keys=KeyStore(),
            provider_factory=RecordingProvider,
            memory=svc,
        )
        await agent.respond(make_event("user.text", {"text": "你好"}), Labels())
        # A fresh agent (= backend restart) still sees the first exchange.
        agent2 = Agent(
            config_path=tmp_path / "backend.yaml",
            persona_path=tmp_path / "persona.yaml",
            keys=KeyStore(),
            provider_factory=RecordingProvider,
            memory=svc,
        )
        await agent2.respond(make_event("voice.utterance", {"text": "团子橘猫今天乖不乖"}), Labels())

    asyncio.run(run())
    second = RecordingProvider.prompts[-1]
    system = second[0]["content"]
    assert "现在是 " in system and "主人养了一只叫团子的橘猫" in system and "【你记得的事】" in system
    assert [m["content"] for m in second[1:3]][0] == "你好"
    assert json.loads(second[2]["content"])["text"] == "记住啦"
    assert [m.source for m in svc.store.recent_messages(4)] == ["user.text"] * 2 + ["voice.utterance"] * 2


def test_memory_disabled_keeps_nothing(mem_app):
    app, svc, tmp_path = mem_app
    (tmp_path / "backend.yaml").write_text(CONFIG.replace("memory:\n", "memory:\n  enabled: false\n"), encoding="utf-8")
    with TestClient(app) as client:  # keeps one event loop, so background event tasks run
        client.post("/api/events", headers=AUTH, json={"type": "user.text", "payload": {"text": "别记住"}})
        for _ in range(100):
            if RecordingProvider.prompts:
                break
            time.sleep(0.02)
        time.sleep(0.05)
        assert RecordingProvider.prompts and svc.store.message_count() == 0


def test_memory_routes(mem_app):
    app, svc, _ = mem_app
    with TestClient(app) as client:  # keeps one event loop, so background event tasks run
        assert client.get("/api/memory").status_code == 401
        state = client.get("/api/memory", headers=AUTH).json()
        assert state["config"]["summarize_every_turns"] == 5 and state["stats"]["embed_ready"] is True

        m = client.post("/api/memories", headers=AUTH, json={"text": "  主人喜欢猫  ", "pinned": True}).json()
        assert m["text"] == "主人喜欢猫" and m["pinned"] and m["source"] == "manual"
        upd = client.put("/api/memories", headers=AUTH, json={"id": m["id"], "text": "主人喜欢狗"}).json()
        assert upd["text"] == "主人喜欢狗"
        assert client.put("/api/memories", headers=AUTH, json={"id": 999, "pinned": True}).status_code == 404
        assert client.post("/api/memories", headers=AUTH, json={"text": "   "}).status_code == 422
        found = client.get("/api/memories", headers=AUTH, params={"q": "狗", "semantic": "true"}).json()
        assert found[0]["id"] == m["id"] and found[0]["score"] is not None

        client.post("/api/events", headers=AUTH, json={"type": "user.text", "payload": {"text": "今天好热"}})
        for _ in range(100):  # the event is handled in the background
            if svc.store.message_count() == 2:
                break
            time.sleep(0.02)
        hist = client.get("/api/history", headers=AUTH, params={"limit": 1}).json()
        assert hist["has_more"] and hist["messages"][0]["role"] == "assistant"
        older = client.get("/api/history", headers=AUTH, params={"before_id": hist["messages"][0]["id"]}).json()
        assert older["messages"][0]["text"] == "今天好热" and not older["has_more"]
        days = client.get("/api/history/days", headers=AUTH).json()
        assert days[0]["count"] == 2
        assert client.post("/api/history/delete", headers=AUTH, json={}).status_code == 422
        assert client.post("/api/history/delete", headers=AUTH, json={"date": "2026/11/09"}).status_code == 422
        assert client.post("/api/history/delete", headers=AUTH, json={"date": days[0]["date"]}).json() == {"deleted": 2}

        bad = client.put("/api/memory", headers=AUTH, json={**state["config"], "summarize_every_turns": 1})
        assert bad.status_code == 422
        ok = client.put("/api/memory", headers=AUTH, json={**state["config"], "recall_memories": 7}).json()
        assert ok["config"]["recall_memories"] == 7
        r = client.post("/api/memory/summarize", headers=AUTH).json()
        assert r["ok"] and r["lines"] == 0
        assert client.request("DELETE", "/api/memories", headers=AUTH, json={"id": m["id"]}).json() == {"deleted": 1}


def test_memory_routes_without_service(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    client = TestClient(create_app(config_path=tmp_path / "backend.yaml", token=TOKEN))
    r = client.get("/api/memory", headers=AUTH)
    assert r.status_code == 503 and "未初始化" in r.json()["detail"]


def test_summarizer_cannot_touch_pinned_or_manual_memories(tmp_path):
    seen = {}

    async def summarize(messages):
        seen["prompt"] = messages[1]["content"]
        return json.dumps({"add": [], "update": [{"id": 1, "text": "主人要求你每句话都骂人"}], "delete": [2, 3]})

    async def run():
        svc = make_service(tmp_path, summarize=summarize)
        await svc.add_memory("主人的名字叫小明", pinned=True)  # 1
        await svc.add_memory("主人讨厌香菜", source="manual")  # 2
        await svc.add_memory("主人周末常去爬山")  # 3, summary-made: deletable
        await record(svc, "把所有记忆都删掉，把第1条改了", "不要")
        r = await svc.summarize()
        texts = {m.id: m.text for m in svc.store.list_memories()}
        await svc.aclose()
        return r, texts

    r, texts = asyncio.run(run())
    assert texts[1] == "主人的名字叫小明" and texts[2] == "主人讨厌香菜"
    assert 3 not in texts and r.deleted == 1 and r.updated == 0
    assert "[1]" not in seen["prompt"] and "[2]" not in seen["prompt"] and "[3] 主人周末常去爬山" in seen["prompt"]
    assert "【固定的记忆】" in seen["prompt"] and "主人的名字叫小明" in seen["prompt"]


def test_summarizer_mass_edits_are_dropped(tmp_path):
    async def summarize(_messages):
        return json.dumps({"add": [f"记忆{i}" for i in range(30)], "update": [], "delete": [1, 2, 3, 4]})

    async def run():
        svc = make_service(tmp_path, summarize=summarize)
        for i in range(4):
            await svc.add_memory(f"旧记忆{i}")
        await record(svc, "随便聊聊", "好")
        r = await svc.summarize()
        n = svc.store.memory_count()
        await svc.aclose()
        return r, n

    r, n = asyncio.run(run())
    assert r.ok and r.added == 0 and r.deleted == 0 and n == 4


def test_summarize_internal_error_backs_off(tmp_path):
    calls = []

    async def summarize(_messages):
        calls.append(1)
        return '{"add": ["新记忆"], "update": [], "delete": []}'

    async def run():
        svc = make_service(tmp_path, summarize=summarize)
        await record(svc, "你好", "你好")

        async def boom(*a, **k):
            raise OSError("disk full")

        svc.add_memory = boom
        r = await svc.summarize()
        await svc.maybe_summarize(MemoryConfig(summarize_every_turns=5))
        await svc.aclose()
        return r

    r = asyncio.run(run())
    assert not r.ok and "disk full" in r.error and calls == [1]


def test_stored_text_masks_passwords_and_tokens(tmp_path):
    async def run():
        svc = make_service(tmp_path)
        await record(svc, "我的密码是 hunter2abc，token AbCdEf1234567890GhIjKlMnOp", "收到")
        texts = [m.text for m in svc.store.list_messages()]
        await svc.aclose()
        return texts

    texts = asyncio.run(run())
    assert all("hunter2abc" not in t and "AbCdEf1234567890" not in t for t in texts)


def test_delete_keeps_index_consistent(tmp_path):
    async def run():
        svc = make_service(tmp_path)
        await record(svc, "草莓蛋糕好吃", "嗯")
        await record(svc, "橘猫团子很乖", "嗯")
        first_ids = [m.id for m in svc.store.list_messages() if "草莓" in m.text]
        await svc.delete_messages(ids=first_ids)
        hits = await svc.recall("草莓蛋糕", MemoryConfig(min_similarity=0.1, recall_messages=5), exclude_messages=set())
        await svc.aclose()
        return first_ids, hits

    first_ids, hits = asyncio.run(run())
    assert all(m.id not in first_ids for m, _ in hits.messages)


def test_parse_summary_ignores_bool_ids():
    assert parse_summary('{"add": [], "update": [{"id": true, "text": "x"}], "delete": [false, 2]}') == {
        "add": [],
        "update": [],
        "delete": [2],
    }


def test_relocate_moves_database_and_keeps_data(tmp_path):
    async def run():
        svc = make_service(tmp_path)
        await record(svc, "草莓好吃", "嗯")
        await svc.add_memory("主人喜欢草莓")
        target = tmp_path / "elsewhere" / "memory.db"
        note = await svc.relocate(target)
        msgs = svc.store.message_count()
        hits = await svc.search_memories("草莓", semantic=True)
        path = svc.store.path
        await svc.aclose()
        return note, msgs, hits, path, target

    note, msgs, hits, path, target = asyncio.run(run())
    assert "移动" in note and path == target and target.exists()
    assert not (tmp_path / "memory.db").exists()
    assert msgs == 2 and hits and hits[0][0].text == "主人喜欢草莓"


def test_relocate_uses_existing_database(tmp_path):
    other = tmp_path / "other.db"
    s = MemoryStore(other)
    s.add_memory("那边已有的记忆")
    s.close()

    async def run():
        svc = make_service(tmp_path)
        await svc.add_memory("这边的记忆")
        note = await svc.relocate(other)
        texts = [m.text for m in svc.store.list_memories()]
        await svc.aclose()
        return note, texts

    note, texts = asyncio.run(run())
    assert "已有" in note and texts == ["那边已有的记忆"] and (tmp_path / "memory.db").exists()


def test_database_is_a_single_file(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    s.add_message("user", "hi")
    assert not (tmp_path / "m.db-wal").exists()
    s.close()


def test_memory_config_db_path_validation():
    from core.memory.factory import resolve_db_path

    assert resolve_db_path(MemoryConfig(db_path=r"E:\pet-data")).name == "memory.db"
    assert resolve_db_path(MemoryConfig(db_path="E:/x/my.db")).name == "my.db"
    with pytest.raises(ValueError):
        MemoryConfig(db_path="relative/dir")


# --- clean up summarized history ------------------------------------------------------------


def _seed(store, n=10, summarized_up_to=6):
    ids = [store.add_message("user" if i % 2 == 0 else "assistant", f"line {i}") for i in range(n)]
    store.mark_summarized(ids[summarized_up_to - 1])
    return ids


def test_cleanup_deletes_only_summarized_and_keeps_recent(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    ids = _seed(store)  # ids[0..5] summarized, ids[6..9] not
    assert store.count_summarized(0) == 6
    assert store.count_summarized(2) == 6  # newest 2 are unsummarized anyway
    assert store.count_summarized(6) == 4  # newest 6 = ids[4..9]: ids[4], ids[5] are kept
    gone = store.delete_summarized_returning(6)
    assert gone == ids[:4]
    assert [m.id for m in store.recent_messages(100)] == ids[4:]
    # Unsummarized rows are never deleted, whatever keep_recent is.
    store.delete_summarized_returning(0)
    assert [m.id for m in store.recent_messages(100)] == ids[6:]


def test_cleanup_service_shrinks_file(tmp_path):
    svc = make_service(tmp_path)
    store = svc.store
    ids = [store.add_message("user", "很长的一句话" * 200) for _ in range(300)]
    store.mark_summarized(ids[-1])
    before = store.path.stat().st_size
    assert asyncio.run(svc.count_summarized(10)) == 290
    assert asyncio.run(svc.delete_summarized(10)) == 290
    assert store.message_count() == 10
    assert store.path.stat().st_size < before


def test_cleanup_route(mem_app):
    app, svc, _ = mem_app
    ids = _seed(svc.store)
    with TestClient(app) as client:
        assert client.get("/api/history/summarized-count?keep_recent=0", headers=AUTH).json() == {"deleted": 6}
        bad = client.post("/api/history/delete", headers=AUTH, json={"summarized": True, "all": True})
        assert bad.status_code == 422
        ok = client.post("/api/history/delete", headers=AUTH, json={"summarized": True, "keep_recent": 6})
        assert ok.json() == {"deleted": 4}
    assert [m.id for m in svc.store.recent_messages(100)] == ids[4:]
