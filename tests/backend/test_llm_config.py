import pytest
from pydantic import ValidationError

from core.llm_config import (
    BackendConfig,
    ConfigError,
    LlmConfig,
    LlmProfile,
    load_backend_config,
    parse_backend_config,
    save_llm_config,
)


def profile(**kw) -> dict:
    return {"name": "main", "base_url": "https://api.example.com/v1", "model": "m", **kw}


def test_empty_text_gives_defaults():
    cfg = parse_backend_config("")
    assert cfg == BackendConfig()
    assert cfg.server.port == 8765


def test_missing_file_gives_defaults(tmp_path):
    assert load_backend_config(tmp_path / "none.yaml") == BackendConfig()


def test_parses_profiles_and_roles():
    cfg = parse_backend_config(
        """
server: {port: 9000}
llm:
  profiles:
    - name: main
      base_url: https://api.example.com/v1/
      model: gpt
      temperature: 0.3
      top_p: null
      extra: {seed: 1}
  roles: {chat: main, vision: null}
"""
    )
    p = cfg.llm.profiles[0]
    assert cfg.server.port == 9000
    assert p.base_url == "https://api.example.com/v1"
    assert p.temperature == 0.3 and p.top_p is None and p.max_tokens == 1024
    assert p.extra == {"seed": 1}
    assert cfg.llm.for_role("chat") == p
    assert cfg.llm.for_role("vision") is None


@pytest.mark.parametrize(
    "override, fragment",
    [
        ({"name": ""}, "name"),
        ({"name": "a/b"}, "name"),
        ({"base_url": "ftp://x"}, "base_url"),
        ({"model": ""}, "model"),
        ({"temperature": 2.5}, "temperature"),
        ({"top_p": -0.1}, "top_p"),
        ({"max_tokens": 0}, "max_tokens"),
        ({"timeout": 0}, "timeout"),
        ({"extra": {"messages": []}}, "messages"),
        ({"unknown": 1}, "unknown"),
    ],
)
def test_profile_validation(override, fragment):
    with pytest.raises(ValidationError) as e:
        LlmProfile(**profile(**override))
    assert fragment in str(e.value)


def test_duplicate_names_and_dangling_roles_rejected():
    with pytest.raises(ValidationError, match="重复"):
        LlmConfig(profiles=[profile(), profile()])
    with pytest.raises(ValidationError, match="roles.chat"):
        LlmConfig(profiles=[profile()], roles={"chat": "other"})


def test_config_errors_are_readable():
    with pytest.raises(ConfigError, match="YAML"):
        parse_backend_config("llm: [")
    with pytest.raises(ConfigError, match="llm.profiles.0.temperature"):
        parse_backend_config("llm:\n  profiles:\n    - {name: a, base_url: 'http://x', model: m, temperature: 9}\n")


def test_save_replaces_llm_and_keeps_other_sections(tmp_path):
    path = tmp_path / "backend.yaml"
    path.write_text("# 顶部注释\nserver:\n  port: 9001 # 端口\nllm:\n  profiles: []\n", encoding="utf-8")
    llm = LlmConfig(profiles=[LlmProfile(**profile(extra={"seed": 7}))], roles={"chat": "main"})
    save_llm_config(path, llm)
    text = path.read_text(encoding="utf-8")
    assert "# 顶部注释" in text and "# 端口" in text
    cfg = load_backend_config(path)
    assert cfg.server.port == 9001
    assert cfg.llm == llm
    assert not (tmp_path / "backend.yaml.tmp").exists()
