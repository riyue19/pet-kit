import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.agent import Agent, Labels
from core.credentials import KeyStore
from core.dispatcher import Dispatcher
from core.events import client_event, make_event
from core.image_stash import MAX_ENTRIES, MAX_IMAGE_BYTES, TTL_SECONDS, ImageStash
from providers.base import ChatResult

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LABELS = Labels(expressions=["happy"], motions=[])
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake bytes").decode()
CONFIG = """llm:
  profiles:
    - {name: chat1, base_url: "https://x.example/v1", model: m}
    - {name: vis1, base_url: "https://x.example/v1", model: v}
  roles: {chat: chat1, vision: vis1}
"""


class Recorder:
    calls: list = []

    def __init__(self, profile, key):
        self.profile = profile

    async def chat(self, messages, **_kw):
        Recorder.calls.append((self.profile.name, messages))
        return ChatResult(text='{"text": "这是一张图"}', model="m", latency_ms=1)

    async def aclose(self):
        pass


# --- stash ----------------------------------------------------------------------------------


def test_stash_roundtrip_takes_once():
    s = ImageStash()
    i = s.put(PNG)
    assert i.startswith("img_") and s.take(i) == PNG and s.take(i) is None


@pytest.mark.parametrize("bad", ["http://x/y.png", "data:text/html;base64,AAAA", "data:image/png;base64,@@@", "data:image/svg+xml;base64,AAAA"])
def test_stash_rejects_non_images(bad):
    with pytest.raises(ValueError):
        ImageStash().put(bad)


def test_stash_limits_size_count_and_age(monkeypatch):
    s = ImageStash()
    big = "data:image/png;base64," + base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode()
    with pytest.raises(ValueError, match="太大"):
        s.put(big)
    ids = [s.put(PNG) for _ in range(MAX_ENTRIES + 2)]
    assert s.take(ids[0]) is None and s.take(ids[-1]) == PNG  # oldest evicted
    import core.image_stash as mod

    t = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])
    s2 = ImageStash()
    i = s2.put(PNG)
    t[0] += TTL_SECONDS + 1
    assert s2.take(i) is None


# --- event ----------------------------------------------------------------------------------


def test_event_payload_carries_only_the_id():
    e = client_event("user.text", {"text": "翻译", "image_id": "img_0123456789abcdef"})
    assert e.payload == {"text": "翻译", "image_id": "img_0123456789abcdef"}
    assert client_event("user.text", {"text": "hi"}).payload == {"text": "hi"}
    with pytest.raises(ValueError):
        client_event("user.text", {"text": "x", "image_id": "../etc"})


# --- route + full flow ----------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    Recorder.calls = []
    return create_app(config_path=tmp_path / "backend.yaml", token=TOKEN, provider_factory=Recorder), tmp_path


def test_upload_route_validates_and_requires_token(app):
    a, _ = app
    with TestClient(a) as c:
        assert c.post("/api/pet/image", json={"data_url": PNG}).status_code == 401
        assert c.post("/api/pet/image", headers=AUTH, json={"data_url": "nope"}).status_code == 422
        r = c.post("/api/pet/image", headers=AUTH, json={"data_url": PNG})
        assert r.status_code == 200 and r.json()["id"].startswith("img_")


def make_dispatcher(tmp_path, images, out):
    agent = Agent(config_path=tmp_path / "backend.yaml", persona_path=tmp_path / "p.yaml", keys=KeyStore(), provider_factory=Recorder)

    async def emit(output, event):
        out.append(output)

    async def thinking(_v):
        pass

    return Dispatcher(agent=agent, rules=None, labels=lambda: LABELS, emit=emit, set_thinking=thinking, images=images)


def test_image_goes_to_vision_profile_and_is_not_kept(app):
    _, tmp = app
    images, out = ImageStash(), []
    d = make_dispatcher(tmp, images, out)
    image_id = images.put(PNG)
    asyncio.run(d(make_event("user.text", {"text": "翻译一下", "image_id": image_id})))
    name, messages = Recorder.calls[0]
    assert name == "vis1" and out[0].text == "这是一张图"
    parts = messages[-1]["content"]
    assert parts[0]["text"] == "翻译一下" and parts[1]["image_url"]["url"] == PNG
    assert images.take(image_id) is None  # consumed
    # History keeps the words and a marker, never the picture.
    assert "data:image" not in str(d._agent._history)


def test_expired_image_gets_a_friendly_reply_and_no_llm_call(app):
    _, tmp = app
    out = []
    d = make_dispatcher(tmp, ImageStash(), out)
    asyncio.run(d(make_event("user.text", {"text": "看图", "image_id": "img_0123456789abcdef"})))
    assert "过期" in out[0].text and Recorder.calls == []


def test_falls_back_to_chat_profile_without_vision_role(app):
    _, tmp = app
    (tmp / "backend.yaml").write_text(CONFIG.replace("vision: vis1", "vision: null"), encoding="utf-8")
    images, out = ImageStash(), []
    d = make_dispatcher(tmp, images, out)
    asyncio.run(d(make_event("user.text", {"text": "看图", "image_id": images.put(PNG)})))
    assert Recorder.calls[0][0] == "chat1"
