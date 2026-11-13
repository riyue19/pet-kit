"""Shared voice state: cached asr/tts config, the pet's playback state, and the echo guard."""

import asyncio
import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from core.credentials import KeyStore
from core.paths import key_service
from core.llm_config import ConfigError, load_backend_config
from core.voice_config import AsrConfig, TtsConfig
from perception.asr import AsrEngine, resolve_model_dir
from providers.sherpa_asr import SenseVoiceRecognizer, models_ready

log = logging.getLogger(__name__)

TTS_KEY_SERVICE = key_service("tts")
TTS_KEY_NAME = "minimax"
# After playback stops, the room may still echo for a moment.
ECHO_TAIL_SECONDS = 1.5
ECHO_MATCH_RATIO = 0.6
PLAYBACK_POLL_S = 0.05
PLAYBACK_REPORT_WAIT_S = 0.5
_PUNCT = re.compile(r"[\W_]+")

BACKEND_DIR = Path(__file__).resolve().parents[1]


def is_echo(heard: str, spoken: str) -> bool:
    """True if most of `heard` appears in `spoken` (the pet hearing its own voice through the speakers)."""
    a, b = _PUNCT.sub("", heard), _PUNCT.sub("", spoken)
    if not a or not b:
        return False
    matched = sum(m.size for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return matched / len(a) >= ECHO_MATCH_RATIO


class VoiceState:
    def __init__(self, config_path: Path, keys: KeyStore | None = None) -> None:
        self._config_path = config_path
        self.keys = keys or KeyStore(TTS_KEY_SERVICE)
        self.asr = AsrConfig()
        self.tts = TtsConfig()
        self.playing = False
        self._playback_ended = 0.0
        self._warm_task: asyncio.Task | None = None
        self.engine = AsrEngine(lambda: SenseVoiceRecognizer(self.model_dir, self.asr.threads, self.asr.language))
        self.reload()

    def reload(self) -> None:
        try:
            cfg = load_backend_config(self._config_path)
        except ConfigError as e:
            log.warning("backend.yaml 有误，语音配置使用默认值：%s", e)
            return
        engine_changed = (cfg.asr.model_dir, cfg.asr.threads, cfg.asr.language) != (
            self.asr.model_dir,
            self.asr.threads,
            self.asr.language,
        )
        self.asr, self.tts = cfg.asr, cfg.tts
        if engine_changed and hasattr(self, "engine"):
            self.engine = AsrEngine(lambda: SenseVoiceRecognizer(self.model_dir, self.asr.threads, self.asr.language))

    @property
    def model_dir(self) -> Path:
        return resolve_model_dir(BACKEND_DIR, self.asr.model_dir)

    @property
    def models_ready(self) -> bool:
        return models_ready(self.model_dir)

    @property
    def listening(self) -> bool:
        return self.asr.listen and self.models_ready

    def api_key(self, provider: str | None = None) -> str | None:
        """Each TTS provider's key is stored under the provider's name."""
        return self.keys.get(provider or self.tts.provider)

    def set_playing(self, playing: bool) -> None:
        if self.playing and not playing:
            self._playback_ended = time.monotonic()
        self.playing = playing

    async def wait_playback_done(self, max_seconds: float) -> None:
        """Waits until the pet window reports playback finished (bounded, in case it never reports)."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        # The "playing" report may still be in flight when the stream has just ended.
        while not self.playing and loop.time() - start < PLAYBACK_REPORT_WAIT_S:
            await asyncio.sleep(PLAYBACK_POLL_S)
        while self.playing and loop.time() - start < max_seconds:
            await asyncio.sleep(PLAYBACK_POLL_S)

    def echo_window(self) -> bool:
        return self.playing or time.monotonic() - self._playback_ended < ECHO_TAIL_SECONDS

    def warm_up(self, *, force: bool = False) -> None:
        """Loads the recognizer in the background so the first utterance isn't delayed.
        `force`: also when always-on listening is off (push-to-talk is about to need it)."""
        ready = self.models_ready if force else self.listening
        if not ready or (self._warm_task and not self._warm_task.done()):
            return

        async def run() -> None:
            try:
                await self.engine.warm_up()
                log.info("语音识别模型已加载")
            except Exception:
                log.exception("语音识别模型加载失败")

        self._warm_task = asyncio.get_running_loop().create_task(run())
