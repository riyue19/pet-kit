import asyncio
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from action.skills import SkillRunner
from action.skills.runner import Launcher, launch
from action.tts import Speaker, TtsFactory
from api import camera_routes, console_routes, fishing_routes, game_routes, image_routes, llm_routes, memory_routes, pet_ws, prompt_routes, screen_routes, skill_routes, status_routes, voice_routes
from api.auth import require_token
from api.fishing_wiring import build_fishing
from api.pet_ws import PetHub
from api.tools import ToolRunner
from core.agent import Agent, AgentOutput, ReactionRules
from core.credentials import KeyStore
from core.camera_config import CameraConfig
from core.camera_react import CameraReactor
from core.dispatcher import Dispatcher
from core.image_stash import ImageStash
from core.event_bus import EventBus
from core.events import Event
from core.environment import Environment
from core.hotkeys import HotkeyRules
from core.llm_config import ConfigError, load_backend_config
from core.proactive import ProactiveGate
from core.scheduler import STATE_FILE, Scheduler
from core.status import STATE_FILE as STATUS_STATE_FILE, StatusService
from core.games import GameBrain, GameHall
from core.games import STATE_FILE as GAMES_STATE_FILE
from core.screen_comment import PrivacyFile, ScreenCommenter
from perception.idle import idle_seconds
from perception.screen import foreground, visible_windows
from core.screen_config import EnvironmentConfig, ScreenConfig
from perception.camera import CameraWatcher
from perception.screen_watch import ScreenWatcher
from core.logs import RingHandler
from core.memory.service import MemoryService
from core.voice import BACKEND_DIR, VoiceState
from providers.factory import TextProviderFactory, make_text_provider, make_tts_provider

VERSION = "0.1.0"
log = logging.getLogger(__name__)


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"] if x != "body") or "<body>"
        parts.append(f"{loc}: {str(err['msg']).removeprefix('Value error, ')}")
    return "；".join(parts)


def create_app(
    *,
    config_path: Path,
    token: str,
    keys: KeyStore | None = None,
    provider_factory: TextProviderFactory = make_text_provider,
    voice: VoiceState | None = None,
    tts_factory: TtsFactory = make_tts_provider,
    log_ring: RingHandler | None = None,
    log_dir: Path | None = None,
    memory: MemoryService | None = None,
    state_dir: Path | None = None,
    skill_launcher: Launcher | None = None,
    skill_keys: KeyStore | None = None,
) -> FastAPI:
    # No docs/openapi routes: nothing should be reachable without the token.
    app = FastAPI(title="pet-backend", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config_path = config_path
    app.state.token = token
    app.state.keys = keys or KeyStore()
    app.state.provider_factory = provider_factory
    app.state.save_lock = asyncio.Lock()
    app.state.images = ImageStash()
    # The console's 日志 page reads this ring; __main__ attaches it to the root logger.
    app.state.log_ring = log_ring or RingHandler()
    app.state.log_dir = log_dir

    @app.exception_handler(RequestValidationError)
    async def _on_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": _validation_message(exc)}, status_code=422)

    @app.get("/api/health", dependencies=[Depends(require_token)])
    def health() -> dict:
        return {"ok": True, "version": VERSION}

    app.include_router(llm_routes.router)
    app.include_router(image_routes.router)
    app.include_router(voice_routes.router)
    app.include_router(console_routes.router)
    app.include_router(memory_routes.router)
    app.include_router(screen_routes.router)
    app.include_router(skill_routes.router)
    app.include_router(screen_routes.privacy_router)
    app.include_router(camera_routes.router)
    app.include_router(prompt_routes.router)
    app.include_router(status_routes.router)
    app.include_router(game_routes.router)
    app.include_router(fishing_routes.router)

    config_dir = config_path.parent

    def env_config() -> EnvironmentConfig:
        try:
            return load_backend_config(config_path).environment
        except ConfigError:
            return EnvironmentConfig()

    def screen_config() -> ScreenConfig:
        try:
            return load_backend_config(config_path).screen
        except ConfigError:
            return ScreenConfig()

    def camera_config() -> CameraConfig:
        try:
            return load_backend_config(config_path).camera
        except ConfigError:
            return CameraConfig()

    environment = Environment(env_config)
    app.state.environment = environment
    voice = voice or VoiceState(config_path)
    hub = PetHub(voice)
    hub.hotkeys_path = config_dir / "hotkeys.yaml"
    hub.speaker = Speaker(
        config=lambda: voice.tts,
        api_key=voice.api_key,
        factory=tts_factory,
        send_json=hub.broadcast,
        send_bytes=hub.broadcast_bytes,
        queue_mode=lambda: not voice.asr.barge_in,
        wait_idle=voice.wait_playback_done,
    )
    app.state.voice = voice
    app.state.tts_factory = tts_factory
    bus = EventBus()
    skills = SkillRunner(
        config_path=config_dir / "skills.yaml",
        confirm=hub.confirm,
        launcher=skill_launcher or launch,
        keys=skill_keys,
    )
    app.state.skills = skills
    skill_tasks: set[asyncio.Task] = set()
    # Values persist next to the other runtime state; without a state dir (tests) they live in memory.
    app.state.status = StatusService(config_dir / "status.yaml", state_dir / STATUS_STATE_FILE if state_dir else None)
    agent = Agent(
        status=app.state.status,
        skills=skills,
        config_path=config_path,
        persona_path=config_dir / "persona.yaml",
        keys=app.state.keys,
        provider_factory=provider_factory,
        memory=memory,
        environment=environment,
    )
    app.state.agent = agent
    # Mini games (console 小游戏 page): the pet's moves come from the chat profile chain.
    app.state.games = GameHall(
        GameBrain(config_path=config_path, persona_path=config_dir / "persona.yaml", keys=app.state.keys, provider_factory=provider_factory),
        state_path=state_dir / GAMES_STATE_FILE if state_dir else None,
    )
    # Only the tally reaches ordinary conversation; what was said in a game is never stored.
    environment.extra_lines.append(app.state.games.record_line)
    # None in tests that don't exercise memory: the agent then keeps an in-memory history only.
    app.state.memory = memory
    rules = ReactionRules(config_dir / "reactions.yaml")

    async def emit(output: AgentOutput, event: Event) -> None:
        if event.ts < hub.stopped_at:
            # The user pressed "别说了" while this reply was being generated.
            log.info("已按下停止，丢弃 %s 的回复", event.id)
            bus.record_result(event.id, {"error": "已被「停止说话」丢弃"})
            return
        if output.status:
            # Applied only now that the reply is really going out (not discarded by 停止说话): the model
            # only names a change; the service limits it, does the sum and saves.
            try:
                output.status = app.state.status.apply(output.status)
            except Exception:
                log.exception("更新状态值失败")
                output.status = []
        result: dict = {"output": output.model_dump(include={"text", "speech", "expression", "motion"})}
        if output.status:
            result["status"] = [c.model_dump(include={"id", "delta", "value", "reason", "tier"}) for c in output.status]
        bus.record_result(event.id, result)
        await hub.emit(output, event)
        if output.tool_calls:
            # Separate task: a confirmation can take a while and must not hold up the conversation.
            task = asyncio.create_task(tools.run(output, event, result))
            skill_tasks.add(task)
            task.add_done_callback(skill_tasks.discard)

    gate = ProactiveGate()
    privacy = PrivacyFile(config_dir / "privacy.yaml")
    dispatcher = Dispatcher(
        agent=agent,
        images=app.state.images,
        rules=rules,
        labels=lambda: hub.labels,
        emit=emit,
        set_thinking=hub.set_thinking,
        hotkeys=HotkeyRules(config_dir / "hotkeys.yaml"),
        gate=gate,
    )
    def busy() -> str | None:
        if not hub.clients:
            return "桌宠未连接"
        if dispatcher.thinking:
            return "正在思考"
        if voice.playing or (hub.speaker and hub.speaker.busy):
            return "正在说话"
        return None

    dispatcher.screen = ScreenCommenter(
        agent=agent,
        gate=gate,
        config_path=config_path,
        privacy=privacy,
        labels=lambda: hub.labels,
        busy=busy,
    )
    # Started by __main__'s lifespan (tests don't open the real camera).
    camera_watcher = CameraWatcher(
        config=camera_config,
        privacy=privacy,
        publish=bus.publish,
        base_dir=BACKEND_DIR,
        windows=visible_windows,
        on_state=hub.set_camera,
        has_viewer=lambda: bool(hub.clients),
    )
    dispatcher.camera = CameraReactor(
        agent=agent,
        gate=gate,
        config_path=config_path,
        privacy=privacy,
        labels=lambda: hub.labels,
        busy=busy,
        snapshot=camera_watcher.snapshot_jpeg,
        windows=visible_windows,
    )
    app.state.camera_watcher = camera_watcher
    # Tool calls of a reply (skills and built-in actions): api/tools.py. `emit` above uses it.
    tools = ToolRunner(
        skills=skills,
        hub=hub,
        bus=bus,
        games=app.state.games,
        camera_working=lambda: camera_watcher.open and camera_watcher.present is not None,
    )
    agent.extra_tools = tools.extra_tools
    agent.proactive_tools = tools.proactive_tools
    environment.extra_lines.append(camera_watcher.status_line)
    app.state.camera_reactor = dispatcher.camera
    bus.subscribe(dispatcher)

    # Idle fishing: api/fishing_wiring.py.
    fishing = build_fishing(
        config_path=config_path,
        state_dir=state_dir,
        hub=hub,
        brain=app.state.games.brain,
        status=app.state.status,
        privacy=privacy,
        gate=gate,
        busy=busy,
        publish=bus.publish,
    )
    # Started by __main__'s lifespan (tests drive it by hand).
    app.state.fishing = hub.fishing = fishing
    bus.subscribe(fishing.on_event)
    dispatcher.commands.append(fishing.handle_text)
    environment.extra_lines.append(fishing.record_line)
    app.state.gate = gate
    app.state.privacy = privacy
    app.state.screen_commenter = dispatcher.screen
    # Started by __main__'s lifespan (tests don't poll the real screen).
    app.state.screen_watcher = ScreenWatcher(config=screen_config, privacy=privacy, publish=bus.publish)
    # Started by __main__'s lifespan, like the screen watcher.
    dispatcher.scheduler = app.state.scheduler = Scheduler(
        config_path=config_path,
        privacy=privacy,
        gate=gate,
        publish=bus.publish,
        # Also wait while a screen look is in flight (it runs outside dispatcher.thinking).
        busy=lambda: "正在看屏幕" if dispatcher.screen.inflight else "主人正在说话" if hub.hearing else busy(),
        idle=idle_seconds,
        probe=foreground,
        windows=visible_windows,
        state_path=state_dir / STATE_FILE if state_dir else None,
    )
    app.state.pet_hub = hub
    app.state.bus = bus
    app.include_router(pet_ws.router)
    return app
