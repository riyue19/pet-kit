"""Entry point: `uv run python -m api` (from backend/)."""

import asyncio
import logging
import socket
import sys
from contextlib import asynccontextmanager

import uvicorn

from api.app import create_app
from api.auth import new_token, remove_endpoint_file, write_endpoint_file
from core import net
from core.llm_config import ConfigError, load_backend_config
from core.paths import CONFIG_DIR, runtime_dir
from core.logs import setup_logging
from core.memory.factory import build_memory_service, resolve_db_path
from core.credentials import KeyStore
from providers.factory import make_text_provider
from core.redact import register_secret

HOST = "127.0.0.1"


def main() -> int:
    # Piped output (e.g. spawned by Electron or a bash terminal) would otherwise use the ANSI code page.
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    log_dir = runtime_dir() / "logs"
    ring = setup_logging(log_dir)
    # Anything escaping main() still ends up in backend.jsonl with its stack.
    sys.excepthook = lambda t, v, tb: logging.critical("后端未捕获异常", exc_info=(t, v, tb))
    config_path = CONFIG_DIR / "backend.yaml"
    try:
        cfg = load_backend_config(config_path)
    except ConfigError as e:
        logging.error("配置错误 %s: %s", config_path, e)
        return 1

    net.set_proxy(cfg.network.proxy)
    logging.info("网络代理：%s", cfg.network.proxy)
    token = new_token()
    register_secret(token)
    port = cfg.server.port
    keys = KeyStore()
    # Register every stored secret up front: a key pasted into chat is masked even if its profile hasn't been used.
    for profile in cfg.llm.profiles:
        keys.get(profile.name)
    from core.voice import TTS_KEY_SERVICE
    from core.voice_config import TTS_PROVIDERS

    for provider_name in TTS_PROVIDERS:
        KeyStore(TTS_KEY_SERVICE).get(provider_name)
    memory = build_memory_service(
        config_path=config_path, db_path=resolve_db_path(cfg.memory), keys=keys, provider_factory=make_text_provider
    )
    app = create_app(config_path=config_path, token=token, keys=keys, log_ring=ring, log_dir=log_dir, memory=memory, state_dir=runtime_dir())
    endpoint = runtime_dir()

    @asynccontextmanager
    async def lifespan(_app):
        path = write_endpoint_file(endpoint, port, token)
        logging.info("后端已启动 http://%s:%d （连接信息：%s）", HOST, port, path)
        await memory.warm_up()
        loop = asyncio.get_running_loop()
        tasks = [
            loop.create_task(app.state.screen_watcher.run()),
            loop.create_task(app.state.scheduler.run()),
            loop.create_task(app.state.camera_watcher.run()),
            loop.create_task(app.state.fishing.run()),
        ]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.state.bus.aclose()
            await app.state.fishing.aclose()
            await memory.aclose()
            await app.state.environment.aclose()
            remove_endpoint_file(path, token)

    # Bind before the lifespan writes the endpoint file: uvicorn runs lifespan first, so a second instance
    # failing to bind would otherwise overwrite and then delete the running instance's endpoint file.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, port))
    except OSError as e:
        sock.close()
        logging.error("端口 %d 无法监听（后端可能已在运行）：%s", port, e)
        return 1

    app.router.lifespan_context = lifespan
    # log_config=None: uvicorn's own loggers propagate to our root handlers (file, ring, redaction).
    config = uvicorn.Config(app, log_level="info", access_log=False, ws_max_size=64 * 1024, log_config=None)
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    sys.exit(main())
