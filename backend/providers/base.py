from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

# OpenAI-style chat messages; content may be a string or a list of text/image_url parts.
Message = dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str | None
    latency_ms: int
    # Sources the model cited after searching the web (0 = it didn't search).
    citations: int = 0


class ProviderError(Exception):
    """Message is already redacted and safe to show in the console or logs."""


class TextProvider(Protocol):
    async def chat(self, messages: list[Message], *, web_search: bool = False) -> ChatResult: ...

    async def aclose(self) -> None: ...


def user_text(text: str) -> Message:
    return {"role": "user", "content": text}


def user_image(text: str, image_data_url: str) -> Message:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ],
    }


class TtsProvider(Protocol):
    """Streams 16-bit little-endian mono PCM at `sample_rate`."""

    sample_rate: int

    def stream(
        self, text: str, *, emotion: str | None = None, language: str | None = None
    ) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


class Recognizer(Protocol):
    """Offline (whole-utterance) speech recognition; blocking, call from a worker thread."""

    def transcribe(self, samples: "np.ndarray", sample_rate: int) -> str: ...


class VoiceActivity(Protocol):
    """Streaming VAD over 16 kHz float32 samples."""

    window: int

    def accept(self, samples: "np.ndarray") -> None: ...

    def speaking(self) -> bool: ...

    def pop_segments(self) -> list["np.ndarray"]: ...

    def reset(self) -> None: ...


class Embedder(Protocol):
    """Text -> L2-normalized vectors; blocking, call from a worker thread."""

    model_id: str
    dim: int

    def embed(self, texts: list[str], *, query: bool = False) -> "np.ndarray": ...
