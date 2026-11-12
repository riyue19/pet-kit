"""Camera presence detection (M9). Tier 1 and 2 are local: a face detector decides present / away;
frames live in memory for the moment of analysis and are never written anywhere or put in events/logs.
The vision model only ever sees a frame when core/camera_react.py asks for one (after the proactive gate).
"""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from core.camera_config import CameraConfig
from core.events import Event, make_event
from core.screen_config import PrivacyConfig

log = logging.getLogger(__name__)

MODEL_FILE = "face_detection_yunet.onnx"
SCORE_THRESHOLD = 0.7
ARRIVE_HITS = 2  # consecutive checks with a face before "arrived"
OPEN_RETRY_SECONDS = 30
MAX_BAD_READS = 5  # consecutive failed reads / detections before the device is released and retried later
WARMUP_FRAMES = 3  # DirectShow's first frames are often black while auto-exposure settles
JPEG_MAX_SIDE = 640


@dataclass(frozen=True)
class Transition:
    state: Literal["arrived", "left"]
    away_seconds: float = 0.0


class PresenceTracker:
    """Debounced present/away state machine. Starts as "away" (silently): the first face after start-up
    counts as an arrival, with away_seconds measured from start-up."""

    def __init__(self, now: float) -> None:
        self.present = False
        self._hits = 0
        self._last_face = now
        self._left_at = now

    def update(self, now: float, face: bool, away_after: float) -> Transition | None:
        if face:
            self._last_face = now
            self._hits += 1
            if not self.present and self._hits >= ARRIVE_HITS:
                self.present = True
                return Transition("arrived", away_seconds=max(0.0, now - self._left_at))
            return None
        self._hits = 0
        if self.present and now - self._last_face >= away_after:
            self.present = False
            self._left_at = self._last_face
            return Transition("left")
        return None


class Camera:
    """cv2.VideoCapture + YuNet. All calls block: run them in a thread."""

    def __init__(self, model_path: Path, index: int) -> None:
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            self._cap.release()
            raise RuntimeError(f"打不开摄像头 {index}（被占用或没有这个设备）")
        for _ in range(WARMUP_FRAMES):
            self._cap.read()
        self._model_path = model_path
        self._detector = None
        self._size: tuple[int, int] | None = None

    def read(self) -> np.ndarray | None:
        # Drop one buffered frame so we analyse "now", not the frame from the previous check.
        self._cap.grab()
        ok, frame = self._cap.read()
        return frame if ok and frame is not None else None

    def has_face(self, frame: np.ndarray) -> bool:
        cv2 = self._cv2
        h, w = frame.shape[:2]
        if self._detector is None:
            self._detector = cv2.FaceDetectorYN.create(str(self._model_path), "", (w, h), SCORE_THRESHOLD)
        if self._size != (w, h):
            self._detector.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self._detector.detect(frame)
        return faces is not None and len(faces) > 0

    def jpeg(self, frame: np.ndarray) -> bytes | None:
        cv2 = self._cv2
        h, w = frame.shape[:2]
        scale = JPEG_MAX_SIDE / max(h, w)
        if scale < 1:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def close(self) -> None:
        self._cap.release()


class CameraWatcher:
    def __init__(
        self,
        *,
        config: Callable[[], CameraConfig],
        privacy: Callable[[], PrivacyConfig],
        publish: Callable[[Event], None],
        base_dir: Path,
        windows: Callable[[], list[tuple[str, str]]],
        on_state: Callable[[bool, bool | None, bool], None] | None = None,
        has_viewer: Callable[[], bool] = lambda: True,
        opener: Callable[[Path, int], Camera] = Camera,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._privacy = privacy
        self._publish = publish
        self._base_dir = base_dir
        self._windows = windows
        self._on_state = on_state
        self._has_viewer = has_viewer
        self._stopped = False
        self._bad_reads = 0
        self._wake = asyncio.Event()
        self._opener = opener
        self._clock = clock
        self._cam: Camera | None = None
        self._cam_index = -1
        self._tracker: PresenceTracker | None = None
        self._retry_at = 0.0
        self._lock = threading.Lock()  # one thread at a time touches the device
        self.error: str | None = None
        self.open = False  # camera device currently held
        self._notified: tuple[bool, bool | None, bool] = (False, None, False)
        self.present: bool | None = None  # None = not watching

    # --- helpers (blocking) ---

    def _set_open(self, value: bool) -> None:
        self.open = value

    def _notify(self) -> None:
        # Called on the event loop (not from the worker threads), so callbacks may schedule loop work.
        now = (self.open, self.present if self.open else None, self._config().enabled)
        if now != self._notified:
            self._notified = now
            if self._on_state:
                self._on_state(*now)

    def _close(self) -> None:
        with self._lock:
            if self._cam is not None:
                self._cam.close()
                self._cam = None
        self._tracker = None
        self.present = None
        self._set_open(False)

    def _quiet_apps(self) -> bool:
        # Meetings / recording / streaming apps want the camera themselves, and the user doesn't want a pet in there.
        privacy = self._privacy()
        try:
            return any(privacy.quiet(p, t) for p, t in self._windows())
        except Exception:  # noqa: BLE001
            log.exception("枚举窗口失败，释放摄像头")
            return True

    def _check(self, cfg: CameraConfig) -> Transition | None:
        """Open if needed, analyse one frame. Blocking."""
        now = self._clock()
        with self._lock:
            # Re-checked under the lock: a switch-off / shutdown that landed meanwhile must not (re)open the device.
            if self._stopped or not self._config().enabled:
                return None
            if self._cam is not None and self._cam_index != cfg.device_index:
                self._cam.close()
                self._cam = None
                self._tracker = None
            if self._cam is None:
                if now < self._retry_at:
                    return None
                model = self._base_dir / cfg.model_dir / MODEL_FILE
                if not model.exists():
                    self.error = "缺少人脸检测模型，请运行 uv run python scripts/fetch_models.py"
                    self._retry_at = now + OPEN_RETRY_SECONDS
                    return None
                try:
                    self._cam = self._opener(model, cfg.device_index)
                except Exception as e:  # noqa: BLE001
                    self.error = str(e)
                    self._retry_at = now + OPEN_RETRY_SECONDS
                    log.warning("摄像头打开失败，%d 秒后重试：%s", OPEN_RETRY_SECONDS, e)
                    return None
                self._cam_index = cfg.device_index
                self._tracker = PresenceTracker(now)
                self._bad_reads = 0
                self.error = None
                self._set_open(True)  # the LED is on from here on, whatever the first frames do
                log.info("摄像头已打开（设备 %d）", cfg.device_index)
            try:
                frame = self._cam.read()
                face = self._cam.has_face(frame) if frame is not None else None
            except Exception as e:  # noqa: BLE001
                log.warning("人脸检测出错：%s", e)
                frame, face = None, None
                self.error = f"人脸检测出错：{e}"
            del frame
            if face is None:
                self._bad_reads += 1
                self.error = self.error or "读不到摄像头画面"
                if self._bad_reads >= MAX_BAD_READS:
                    log.warning("摄像头连续 %d 次读不到有效画面，释放后稍后重试", self._bad_reads)
                    self._cam.close()
                    self._cam = None
                    self._tracker = None
                    self.present = None
                    self._set_open(False)
                    self._retry_at = now + OPEN_RETRY_SECONDS
                return None
            self._bad_reads = 0
            self.error = None
        assert self._tracker is not None
        t = self._tracker.update(self._clock(), face, cfg.away_after_seconds)
        self.present = self._tracker.present
        return t

    def status_line(self) -> str:
        """One prompt line telling the chat model what the camera can and cannot do right now, so that what the
        pet says about "seeing" the user matches the real detection result."""
        if not self._config().enabled:
            return "【摄像头】关闭中，你看不到主人；被问到时如实说没开摄像头，不要假装看得到。"
        if not self.open:
            why = f"（{self.error}）" if self.error else "（暂停中，比如在开会，或还没打开）"
            return f"【摄像头】已启用但此刻没在工作{why}，你现在看不到主人，不要假装看得到。"
        head = "【摄像头】开着，只在本机做人脸检测（判断主人在不在），不是一直在看画面内容；"
        if self.present:
            return head + "此刻检测到了主人的脸。你并没有看到画面细节；主人让你看看他时，在 tool_calls 里调用 look_camera，不要凭空描述。"
        return head + "此刻没有检测到人脸（可能主人不在画面里、侧着脸/被遮挡或光线太暗）；如实说「没检测到」，别编造；主人坚持让你看时可调用 look_camera。"

    def snapshot_jpeg(self) -> bytes | None:
        """One fresh frame as JPEG (<=640px, in memory) or None if the camera isn't open. Blocking."""
        with self._lock:
            if self._cam is None or self._stopped or not self._config().enabled:
                return None
            frame = self._cam.read()
            if frame is None:
                return None
            return self._cam.jpeg(frame)

    # --- loop ---

    async def run(self) -> None:
        try:
            while True:
                cfg = self._config()
                try:
                    if cfg.enabled and not self._has_viewer():
                        # No pet window connected = nowhere to show the "camera on" indicator: don't hold the camera.
                        await asyncio.to_thread(self._close)
                    elif cfg.enabled and not await asyncio.to_thread(self._quiet_apps):
                        t = await asyncio.to_thread(self._check, cfg)
                        if t is not None:
                            payload: dict = {"state": t.state}
                            if t.state == "arrived":
                                payload["away_seconds"] = round(t.away_seconds)
                            self._publish(make_event("camera.presence", payload))
                    else:
                        if self.open:
                            log.info("摄像头已关闭（%s）", "开关已关" if not cfg.enabled else "会议/录屏中，让出摄像头")
                        await asyncio.to_thread(self._close)
                        self.error = None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("摄像头感知出错")
                self._notify()
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), cfg.check_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._stopped = True
            self._close()
            self._notify()

    async def apply_now(self) -> None:
        """Call after the config changed: releases the camera at once if it was switched off (don't wait for the
        next check), and wakes the loop."""
        if not self._config().enabled:
            if self.open:
                log.info("摄像头已关闭（开关已关）")
            await asyncio.to_thread(self._close)
        self._notify()
        self._wake.set()
