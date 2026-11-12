"""Outbound HTTP proxy policy shared by every backend HTTP client (LLM, TTS, weather).

httpx reads the Windows system proxy / HTTP(S)_PROXY by default. When the proxy app is closed or has crashed
while the system proxy flag is still on, every request fails instantly, so the default here is "direct".
"""

from typing import Any

import httpx

DIRECT = "direct"
SYSTEM = "system"
PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")

_proxy = DIRECT


def validate_proxy(v: str) -> str:
    v = v.strip()
    if v.lower() in (DIRECT, SYSTEM):
        return v.lower()
    if not v.lower().startswith(PROXY_SCHEMES):
        raise ValueError("必须是 direct、system 或代理地址（如 http://127.0.0.1:7897）")
    return v


def set_proxy(v: str) -> None:
    global _proxy
    _proxy = validate_proxy(v)


def get_proxy() -> str:
    return _proxy


def client_kwargs() -> dict[str, Any]:
    """Keyword arguments for httpx.AsyncClient (also accepted by openai.DefaultAsyncHttpxClient)."""
    if _proxy == SYSTEM:
        return {}
    if _proxy == DIRECT:
        return {"trust_env": False}
    return {"trust_env": False, "proxy": _proxy}


def make_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(**client_kwargs(), **kwargs)


def ws_proxy() -> Any:
    """`proxy` argument for websockets.connect under the same policy: None = direct, True = system."""
    if _proxy == SYSTEM:
        return True
    return None if _proxy == DIRECT else _proxy
