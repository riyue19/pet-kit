"""Speaks agent output: text -> streaming TTS -> PCM frames to the pet window.

Protocol to the pet (see api/pet_ws.py): {"type":"audio_start","id","sample_rate"} then binary PCM
frames (16-bit LE mono) then {"type":"audio_end","id","error"?}. Only one utterance plays at a time.

Agent outputs go through say(): in queue mode (barge-in off) each reply is shown and spoken only after the
previous one has finished playing in the pet window; otherwise a new reply cuts the current one.
"""

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.logs import current_event
from core.voice_config import TtsConfig
from providers.base import ProviderError, TtsProvider

log = logging.getLogger(__name__)

# Stage directions such as （歪头） / (laughs) / *wink* / 【旁白】 are shown but not spoken.
_STAGE = re.compile(r"（[^）]*）|\([^)]*\)|\*[^*]*\*|【[^】]*】|\[[^\]]*\]")
_SPEAKABLE = re.compile(r"[\w一-鿿]")
MAX_TTS_CHARS = 500
# Markers of "this tone is not supported" in a provider error (MiniMax 2013, CosyVoice InvalidParameter).
TONE_REJECTED = ("2013", "InvalidParameter")
# Replies waiting their turn in queue mode; beyond this they are shown without voice.
MAX_QUEUED = 3
# Extra wait for the pet to finish playing after the stream's audio length has elapsed.
PLAYBACK_GRACE_S = 2.0


def speakable(text: str) -> str:
    """Text to send to TTS, or "" if nothing is worth speaking (e.g. a local error notice in brackets)."""
    out = _STAGE.sub("", text).strip()
    return out[:MAX_TTS_CHARS] if _SPEAKABLE.search(out) else ""


@dataclass
class SpeakResult:
    ok: bool
    first_audio_ms: int | None = None
    error: str | None = None
    audio_seconds: float = 0.0


SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]
TtsFactory = Callable[[TtsConfig, str | None], TtsProvider]
Announce = Callable[[], Awaitable[None]]
WaitIdle = Callable[[float], Awaitable[None]]


async def _no_wait(_seconds: float) -> None:
    return None


class Speaker:
    def __init__(
        self,
        *,
        config: Callable[[], TtsConfig],
        api_key: Callable[[str], str | None],
        factory: TtsFactory,
        send_json: SendJson,
        send_bytes: SendBytes,
        queue_mode: Callable[[], bool] = lambda: False,
        wait_idle: WaitIdle = _no_wait,
    ) -> None:
        self._config = config
        self._unsupported_emotions: set[tuple[str, str]] = set()
        self._api_key = api_key
        self._factory = factory
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._queue_mode = queue_mode
        # Waits (at most the given seconds) until the pet window reports playback finished.
        self._wait_idle = wait_idle
        self._queue: deque[tuple[str, str | None, Announce, str | None]] = deque()
        self._worker: asyncio.Task[None] | None = None
        self._task: asyncio.Task[SpeakResult] | None = None
        self._seq = 0
        # Serializes speak/interrupt: without it two concurrent speak() calls could both start a stream.
        self._lock = asyncio.Lock()
        # Text currently being spoken, for the echo guard.
        self.current_text = ""

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def busy(self) -> bool:
        return self.active or bool(self._queue) or (self._worker is not None and not self._worker.done())

    async def say(self, text: str, expression: str | None, announce: Announce) -> None:
        """Shows (announce) and speaks one agent output."""
        cfg = self._config()
        if not cfg.enabled or not speakable(text):
            await announce()  # nothing to speak (e.g. a bracketed notice): show it right away
            return
        if not self._queue_mode():
            await announce()
            await self.speak(text, expression)
            return
        if len(self._queue) >= MAX_QUEUED:
            log.warning("朗读队列已满，这条回复只显示不朗读")
            await announce()
            return
        self._queue.append((text, expression, announce, current_event.get()))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._queue:
            text, expression, announce, event_id = self._queue.popleft()
            current_event.set(event_id)
            try:
                await announce()
                task = await self.speak(text, expression)
                if task is None:
                    continue
                (result,) = await asyncio.gather(task, return_exceptions=True)
                if isinstance(result, SpeakResult) and result.audio_seconds > 0:
                    await self._wait_idle(result.audio_seconds + PLAYBACK_GRACE_S)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("朗读队列处理失败")

    async def speak(
        self, text: str, expression: str | None = None, *, config: TtsConfig | None = None
    ) -> asyncio.Task[SpeakResult] | None:
        """Starts speaking (cancelling any current utterance). Returns None if TTS is off or nothing to say.

        `config` overrides the saved settings (console test with unsaved form values).
        """
        cfg = config or self._config()
        spoken = speakable(text)
        if not cfg.enabled or not spoken:
            return None
        async with self._lock:
            await self._stop_current()
            self._seq += 1
            self.current_text = spoken
            self._task = asyncio.create_task(self._run(self._seq, cfg, spoken, cfg.emotion_for(expression)))
            return self._task

    async def interrupt(self) -> None:
        """Stops the current utterance and drops queued ones."""
        self._queue.clear()
        worker, self._worker = self._worker, None
        if worker and not worker.done() and worker is not asyncio.current_task():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        async with self._lock:
            await self._stop_current()

    async def _stop_current(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self, sid: int, cfg: TtsConfig, text: str, emotion: str | None) -> SpeakResult:
        key = await asyncio.to_thread(self._api_key, cfg.provider)
        start = time.perf_counter()
        first: int | None = None
        error: str | None = None
        provider: TtsProvider | None = None
        started = False
        sent_bytes = 0
        rate = 1
        try:
            provider = self._factory(cfg, key)
            await self._send_json({"type": "audio_start", "id": sid, "sample_rate": provider.sample_rate})
            started = True
            rate = provider.sample_rate
            language = cfg.speech_language if cfg.speech_language != "same" else None
            model = cfg.tone_key
            if emotion and (model, emotion) in self._unsupported_emotions:
                emotion = None
            while True:
                try:
                    async for chunk in provider.stream(text, emotion=emotion, language=language):
                        if first is None:
                            first = round((time.perf_counter() - start) * 1000)
                            log.info("语音合成首包 %d ms（%d 字，语气 %s）", first, len(text), emotion or "默认")
                        await self._send_bytes(chunk)
                        sent_bytes += len(chunk)
                    break
                except ProviderError as e:
                    # Some models reject some tones (e.g. speech-2.8 has no whisper) with 2013 before any audio.
                    # Speak without the tone rather than not at all, and don't try that tone again with this model.
                    # (CosyVoice: InvalidParameter for a voice without Instruct.)
                    if emotion is None or first is not None or not any(m in str(e) for m in TONE_REJECTED):
                        raise
                    log.warning("模型 %s 不支持语气 %s，改用默认语气（之后不再尝试该语气）", model, emotion)
                    self._unsupported_emotions.add((model, emotion))
                    emotion = None
        except asyncio.CancelledError:
            error = "interrupted"
            raise
        except ProviderError as e:
            error = str(e)
            log.warning("语音合成失败：%s", error)
        except Exception:
            error = "语音合成失败"
            log.exception("语音合成失败")
        finally:
            if provider is not None:
                await asyncio.shield(provider.aclose())
            if started:
                msg: dict = {"type": "audio_end", "id": sid}
                if error:
                    msg["error"] = error
                try:
                    await asyncio.shield(self._send_json(msg))
                except Exception:  # noqa: BLE001 - client gone
                    pass
        return SpeakResult(
            ok=error is None, first_audio_ms=first, error=error, audio_seconds=sent_bytes / 2 / rate
        )
