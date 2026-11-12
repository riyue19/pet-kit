"""Console API for the camera card on the 感知 page (M9): settings, live status, test look."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from api.auth import require_token
from core.camera_config import CameraConfig
from core.events import make_event
from core.llm_config import format_validation_error, load_backend_config, save_sections
from core.voice import BACKEND_DIR
from perception.camera import MODEL_FILE

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/camera", dependencies=[Depends(require_token)])


class CameraStatusOut(BaseModel):
    camera: CameraConfig
    # The device is open right now (its LED is lit).
    open: bool
    # True / False once watching; null = not watching (off, paused for a meeting, or can't open).
    present: bool | None
    error: str | None
    model_ready: bool
    vision_profile: str | None
    last_greeting_at: float | None
    last_greeting_text: str | None


class LookOut(BaseModel):
    scheduled: bool
    error: str | None = None


def _state(request: Request) -> CameraStatusOut:
    app = request.app
    cfg = load_backend_config(app.state.config_path)
    w = app.state.camera_watcher
    last = app.state.camera_reactor.last
    return CameraStatusOut(
        camera=cfg.camera,
        open=w.open,
        present=w.present,
        error=w.error,
        model_ready=(BACKEND_DIR / cfg.camera.model_dir / MODEL_FILE).exists(),
        vision_profile=cfg.llm.roles.vision,
        last_greeting_at=last.at if last else None,
        last_greeting_text=last.text if last else None,
    )


@router.get("")
def get_camera(request: Request) -> CameraStatusOut:
    return _state(request)


@router.put("")
async def put_camera(request: Request, body: dict) -> CameraStatusOut:
    try:
        camera = CameraConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None
    app = request.app
    async with app.state.save_lock:
        await asyncio.to_thread(save_sections, app.state.config_path, {"camera": camera})
    await app.state.camera_watcher.apply_now()
    log.info("摄像头设置已保存（%s，开机检测间隔 %g 秒）", "开" if camera.enabled else "关", camera.check_seconds)
    return _state(request)


@router.post("/look")
async def look_now(request: Request) -> LookOut:
    """Pretend the user just came back: one frame to the vision model (do-not-disturb and busy still apply)."""
    app = request.app
    cfg = load_backend_config(app.state.config_path)
    if not cfg.camera.enabled:
        return LookOut(scheduled=False, error="摄像头已关闭，先打开开关并保存")
    if not app.state.camera_watcher.open:
        return LookOut(scheduled=False, error=app.state.camera_watcher.error or "摄像头还没打开，稍等几秒再试")
    if not cfg.llm.roles.vision:
        return LookOut(scheduled=False, error="还没有指定识图用的 LLM（在「识图」页设置）")
    if not app.state.pet_hub.clients:
        return LookOut(scheduled=False, error="桌宠未连接")
    if app.state.camera_reactor.inflight:
        return LookOut(scheduled=False, error="上一次还没看完")
    app.state.bus.publish(make_event("camera.presence", {"state": "arrived", "away_seconds": 0, "reason": "manual"}))
    return LookOut(scheduled=True)
