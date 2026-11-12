import asyncio
import json
import logging

from action.skills import SkillRunner
from api.pet_ws import PetHub
from core.agent import Agent, Labels, parse_output
from core.credentials import KeyStore
from core.events import make_event

SKILLS = """enabled: true
confirm_timeout_seconds: 5
skills:
  open_app:
    targets:
      notepad: {label: 记事本, exe: notepad.exe}
  play_music:
    targets:
      netease: {label: 网易云音乐, url: "orpheus://", search_url: "orpheus://search/{query}"}
  open_game:
    confirm: true
    targets:
      mygame:
        label: 某游戏
        exe: 'C:\\Games\\game.exe'
        args: ["--user", "bob", "--pass", "{secret}"]
        credential: mygame
"""


class Recorder:
    def __init__(self, allow=True, fail=False):
        self.launched = []
        self.asked = []
        self.allow = allow
        self.fail = fail

    def launcher(self, exe, args, url):
        if self.fail:
            raise OSError(f"cannot start {args}")
        self.launched.append((exe, args, url))

    async def confirm(self, title, detail, timeout):
        self.asked.append(detail)
        return self.allow


def make_runner(tmp_path, rec, text=SKILLS):
    path = tmp_path / "skills.yaml"
    path.write_text(text, encoding="utf-8")
    return SkillRunner(config_path=path, confirm=rec.confirm, launcher=rec.launcher, keys=KeyStore("pet-kit/skill"))


def run(coro):
    return asyncio.run(coro)


def test_registered_skill_launches_without_confirmation(tmp_path):
    rec = Recorder()
    r = run(make_runner(tmp_path, rec).run("open_app", {"app": "notepad"}))
    assert r.ok and rec.launched == [("notepad.exe", [], None)] and rec.asked == []


def test_builtin_actions_are_on_by_default_and_follow_the_file(tmp_path):
    """内置动作不写在文件里 = 开启；可以单独关掉、要求确认；总开关关了全都没有。"""
    all_four = {"show_code", "set_style", "look_camera", "invite_game"}
    rec = Recorder()
    runner = make_runner(tmp_path, rec)
    assert runner.enabled_builtins() == all_four
    assert runner.names() == {"open_app", "play_music", "open_game"}  # launchable skills only
    assert run(runner.allow_builtin("show_code")) is None and rec.asked == []
    assert run(runner.allow_builtin("delete_file")) is not None  # not a built-in action

    text = SKILLS + "  set_style: {enabled: false}\n  look_camera: {confirm: true}\n"
    rec = Recorder(allow=False)
    runner = make_runner(tmp_path, rec, text)
    assert runner.enabled_builtins() == all_four - {"set_style"}
    assert run(runner.allow_builtin("set_style")) is not None and rec.asked == []  # off: refused, nobody asked
    assert run(runner.allow_builtin("look_camera")) is not None and rec.asked == ["用摄像头看一眼"]  # the user said no
    rec.allow = True
    assert run(runner.allow_builtin("look_camera")) is None

    off = make_runner(tmp_path, Recorder(), SKILLS.replace("enabled: true", "enabled: false", 1))
    assert off.enabled_builtins() == set() and run(off.allow_builtin("show_code")) is not None

    # A built-in action has no targets.
    bad = make_runner(tmp_path, Recorder(), SKILLS + "  show_code:\n    targets:\n      x: {label: x, exe: a.exe}\n")
    assert bad.enabled_builtins() == set()  # broken file: everything is off


def test_unregistered_skill_and_target_are_refused(tmp_path):
    rec = Recorder()
    runner = make_runner(tmp_path, rec)
    assert not run(runner.run("delete_file", {"path": "C:/x"})).ok
    assert not run(runner.run("open_app", {"app": "cmd"})).ok
    assert not run(runner.run("open_app", {"app": ["notepad"]})).ok
    assert rec.launched == []


def test_missing_target_defaults_only_when_unambiguous(tmp_path):
    rec = Recorder()
    assert run(make_runner(tmp_path, rec).run("open_app", {})).ok  # single target


def test_disabled_skill_or_master_switch(tmp_path):
    rec = Recorder()
    off = SKILLS.replace("enabled: true", "enabled: false", 1)
    runner = make_runner(tmp_path, rec, off)
    assert runner.names() == set() and runner.catalog() == []
    assert not run(runner.run("open_app", {"app": "notepad"})).ok
    assert rec.launched == []


def test_bad_config_disables_everything(tmp_path):
    rec = Recorder()
    runner = make_runner(tmp_path, rec, "skills:\n  rm_rf:\n    targets: {}\n")
    assert runner.names() == set()
    assert not run(runner.run("open_app", {"app": "notepad"})).ok


def test_confirm_denied_does_not_launch(tmp_path):
    rec = Recorder(allow=False)
    r = run(make_runner(tmp_path, rec).run("open_game", {"game": "mygame"}))
    assert not r.ok and rec.launched == [] and rec.asked == ["启动游戏某游戏"]


def test_music_query_is_url_encoded_and_cleaned(tmp_path):
    rec = Recorder()
    run(make_runner(tmp_path, rec).run("play_music", {"query": "周杰伦/晴天\n&x=1"}))
    ((_, _, url),) = rec.launched
    assert url.startswith("orpheus://search/") and "\n" not in url and "/晴" not in url and "&" not in url


def test_music_without_query_just_opens_app(tmp_path):
    rec = Recorder()
    run(make_runner(tmp_path, rec).run("play_music", {}))
    assert rec.launched == [(None, [], "orpheus://")]


def test_secret_goes_to_launcher_only(tmp_path, caplog):
    KeyStore("pet-kit/skill").set("mygame", "hunter2-secret")
    rec = Recorder()
    with caplog.at_level(logging.DEBUG):
        r = run(make_runner(tmp_path, rec).run("open_game", {"game": "mygame"}))
    assert r.ok and rec.launched[0][1] == ["--user", "bob", "--pass", "hunter2-secret"]
    assert "hunter2-secret" not in r.message and "hunter2-secret" not in caplog.text
    assert all("hunter2" not in d for d in rec.asked)


def test_missing_secret_fails_without_launching(tmp_path):
    rec = Recorder()
    r = run(make_runner(tmp_path, rec).run("open_game", {"game": "mygame"}))
    assert not r.ok and rec.launched == []


def test_launch_failure_does_not_leak_args(tmp_path):
    KeyStore("pet-kit/skill").set("mygame", "hunter2-secret")
    rec = Recorder(fail=True)
    r = run(make_runner(tmp_path, rec).run("open_game", {"game": "mygame"}))
    assert not r.ok and "hunter2" not in r.message


def test_target_validation(tmp_path):
    rec = Recorder()
    both = "skills:\n  open_app:\n    targets:\n      a: {label: A, exe: a.exe, url: 'x://'}\n"
    assert make_runner(tmp_path, rec, both).names() == set()
    cred = "skills:\n  open_app:\n    targets:\n      a: {label: A, exe: a.exe, credential: k}\n"
    assert make_runner(tmp_path, rec, cred).names() == set()


def test_catalog_lists_only_whitelisted_ids(tmp_path):
    lines = make_runner(tmp_path, Recorder()).catalog()
    text = "\n".join(lines)
    assert "notepad" in text and "netease" in text and "mygame" in text and "hunter" not in text


# --- parse_output ---

LABELS = Labels(expressions=["happy"], motions=[])


def test_parse_output_keeps_only_registered_skills():
    raw = json.dumps(
        {
            "text": "好",
            "tool_calls": [
                {"name": "open_app", "args": {"app": "notepad"}},
                {"name": "delete_file", "args": {"path": "x"}},
                {"name": "open_app", "args": "bad"},
                "junk",
            ],
        },
        ensure_ascii=False,
    )
    assert parse_output(raw, LABELS).tool_calls == []
    out = parse_output(raw, LABELS, {"open_app"})
    assert [(c.name, c.args) for c in out.tool_calls] == [("open_app", {"app": "notepad"}), ("open_app", {})]


def test_parse_output_caps_tool_calls():
    raw = json.dumps({"text": "x", "tool_calls": [{"name": "open_app"}] * 10})
    assert len(parse_output(raw, LABELS, {"open_app"}).tool_calls) == 3


# --- agent prompt ---


class Capture:
    messages: list = []
    reply = ""

    def __init__(self, profile, key):
        pass

    async def chat(self, messages, **_kw):
        from providers.base import ChatResult

        Capture.messages = messages
        return ChatResult(text=Capture.reply, model="m", latency_ms=1)

    async def aclose(self):
        pass


def make_agent(tmp_path, rec):
    (tmp_path / "backend.yaml").write_text(
        "server:\n  port: 8765\nllm:\n  profiles:\n    - name: main\n      base_url: https://a.example/v1\n"
        "      model: m\n  roles:\n    chat: main\n",
        encoding="utf-8",
    )
    return Agent(
        config_path=tmp_path / "backend.yaml",
        persona_path=tmp_path / "persona.yaml",
        keys=KeyStore(),
        provider_factory=Capture,
        skills=make_runner(tmp_path, rec),
    )


def test_agent_offers_skills_to_user_chat_only(tmp_path):
    agent = make_agent(tmp_path, Recorder())
    Capture.reply = json.dumps({"text": "好", "tool_calls": [{"name": "open_app", "args": {"app": "notepad"}}]})
    out = run(agent.respond(make_event("user.text", {"text": "开记事本"}), LABELS))
    assert "【技能】" in Capture.messages[0]["content"]
    assert [c.name for c in out.tool_calls] == ["open_app"]

    Capture.messages = []
    turn = run(agent.respond_proactive(make_event("timer.idle", {}), LABELS, situation="闲着"))
    assert "【技能】" not in Capture.messages[0]["content"]
    assert turn.output.tool_calls == []


# --- confirmation channel ---


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)


def test_confirm_roundtrip_and_failures():
    async def go():
        hub = PetHub(voice=None)  # type: ignore[arg-type]
        assert await hub.confirm("t", "d", 1) is False  # no pet window
        ws = FakeWs()
        hub.clients.add(ws)  # type: ignore[arg-type]
        task = asyncio.create_task(hub.confirm("t", "d", 1))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        req = ws.sent[0]
        assert req["type"] == "confirm_request" and req["detail"] == "d"
        hub.resolve_confirm("wrong-id", True)
        hub.resolve_confirm(req["id"], True)
        assert await task is True
        assert await hub.confirm("t", "d", 0.05) is False  # nobody answers in time
        hub.resolve_confirm(req["id"], True)  # stale answer is ignored

    asyncio.run(go())


# --- console API ---


def _client(tmp_path):
    from fastapi.testclient import TestClient

    from api.app import create_app

    (tmp_path / "backend.yaml").write_text("server:\n  port: 8765\n", encoding="utf-8")
    app = create_app(config_path=tmp_path / "backend.yaml", token="t")
    app.state.scan_programs = lambda: [{"name": "记事本", "exe": "C:\n.exe"}]
    return TestClient(app), {"Authorization": "Bearer t"}


def test_skills_api_roundtrip_and_validation(tmp_path):
    c, h = _client(tmp_path)
    assert c.get("/api/skills").status_code == 401
    assert c.get("/api/skills", headers=h).json()["skills"] == {}
    body = {"enabled": True, "skills": {"open_app": {"targets": {"npp": {"label": "N++", "exe": "C:\npp.exe"}}}}}
    r = c.put("/api/skills", headers=h, json=body)
    assert r.status_code == 200 and "npp" in r.json()["skills"]["open_app"]["targets"]
    assert c.get("/api/skills", headers=h).json()["skills"]["open_app"]["targets"]["npp"]["exe"] == "C:\npp.exe"
    assert (tmp_path / "skills.yaml").exists()
    for bad in (
        {"skills": {"rm": {"targets": {}}}},
        {"skills": {"open_app": {"targets": {"Bad Id": {"label": "x", "exe": "a.exe"}}}}},
        {"skills": {"open_app": {"targets": {"a": {"label": "x"}}}}},
    ):
        assert c.put("/api/skills", headers=h, json=bad).status_code == 422
    assert c.get("/api/skills/installed", headers=h).json() == [{"name": "记事本", "exe": "C:\n.exe"}]


def test_parse_shortcuts():
    from action.skills.installed import parse_shortcuts

    raw = json.dumps(
        [
            {"name": "Zed", "target": r"C:\z\zed.exe"},
            {"name": "Zed 副本", "target": r"c:\z\ZED.exe"},
            {"name": "卸载 Zed", "target": r"C:\z\u.exe"},
            {"name": "Doc", "target": r"C:\doc.pdf"},
            {"name": "Alpha", "target": r"C:\a.exe"},
        ],
        ensure_ascii=False,
    )
    assert [p["name"] for p in parse_shortcuts(raw)] == ["Alpha", "Zed"]
    one = json.dumps({"name": "One", "target": r"C:\o.exe"})
    assert parse_shortcuts(one) == [{"name": "One", "exe": r"C:\o.exe"}]
    assert parse_shortcuts("") == []
