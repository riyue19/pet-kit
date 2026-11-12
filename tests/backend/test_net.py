import pytest

from core import net
from core.llm_config import ConfigError, parse_backend_config
from providers.openai_compat import OpenAICompatProvider
from core.llm_config import LlmProfile


@pytest.fixture(autouse=True)
def _reset():
    yield
    net.set_proxy("direct")


def test_default_is_direct_and_ignores_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    assert parse_backend_config("").network.proxy == "direct"
    assert net.client_kwargs() == {"trust_env": False}
    assert net.make_client()._mounts == {}


def test_system_and_url():
    net.set_proxy("system")
    assert net.client_kwargs() == {}
    net.set_proxy("http://127.0.0.1:7897")
    assert net.client_kwargs() == {"trust_env": False, "proxy": "http://127.0.0.1:7897"}


def test_invalid_proxy_rejected():
    with pytest.raises(ConfigError, match="network.proxy"):
        parse_backend_config("network:\n  proxy: banana\n")


def test_openai_provider_builds_with_direct(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    p = OpenAICompatProvider(LlmProfile(name="a", base_url="https://x.example/v1", model="m"), "k")
    assert p._client._client._mounts == {}
