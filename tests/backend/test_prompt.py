import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.app import create_app
from core import llm_chain
from core.agent import Agent, Labels, format_sections, speech_ok
from core.credentials import KeyStore
from core.events import make_event
from core.persona import Persona
from core.prompt_config import MAX_KNOWLEDGE_CHARS, KnowledgeEntry, PromptConfig
from providers.base import ChatResult, ProviderError

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
TTS_JA = "tts:\n  enabled: true\n  speech_language: ja\n"
SEARCH_CONFIG = """server:
  port: 8765
llm:
  web_search: true
  profiles:
    - name: a
      base_url: https://api.example.com/v1
      model: m
      web_search: "off"
    - name: b
      base_url: https://api.example.com/v1
      model: m
      web_search: google_function
  roles:
    chat: a
  fallbacks:
    chat: [b]
"""
SEARCH_RULE_TEXT = "你可以联网搜索"


class ScriptedProvider:
    calls: list = []
    replies: list = []

    def __init__(self, profile, key):
        self.key = key

    async def chat(self, messages, **_kw):
        ScriptedProvider.calls.append((self.key, messages))
        reply = ScriptedProvider.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(text=reply, model="m", latency_ms=1)

    async def aclose(self):
        pass


class SearchProvider:
    calls: list = []  # (profile name, web_search, messages)

    def __init__(self, profile, key):
        self.name = profile.name

    async def chat(self, messages, **kw):
        SearchProvider.calls.append((self.name, kw["web_search"], messages))
        return ChatResult(text='{"text": "ok"}', model="m", latency_ms=1)

    async def aclose(self):
        pass


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    ScriptedProvider.calls, ScriptedProvider.replies = [], []
    SearchProvider.calls = []
    llm_chain.reset_cooldowns()
    return tmp_path


def make_agent(config_dir, factory=ScriptedProvider):
    return Agent(
        config_path=config_dir / "backend.yaml",
        persona_path=config_dir / "persona.yaml",
        keys=KeyStore(),
        provider_factory=factory,
    )


def say(agent, text):
    return asyncio.run(agent.respond(make_event("user.text", {"text": text}), LABELS))


def write(config_dir, name, text):
    (config_dir / name).write_text(text, encoding="utf-8")


# --- PromptConfig ---------------------------------------------------------------------------


def test_knowledge_for_modes_and_enabled():
    cfg = PromptConfig(
        knowledge=[
            KnowledgeEntry(title="a", content="always-on"),
            KnowledgeEntry(title="k", content="kw-entry", mode="keyword", keywords=["Genshin"]),
            KnowledgeEntry(title="off", content="disabled", enabled=False),
        ]
    )
    assert [e.title for e in cfg.knowledge_for("你好")] == ["a"]
    assert [e.title for e in cfg.knowledge_for("我在玩 genshin")] == ["a", "k"]


def test_knowledge_budget():
    cfg = PromptConfig(knowledge=[KnowledgeEntry(title=str(i), content="x" * 2000) for i in range(3)])
    assert 2 * 2000 <= MAX_KNOWLEDGE_CHARS < 3 * 2000
    assert [e.title for e in cfg.knowledge_for("hi")] == ["0", "1"]


def test_expression_notes_validation():
    for bad in ["Happy", "1a", "a-b", "", "x" * 33]:
        with pytest.raises(ValidationError):
            PromptConfig(expression_notes={bad: "x"})
    cfg = PromptConfig(expression_notes={"happy": "  开心   时 ", "sad": "   "})
    assert cfg.expression_notes == {"happy": "开心 时"}


# --- format_sections / speech_ok ------------------------------------------------------------


def test_format_sections_order_and_content():
    persona = Persona()
    keys = [s.key for s in format_sections(persona, LABELS, None, ["open_app"])]
    assert keys == ["skills", "labels", "format"]
    assert [s.key for s in format_sections(persona, LABELS)] == ["labels", "format"]

    secs = format_sections(persona, LABELS, prompt=PromptConfig(expression_notes={"happy": "开心时"}, format_rules="不要用颜文字"))
    by = {s.key: s for s in secs}
    assert "happy（开心时）" in by["labels"].text
    assert [s.key for s in secs] == ["labels", "format", "format_rules"]
    assert by["format_rules"].text == "不要用颜文字" and "不要用颜文字" not in by["format"].text

    ja = {s.key: s for s in format_sections(persona, LABELS, "ja")}["format"].text
    assert '"speech"' in ja and "日语" in ja
    assert '"speech"' not in by["format"].text


@pytest.mark.parametrize(
    ("speech", "lang", "ok"),
    [("こんにちは", "ja", True), ("你好", "ja", False), ("", "ja", False), ("Hello there", "en", True), ("你好 hello", "en", False)],
)
def test_speech_ok(speech, lang, ok):
    assert speech_ok(speech, lang) is ok


# --- knowledge in the prompt ----------------------------------------------------------------


def test_knowledge_reaches_system_prompt(config_dir):
    write(
        config_dir,
        "prompt.yaml",
        "knowledge:\n"
        "  - {title: 常识, content: ALWAYS-CONTENT}\n"
        "  - {title: 猫, content: CAT-CONTENT, mode: keyword, keywords: [猫]}\n",
    )
    ScriptedProvider.replies = ['{"text": "a"}', '{"text": "b"}']
    agent = make_agent(config_dir)
    say(agent, "你好")
    sys1 = ScriptedProvider.calls[-1][1][0]["content"]
    assert "ALWAYS-CONTENT" in sys1 and "CAT-CONTENT" not in sys1
    say(agent, "我的猫呢")
    sys2 = ScriptedProvider.calls[-1][1][0]["content"]
    assert "ALWAYS-CONTENT" in sys2 and "CAT-CONTENT" in sys2


# --- speech repair --------------------------------------------------------------------------

NO_SPEECH = '{"text": "你好", "expression": null, "motion": null}'


def ja_agent(config_dir, prompt_yaml=None):
    write(config_dir, "backend.yaml", CONFIG + TTS_JA)
    if prompt_yaml:
        write(config_dir, "prompt.yaml", prompt_yaml)
    return make_agent(config_dir)


def test_speech_repaired_when_missing(config_dir):
    ScriptedProvider.replies = [NO_SPEECH, "こんにちは"]
    agent = ja_agent(config_dir)
    out = say(agent, "hi")
    assert out.speech == "こんにちは" and out.text == "你好"
    assert len(ScriptedProvider.calls) == 2
    assert "日语" in ScriptedProvider.calls[1][1][0]["content"]
    assert agent.last_prompt["speech_repaired"] is True


def test_speech_good_no_repair(config_dir):
    ScriptedProvider.replies = ['{"text": "你好", "speech": "こんにちは"}']
    out = say(ja_agent(config_dir), "hi")
    assert out.speech == "こんにちは" and len(ScriptedProvider.calls) == 1


def test_speech_in_chinese_triggers_repair(config_dir):
    ScriptedProvider.replies = ['{"text": "你好", "speech": "你好"}', "こんにちは"]
    out = say(ja_agent(config_dir), "hi")
    assert len(ScriptedProvider.calls) == 2 and out.speech == "こんにちは"


def test_speech_repair_can_be_disabled(config_dir):
    ScriptedProvider.replies = ['{"text": "你好", "speech": "你好"}']
    out = say(ja_agent(config_dir, "speech_repair: false\n"), "hi")
    assert len(ScriptedProvider.calls) == 1 and out.speech == "你好"


def test_speech_repair_failure_is_harmless(config_dir):
    ScriptedProvider.replies = [NO_SPEECH, ProviderError("boom")]
    out = say(ja_agent(config_dir), "hi")
    assert out.text == "你好" and len(ScriptedProvider.calls) == 2


def test_history_keeps_speech(config_dir):
    reply = '{"text": "你好", "speech": "こんにちは", "expression": null, "motion": null}'
    ScriptedProvider.replies = [reply, reply]
    agent = ja_agent(config_dir)
    say(agent, "一")
    say(agent, "二")
    messages = ScriptedProvider.calls[1][1]
    assistants = [m for m in messages if m["role"] == "assistant"]
    assert len(assistants) == 1 and '"speech": "こんにちは"' in assistants[0]["content"]


# --- search rerouting -----------------------------------------------------------------------


def search_agent(config_dir, prompt_yaml=None):
    write(config_dir, "backend.yaml", SEARCH_CONFIG)
    if prompt_yaml:
        write(config_dir, "prompt.yaml", prompt_yaml)
    return make_agent(config_dir, SearchProvider)


def test_search_news_goes_to_searching_profile(config_dir):
    say(search_agent(config_dir), "今天有什么新闻")
    name, ws, messages = SearchProvider.calls[0]
    assert name == "b" and ws is True
    assert SEARCH_RULE_TEXT in messages[0]["content"]


def test_search_chitchat_stays_on_primary(config_dir):
    say(search_agent(config_dir), "你好")
    name, ws, messages = SearchProvider.calls[0]
    assert name == "a" and ws is False
    assert SEARCH_RULE_TEXT not in messages[0]["content"]


def test_search_custom_keyword(config_dir):
    say(search_agent(config_dir, 'search_keywords: ["帮我看看"]\n'), "帮我看看这个")
    assert SearchProvider.calls[0][0] == "b"


def test_search_fallback_can_be_disabled(config_dir):
    say(search_agent(config_dir, "search_use_fallback: false\n"), "今天有什么新闻")
    assert SearchProvider.calls[0][0] == "a"


# --- routes ---------------------------------------------------------------------------------


@pytest.fixture
def client(config_dir):
    app = create_app(config_path=config_dir / "backend.yaml", token=TOKEN, provider_factory=ScriptedProvider)
    return TestClient(app)


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_get_prompt(client):
    assert client.get("/api/prompt").status_code == 401
    r = client.get("/api/prompt", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["config"] == PromptConfig().model_dump(mode="json")
    assert isinstance(body["expressions"], list) and isinstance(body["motions"], list)
    assert body["pet_connected"] is False and body["last"] is None


def test_put_prompt_saves_and_validates(client, config_dir):
    body = {"knowledge": [{"title": "t", "content": "c"}]}
    r = client.put("/api/prompt", headers=auth(), json=body)
    assert r.status_code == 200
    assert (config_dir / "prompt.yaml").exists()
    got = client.get("/api/prompt", headers=auth()).json()["config"]
    assert got["knowledge"][0]["title"] == "t" and got["knowledge"][0]["content"] == "c"
    bad = client.put("/api/prompt", headers=auth(), json={"knowledge": [{"content": "c"}]})
    assert bad.status_code == 422


def test_preview_does_not_call_llm(client):
    r = client.post("/api/prompt/preview", headers=auth(), json={"text": "你好"})
    assert r.status_code == 200
    body = r.json()
    keys = {s["key"] for s in body["sections"]}
    assert {"persona", "environment", "labels", "format"} <= keys
    assert body["total_chars"] > 0
    assert ScriptedProvider.calls == []


# --- editable built-in pieces + keyword-triggered commands ---------------------------------

SKILLS_YAML = """enabled: true
skills:
  open_app:
    enabled: true
    targets:
      notepad: {label: 记事本, exe: notepad.exe}
      genshin: {label: 原神, exe: game.exe}
  play_music:
    enabled: true
    targets:
      netease: {label: 网易云, url: "orpheus://"}
"""


def _skill_agent(config_dir):
    from action.skills import SkillRunner

    (config_dir / "skills.yaml").write_text(SKILLS_YAML, encoding="utf-8")

    async def confirm(*_a):
        return True

    agent = make_agent(config_dir)
    agent._skills = SkillRunner(config_path=config_dir / "skills.yaml", confirm=confirm, launcher=lambda *a: None)
    return agent


def _sections(agent, text):
    view = asyncio.run(agent.preview(text, LABELS))
    return {s["key"]: s for s in view["sections"]}, view


def test_commands_are_described_only_when_named(config_dir):
    agent = _skill_agent(config_dir)
    plain, view = _sections(agent, "你好呀")
    assert "skills" not in plain and any("指令" in n for n in view["notes"])
    named, _ = _sections(agent, "帮我打开原神")
    text = named["skills"]["text"]
    assert "genshin（原神）" in text and "记事本" not in text and "play_music" not in text
    music, _ = _sections(agent, "来首歌听听")
    assert "play_music" in music["skills"]["text"] and "open_app" not in music["skills"]["text"]


def test_unnamed_command_cannot_be_called(config_dir):
    agent = _skill_agent(config_dir)
    reply = '{"text": "好", "tool_calls": [{"name": "open_app", "args": {"app": "genshin"}}]}'
    ScriptedProvider.replies = [reply, reply]
    quiet = asyncio.run(agent.respond(make_event("user.text", {"text": "你好呀"}), LABELS))
    assert quiet.tool_calls == []
    asked = asyncio.run(agent.respond(make_event("user.text", {"text": "打开原神"}), LABELS))
    assert [c.name for c in asked.tool_calls] == ["open_app"]


def test_follow_up_line_keeps_the_command(config_dir):
    agent = _skill_agent(config_dir)
    ScriptedProvider.replies = ['{"text": "开了"}']
    asyncio.run(agent.respond(make_event("user.text", {"text": "打开记事本"}), LABELS))
    after, _ = _sections(agent, "再开一次")
    assert "notepad（记事本）" in after["skills"]["text"]


def test_skills_mode_always_lists_everything(config_dir):
    (config_dir / "prompt.yaml").write_text("skills_mode: always\n", encoding="utf-8")
    agent = _skill_agent(config_dir)
    secs, _ = _sections(agent, "你好呀")
    assert "记事本" in secs["skills"]["text"] and "play_music" in secs["skills"]["text"]


def test_overrides_replace_built_in_text(config_dir):
    (config_dir / "prompt.yaml").write_text(
        "skills_mode: always\nformat_rules: 不要颜文字\noverrides:\n"
        "  skills: \"自定义技能说明\\n{skills}\"\n"
        "  labels: \"表情只有：{expressions}\"\n"
        "  format: \"{extra_rules}\\n只回 JSON：{json}\\n最多 {max_chars} 字\"\n"
        "  environment: \"环境→{environment}\"\n",
        encoding="utf-8",
    )
    agent = _skill_agent(config_dir)
    secs, _ = _sections(agent, "你好呀")
    assert secs["skills"]["text"].startswith("自定义技能说明\n- ") and secs["skills"]["template"] == "skills"
    assert secs["labels"]["text"] == "表情只有：happy、sad"
    assert secs["format"]["text"].startswith('只回 JSON：{"text"') and secs["format"]["text"].endswith("最多 80 字")
    assert secs["format_rules"]["text"] == "不要颜文字"
    assert secs["environment"]["text"].startswith("环境→现在是 ") and secs["persona"]["template"] is None


def test_render_drops_empty_placeholder_lines_and_keeps_unknown_braces():
    from core.agent import render

    cfg = PromptConfig(overrides={"format": "A\n{extra_rules}\nB {json} {nope}"})
    assert render(cfg, "format", {"extra_rules": "", "json": '{"args": {}}'}) == 'A\nB {"args": {}} {nope}'
    assert "{json}" not in render(None, "format", {"json": "J", "max_chars": "80", "extra_rules": ""})


def test_format_template_follows_speech_language():
    cfg = PromptConfig(overrides={"format_speech": "{lang}版：{json}"})
    ja = format_sections(Persona(), LABELS, "ja", None, cfg)[-1]
    plain = format_sections(Persona(), LABELS, None, None, cfg)[-1]
    assert ja.template == "format_speech" and ja.text.startswith('日语版：{"text"') and '"speech"' in ja.text
    assert plain.template == "format" and plain.text.startswith("【输出格式】")


def test_override_routes(tmp_path):
    from fastapi.testclient import TestClient

    from api.app import create_app

    (tmp_path / "backend.yaml").write_text(CONFIG, encoding="utf-8")
    client = TestClient(create_app(config_path=tmp_path / "backend.yaml", token="t", provider_factory=ScriptedProvider))
    h = {"Authorization": "Bearer t"}
    state = client.get("/api/prompt", headers=h).json()
    keys = [t["key"] for t in state["templates"]]
    assert {"skills", "labels", "format", "format_speech", "environment", "memory", "screen"} <= set(keys)
    assert state["config"]["skills_mode"] == "keyword"
    fmt = next(t for t in state["templates"] if t["key"] == "format")
    assert fmt["required"] == ["json"] and "{json}" in fmt["default"] and {"name", "desc"} == set(fmt["placeholders"][0])
    assert {t["name"] for t in state["tools"]} == {"play_music", "show_code", "set_style", "look_camera", "invite_game"}
    body = {**state["config"], "overrides": {"screen": "看屏幕时这样说", "format": "   "}}
    saved = client.put("/api/prompt", headers=h, json=body).json()
    assert saved["config"]["overrides"] == {"screen": "看屏幕时这样说"}
    bad = client.put("/api/prompt", headers=h, json={**state["config"], "overrides": {"nope": "x"}})
    assert bad.status_code == 422


def test_stickers_section_only_when_the_pet_has_some():
    labels = Labels(expressions=["happy"], motions=[], stickers=["猫猫震惊", "好耶"])
    secs = {s.key: s for s in format_sections(Persona(), labels)}
    assert "猫猫震惊、好耶" in secs["stickers"].text and secs["stickers"].template == "stickers"
    assert '"sticker"' in secs["format"].text
    assert [s.key for s in format_sections(Persona(), labels, None, ["open_app"])] == ["skills", "labels", "stickers", "format"]
    plain = {s.key: s for s in format_sections(Persona(), LABELS)}
    assert "stickers" not in plain and "sticker" not in plain["format"].text


def test_section_order():
    from core.agent import SECTIONS, section_order

    default = list(SECTIONS)
    assert section_order() == default and section_order(["nope"]) == default
    moved = section_order(["format", "persona"])
    assert moved[:2] == ["format", "format_rules"] and moved[2:4] == ["persona", "environment"]
    assert sorted(moved) == sorted(default)


def test_order_rearranges_sections_and_preview_takes_unsaved_config(client, config_dir):
    body = {"order": ["format", "labels", "persona"], "format_rules": "不要颜文字"}
    r = client.post("/api/prompt/preview", headers=auth(), json={"text": "你好", "config": body})
    assert r.status_code == 200
    keys = [s["key"] for s in r.json()["sections"]]
    assert keys[:4] == ["format", "format_rules", "labels", "persona"]
    # Nothing was saved: the stored settings still give the built-in order.
    plain = [s["key"] for s in client.post("/api/prompt/preview", headers=auth(), json={"text": "你好"}).json()["sections"]]
    assert plain[0] == "persona" and plain[-1] == "format"
    assert client.post("/api/prompt/preview", headers=auth(), json={"text": "x", "config": {"nope": 1}}).status_code == 422
    assert client.put("/api/prompt", headers=auth(), json={"order": ["nope"]}).status_code == 422
    saved = client.put("/api/prompt", headers=auth(), json={"order": ["format", "persona"]}).json()
    assert [s["key"] for s in saved["sections"]][:3] == ["format", "format_rules", "persona"]
    assert saved["sections"][0]["templates"] == ["format", "format_speech", "speech_repair"]
