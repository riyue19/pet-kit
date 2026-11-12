import asyncio
from datetime import datetime

from core.agent import AgentOutput, Labels, Turn
from core.dispatcher import Dispatcher
from core.events import make_event
from core.proactive import GateLimits, ProactiveGate
from core.scheduler import PRESENT_SECONDS, Scheduler, greeting_slot
from core.screen_config import PrivacyConfig
from perception.screen import Foreground

CFG = (
    "llm:\n  profiles:\n    - {name: c, base_url: https://x.example/v1, model: m}\n  roles: {chat: c}\n"
    "screen:\n  quiet_after_user_seconds: 0\n"
    "proactive:\n  enabled: true\n  mode: chatty\n  idle_after_minutes: 5\n  away_minutes: 10\n"
)


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


def fg(process="code.exe", title="main.py"):
    return Foreground(process=process, title=title, pid=1, rect=(0, 0, 800, 600), fullscreen=False)


def make(tmp_path, cfg=CFG, *, hour=15, busy=None, greetings=False):
    if not greetings:
        cfg = cfg + "  greetings: false\n"
    (tmp_path / "backend.yaml").write_text(cfg, encoding="utf-8")
    clock = Clock()
    gate = ProactiveGate(clock=clock)
    s = Scheduler(
        config_path=tmp_path / "backend.yaml",
        privacy=lambda: PrivacyConfig(),
        gate=gate,
        publish=lambda e: None,
        busy=lambda: busy,
        idle=lambda: 0.0,
        probe=lambda: fg(),
        state_path=tmp_path / "scheduler.json",
        clock=clock,
        now=lambda: datetime(2026, 11, 9, hour, 30),
    )
    return s, gate, clock


def test_greeting_slots():
    assert greeting_slot(7)[0] == "morning" and greeting_slot(12)[0] == "lunch"
    assert greeting_slot(23)[0] == "night" and greeting_slot(1)[0] == "night"
    assert greeting_slot(15) is None and greeting_slot(4) is None


def test_idle_chat_needs_quiet_time_then_repeats_slowly(tmp_path):
    s, gate, clock = make(tmp_path)
    assert s.decide(0, fg(), []) is None  # just started
    clock.t += 5 * 60
    sit = s.decide(0, fg(), [])
    assert sit.kind == "idle" and "code.exe" in sit.text and gate.attempts_last_hour == 1
    clock.t += 6 * 60  # past idle_after again, but chatty's idle_repeat is 15 min
    assert s.decide(0, fg(), []) is None
    clock.t += 10 * 60
    assert s.decide(0, fg(), []).kind == "idle"


def test_user_activity_resets_idle(tmp_path):
    s, gate, clock = make(tmp_path)
    clock.t += 4 * 60
    gate.user_active()
    clock.t += 4 * 60
    assert s.decide(0, fg(), []) is None


def test_not_present_or_busy_or_disabled(tmp_path):
    s, _, clock = make(tmp_path)
    clock.t += 10 * 60
    assert s.decide(PRESENT_SECONDS + 1, fg(), []) is None  # not clearly at the keyboard
    s2, _, c2 = make(tmp_path, busy="正在说话")
    c2.t += 10 * 60
    assert s2.decide(0, fg(), []) is None
    s3, _, c3 = make(tmp_path, CFG.replace("enabled: true", "enabled: false"))
    c3.t += 10 * 60
    assert s3.decide(0, fg(), []) is None


def test_quiet_and_blocked_windows(tmp_path):
    s, _, clock = make(tmp_path)
    clock.t += 10 * 60
    assert s.decide(0, fg("wemeetapp.exe", "腾讯会议"), []) is None  # do-not-disturb
    sit = s.decide(0, fg("chrome.exe", "招商银行 网银"), [])
    assert sit is not None and "网银" not in sit.text and "chrome" not in sit.text  # topic dropped, not the chat


def test_welcome_back(tmp_path):
    s, _, clock = make(tmp_path)
    assert s.decide(11 * 60, fg(), []) is None  # away
    clock.t += 30
    sit = s.decide(1, fg(), [])
    assert sit.kind == "welcome_back"
    clock.t += 3600
    assert s.decide(1, fg(), []).kind == "idle"  # welcomed once only


def test_greeting_once_a_day_survives_restart(tmp_path):
    s, _, clock = make(tmp_path, hour=12, greetings=True)
    sit = s.decide(0, fg(), [])
    assert sit.kind == "greeting" and sit.slot == "lunch"
    clock.t += 3600
    assert s.decide(0, fg(), []).kind == "idle"  # tried today already: not repeated
    s.spoken({"kind": "greeting", "slot": "lunch"}, "吃饭了吗", 1.0)
    s2, _, c2 = make(tmp_path, hour=12, greetings=True)
    assert s2.decide(0, fg(), []) is None  # remembered in scheduler.json; idle chat not due yet


def test_unsaid_greeting_is_not_saved(tmp_path):
    s, _, _ = make(tmp_path, hour=12, greetings=True)
    assert s.decide(0, fg(), []).slot == "lunch"  # tried, but never spoken()
    s2, _, _ = make(tmp_path, hour=12, greetings=True)
    assert s2.decide(0, fg(), []).slot == "lunch"


def test_dnd_fullscreen_background_meeting_and_unknown(tmp_path):
    s, _, clock = make(tmp_path)
    clock.t += 10 * 60
    full = Foreground(process="powerpnt.exe", title="演示", pid=1, rect=(0, 0, 1, 1), fullscreen=True)
    assert s.decide(0, full, []) is None
    assert s.decide(0, fg(), [("wemeetapp.exe", "腾讯会议")]) is None  # meeting not in front
    assert s.decide(0, None, []) is None  # foreground unreadable
    assert s.decide(0, fg(), None) is None  # windows couldn't be listed
    assert s.decide(0, fg(), []) is not None


def test_no_chat_profile_means_nothing(tmp_path):
    s, _, clock = make(tmp_path, CFG.replace("roles: {chat: c}", "roles: {}"))
    clock.t += 10 * 60
    assert s.decide(0, fg(), []) is None


class FakeAgent:
    def __init__(self, text="在写代码呀？", fail=False, on_call=None):
        self.text, self.fail, self.on_call = text, fail, on_call
        self.committed = []
        self.calls = []

    def chat_configured(self):
        return True

    async def respond_proactive(self, event, labels, *, situation):
        self.calls.append(situation)
        if self.on_call:
            self.on_call()
        out = AgentOutput(text="" if self.fail else self.text)

        async def record():
            self.committed.append(out.text)

        return Turn(out, failed=self.fail, _record=record)


def run_dispatch(agent, gate, scheduler=None):
    emitted = []

    async def emit(out, ev):
        emitted.append(out.text)

    async def set_thinking(_):
        pass

    d = Dispatcher(agent=agent, rules=None, labels=lambda: Labels(), emit=emit, set_thinking=set_thinking, gate=gate)
    d.scheduler = scheduler
    asyncio.run(d(make_event("timer.idle", {"kind": "greeting", "situation": "午饭时间", "slot": "lunch"})))
    return emitted


class FakeScheduler:
    def __init__(self):
        self.calls = []

    def spoken(self, payload, text, at):
        self.calls.append((payload["slot"], text))


def test_dispatcher_speaks_and_counts():
    gate, sched = ProactiveGate(), FakeScheduler()
    assert run_dispatch(FakeAgent(), gate, sched) == ["在写代码呀？"] and gate.spoken_last_hour == 1
    assert sched.calls == [("lunch", "在写代码呀？")]


def test_dispatcher_silent_reply_marks_nothing():
    sched = FakeScheduler()
    assert run_dispatch(FakeAgent(text=""), ProactiveGate(), sched) == [] and sched.calls == []


def test_dispatcher_failure_backs_off():
    gate = ProactiveGate()
    assert run_dispatch(FakeAgent(fail=True), gate) == []
    assert gate.check(GateLimits(0, 99, 0), busy=None, quiet_app=False)


def test_dispatcher_drops_remark_if_user_spoke():
    gate, agent = ProactiveGate(), FakeAgent()
    agent.on_call = gate.user_active
    assert run_dispatch(agent, gate) == [] and agent.committed == []  # not said, so not remembered


def test_dispatcher_commits_what_it_says():
    agent = FakeAgent()
    assert run_dispatch(agent, ProactiveGate()) == ["在写代码呀？"] and agent.committed == ["在写代码呀？"]


IDLE_ACTION = "  idle_action: true\n  idle_action_minutes: 5\n  idle_action_motions: [nap, stretch]\n  idle_action_expressions: [sleepy]\n"


def test_idle_action_ignores_keyboard_and_resets_on_engaging_the_pet(tmp_path):
    # Works with the proactive master switch off, and never looks at the system idle time.
    s, gate, clock = make(tmp_path, CFG.replace("enabled: true", "enabled: false") + IDLE_ACTION)
    assert s.idle_action() is None  # just started
    clock.t += 5 * 60
    assert s.idle_action() == {"kind": "action", "motions": ["nap", "stretch"], "expressions": ["sleepy"]}
    assert gate.attempts_last_hour == 0  # no LLM budget used
    clock.t += 4 * 60
    assert s.idle_action() is None  # repeats only after the same wait
    clock.t += 60
    assert s.idle_action() is not None
    clock.t += 4 * 60
    gate.user_active()
    clock.t += 4 * 60
    assert s.idle_action() is None
    clock.t += 60
    assert s.idle_action() is not None


def test_idle_action_off_empty_or_busy(tmp_path):
    s, _, clock = make(tmp_path)
    clock.t += 3600
    assert s.idle_action() is None  # off by default
    s2, _, c2 = make(tmp_path, CFG + "  idle_action: true\n")
    c2.t += 3600
    assert s2.idle_action() is None  # nothing chosen
    s3, _, c3 = make(tmp_path, CFG + IDLE_ACTION, busy="正在说话")
    c3.t += 3600
    assert s3.idle_action() is None


def test_dispatcher_idle_action_uses_only_labels_the_model_has():
    emitted = []

    async def emit(out, ev):
        emitted.append((out.text, out.expression, out.motion))

    async def set_thinking(_):
        pass

    agent = FakeAgent()
    payload = {"kind": "action", "motions": ["nap", "stretch"], "expressions": ["sleepy"]}
    for labels in (Labels(expressions=["happy"], motions=["stretch"]), Labels(expressions=["happy"], motions=["wave"])):
        d = Dispatcher(agent=agent, rules=None, labels=lambda labels=labels: labels, emit=emit, set_thinking=set_thinking, gate=ProactiveGate())
        asyncio.run(d(make_event("timer.idle", payload)))
    assert emitted == [("", None, "stretch")]  # the second model has none of them: nothing happens
    assert agent.calls == []  # never reaches the LLM
