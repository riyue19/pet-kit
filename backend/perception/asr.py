"""Microphone audio from the pet window -> VAD -> local recognition -> voice.utterance text.

Audio arrives as 16 kHz mono 16-bit PCM WebSocket frames. Everything here runs locally (no network);
the recognizer is loaded once on first use and shared by all connections.
"""

import asyncio
import logging
import re
import threading
from collections.abc import Awaitable, Callable, Collection
from pathlib import Path

import numpy as np

from providers.base import Recognizer, VoiceActivity

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# Utterances with no letters/digits/CJK (e.g. only "。" from a cough) are dropped.
_MEANINGFUL = re.compile(r"[\w一-鿿]")
MAX_QUEUED_SEGMENTS = 4
# Push-to-talk recordings: shorter is an accidental tap, longer is cut off.
MIN_HOLD_SECONDS = 0.3
MAX_HOLD_SECONDS = 60


def meaningful(text: str) -> bool:
    return bool(_MEANINGFUL.search(text))


_PUNCT_SPACE = re.compile(r"[\W_]+")
# Sounds people make without saying anything (and that noise is often recognized as).
FILLERS = set("嗯啊哦呃额唔哈嘿诶欸噢喔呀哎唉呵嘛呢吧哼咦嗷呜")


# Results this short are only kept if the audio lasted at least `short_min_seconds`.
SHORT_CHARS = 5


def drop_reason(
    text: str,
    min_chars: int,
    duration: float | None = None,
    short_min_seconds: float = 0.0,
    *,
    short_chars: int = SHORT_CHARS,
    drop_fillers: bool = True,
    ignore_phrases: Collection[str] = (),
) -> str | None:
    """Why a recognized result should not be answered (shown in the console), or None to keep it.

    One-character results (\"我。\" \"嗯。\") and pure filler (\"嗯嗯\" \"啊啊啊\") are almost always noise, a
    cough or a half-word picked up by the mic. So are very short results from a short blip (\"不是。\" from
    0.39 s, \"这个我。\" from 0.8 s): real sentences in practice last well over a second."""
    core = _PUNCT_SPACE.sub("", text)
    if len(core) < min_chars:
        return f"少于 {min_chars} 个字"
    if drop_fillers and all(ch in FILLERS for ch in core):
        return "只有语气词"
    if core in {_PUNCT_SPACE.sub("", p) for p in ignore_phrases}:
        return "在忽略词表里"
    if duration is not None and len(core) <= short_chars and duration < short_min_seconds:
        return f"{short_chars} 个字以内且录音不到 {short_min_seconds:g} 秒"
    return None


def worth_sending(text: str, min_chars: int, duration: float | None = None, short_min_seconds: float = 0.0) -> bool:
    return drop_reason(text, min_chars, duration, short_min_seconds) is None


class AsrEngine:
    """Lazily loads the recognizer on a worker thread; shared across connections."""

    def __init__(self, factory: Callable[[], Recognizer]) -> None:
        self._factory = factory
        self._rec: Recognizer | None = None
        self._lock = threading.Lock()

    def _get(self) -> Recognizer:
        with self._lock:
            if self._rec is None:
                self._rec = self._factory()
            return self._rec

    async def warm_up(self) -> None:
        await asyncio.to_thread(self._get)

    async def transcribe(self, samples: np.ndarray) -> str:
        def run() -> str:
            rec = self._get()
            with self._lock:  # sherpa-onnx recognizers are not documented as thread-safe
                return rec.transcribe(samples, SAMPLE_RATE)

        return await asyncio.to_thread(run)


class VoiceListener:
    """One per pet connection. feed() is cheap (VAD only); recognition runs on a background task."""

    def __init__(
        self,
        *,
        vad: VoiceActivity,
        engine: AsrEngine,
        barge_in_seconds: Callable[[], float],
        playing: Callable[[], bool],
        on_speech: Callable[[bool], Awaitable[None]],
        on_barge_in: Callable[[], Awaitable[None]],
        on_utterance: Callable[[str, float, bool], Awaitable[None]],
        on_noise: Callable[[str, float, bool], Awaitable[None]] | None = None,
    ) -> None:
        """on_utterance(text, seconds, manual): `manual` = recorded while the push-to-talk key was held.
        on_noise gets segments that produced no words at all."""
        self._vad = vad
        self._engine = engine
        self._barge_in_seconds = barge_in_seconds
        self._playing = playing
        self._on_speech = on_speech
        self._on_barge_in = on_barge_in
        self._on_utterance = on_utterance
        self._on_noise = on_noise
        self._pending = np.zeros(0, dtype=np.float32)
        self._speech_samples = 0
        self._speaking = False
        self._barged = False
        # Push-to-talk: while not None, audio is collected whole instead of going through the VAD.
        self._hold: list[np.ndarray] | None = None
        self._hold_samples = 0
        self._recognizing = False
        self._queue: asyncio.Queue[tuple[np.ndarray, bool]] = asyncio.Queue(MAX_QUEUED_SEGMENTS)
        self._worker = asyncio.create_task(self._recognize_loop())

    @property
    def holding(self) -> bool:
        return self._hold is not None

    @property
    def busy(self) -> bool:
        """Something is still on its way to on_utterance; closing now would lose it."""
        return self._recognizing or not self._queue.empty() or self.holding

    def _forget(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._vad.reset()
        self._speech_samples = 0
        self._barged = False

    async def reset(self) -> None:
        """Forgets any half-heard sentence (the microphone was switched off)."""
        self._forget()
        if self._speaking:
            self._speaking = False
            await self._on_speech(False)

    async def hold_begin(self) -> None:
        """Push-to-talk key went down: record everything until hold_end, no VAD cutting."""
        self._forget()
        self._hold, self._hold_samples = [], 0
        if not self._speaking:
            self._speaking = True
            await self._on_speech(True)

    async def hold_end(self) -> None:
        """Key released: the whole recording goes to recognition as one utterance."""
        chunks, self._hold = self._hold, None
        if self._speaking:
            self._speaking = False
            await self._on_speech(False)
        if not chunks or self._hold_samples < MIN_HOLD_SECONDS * SAMPLE_RATE:
            return
        if self._queue.full():
            log.warning("语音识别积压，丢弃一段语音")
            return
        self._queue.put_nowait((np.concatenate(chunks), True))

    async def feed(self, pcm16: bytes) -> None:
        if len(pcm16) % 2:
            pcm16 = pcm16[:-1]
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        if self._hold is not None:
            if self._hold_samples < MAX_HOLD_SECONDS * SAMPLE_RATE:
                self._hold.append(samples)
                self._hold_samples += len(samples)
            return
        self._pending = np.concatenate([self._pending, samples])
        win = self._vad.window
        while len(self._pending) >= win:
            chunk, self._pending = self._pending[:win], self._pending[win:]
            self._vad.accept(chunk)
            await self._track(win)
        for seg in self._vad.pop_segments():
            if self._queue.full():
                log.warning("语音识别积压，丢弃一段语音")
                continue
            self._queue.put_nowait((seg, False))

    async def _track(self, n: int) -> None:
        if not self._vad.speaking():
            if self._speaking:
                self._speaking = False
                await self._on_speech(False)
            self._speech_samples = 0
            self._barged = False
            return
        if not self._speaking:
            self._speaking = True
            await self._on_speech(True)
        self._speech_samples += n
        # While the pet talks, require sustained speech so echo or a short noise doesn't cut it off.
        need = self._barge_in_seconds() * SAMPLE_RATE if self._playing() else 0
        if not self._barged and self._speech_samples >= need:
            self._barged = True
            await self._on_barge_in()

    async def _recognize_loop(self) -> None:
        while True:
            seg, manual = await self._queue.get()
            self._recognizing = True
            try:
                text = await self._engine.transcribe(seg)
                seconds = round(len(seg) / SAMPLE_RATE, 2)
                if meaningful(text):
                    await self._on_utterance(text, seconds, manual)
                elif self._on_noise is not None:
                    await self._on_noise(text, seconds, manual)
            except Exception:
                log.exception("语音识别失败")
            finally:
                self._recognizing = False

    async def aclose(self) -> None:
        self._worker.cancel()
        await asyncio.gather(self._worker, return_exceptions=True)


def resolve_model_dir(backend_dir: Path, model_dir: str) -> Path:
    p = Path(model_dir)
    return p if p.is_absolute() else backend_dir / p
