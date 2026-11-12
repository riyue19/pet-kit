"""WebSocket /ws/pet between the Electron main process and the backend.

Browsers can't set headers on a WebSocket, so the first frame must be
{"type": "hello", "token": ..., "labels": {...}}; anything else closes with 4401.

client -> server (text):  hello | {"type": "labels", "labels": {...}} | {"type": "event", "event": {"type", "payload"}}
                          | {"type": "playback", "playing": bool} | {"type": "stop"} (stop speaking, drop queue)
                          | {"type": "confirm_response", "id", "allow": bool} (answer to confirm_request)
                          | {"type": "fishing.hook", "seq", "clicked": bool} (answer to a bite window)
                          | {"type": "fishing.cmd", "cmd": "start" | "stop"} (the pet menu's fishing switch)
                          | {"type": "ptt", "id"} (the push_to_talk hotkey `id` from hotkeys.yaml went down; the
                            backend opens the mic, records until that combo is released, then recognizes it whole)
client -> server (binary): microphone audio, 16 kHz mono 16-bit LE PCM (only while the server says mic.listen;
                           mic.listen is also true while push-to-talk is held)
server -> client (text):  {"type": "ready"} | {"type": "output", "event_id", "output"} | {"type": "status", "thinking"}
                          | {"type": "error", "message"} | {"type": "mic", "listen"} | {"type": "camera", "on", "present", "enabled"} | {"type": "hearing", "active"}
                          | {"type": "heard", "text"} | {"type": "interrupt"}
                          | {"type": "confirm_request", "id", "title", "detail"} (a skill needs the user's OK)
                          | {"type": "game_invite", "game", "name", "pet"} | {"type": "code", ...} | {"type": "set_css", ...}
                          | {"type": "fishing.state", "enabled", "lite", "placed", "scale", "cast_ts", ...} (HUD on/off, shown, size)
                          | {"type": "fishing.anim", "seq", "phase", "outcome", "steps": [{"state", "dur", "window"?, "card"?}], "lite"}
                          | {"type": "audio_start", "id", "sample_rate"} | {"type": "audio_end", "id", "error"?}
server -> client (binary): TTS audio for the current audio_start, 16-bit LE mono PCM
"""

import asyncio
import json
import logging
import time
import re
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from action.tts import Speaker
from core.agent import AgentOutput, Labels
from core.event_bus import EventBus
from core.events import MAX_TEXT_CHARS, Event, client_event, make_event
from core.games.fishing.game import MENU_COMMANDS
from core.hotkeys import load_hotkeys
from core.llm_config import format_validation_error
from core.voice import VoiceState, is_echo
from perception.asr import MAX_HOLD_SECONDS, VoiceListener, drop_reason
from perception.keystate import Combo, combo_down, combo_keys
from providers.sherpa_asr import SileroVad

HELLO_TIMEOUT_S = 5
CLOSE_UNAUTHORIZED = 4401
MAX_LABELS = 64
LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# Sticker note names are free text (shown to the model, echoed back in its JSON reply).
MAX_STICKERS = 60
STICKER_NAME_MAX = 20
STICKER_NAME_FORBIDDEN = re.compile(r"""[\x00-\x1f\x7f"'\\{}\[\]]""")
# Push-to-talk: how often the held combo is checked, and how long after release audio still in flight is accepted.
PTT_POLL_S = 0.03
PTT_TAIL_S = 0.2
MAX_HEARD_LOG = 50
# receive_json raises KeyError/TypeError on binary frames.
BAD_FRAME = (ValueError, KeyError, TypeError)

log = logging.getLogger(__name__)
router = APIRouter()


def parse_labels(raw: Any) -> Labels:
    def names(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [x for x in v if isinstance(x, str) and LABEL_RE.fullmatch(x)][:MAX_LABELS]

    def stickers(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for x in v:
            x = x.strip() if isinstance(x, str) else ""
            if x and len(x) <= STICKER_NAME_MAX and not STICKER_NAME_FORBIDDEN.search(x) and x not in out:
                out.append(x)
        return out[:MAX_STICKERS]

    raw = raw if isinstance(raw, dict) else {}
    return Labels(
        expressions=names(raw.get("expressions")), motions=names(raw.get("motions")), stickers=stickers(raw.get("stickers"))
    )


class PetHub:
    def __init__(self, voice: VoiceState) -> None:
        self.clients: set[WebSocket] = set()
        self.labels = Labels()
        self.voice = voice
        self.speaker: Speaker | None = None
        # Wall-clock time of the last "stop": replies to events published before it are dropped.
        self.stopped_at = 0.0
        # VAD hears the user talking right now.
        self.hearing = False
        self._tasks: set[asyncio.Task] = set()
        self.camera_present: bool | None = None
        self.camera_enabled = False  # camera.enabled (the switch), which can differ from "open" while paused
        self.camera_on = False  # the camera device is open (shown as an indicator on the pet)
        # Pending skill confirmations: id -> future resolved by the pet window's answer.
        self._confirms: dict[str, asyncio.Future[bool]] = {}
        # Connections currently holding push-to-talk: the mic is open even when asr.listen is off.
        self.ptt_holds = 0
        # config/hotkeys.yaml (set by the app wiring); push-to-talk only works for a combo declared there.
        self.hotkeys_path: Path | None = None
        # Is this declared combo still held? Injectable for tests.
        self.key_down: Callable[[Combo], bool] = combo_down
        # What the recognizer heard lately and what happened to it (console 语音 page). Memory only.
        self.heard_log: deque[dict[str, Any]] = deque(maxlen=MAX_HEARD_LOG)
        # core.games.fishing.FishingGame (set by the app wiring).
        self.fishing: Any = None

    @property
    def ptt(self) -> bool:
        return self.ptt_holds > 0

    @property
    def mic_open(self) -> bool:
        return self.voice.listening or self.ptt

    def note_heard(self, text: str, seconds: float, manual: bool, reason: str | None) -> None:
        self.heard_log.append(
            {
                "ts": time.time(),
                "text": text[:MAX_TEXT_CHARS],
                "seconds": seconds,
                "manual": manual,
                "sent": reason is None,
                "reason": reason,
            }
        )

    async def confirm(self, title: str, detail: str, timeout: float) -> bool:
        """Asks the user on the pet window. No window, no answer in time, or any doubt means no."""
        if not self.clients:
            return False
        cid = secrets.token_hex(8)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._confirms[cid] = future
        try:
            await self.broadcast({"type": "confirm_request", "id": cid, "title": title, "detail": detail})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._confirms.pop(cid, None)

    def deny_pending(self) -> None:
        for future in self._confirms.values():
            if not future.done():
                future.set_result(False)

    def resolve_confirm(self, cid: str, allow: bool) -> None:
        future = self._confirms.get(cid)
        if future is not None and not future.done():
            future.set_result(allow)

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception as e:  # noqa: BLE001 - a dead socket is removed by its own receive loop
                log.debug("推送失败，移除连接：%s", type(e).__name__)
                self.clients.discard(ws)

    async def broadcast_bytes(self, data: bytes) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_bytes(data)
            except Exception as e:  # noqa: BLE001
                log.debug("推送失败，移除连接：%s", type(e).__name__)
                self.clients.discard(ws)

    async def emit(self, output: AgentOutput, event: Event) -> None:
        async def announce() -> None:
            await self.broadcast({"type": "output", "event_id": event.id, "output": output.model_dump()})

        # Without a pet window nobody would hear it: skip the paid synthesis (e.g. console injection).
        if self.speaker and self.clients:
            await self.speaker.say(output.speech or output.text, output.expression, announce)
        else:
            await announce()

    async def set_thinking(self, thinking: bool) -> None:
        await self.broadcast({"type": "status", "thinking": thinking})

    def set_camera(self, on: bool, present: bool | None = None, enabled: bool = False) -> None:
        """Sync entry for the camera watcher (runs on the event loop). `present`: a face is currently seen."""
        self.camera_on, self.camera_present, self.camera_enabled = on, present, enabled
        task = asyncio.get_running_loop().create_task(self.broadcast({"type": "camera", "on": on, "present": present, "enabled": enabled}))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def push_mic(self) -> None:
        await self.broadcast({"type": "mic", "listen": self.mic_open})

    async def barge_in(self) -> None:
        """The user started talking over the pet; only cuts it off when barge-in is enabled."""
        if self.voice.asr.barge_in:
            await self.interrupt()

    async def interrupt(self) -> None:
        if self.speaker and (self.speaker.busy or self.voice.playing):
            await self.speaker.interrupt()
            await self.broadcast({"type": "interrupt"})


def _make_listener(hub: PetHub, bus: EventBus) -> VoiceListener:
    voice = hub.voice

    async def on_speech(active: bool) -> None:
        hub.hearing = active  # the pet doesn't start a conversation over the user
        await hub.broadcast({"type": "hearing", "active": active})

    async def on_noise(text: str, duration: float, manual: bool) -> None:
        hub.note_heard(text, duration, manual, "没有识别出文字")

    async def on_utterance(text: str, duration: float, manual: bool) -> None:
        asr = voice.asr
        if manual:
            # Held the push-to-talk key: the user meant to speak, so the noise filters don't apply.
            reason = None
        elif not voice.listening:  # listening was switched off while this segment was being recognized
            reason = "麦克风已关闭"
        else:
            reason = drop_reason(
                text,
                asr.min_chars,
                duration,
                asr.short_min_seconds,
                short_chars=asr.short_chars,
                drop_fillers=asr.drop_fillers,
                ignore_phrases=asr.ignore_phrases,
            )
            spoken = hub.speaker.current_text if hub.speaker else ""
            if reason is None and voice.echo_window() and is_echo(text, spoken):
                reason = "是桌宠自己的声音（回声）"
        hub.note_heard(text, duration, manual, reason)
        if reason is not None:
            log.info("忽略语音（%s）：%s（%.2f 秒）", reason, text, duration)
            return
        text = text[:MAX_TEXT_CHARS]
        await hub.broadcast({"type": "heard", "text": text})
        bus.publish(make_event("voice.utterance", {"text": text, "duration": duration}))

    return VoiceListener(
        vad=SileroVad(
            voice.model_dir,
            threshold=voice.asr.vad_threshold,
            min_silence_seconds=voice.asr.min_silence_seconds,
            min_speech_seconds=voice.asr.min_speech_seconds,
            max_speech_seconds=voice.asr.max_speech_seconds,
        ),
        engine=voice.engine,
        barge_in_seconds=lambda: voice.asr.barge_in_seconds,
        playing=lambda: voice.playing or bool(hub.speaker and hub.speaker.active),
        on_speech=on_speech,
        on_barge_in=hub.barge_in,
        on_utterance=on_utterance,
        on_noise=on_noise,
    )


def _vad_key(voice: VoiceState) -> tuple:
    a = voice.asr
    return (str(voice.model_dir), a.vad_threshold, a.min_silence_seconds, a.min_speech_seconds, a.max_speech_seconds)


@dataclass
class _Conn:
    """Per-connection microphone state."""

    hub: PetHub
    bus: EventBus
    listener: VoiceListener | None = None
    listener_failed: bool = False
    vad_key: tuple | None = None
    ptt_task: asyncio.Task | None = None
    ptt_held: bool = False

    def _set_ptt(self, held: bool) -> None:
        # Counted per connection, so one connection ending its hold can't close another one's microphone.
        if held != self.ptt_held:
            self.ptt_held = held
            self.hub.ptt_holds += 1 if held else -1

    async def ensure_listener(self) -> VoiceListener | None:
        key = _vad_key(self.hub.voice)
        if self.listener is not None and key != self.vad_key and not self.listener.busy:
            # VAD settings were changed in the console: rebuild so they apply right away.
            await self.listener.aclose()
            self.listener = None
        if self.listener is None and (not self.listener_failed or key != self.vad_key):
            self.vad_key = key
            try:
                self.listener = _make_listener(self.hub, self.bus)
                self.listener_failed = False
            except Exception:
                self.listener_failed = True
                log.exception("语音监听初始化失败（检查 VAD 模型文件，可重新运行 scripts/fetch_models.py）")
        return self.listener

    async def feed(self, data: bytes) -> None:
        if not self.hub.mic_open:
            # Audio is only accepted while the mic is meant to be open. Forget any half-heard sentence so it
            # can't merge with speech after re-enabling (a segment already being recognized still finishes).
            if self.listener is not None:
                await self.listener.reset()
            self.listener_failed = False
            return
        listener = await self.ensure_listener()
        if listener is not None:
            await listener.feed(data)

    async def start_ptt(self, hotkey_id: str) -> None:
        """Push-to-talk went down. Only a combo declared as push_to_talk in hotkeys.yaml is ever watched."""
        hub = self.hub
        if self.ptt_task is not None and not self.ptt_task.done():
            return  # still held (key auto-repeat)
        hk = next((h for h in load_hotkeys(hub.hotkeys_path) if h.id == hotkey_id), None) if hub.hotkeys_path else None
        if hk is None or not hk.enabled or hk.action != "builtin" or hk.builtin != "push_to_talk":
            log.info("未知或已停用的按住说话快捷键：%s", hotkey_id)
            return
        combo = combo_keys(hk.keys)
        if combo is None:
            log.warning("按住说话：无法识别组合键 %s", hk.keys)
            return
        if not hub.voice.models_ready:
            await hub.broadcast({"type": "error", "message": "语音识别模型未下载"})
            return
        if not hub.key_down(combo):
            return  # already released (a quick tap): nothing to record
        hub.voice.warm_up(force=True)
        listener = await self.ensure_listener()
        if listener is None:
            return
        await hub.interrupt()  # the user is about to speak: the pet stops talking
        await listener.hold_begin()
        self._set_ptt(True)
        await hub.push_mic()
        self.ptt_task = asyncio.create_task(self._hold(listener, combo))

    async def _hold(self, listener: VoiceListener, combo: Combo) -> None:
        hub = self.hub
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            while hub.key_down(combo) and loop.time() - start < MAX_HOLD_SECONDS:
                await asyncio.sleep(PTT_POLL_S)
            await asyncio.sleep(PTT_TAIL_S)  # frames recorded just before the release are still on their way
        finally:
            self._set_ptt(False)
            try:
                await listener.hold_end()
            except Exception:
                log.exception("按住说话结束时出错")
            try:
                await hub.push_mic()
            except Exception:
                log.exception("按住说话结束后同步麦克风状态出错")
        # Cut off at the time limit while the key is still down: wait for the release here, so the key's
        # auto-repeat doesn't start another recording.
        while hub.key_down(combo):
            await asyncio.sleep(PTT_POLL_S)

    async def aclose(self) -> None:
        if self.ptt_task is not None and not self.ptt_task.done():
            self.ptt_task.cancel()
            await asyncio.gather(self.ptt_task, return_exceptions=True)
        self._set_ptt(False)
        if self.listener is not None:
            await self.listener.aclose()


@router.websocket("/ws/pet")
async def pet_ws(ws: WebSocket) -> None:
    await ws.accept()
    hub: PetHub = ws.app.state.pet_hub
    bus: EventBus = ws.app.state.bus
    try:
        hello = await asyncio.wait_for(ws.receive_json(), HELLO_TIMEOUT_S)
    except (asyncio.TimeoutError, WebSocketDisconnect, *BAD_FRAME) as e:
        log.warning("桌宠连接握手失败：%s", type(e).__name__)
        await _close(ws, CLOSE_UNAUTHORIZED)
        return
    if not isinstance(hello, dict):
        hello = {}
    token = hello.get("token")
    expected: str = ws.app.state.token
    if (
        hello.get("type") != "hello"
        or not isinstance(token, str)
        or not secrets.compare_digest(token.encode(), expected.encode())
    ):
        log.warning("桌宠连接鉴权失败（令牌不匹配或首条消息不是 hello）")
        await _close(ws, CLOSE_UNAUTHORIZED)
        return

    hub.labels = parse_labels(hello.get("labels"))
    hub.clients.add(ws)
    await ws.send_json({"type": "ready"})
    await ws.send_json({"type": "mic", "listen": hub.mic_open})
    if hub.camera_on or hub.camera_enabled:
        await ws.send_json(
            {"type": "camera", "on": hub.camera_on, "present": hub.camera_present, "enabled": hub.camera_enabled}
        )
    # Off is the pet window's default (it also resets to off when the link drops), so only "on" is sent.
    if hub.fishing is not None and hub.fishing.enabled:
        await ws.send_json(hub.fishing.state_message())
    log.info("桌宠已连接（当前 %d 个连接）", len(hub.clients))
    hub.voice.warm_up()
    conn = _Conn(hub, bus)
    try:
        while True:
            frame = await ws.receive()
            if frame["type"] == "websocket.disconnect":
                break
            data = frame.get("bytes")
            if data is not None:
                await conn.feed(data)
                continue
            await _handle_text(ws, hub, bus, frame.get("text"), conn)
    except WebSocketDisconnect:
        pass
    except RuntimeError as e:
        log.debug("桌宠连接异常关闭：%s", e)
    finally:
        log.info("桌宠已断开")
        hub.clients.discard(ws)
        await conn.aclose()
        if not hub.clients:
            hub.voice.set_playing(False)
            if hub.speaker:  # nobody left to hear it; stop the paid stream
                await hub.speaker.interrupt()


async def _handle_text(ws: WebSocket, hub: PetHub, bus: EventBus, text: str | None, conn: "_Conn") -> None:
    try:
        msg = json.loads(text or "")
    except ValueError:
        log.warning("桌宠发来无法解析的 JSON：%s", (text or "")[:200])
        await ws.send_json({"type": "error", "message": "无法解析的消息"})
        return
    kind = msg.get("type") if isinstance(msg, dict) else None
    if kind == "labels":
        hub.labels = parse_labels(msg.get("labels"))
    elif kind == "playback":
        hub.voice.set_playing(msg.get("playing") is True)
    elif kind == "confirm_response":
        hub.resolve_confirm(str(msg.get("id")), msg.get("allow") is True)
    elif kind == "ptt":
        await conn.start_ptt(str(msg.get("id")))
    elif kind == "fishing.hook":
        seq = msg.get("seq")
        if hub.fishing is not None and isinstance(seq, int) and not isinstance(seq, bool):
            await hub.fishing.hook(seq, msg.get("clicked") is True)
    elif kind == "fishing.cmd":
        # Only the on/off switch: anything that spends or sells needs the console or a typed command.
        if hub.fishing is not None and msg.get("cmd") in MENU_COMMANDS:
            result = await hub.fishing.command(msg["cmd"])
            await hub.broadcast({"type": "output", "event_id": None, "output": AgentOutput(text=result.text).model_dump()})
    elif kind == "stop":
        hub.stopped_at = time.time()
        hub.deny_pending()
        await hub.interrupt()
    elif kind == "event":
        raw = msg.get("event") if isinstance(msg.get("event"), dict) else {}
        try:
            bus.publish(client_event(str(raw.get("type")), raw.get("payload")))
        except ValidationError as e:
            detail = format_validation_error(e)
            log.warning("桌宠事件校验失败：%s", detail)
            await ws.send_json({"type": "error", "message": detail})
        except ValueError as e:
            log.warning("桌宠事件校验失败：%s", e)
            await ws.send_json({"type": "error", "message": str(e)})
    else:
        log.warning("桌宠发来未知消息类型：%s", kind)
        await ws.send_json({"type": "error", "message": f"未知消息类型：{kind}"})


async def _close(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code)
    except Exception:  # noqa: BLE001
        pass
