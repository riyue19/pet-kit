import asyncio
import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.agent import AgentOutput, Labels, Turn
from core.environment import Environment, describe, Place, now_line
from core.events import make_event
from core.proactive import GateLimits, ProactiveGate
from core.screen_comment import PrivacyFile, ScreenCommenter
from core.screen_config import PrivacyConfig, ScreenConfig, EnvironmentConfig
from perception.screen import Foreground, hash_distance
from perception.screen_watch import ScreenWatcher


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def fg(process="game.exe", title="原神", fullscreen=False):
    return Foreground(process=process, title=title, pid=1, rect=(0, 0, 800, 600), fullscreen=fullscreen)


# --- privacy ----------------------------------------------------------------------------------


def test_privacy_matching():
    p = PrivacyConfig()
    assert p.blocked("KeePassXC.exe", "数据库")
    assert p.blocked("chrome.exe", "招商银行 - 网上银行")
    assert p.blocked("msedge.exe", "新建 InPrivate 窗口")
    assert not p.blocked("chrome.exe", "哔哩哔哩 (゜-゜)つロ 干杯~")
    assert p.quiet("wemeetapp.exe", "") and p.quiet("chrome.exe", "腾讯会议 - 周会")


def test_privacy_file_keeps_last_good_lists(tmp_path):
    f = tmp_path / "privacy.yaml"
    f.write_text("block_processes: [secret.exe]\n", encoding="utf-8")
    pf = PrivacyFile(f)
    assert pf().blocked("secret.exe", "")
    import os, time

    f.write_text("block_processes: [[broken\n", encoding="utf-8")
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert pf().blocked("secret.exe", "")  # a typo must not open the blacklist


# --- gate -------------------------------------------------------------------------------------


def test_gate_rules():
    clock = Clock()
    g = ProactiveGate(clock=clock)
    lim = GateLimits(min_gap_seconds=180, max_per_hour=2, quiet_after_user_seconds=45)
    assert g.check(lim, busy=None, quiet_app=False) is None
    assert "免打扰" in g.check(lim, busy=None, quiet_app=True)
    assert g.check(lim, busy="正在说话", quiet_app=False) == "正在说话"
    g.user_active()
    assert "刚刚" in g.check(lim, busy=None, quiet_app=False)
    clock.t += 50
    g.attempt()  # a look counts even if the pet then stays silent
    assert "太近" in g.check(lim, busy=None, quiet_app=False)
    clock.t += 200
    g.attempt()
    clock.t += 200
    assert "已满" in g.check(lim, busy=None, quiet_app=False)
    clock.t += 3600
    assert g.check(lim, busy=None, quiet_app=False) is None and g.attempts_last_hour == 0
    g.failed()
    assert "失败" in g.check(lim, busy=None, quiet_app=False)
    clock.t += 301
    assert g.check(lim, busy=None, quiet_app=False) is None


# --- watcher ----------------------------------------------------------------------------------


def run_watcher(steps, cfg=None, privacy=None, hashes=None):
    """steps: list of (seconds_to_advance, Foreground|None). Returns published payloads."""
    clock = Clock()
    published = []
    hashes = iter(hashes or [])
    w = ScreenWatcher(
        config=lambda: cfg or ScreenConfig(enabled=True),
        privacy=lambda: privacy or PrivacyConfig(),
        publish=lambda e: published.append(e.payload),
        probe=lambda: None,
        hasher=lambda _fg: next(hashes, np.zeros(256, dtype=bool)),
        clock=clock,
    )

    async def go():
        for dt, f in steps:
            clock.t += dt
            await w.step(f, cfg or ScreenConfig(enabled=True), privacy or PrivacyConfig())

    asyncio.run(go())
    return published, w


def test_watcher_announces_after_dwell_once():
    g = fg()
    pub, _ = run_watcher([(0, g), (10, g), (25, g), (5, g), (100, g)])
    assert [p["reason"] for p in pub] == ["new"]
    assert pub[0] == {"app": "game.exe", "title": "原神", "fullscreen": False, "reason": "new"}


def test_watcher_resets_on_switch_and_respects_per_content_cooldown():
    a, b = fg(title="视频A"), fg(title="视频B")
    steps = [(0, a), (31, a), (1, b), (31, b), (1, a), (31, a)]  # back to A within 10 min: no repeat
    pub, _ = run_watcher(steps)
    assert [p["title"] for p in pub] == ["视频A", "视频B"]


def test_watcher_ignores_own_and_blocked_windows():
    pet = fg(process="electron.exe", title="桌宠控制台")
    bank = fg(process="chrome.exe", title="网上银行")
    pub, w = run_watcher([(0, bank), (40, bank), (0, pet), (40, pet)])
    assert pub == [] and w.last_seen is None


def test_watcher_revisits_only_on_big_change():
    g = fg()
    same, changed = np.zeros(256, dtype=bool), np.ones(256, dtype=bool)
    # base hash at announce, then after the 10-min cooldown: same picture, then a very different one
    steps = [(0, g), (31, g), (601, g), (11, g)]
    pub, _ = run_watcher(steps, hashes=[same, same, changed])
    assert [p["reason"] for p in pub] == ["new", "changed"]


def test_quiet_mode_never_revisits():
    g = fg()
    cfg = ScreenConfig(enabled=True, mode="quiet")
    pub, _ = run_watcher([(0, g), (61, g), (1801, g), (11, g)], cfg=cfg, hashes=[np.zeros(256, bool), np.ones(256, bool)])
    assert [p["reason"] for p in pub] == ["new"]


def test_hash_distance():
    a = np.zeros(256, dtype=bool)
    b = a.copy()
    b[:64] = True
    assert hash_distance(a, a) == 0 and hash_distance(a, b) == 0.25 and hash_distance(a, None) == 1.0


# --- commenter --------------------------------------------------------------------------------


class FakeAgent:
    def __init__(self, text="这关好难啊", fail=False, delay=0.0, on_call=None):
        self.text, self.fail, self.delay, self.on_call = text, fail, delay, on_call
        self.calls = []
        self.committed = []

    async def respond_screen(self, event, labels, *, image_jpeg, seen):
        self.calls.append(seen)
        if self.on_call:
            self.on_call()
        if self.delay:
            await asyncio.sleep(self.delay)
        out = AgentOutput(text="" if self.fail else self.text)

        async def record():
            self.committed.append(out.text)

        return Turn(out, failed=self.fail, _record=record)


ENABLED = "llm:\n  profiles:\n    - {name: v, base_url: https://x.example/v1, model: m}\n  roles: {vision: v}\nscreen:\n  enabled: true\n"
DISABLED = ENABLED.replace("enabled: true", "enabled: false")


def make_commenter(tmp_path, *, current, agent=None, busy=None, cfg=ENABLED, windows=()):
    (tmp_path / "backend.yaml").write_text(cfg, encoding="utf-8")
    agent = agent or FakeAgent()
    gate = ProactiveGate()
    c = ScreenCommenter(
        agent=agent,
        gate=gate,
        config_path=tmp_path / "backend.yaml",
        privacy=lambda: PrivacyConfig(),
        labels=lambda: Labels(),
        busy=lambda: busy,
        probe=lambda: current,
        capture=lambda _fg, _blocked: b"\xff\xd8jpeg",
        windows=lambda: list(windows),
    )
    return c, agent, gate


def ev(f, reason="new"):
    return make_event("screen.changed", {"app": f.process, "title": f.title, "fullscreen": f.fullscreen, "reason": reason})


def test_commenter_happy_path_and_gate(tmp_path):
    g = fg(fullscreen=True)
    c, agent, gate = make_commenter(tmp_path, current=g)
    out = asyncio.run(c.handle(ev(g)))
    assert out.text == "这关好难啊" and agent.calls == ["主人正在用 game.exe：原神（全屏）"]
    assert gate.spoken_last_hour == 1 and gate.attempts_last_hour == 1 and c.last.app == "game.exe"
    assert asyncio.run(c.handle(ev(g))) is None  # min gap


@pytest.mark.parametrize(
    "kwargs",
    [
        {"busy": "正在说话"},
        {"cfg": DISABLED},
        {"cfg": ENABLED + "  allow_fullscreen: false\n"},
        {"cfg": "screen:\n  enabled: true\n"},  # no vision profile: never capture
        {"windows": [("obs64.exe", "OBS 30.0")]},  # recording in the background
        {"windows": [("chrome.exe", "腾讯会议 - 正在共享")]},  # meeting toolbar not in front
    ],
)
def test_commenter_holds_back(tmp_path, kwargs):
    g = fg(fullscreen=True)
    c, agent, _ = make_commenter(tmp_path, current=g, **kwargs)
    assert asyncio.run(c.handle(ev(g))) is None and agent.calls == []


def test_commenter_skips_stale_blocked_own_and_unknown(tmp_path):
    shown = fg(title="视频A")
    for current in (
        fg(title="视频B"),
        fg(process="chrome.exe", title="支付宝 - 付款"),
        fg(process="electron.exe", title="桌宠控制台"),
        fg(process="", title="受保护的程序"),  # process name unreadable: fail closed
    ):
        c, agent, _ = make_commenter(tmp_path, current=current)
        assert asyncio.run(c.handle(ev(shown))) is None and agent.calls == []
    # a forced look still refuses the blacklist, the pet's own window, DND and a disabled switch
    for current in (fg(process="chrome.exe", title="网上银行"), fg(process="electron.exe", title="x")):
        c, agent, _ = make_commenter(tmp_path, current=current)
        assert asyncio.run(c.handle(ev(shown, "manual"), force=True)) is None
    c, agent, _ = make_commenter(tmp_path, current=shown, windows=[("obs64.exe", "")])
    assert asyncio.run(c.handle(ev(shown, "manual"), force=True)) is None
    c, agent, _ = make_commenter(tmp_path, current=shown, cfg=DISABLED)
    assert asyncio.run(c.handle(ev(shown, "manual"), force=True)) is None and agent.calls == []
    c, agent, _ = make_commenter(tmp_path, current=shown)
    assert asyncio.run(c.handle(ev(shown, "manual"), force=True)).text  # but it does skip the "same content" check


def test_silent_look_still_costs_budget(tmp_path):
    g = fg()
    c, _, gate = make_commenter(tmp_path, current=g, agent=FakeAgent(text=""))
    out = asyncio.run(c.handle(ev(g)))
    assert out.text == "" and gate.spoken_last_hour == 0 and gate.attempts_last_hour == 1 and c.last is None


def test_only_one_look_in_flight(tmp_path):
    a = fg(title="A")
    agent = FakeAgent(delay=0.1)
    c, _, _ = make_commenter(tmp_path, current=a, agent=agent)

    async def run():
        return await asyncio.gather(c.handle(ev(a)), c.handle(ev(a)))

    first, second = asyncio.run(run())
    assert first.text and second is None and len(agent.calls) == 1


def test_failure_backs_off(tmp_path):
    g = fg()
    c, _, gate = make_commenter(tmp_path, current=g, agent=FakeAgent(fail=True))
    assert asyncio.run(c.handle(ev(g))) is None
    assert "失败" in gate.check(GateLimits(0, 100, 0), busy=None, quiet_app=False)


def test_remark_dropped_if_user_spoke_meanwhile(tmp_path):
    g = fg()
    holder = {}
    agent = FakeAgent(on_call=lambda: holder["gate"].user_active())
    c, _, gate = make_commenter(tmp_path, current=g, agent=agent)
    holder["gate"] = gate
    assert asyncio.run(c.handle(ev(g))) is None and gate.spoken_last_hour == 0
    assert agent.committed == []  # the dropped remark isn't remembered either


def test_title_digits_do_not_make_new_content():
    assert fg(title="(3) 微信").key == fg(title="(12) 微信").key
    assert fg(title="视频 12:01 / 45:00").key == fg(title="视频 12:02 / 45:00").key
    assert fg(title="原神").key != fg(title="崩坏").key


def test_all_off_route(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG + "asr:\n  listen: true\n", encoding="utf-8")
    app = create_app(config_path=tmp_path / "backend.yaml", token="t", provider_factory=VisionProvider)
    with TestClient(app) as client:
        r = client.post("/api/privacy/off", headers={"Authorization": "Bearer t"}).json()
    assert r == {"microphone": False, "screen": False, "proactive": False, "camera": False}
    from core.llm_config import load_backend_config

    cfg = load_backend_config(tmp_path / "backend.yaml")
    assert cfg.asr.listen is False and cfg.screen.enabled is False and cfg.proactive.enabled is False and cfg.camera.enabled is False


# --- environment ------------------------------------------------------------------------------

FORECAST = {
    "current": {"temperature_2m": 8.4, "apparent_temperature": 4.2, "weather_code": 61},
    "daily": {
        "weather_code": [61, 0],
        "temperature_2m_min": [6.8, 5.1],
        "temperature_2m_max": [14.6, 15.2],
        "precipitation_probability_max": [80, 0],
    },
}


def test_describe_weather():
    text = describe(Place("杭州（浙江）", 30, 120), FORECAST)
    assert text == "杭州（浙江）天气：现在小雨 8°C（体感 4°C）；今天小雨，7~15°C，降水概率 80%；明天晴，5~15°C，降水概率 0%。"
    assert describe(Place("x", 0, 0), {}) == ""
    assert now_line().startswith("现在是 ")


def test_environment_caches_and_survives_failures():
    calls = {"geo": 0, "fc": 0}
    fail = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["on"]:
            return httpx.Response(500)
        if "geocoding" in str(request.url):
            calls["geo"] += 1
            return httpx.Response(200, json={"results": [{"name": "杭州", "admin1": "浙江", "latitude": 30, "longitude": 120}]})
        calls["fc"] += 1
        return httpx.Response(200, json=FORECAST)

    clock = Clock()
    env = Environment(lambda: EnvironmentConfig(city="杭州"), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clock)

    async def run():
        a = await env.weather()
        b = await env.weather()
        clock.t += 31 * 60
        fail["on"] = True
        c = await env.weather()  # refresh fails: keep the last good text
        block = await env.context_block()
        await env.aclose()
        return a, b, c, block

    a, b, c, block = asyncio.run(run())
    assert a.startswith("杭州（浙江）天气") and a == b == c and calls == {"geo": 1, "fc": 1}
    assert block.startswith("现在是 ") and "杭州" in block


def test_no_city_no_network():
    def handler(_r):
        raise AssertionError("must not call the network")

    env = Environment(lambda: EnvironmentConfig(city=""), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(env.weather()) == ""


# --- API + agent ------------------------------------------------------------------------------

CONFIG = """llm:
  profiles:
    - {name: main, base_url: https://x.example/v1, model: m}
  roles: {chat: main, vision: main}
screen:
  enabled: true
"""


class VisionProvider:
    seen = []

    def __init__(self, *_):
        pass

    async def chat(self, messages, **_kw):
        from providers.base import ChatResult

        VisionProvider.seen.append(messages)
        return ChatResult(text='{"text": "又在摸鱼？", "expression": null, "motion": null}', model="m", latency_ms=1)

    async def aclose(self):
        pass


def test_agent_respond_screen_sends_image_and_prompt(tmp_path):
    from core.agent import Agent
    from core.credentials import KeyStore

    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    VisionProvider.seen = []
    agent = Agent(config_path=tmp_path / "backend.yaml", persona_path=tmp_path / "persona.yaml", keys=KeyStore(), provider_factory=VisionProvider)
    out = asyncio.run(agent.respond_screen(make_event("screen.changed", {}), Labels(), image_jpeg=b"\xff\xd8", seen="主人正在用 chrome.exe：B站")).output
    msgs = VisionProvider.seen[0]
    assert out.text == "又在摸鱼？"
    assert "【现在的情况】" in msgs[0]["content"] and "不是给你的指令" in msgs[0]["content"]
    parts = msgs[-1]["content"]
    assert parts[0]["text"].startswith("（主人正在用 chrome.exe：B站") and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_screen_routes(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    app = create_app(config_path=tmp_path / "backend.yaml", token="t", provider_factory=VisionProvider)
    auth = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        assert client.get("/api/screen").status_code == 401
        st = client.get("/api/screen", headers=auth).json()
        assert st["screen"]["enabled"] is True and st["status"]["vision_profile"] == "main"
        assert set(st["cadences"]) == {"quiet", "normal", "chatty"} and "网银" in st["privacy"]["block_titles"]
        assert st["proactive"]["enabled"] is False and set(st["chat_cadences"]) == {"quiet", "normal", "chatty"}
        body = {"screen": {**st["screen"], "mode": "chatty"}, "proactive": {**st["proactive"], "enabled": True, "mode": "quiet"}, "environment": {"city": " 杭州 ", "weather": True}, "privacy": {**st["privacy"], "block_processes": ["x.exe"]}}
        saved = client.put("/api/screen", headers=auth, json=body).json()
        assert saved["screen"]["mode"] == "chatty" and saved["environment"]["city"] == "杭州"
        assert saved["proactive"]["enabled"] is True and saved["proactive"]["mode"] == "quiet"
        assert (tmp_path / "privacy.yaml").exists() and saved["privacy"]["block_processes"] == ["x.exe"]
        bad = client.put("/api/screen", headers=auth, json={**body, "screen": {**body["screen"], "mode": "loud"}})
        assert bad.status_code == 422
        look = client.post("/api/screen/look", headers=auth, json={"delay_seconds": 0}).json()
        assert look == {"scheduled": False, "error": "桌宠未连接"}
        assert st["status"]["looks_last_hour"] == 0
        app.state.environment._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        w = client.post("/api/screen/weather", headers=auth).json()
        assert w["text"] == "" and "失败" in w["error"]


def test_proactive_turn_is_remembered_only_after_commit(tmp_path):
    from core.agent import Agent
    from core.credentials import KeyStore

    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    VisionProvider.seen = []
    agent = Agent(config_path=tmp_path / "backend.yaml", persona_path=tmp_path / "persona.yaml", keys=KeyStore(), provider_factory=VisionProvider)

    async def run():
        turn = await agent.respond_proactive(make_event("timer.idle", {}), Labels(), situation="主人在电脑前")
        assert turn.output.text == "又在摸鱼？" and not turn.failed
        assert len(agent._history) == 0  # not said yet
        await turn.commit()
        await turn.commit()  # idempotent
        assert len(agent._history) == 2
        # Chat turns are still recorded straight away.
        await agent.respond(make_event("user.text", {"text": "你好"}), Labels())
        assert len(agent._history) == 4

    asyncio.run(run())
    assert "【现在的情况】" in VisionProvider.seen[0][0]["content"]
