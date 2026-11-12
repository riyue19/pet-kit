import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.agent import AgentOutput, Labels, Turn
from core.camera_config import CameraConfig
from core.camera_react import CameraReactor, away_phrase
from core.events import make_event
from core.proactive import GateLimits, ProactiveGate
from core.screen_config import PrivacyConfig
from perception.camera import CameraWatcher, PresenceTracker, Transition


# --- presence state machine -------------------------------------------------------------------


def test_tracker_arrives_after_two_hits_and_leaves_after_timeout():
    t = PresenceTracker(0.0)
    assert t.update(100.0, True, 30) is None  # one hit is not enough (flicker)
    assert t.update(103.0, False, 30) is None  # ...and a miss resets the count
    assert t.update(106.0, True, 30) is None
    assert t.update(109.0, True, 30) == Transition("arrived", away_seconds=109.0)
    assert t.present
    assert t.update(112.0, False, 30) is None  # briefly out of frame: still here
    assert t.update(120.0, True, 30) is None
    assert t.update(130.0, False, 30) is None
    assert t.update(150.0, False, 30) == Transition("left")
    assert not t.present
    assert t.update(160.0, False, 30) is None  # no repeated "left"
    t.update(1000.0, True, 30)
    arrived = t.update(1003.0, True, 30)
    assert arrived.state == "arrived" and arrived.away_seconds == pytest.approx(1003.0 - 120.0)


# --- watcher ----------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeCam:
    def __init__(self, faces):
        self.faces = faces  # per-check results, consumed in order
        self.closed = False

    def read(self):
        return np.zeros((4, 4, 3), np.uint8)

    def has_face(self, frame):
        return self.faces.pop(0) if self.faces else False

    def jpeg(self, frame):
        return b"\xff\xd8jpeg"

    def close(self):
        self.closed = True


def make_watcher(tmp_path, *, cfg, faces=(), quiet=False, opener=None, model=True):
    if model:
        d = tmp_path / "models" / "camera"
        d.mkdir(parents=True, exist_ok=True)
        (d / "face_detection_yunet.onnx").write_bytes(b"x")
    events, states = [], []
    clock = Clock()
    cams = []

    def default_opener(_path, _idx):
        cam = FakeCam(list(faces))
        cams.append(cam)
        return cam

    w = CameraWatcher(
        config=lambda: cfg,
        privacy=lambda: PrivacyConfig(),
        publish=events.append,
        base_dir=tmp_path,
        windows=lambda: [("zoom.exe", "Zoom")] if quiet else [],
        # records only changes of the "open" flag (the initial state is closed)
        on_state=lambda o, p, e: states.append(o) if o != (states[-1] if states else False) else None,
        opener=opener or default_opener,
        clock=clock,
    )
    return w, events, states, clock, cams


def test_watcher_detects_arrival_and_leave(tmp_path):
    cfg = CameraConfig(enabled=True, away_after_seconds=10)
    w, events, _, clock, cams = make_watcher(tmp_path, cfg=cfg, faces=[True, True, False, False, False])
    out = []
    for step in range(5):
        clock.t = step * 6.0
        out.append(w._check(cfg))
    assert [o and o.state for o in out] == [None, "arrived", None, "left", None]
    assert w.open and w.present is False


def test_watcher_missing_model_and_open_failure_back_off(tmp_path):
    cfg = CameraConfig(enabled=True)
    w, *_ = make_watcher(tmp_path, cfg=cfg, model=False)
    assert w._check(cfg) is None and "fetch_models" in w.error and not w.open

    calls = []

    def failing(_p, _i):
        calls.append(1)
        raise RuntimeError("打不开摄像头 0")

    w, _, _, clock, _ = make_watcher(tmp_path, cfg=cfg, opener=failing)
    assert w._check(cfg) is None and w.error == "打不开摄像头 0"
    clock.t = 5
    w._check(cfg)
    assert len(calls) == 1  # retry only after the back-off
    clock.t = 31
    w._check(cfg)
    assert len(calls) == 2


def test_watcher_run_releases_camera_when_switched_off_or_in_meeting(tmp_path):
    state = {"cfg": CameraConfig(enabled=True, check_seconds=1)}
    w, events, states, clock, cams = make_watcher(tmp_path, cfg=state["cfg"], faces=[True] * 50)
    w._config = lambda: state["cfg"]

    async def scenario():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.3)
        assert w.open and states == [True]
        state["cfg"] = CameraConfig(enabled=False, check_seconds=1)
        await asyncio.sleep(1.3)
        assert not w.open and states == [True, False] and cams[0].closed
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    # a meeting app is running: the camera is never opened
    w, _, states, *_ = make_watcher(tmp_path, cfg=CameraConfig(enabled=True, check_seconds=1), quiet=True)

    async def meeting():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(meeting())
    assert not w.open and states == []


def test_snapshot_only_when_open(tmp_path):
    cfg = CameraConfig(enabled=True)
    w, *_ = make_watcher(tmp_path, cfg=cfg, faces=[True])
    assert w.snapshot_jpeg() is None
    w._check(cfg)
    assert w.snapshot_jpeg() == b"\xff\xd8jpeg"


# --- reactor ----------------------------------------------------------------------------------

CFG = (
    "llm:\n  profiles:\n    - {name: v, base_url: https://x.example/v1, model: m}\n  roles: {vision: v}\n"
    "camera:\n  enabled: true\nproactive:\n  enabled: true\n"
)
NO_GREET = CFG.replace("camera:\n  enabled: true\n", "camera:\n  enabled: true\n  greet_on_return: false\n")


class FakeAgent:
    def __init__(self, text="欢迎回来～", fail=False):
        self.text, self.fail = text, fail
        self.calls = []
        self.committed = []

    async def respond_camera(self, event, labels, *, image_jpeg, seen, ask=False):
        self.calls.append((seen, image_jpeg) if not ask else ("ask", seen))
        out = AgentOutput(text="" if self.fail else self.text)

        async def record():
            self.committed.append(out.text)

        return Turn(out, failed=self.fail, _record=record)


def make_reactor(tmp_path, *, cfg=CFG, busy=None, windows=(), snapshot=b"\xff\xd8jpeg"):
    (tmp_path / "backend.yaml").write_text(cfg, encoding="utf-8")
    agent, gate = FakeAgent(), ProactiveGate()
    r = CameraReactor(
        agent=agent,
        gate=gate,
        config_path=tmp_path / "backend.yaml",
        privacy=lambda: PrivacyConfig(),
        labels=lambda: Labels(),
        busy=lambda: busy,
        snapshot=lambda: snapshot,
        windows=lambda: list(windows),
    )
    return r, agent, gate


def arrived(away=600, **extra):
    return make_event("camera.presence", {"state": "arrived", "away_seconds": away, **extra})


def test_reactor_greets_after_long_absence_and_charges_budget(tmp_path):
    r, agent, gate = make_reactor(tmp_path)
    out = asyncio.run(r.handle(arrived(600)))
    assert out.text == "欢迎回来～"
    assert agent.calls == [("主人回到了电脑前，离开了大约 10 分钟", b"\xff\xd8jpeg")]
    assert gate.attempts_last_hour == 1 and gate.spoken_last_hour == 1 and r.last.text == "欢迎回来～"
    assert agent.committed == ["欢迎回来～"]
    assert asyncio.run(r.handle(arrived(600))) is None  # min gap


@pytest.mark.parametrize(
    "kwargs, event",
    [
        ({}, make_event("camera.presence", {"state": "left"})),
        ({}, arrived(60)),  # away too short (< 5 min)
        ({"cfg": CFG.replace("enabled: true", "enabled: false")}, arrived()),
        ({"cfg": NO_GREET}, arrived()),
        ({"cfg": CFG.replace("proactive:\n  enabled: true", "proactive:\n  enabled: false")}, arrived()),  # 主动对话 off
        ({"cfg": "camera:\n  enabled: true\n"}, arrived()),  # no vision profile
        ({"busy": "正在说话"}, arrived()),
        ({"windows": [("zoom.exe", "Zoom")]}, arrived()),
        ({"snapshot": None}, arrived()),
    ],
)
def test_reactor_holds_back(tmp_path, kwargs, event):
    r, agent, gate = make_reactor(tmp_path, **kwargs)
    assert asyncio.run(r.handle(event)) is None and agent.calls == []
    assert gate.attempts_last_hour == 0


def test_reactor_force_skips_rules_but_not_dnd(tmp_path):
    r, agent, _ = make_reactor(tmp_path, cfg=NO_GREET)
    assert asyncio.run(r.handle(arrived(0, reason="manual"), force=True)) is not None
    r, agent, _ = make_reactor(tmp_path, windows=[("obs64.exe", "OBS")])
    assert asyncio.run(r.handle(arrived(0, reason="manual"), force=True)) is None and agent.calls == []


def test_reactor_failure_backs_off_and_silence_still_costs(tmp_path):
    r, agent, gate = make_reactor(tmp_path)
    agent.fail = True
    assert asyncio.run(r.handle(arrived())) is None
    assert gate.check(GateLimits(0, 99, 0), busy=None, quiet_app=False)  # backing off
    r, agent, gate = make_reactor(tmp_path)
    agent.text = ""
    out = asyncio.run(r.handle(arrived()))
    assert out.text == "" and gate.attempts_last_hour == 1 and gate.spoken_last_hour == 0


def test_away_phrase():
    assert away_phrase(30) == "刚离开一小会儿"
    assert away_phrase(1800) == "离开了大约 30 分钟"
    assert away_phrase(7200) == "离开了大约 2.0 小时"


# --- routes -----------------------------------------------------------------------------------


def test_camera_routes(tmp_path):
    cfg = tmp_path / "backend.yaml"
    cfg.write_text(CFG.replace("enabled: true", "enabled: false"), encoding="utf-8")
    app = create_app(config_path=cfg, token="t")
    h = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        s = client.get("/api/camera", headers=h).json()
        assert s["camera"]["enabled"] is False and s["open"] is False and s["vision_profile"] == "v"
        assert client.get("/api/camera").status_code == 401
        assert client.put("/api/camera", headers=h, json={"enabled": True, "check_seconds": 0}).status_code == 422
        s = client.put("/api/camera", headers=h, json={"enabled": True, "greet_min_away_minutes": 10}).json()
        assert s["camera"]["enabled"] is True and s["camera"]["greet_min_away_minutes"] == 10
        r = client.post("/api/camera/look", headers=h).json()
        assert r["scheduled"] is False  # the camera isn't open (the watcher doesn't run in tests)


# --- review fixes -----------------------------------------------------------------------------


def test_open_flag_set_as_soon_as_device_is_held_and_bad_reads_release_it(tmp_path):
    cfg = CameraConfig(enabled=True)

    class Broken(FakeCam):
        def read(self):
            return None  # e.g. the device was taken by another program

    w, _, _, clock, cams = make_watcher(tmp_path, cfg=cfg, opener=lambda _p, _i: Broken([]))
    assert w._check(cfg) is None
    assert w.open  # LED is on, so the indicator must be too
    for _ in range(4):
        w._check(cfg)
    assert not w.open and w.error  # released after 5 bad reads
    n = len(cams)
    clock.t = 5
    assert w._check(cfg) is None and not w.open  # backing off


def test_no_viewer_means_no_camera_and_apply_now_releases_immediately(tmp_path):
    state = {"cfg": CameraConfig(enabled=True, check_seconds=30), "viewer": False}
    w, _, states, _, cams = make_watcher(tmp_path, cfg=state["cfg"], faces=[True] * 50)
    w._config = lambda: state["cfg"]
    w._has_viewer = lambda: state["viewer"]

    async def scenario():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.3)
        assert not w.open and not cams  # nobody to show the indicator to
        state["viewer"] = True
        w._wake.set()
        await asyncio.sleep(0.3)
        assert w.open and states == [True]
        state["cfg"] = CameraConfig(enabled=False, check_seconds=30)  # as after "close all senses"
        await w.apply_now()
        assert not w.open and states == [True, False] and cams[0].closed  # no waiting for the 30 s check
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_no_frame_after_switch_off(tmp_path):
    cfg = CameraConfig(enabled=True)
    state = {"cfg": cfg}
    w, *_ = make_watcher(tmp_path, cfg=cfg, faces=[True])
    w._config = lambda: state["cfg"]
    w._check(cfg)
    assert w.snapshot_jpeg() is not None
    state["cfg"] = CameraConfig(enabled=False)
    assert w.snapshot_jpeg() is None and w._check(cfg) is None


def test_watcher_reports_presence_changes(tmp_path):
    cfg = CameraConfig(enabled=True, away_after_seconds=10)
    w, *_ = make_watcher(tmp_path, cfg=cfg, faces=[False, True, True])
    seen = []
    w._on_state = lambda o, p, e: seen.append((o, p, e))
    for step in range(3):
        w._clock.t = step * 3.0
        w._check(cfg)
        w._notify()
    assert seen == [(True, False, True), (True, True, True)]


def test_enabled_but_paused_is_reported_and_close_is_logged(tmp_path, caplog):
    import logging

    state = {"cfg": CameraConfig(enabled=True, check_seconds=1), "meeting": True}
    w, *_ = make_watcher(tmp_path, cfg=state["cfg"], faces=[True] * 50)
    w._config = lambda: state["cfg"]
    w._windows = lambda: [("zoom.exe", "Zoom")] if state["meeting"] else []
    seen = []
    w._on_state = lambda o, p, e: seen.append((o, p, e))

    async def scenario():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.3)
        assert seen == [(False, None, True)]  # paused for the meeting: not open, but the switch is on
        state["meeting"] = False
        w._wake.set()
        await asyncio.sleep(0.4)
        assert seen[-1][0] is True
        state["cfg"] = CameraConfig(enabled=False, check_seconds=1)
        with caplog.at_level(logging.INFO):
            await w.apply_now()
        assert seen[-1] == (False, None, False) and "摄像头已关闭" in caplog.text
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_force_flag_events_cannot_be_injected_from_the_console(tmp_path):
    """camera.presence / screen.changed carry `reason: manual|ask` (skips cadence limits): only the app's own
    buttons and tool calls publish those; the console's injection API refuses both types."""
    cfg = tmp_path / "backend.yaml"
    cfg.write_text(CFG, encoding="utf-8")
    app = create_app(config_path=cfg, token="t")
    with TestClient(app) as client:
        for etype in ("camera.presence", "screen.changed"):
            r = client.post(
                "/api/events",
                headers={"Authorization": "Bearer t"},
                json={"type": etype, "payload": {"state": "arrived", "reason": "ask"}},
            )
            assert r.status_code == 422


def test_status_line_matches_real_state(tmp_path):
    state = {"cfg": CameraConfig(enabled=False)}
    w, *_ = make_watcher(tmp_path, cfg=state["cfg"], faces=[False, True, True])
    w._config = lambda: state["cfg"]
    assert "关闭中" in w.status_line() and "不要假装" in w.status_line()
    state["cfg"] = CameraConfig(enabled=True, away_after_seconds=10)
    assert "没在工作" in w.status_line()  # enabled, not opened yet
    w._check(state["cfg"])
    assert "没有检测到人脸" in w.status_line()
    w._clock.t = 3
    w._check(state["cfg"])
    w._clock.t = 6
    w._check(state["cfg"])
    assert "检测到了主人的脸" in w.status_line()


def test_environment_block_includes_extra_lines():
    from core.environment import Environment
    from core.screen_config import EnvironmentConfig

    env = Environment(lambda: EnvironmentConfig())
    env.extra_lines.append(lambda: "【摄像头】测试")
    env.extra_lines.append(lambda: 1 / 0)  # a broken line is skipped, not fatal
    block = asyncio.run(env.context_block())
    assert "【摄像头】测试" in block


def ask_event():
    return make_event("camera.presence", {"state": "arrived", "away_seconds": 0, "reason": "ask"})


def test_asked_look_ignores_cadence_but_not_dnd_and_waits_for_speech(tmp_path):
    r, agent, gate = make_reactor(tmp_path)
    # a greeting just happened: proactive cadence would block, a user request must not be blocked
    asyncio.run(r.handle(arrived(600)))
    out = asyncio.run(r.handle(ask_event(), force=True))
    assert out.text == "欢迎回来～" and agent.calls[-1] == ("ask", "主人让你看看他")
    assert gate.attempts_last_hour == 1  # a user-requested look doesn't spend the proactive budget

    r, agent, _ = make_reactor(tmp_path, windows=[("obs64.exe", "OBS")])
    out = asyncio.run(r.handle(ask_event(), force=True))
    assert "开会" in out.text and agent.calls == []

    r, agent, _ = make_reactor(tmp_path, snapshot=None)
    assert "拿不到画面" in asyncio.run(r.handle(ask_event(), force=True)).text

    # busy while the pet says "let me look": waits, then looks
    r, agent, _ = make_reactor(tmp_path)
    busy = iter(["正在说话", "正在说话", None, None])
    r._busy = lambda: next(busy, None)
    assert asyncio.run(r.handle(ask_event(), force=True)).text == "欢迎回来～"

    r, agent, _ = make_reactor(tmp_path)
    agent.fail = True
    assert "没看清" in asyncio.run(r.handle(ask_event(), force=True)).text


def test_chat_is_offered_look_camera_only_while_camera_is_working(tmp_path):
    from core.agent import Agent
    from core.credentials import KeyStore
    from test_screen import VisionProvider

    (tmp_path / "backend.yaml").write_text(CFG.replace("roles: {vision: v}", "roles: {vision: v, chat: v}"), encoding="utf-8")
    state = {"tools": {}}
    agent = Agent(config_path=tmp_path / "backend.yaml", persona_path=tmp_path / "persona.yaml", keys=KeyStore(), provider_factory=VisionProvider)
    agent.extra_tools = lambda: state["tools"]
    VisionProvider.seen = []
    asyncio.run(agent.respond(make_event("user.text", {"text": "看看我"}), Labels()))
    assert "look_camera" not in VisionProvider.seen[-1][0]["content"]
    state["tools"] = {"look_camera": "look_camera：看一眼主人。args: {}"}
    asyncio.run(agent.respond(make_event("user.text", {"text": "看看我"}), Labels()))
    assert "look_camera：看一眼主人" in VisionProvider.seen[-1][0]["content"]


def test_camera_prompts_forbid_guessing_gender_and_uncertain_details():
    from core.agent import CAMERA_ASK_PROMPT, CAMERA_PROMPT

    for prompt in (CAMERA_PROMPT, CAMERA_ASK_PROMPT):
        assert "不要判断主人的性别" in prompt and "拿不准就不提" in prompt
