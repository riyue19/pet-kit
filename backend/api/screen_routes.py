"""Console API for the 感知 page: screen perception settings, privacy lists, weather city, status, test look."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.auth import require_token
from core.events import make_event
from core.llm_config import format_validation_error, load_backend_config, save_sections
from core.persona import save_yaml_model
from core.screen_config import CADENCES, CHAT_CADENCES, ChatCadence, EnvironmentConfig, PrivacyConfig, ProactiveConfig, ScreenConfig

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/screen", dependencies=[Depends(require_token)])


class ForegroundOut(BaseModel):
    app: str
    title: str
    fullscreen: bool
    blocked: bool
    quiet: bool


class CadenceOut(BaseModel):
    dwell_seconds: float
    per_content_seconds: float
    min_gap_seconds: float
    max_per_hour: int


class AllOffOut(BaseModel):
    microphone: bool
    screen: bool
    proactive: bool
    camera: bool


class StatusOut(BaseModel):
    # Last foreground window that wasn't the pet/console (blocked ones show no title).
    foreground: ForegroundOut | None
    spoken_last_hour: int
    # Vision calls this hour (looks are charged whether or not the pet then speaks).
    looks_last_hour: int
    last_comment_at: float | None
    last_comment_app: str | None
    last_comment_text: str | None
    # Profile used to look at the screen (llm.roles.vision); null = screen comments can't work.
    vision_profile: str | None
    pet_connected: bool
    # Seconds since the last keyboard/mouse input (None if it can't be read).
    system_idle_seconds: float | None
    last_proactive_at: float | None
    last_proactive_kind: str | None
    last_proactive_text: str | None


class ScreenStateOut(BaseModel):
    screen: ScreenConfig
    proactive: ProactiveConfig
    chat_cadences: dict[str, ChatCadence]
    environment: EnvironmentConfig
    privacy: PrivacyConfig
    # Details of each mode, for the console to explain them.
    cadences: dict[str, CadenceOut]
    status: StatusOut
    # Labels of the model the pet currently shows (empty while the pet window is not connected).
    labels: dict[str, list[str]]


class ScreenSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screen: dict
    proactive: dict
    environment: dict
    privacy: dict


class LookIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Time to switch to the window you want looked at (the console itself is never captured).
    delay_seconds: float = Field(default=5, ge=0, le=30)


class LookOut(BaseModel):
    scheduled: bool
    error: str | None = None


class WeatherOut(BaseModel):
    text: str
    error: str | None = None


def _state(request: Request) -> ScreenStateOut:
    app = request.app
    cfg = load_backend_config(app.state.config_path)
    privacy: PrivacyConfig = app.state.privacy()
    seen = app.state.screen_watcher.last_seen
    fg = None
    if seen is not None:
        blocked = privacy.blocked(seen.process, seen.title)
        fg = ForegroundOut(
            app=seen.process,
            title="（隐私窗口）" if blocked else seen.title,
            fullscreen=seen.fullscreen,
            blocked=blocked,
            quiet=privacy.quiet(seen.process, seen.title),
        )
    last = app.state.screen_commenter.last
    last_proactive = app.state.scheduler.last_spoken  # (wall time, kind, text)
    return ScreenStateOut(
        screen=cfg.screen,
        proactive=cfg.proactive,
        chat_cadences=CHAT_CADENCES,
        environment=cfg.environment,
        privacy=privacy,
        cadences={
            k: CadenceOut(
                dwell_seconds=c.dwell_seconds,
                per_content_seconds=c.per_content_seconds,
                min_gap_seconds=c.min_gap_seconds,
                max_per_hour=c.max_per_hour,
            )
            for k, c in CADENCES.items()
        },
        status=StatusOut(
            foreground=fg,
            spoken_last_hour=app.state.gate.spoken_last_hour,
            looks_last_hour=app.state.gate.attempts_last_hour,
            last_comment_at=last.at if last else None,
            last_comment_app=last.app if last else None,
            last_comment_text=last.text if last else None,
            vision_profile=cfg.llm.roles.vision,
            pet_connected=bool(app.state.pet_hub.clients),
            system_idle_seconds=app.state.scheduler.idle_seconds(),
            last_proactive_at=last_proactive[0] if last_proactive else None,
            last_proactive_kind=last_proactive[1] if last_proactive else None,
            last_proactive_text=last_proactive[2] if last_proactive else None,
        ),
        labels={"expressions": list(app.state.pet_hub.labels.expressions), "motions": list(app.state.pet_hub.labels.motions)},
    )


@router.get("")
def get_screen(request: Request) -> ScreenStateOut:
    return _state(request)


@router.put("")
async def put_screen(request: Request, body: ScreenSaveIn) -> ScreenStateOut:
    try:
        screen = ScreenConfig.model_validate(body.screen)
        proactive = ProactiveConfig.model_validate(body.proactive)
        env = EnvironmentConfig.model_validate(body.environment)
        privacy = PrivacyConfig.model_validate(body.privacy)
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None
    app = request.app
    async with app.state.save_lock:
        await asyncio.to_thread(save_sections, app.state.config_path, {"screen": screen, "proactive": proactive, "environment": env})
        await asyncio.to_thread(save_yaml_model, app.state.privacy.path, privacy)
    log.info("感知设置已保存（屏幕感知：%s / %s，主动对话：%s / %s）", "开" if screen.enabled else "关", screen.mode, "开" if proactive.enabled else "关", proactive.mode)
    return _state(request)


@router.post("/look")
async def look_now(request: Request, body: LookIn) -> LookOut:
    """Takes one look after a delay, ignoring cooldowns (but never the privacy blacklist)."""
    app = request.app
    cfg = load_backend_config(app.state.config_path)
    if not cfg.screen.enabled:
        return LookOut(scheduled=False, error="屏幕感知已关闭，先打开开关并保存")
    if not cfg.llm.roles.vision:
        return LookOut(scheduled=False, error="还没有指定识图用的 LLM（在「识图」页设置）")
    if not app.state.pet_hub.clients:
        return LookOut(scheduled=False, error="桌宠未连接")
    if app.state.screen_commenter.inflight or getattr(app.state, "look_pending", False):
        return LookOut(scheduled=False, error="上一次还没看完")

    async def later() -> None:
        try:
            await asyncio.sleep(body.delay_seconds)
            app.state.bus.publish(make_event("screen.changed", {"app": "", "title": "", "fullscreen": False, "reason": "manual"}))
        finally:
            app.state.look_pending = False

    app.state.look_pending = True

    task = asyncio.get_running_loop().create_task(later())
    app.state.background = getattr(app.state, "background", set())
    app.state.background.add(task)
    task.add_done_callback(app.state.background.discard)
    return LookOut(scheduled=True)


@router.post("/weather")
async def weather_now(request: Request) -> WeatherOut:
    env = request.app.state.environment
    text = await env.weather(force=True)
    if not text:
        cfg = load_backend_config(request.app.state.config_path).environment
        reason = "未填写城市" if not cfg.city else "天气关闭" if not cfg.weather else "查询失败或找不到这个城市（详见日志）"
        return WeatherOut(text="", error=reason)
    return WeatherOut(text=text)


privacy_router = APIRouter(prefix="/api/privacy", dependencies=[Depends(require_token)])


@privacy_router.post("/off")
async def all_off(request: Request) -> AllOffOut:
    """CLAUDE.md §4.3 one-click off: microphone + screen perception + camera."""
    app = request.app
    async with app.state.save_lock:
        cfg = load_backend_config(app.state.config_path)
        asr = cfg.asr.model_copy(update={"listen": False})
        screen = cfg.screen.model_copy(update={"enabled": False})
        proactive = cfg.proactive.model_copy(update={"enabled": False})
        camera = cfg.camera.model_copy(update={"enabled": False})
        await asyncio.to_thread(
            save_sections, app.state.config_path, {"asr": asr, "screen": screen, "proactive": proactive, "camera": camera}
        )
    app.state.voice.reload()
    await app.state.pet_hub.push_mic()
    await app.state.camera_watcher.apply_now()
    log.info("一键关闭全部感知：麦克风、屏幕感知、主动对话、摄像头已关闭")
    return AllOffOut(microphone=False, screen=False, proactive=False, camera=False)
