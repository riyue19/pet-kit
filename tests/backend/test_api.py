import json

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import remove_endpoint_file, write_endpoint_file
from core.credentials import SERVICE
from core.llm_config import load_backend_config
from providers.base import ChatResult, ProviderError

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeProvider:
    calls: list = []

    def __init__(self, profile, key, fail=False):
        self.profile, self.key, self.fail = profile, key, fail

    async def chat(self, messages, **_kw):
        FakeProvider.calls.append((self.profile.name, self.key, messages))
        if self.fail:
            raise ProviderError("鉴权失败（401），请检查 API Key")
        return ChatResult(text="pong", model=self.profile.model, latency_ms=5)

    async def aclose(self):
        pass


@pytest.fixture
def config_path(tmp_path):
    p = tmp_path / "backend.yaml"
    p.write_text("server:\n  port: 8765\n", encoding="utf-8")
    return p


@pytest.fixture
def client(config_path):
    FakeProvider.calls = []
    app = create_app(config_path=config_path, token=TOKEN, provider_factory=lambda p, k: FakeProvider(p, k))
    return TestClient(app)


def body(*profiles, chat=None, vision=None):
    return {"profiles": list(profiles), "roles": {"chat": chat, "vision": vision}}


def prof(name="main", **kw):
    return {"name": name, "base_url": "https://api.example.com/v1", "model": "m", **kw}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_requires_token(client, headers):
    assert client.get("/api/health", headers=headers).status_code == 401
    assert client.get("/api/llm", headers=headers).status_code == 401


def test_docs_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_health(client):
    assert client.get("/api/health", headers=AUTH).json()["ok"] is True


def test_save_and_read_back(client, config_path):
    r = client.put("/api/llm", headers=AUTH, json=body(prof(temperature=0.2, extra={"seed": 1}), chat="main"))
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["profiles"][0]["key"] == {"set": False, "hint": None}
    assert state["roles"]["chat"] == "main"
    assert load_backend_config(config_path).llm.profiles[0].extra == {"seed": 1}
    assert client.get("/api/llm", headers=AUTH).json() == state


def test_save_rejects_invalid(client):
    r = client.put("/api/llm", headers=AUTH, json=body(prof(extra={"model": "x"})))
    assert r.status_code == 422
    assert "保留字段" in r.json()["detail"]
    r = client.put("/api/llm", headers=AUTH, json=body(prof(), chat="ghost"))
    assert r.status_code == 422


def test_key_is_stored_in_keyring_never_returned(client, config_path, memory_keyring):
    client.put("/api/llm", headers=AUTH, json=body(prof()))
    secret = "sk-live-abcdefghijklmn1234"
    r = client.put("/api/llm/key", headers=AUTH, json={"profile": "main", "api_key": secret})
    assert r.json() == {"set": True, "hint": "…1234"}
    assert memory_keyring.store[(SERVICE, "main")] == secret
    assert secret not in config_path.read_text(encoding="utf-8")
    assert secret not in client.get("/api/llm", headers=AUTH).text


def test_key_for_unknown_profile_rejected(client):
    r = client.put("/api/llm/key", headers=AUTH, json={"profile": "nope", "api_key": "sk-x"})
    assert r.status_code == 404


def test_rename_moves_key_and_removal_deletes_it(client, memory_keyring):
    client.put("/api/llm", headers=AUTH, json=body(prof("a"), prof("b")))
    client.put("/api/llm/key", headers=AUTH, json={"profile": "a", "api_key": "key-aaaaaaaaaaaa"})
    client.put("/api/llm/key", headers=AUTH, json={"profile": "b", "api_key": "key-bbbbbbbbbbbb"})
    r = client.put("/api/llm", headers=AUTH, json=body({**prof("a2"), "renamed_from": "a"}))
    assert r.status_code == 200, r.text
    assert memory_keyring.store == {(SERVICE, "a2"): "key-aaaaaaaaaaaa"}


def test_swapped_names_keep_their_keys(client, memory_keyring):
    client.put("/api/llm", headers=AUTH, json=body(prof("a"), prof("b")))
    client.put("/api/llm/key", headers=AUTH, json={"profile": "a", "api_key": "key-aaaaaaaaaaaa"})
    client.put("/api/llm/key", headers=AUTH, json={"profile": "b", "api_key": "key-bbbbbbbbbbbb"})
    r = client.put(
        "/api/llm", headers=AUTH, json=body({**prof("b"), "renamed_from": "a"}, {**prof("a"), "renamed_from": "b"})
    )
    assert r.status_code == 200, r.text
    assert memory_keyring.store == {(SERVICE, "b"): "key-aaaaaaaaaaaa", (SERVICE, "a"): "key-bbbbbbbbbbbb"}


def test_names_reused_after_delete_start_without_key(client, memory_keyring):
    client.put("/api/llm", headers=AUTH, json=body(prof("a"), prof("b")))
    client.put("/api/llm/key", headers=AUTH, json={"profile": "b", "api_key": "key-bbbbbbbbbbbb"})
    # b deleted; keyless a renamed to b; a brand-new profile takes the freed name a.
    r = client.put("/api/llm", headers=AUTH, json=body({**prof("b"), "renamed_from": "a"}, prof("a")))
    assert r.status_code == 200, r.text
    assert memory_keyring.store == {}


def test_test_without_key_profile_sends_no_key(client):
    client.put("/api/llm", headers=AUTH, json=body(prof()))
    client.put("/api/llm/key", headers=AUTH, json={"profile": "main", "api_key": "key-123456789012"})
    client.post("/api/llm/test", headers=AUTH, json={"kind": "chat", "profile": prof()})
    assert FakeProvider.calls[0][1] is None


def test_delete_key(client, memory_keyring):
    client.put("/api/llm", headers=AUTH, json=body(prof()))
    client.put("/api/llm/key", headers=AUTH, json={"profile": "main", "api_key": "key-123456789012"})
    assert client.request("DELETE", "/api/llm/key", headers=AUTH, json={"profile": "main"}).json() == {"set": False, "hint": None}
    assert memory_keyring.store == {}


def test_chat_and_vision_test_use_stored_key(client):
    client.put("/api/llm", headers=AUTH, json=body(prof()))
    client.put("/api/llm/key", headers=AUTH, json={"profile": "main", "api_key": "key-123456789012"})
    r = client.post(
        "/api/llm/test", headers=AUTH, json={"kind": "chat", "profile": prof(model="unsaved-edit"), "key_profile": "main"}
    )
    assert r.json() == {"ok": True, "latency_ms": 5, "model": "unsaved-edit", "reply": "pong", "error": None}
    r = client.post("/api/llm/test", headers=AUTH, json={"kind": "vision", "profile": prof("renamed"), "key_profile": "main"})
    assert r.json()["ok"] is True
    (_, key1, msgs1), (_, key2, msgs2) = FakeProvider.calls
    assert key1 == key2 == "key-123456789012"
    assert isinstance(msgs1[0]["content"], str)
    assert msgs2[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_test_reports_provider_error(config_path):
    app = create_app(config_path=config_path, token=TOKEN, provider_factory=lambda p, k: FakeProvider(p, k, fail=True))
    r = TestClient(app).post("/api/llm/test", headers=AUTH, json={"kind": "chat", "profile": prof()})
    assert r.json()["ok"] is False
    assert "401" in r.json()["error"]


def test_endpoint_file_lifecycle(tmp_path):
    path = write_endpoint_file(tmp_path / "rt", 8765, "tok")
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "tok"
    remove_endpoint_file(path, "other")
    assert path.exists()
    remove_endpoint_file(path, "tok")
    assert not path.exists()


def test_put_llm_keeps_web_search_when_console_omits_it(tmp_path):
    from fastapi.testclient import TestClient

    from api.app import create_app

    path = tmp_path / "backend.yaml"
    path.write_text(
        "llm:\n  profiles:\n    - {name: a, base_url: https://x.example/v1, model: m, web_search: google_function}\n"
        "  roles: {chat: a}\n  web_search: false\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(config_path=path, token="t"))
    auth = {"Authorization": "Bearer t"}
    old_console = {"profiles": [{"name": "a", "base_url": "https://x.example/v1", "model": "m", "renamed_from": "a"}], "roles": {"chat": "a"}}
    st = client.put("/api/llm", headers=auth, json=old_console).json()
    assert st["profiles"][0]["web_search"] == "google_function" and st["web_search"] is False
    new_console = {**old_console, "profiles": [{**old_console["profiles"][0], "web_search": "off"}], "web_search": True}
    st = client.put("/api/llm", headers=auth, json=new_console).json()
    assert st["profiles"][0]["web_search"] == "off" and st["web_search"] is True
