"""config/instance.yaml: per-copy data folder, credential service names and port."""

from core import paths


def _with(tmp_path, monkeypatch, text):
    if text is not None:
        (tmp_path / "instance.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


def test_default_without_file(tmp_path, monkeypatch):
    _with(tmp_path, monkeypatch, None)
    assert paths.instance_name() == "pet-kit"
    assert paths.runtime_dir() == tmp_path / "local" / "pet-kit"
    assert paths.key_service("llm") == "pet-kit/llm"


def test_named_instance(tmp_path, monkeypatch):
    _with(tmp_path, monkeypatch, "name: pet-test\n")
    assert paths.instance_name() == "pet-test"
    assert paths.runtime_dir() == tmp_path / "local" / "pet-test"
    assert [paths.key_service(k) for k in ("llm", "tts", "skill")] == ["pet-test/llm", "pet-test/tts", "pet-test/skill"]


def test_name_is_sanitized(tmp_path, monkeypatch):
    _with(tmp_path, monkeypatch, "name: '../测试 copy/'\n")
    assert paths.instance_name() == "copy"
    _with(tmp_path, monkeypatch, "name: ''\n")
    assert paths.instance_name() == "pet-kit"
    _with(tmp_path, monkeypatch, "name: 123\n")
    assert paths.instance_name() == "pet-kit"


def test_port_is_stable_and_in_range():
    from api.__main__ import _port_for

    a, b = _port_for("pet-test"), _port_for("pet-test")
    assert a == b and 8766 <= a <= 9065
