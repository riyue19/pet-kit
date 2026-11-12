"""`asr` and `tts` sections of config/backend.yaml (edited from the console's 语音 page)."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Emotions accepted by MiniMax T2A v2 voice_setting.emotion.
TTS_EMOTIONS = ("happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent", "whisper")
TTS_MODELS = (
    "speech-2.8-turbo",
    "speech-2.8-hd",
    "speech-2.6-turbo",
    "speech-2.6-hd",
    "speech-02-turbo",
    "speech-02-hd",
)
LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
TTS_SAMPLE_RATE = 24000


class AsrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Microphone listening. Off = the pet window does not open the microphone at all.
    listen: bool = False
    # Recognition language. "auto" lets SenseVoice guess, which turns breaths/short noises into Japanese
    # or Korean syllables now and then; pinning the language avoids that.
    language: Literal["zh", "auto", "en", "ja", "ko", "yue"] = "zh"
    # Shorter results (after removing punctuation) are dropped, as are pure fillers like 嗯嗯 / 啊.
    min_chars: int = Field(default=2, ge=1, le=10)
    # Results of 5 characters or fewer are also dropped when the audio was shorter than this
    # (stray fragments like "这个我。" from 0.8 s of noise). 0 turns the check off.
    short_min_seconds: float = Field(default=1.0, ge=0, le=2)
    # Relative to backend/; filled by scripts/fetch_models.py.
    model_dir: str = Field(default="models/asr", min_length=1, max_length=260)
    threads: int = Field(default=2, ge=1, le=16)
    # Silero VAD speech probability threshold.
    vad_threshold: float = Field(default=0.5, ge=0.1, le=0.95)
    # Silence that ends an utterance.
    min_silence_seconds: float = Field(default=0.6, ge=0.2, le=3)
    # Sounds shorter than this are not speech at all (clicks, key presses).
    min_speech_seconds: float = Field(default=0.25, ge=0.1, le=1)
    # Longest single utterance; longer speech is cut and sent in pieces.
    max_speech_seconds: float = Field(default=20, ge=5, le=60)
    # What counts as a "short" result for short_min_seconds (characters, punctuation removed).
    short_chars: int = Field(default=5, ge=1, le=20)
    # Drop results made only of filler sounds (嗯 / 啊 / 哦 ...).
    drop_fillers: bool = True
    # Results that are exactly one of these (punctuation ignored) are dropped, e.g. words the recognizer
    # keeps producing from background noise.
    ignore_phrases: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("ignore_phrases")
    @classmethod
    def _phrases(cls, v: list[str]) -> list[str]:
        out = [p.strip()[:30] for p in v if p.strip()]
        return list(dict.fromkeys(out))
    # Talking over the pet cuts its current sentence. Off: it finishes, and replies to later speech queue up.
    barge_in: bool = False
    # Continuous speech needed to interrupt the pet while it is talking (guards against echo/noise).
    barge_in_seconds: float = Field(default=0.4, ge=0, le=3)


class MiniMaxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://api.minimax.cn"
    model: Literal[TTS_MODELS] = "speech-2.8-turbo"  # type: ignore[valid-type]
    voice_id: str = Field(default="female-shaonv", min_length=1, max_length=256)
    speed: float = Field(default=1.0, ge=0.5, le=2)
    vol: float = Field(default=1.0, gt=0, le=10)
    pitch: int = Field(default=0, ge=-12, le=12)
    timeout: float = Field(default=30, gt=0, le=300)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not re.match(r"^https://[^\s/]+$", v):
            raise ValueError("必须是 https://域名，例如 https://api.minimax.cn")
        return v


TTS_PROVIDERS = ("minimax", "cosyvoice", "openai")
# Suggestions shown in the console; any model name the account has access to may be typed.
# A voice only works with the model it belongs to (otherwise the service answers 418): the built-in voices
# suggested in the console are cosyvoice-v3-flash ones; the others are for voices made for those models.
COSYVOICE_MODELS = ("cosyvoice-v3-flash", "cosyvoice-v3-plus", "cosyvoice-v3.5-flash", "cosyvoice-v3.5-plus", "cosyvoice-v2")
# Emotions accepted by CosyVoice's instruction ("你说话的情感是<…>。"); only a few voices support it.
COSYVOICE_EMOTIONS = ("neutral", "fearful", "angry", "sad", "surprised", "happy", "disgusted")
_LOCAL_HTTP = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/\S*)?$")


class CosyVoiceConfig(BaseModel):
    """Alibaba Cloud Model Studio (百炼) CosyVoice over its duplex WebSocket API, Beijing region by default."""

    model_config = ConfigDict(extra="forbid")

    url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    model: str = Field(default="cosyvoice-v3-flash", min_length=1, max_length=64)
    voice: str = Field(default="longanhuan", min_length=1, max_length=256)
    volume: int = Field(default=50, ge=0, le=100)
    rate: float = Field(default=1.0, ge=0.5, le=2)
    pitch: float = Field(default=1.0, ge=0.5, le=2)
    # Send the expression's emotion as an instruction. Only voices that support Instruct accept it
    # (longanhuan, longanyang, longhuhu_v3); others fail, and the sentence is then retried without it.
    emotion_instruct: bool = False
    # A fixed instruction (dialect / role), used when no emotion instruction applies. Must follow the
    # documented formats, e.g. 请用四川话表达。
    instruction: str = Field(default="", max_length=200)
    timeout: float = Field(default=30, gt=0, le=300)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^wss://[^\s/]+(/\S*)?$", v):
            raise ValueError("必须是 wss:// 开头的地址，例如 wss://dashscope.aliyuncs.com/api-ws/v1/inference")
        return v


class OpenAITtsConfig(BaseModel):
    """Any service with an OpenAI-compatible POST {base_url}/audio/speech endpoint."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://api.openai.com/v1"
    model: str = Field(default="gpt-4o-mini-tts", min_length=1, max_length=128)
    voice: str = Field(default="alloy", min_length=1, max_length=256)
    speed: float = Field(default=1.0, ge=0.25, le=4)
    # pcm = raw 16-bit mono; wav = the same with a header (stripped here). Both must be at sample_rate.
    response_format: Literal["pcm", "wav"] = "pcm"
    # What the service returns for this format (OpenAI's pcm is 24000). A wrong value plays too fast or slow.
    sample_rate: int = Field(default=TTS_SAMPLE_RATE, ge=8000, le=48000)
    # Extra request-body fields, e.g. {"sample_rate": 24000} for services that let the client choose.
    extra: dict = Field(default_factory=dict)
    timeout: float = Field(default=30, gt=0, le=300)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not (re.match(r"^https://[^\s/]+(/\S*)?$", v) or _LOCAL_HTTP.match(v)):
            raise ValueError("必须是 https:// 地址（本机服务可用 http://127.0.0.1:端口），例如 https://api.openai.com/v1")
        return v

    @field_validator("extra")
    @classmethod
    def _extra(cls, v: dict) -> dict:
        reserved = {"model", "input", "voice", "response_format", "stream"} & set(v)
        if reserved:
            raise ValueError(f"extra 里不能有这些字段：{', '.join(sorted(reserved))}")
        return v


class TtsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    provider: Literal[TTS_PROVIDERS] = "minimax"  # type: ignore[valid-type]
    minimax: MiniMaxConfig = Field(default_factory=MiniMaxConfig)
    cosyvoice: CosyVoiceConfig = Field(default_factory=CosyVoiceConfig)
    openai: OpenAITtsConfig = Field(default_factory=OpenAITtsConfig)
    # Language the pet *speaks*: "same" reads the bubble text; "ja"/"en" asks the LLM for a separate spoken line
    # in that language while the bubble stays Chinese (pick a matching voice, e.g. Japanese_GracefulMaiden).
    speech_language: Literal["same", "ja", "en"] = "same"
    # Expression label -> TTS emotion. A label that is itself an emotion name (e.g. happy) needs no entry.
    emotions: dict[str, Literal[TTS_EMOTIONS]] = Field(default_factory=dict)  # type: ignore[valid-type]

    @field_validator("emotions")
    @classmethod
    def _labels(cls, v: dict[str, str]) -> dict[str, str]:
        bad = sorted(k for k in v if not LABEL_RE.match(k))
        if bad:
            raise ValueError(f"标签格式不正确：{', '.join(bad)}")
        return v

    def emotion_for(self, expression: str | None) -> str | None:
        if not expression:
            return None
        if expression in self.emotions:
            return self.emotions[expression]
        return expression if expression in TTS_EMOTIONS else None

    @property
    def model_name(self) -> str:
        """Model of the selected provider (for logs)."""
        return getattr(self, self.provider).model

    @property
    def tone_key(self) -> str:
        """What decides whether a tone is supported: provider + model (+ voice for CosyVoice)."""
        sub = getattr(self, self.provider)
        return f"{self.provider}/{sub.model}/{getattr(sub, 'voice', '')}"

    def pin_endpoints(self, saved: "TtsConfig") -> "TtsConfig":
        """A copy whose service addresses are the saved ones: a stored key is never sent to an unsaved URL."""
        return self.model_copy(
            update={
                "minimax": self.minimax.model_copy(update={"base_url": saved.minimax.base_url}),
                "cosyvoice": self.cosyvoice.model_copy(update={"url": saved.cosyvoice.url}),
                "openai": self.openai.model_copy(update={"base_url": saved.openai.base_url}),
            }
        )
