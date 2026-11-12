"""Local ASR via sherpa-onnx: SenseVoice-Small (int8) recognition + Silero VAD. CPU only, no network."""

from pathlib import Path

import numpy as np
import sherpa_onnx

SAMPLE_RATE = 16000
SENSE_VOICE_MODEL = "sense-voice/model.int8.onnx"
SENSE_VOICE_TOKENS = "sense-voice/tokens.txt"
VAD_MODEL = "silero_vad.onnx"
# Longest single utterance; longer speech is split.
MAX_SPEECH_SECONDS = 20.0
MIN_SPEECH_SECONDS = 0.25


def models_ready(model_dir: Path) -> bool:
    return all((model_dir / f).is_file() for f in (SENSE_VOICE_MODEL, SENSE_VOICE_TOKENS, VAD_MODEL))


class SenseVoiceRecognizer:
    def __init__(self, model_dir: Path, threads: int, language: str = "zh") -> None:
        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_dir / SENSE_VOICE_MODEL),
            tokens=str(model_dir / SENSE_VOICE_TOKENS),
            num_threads=threads,
            use_itn=True,
            language=language,
        )

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        stream = self._rec.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self._rec.decode_stream(stream)
        return stream.result.text.strip()


class SileroVad:
    def __init__(
        self,
        model_dir: Path,
        *,
        threshold: float,
        min_silence_seconds: float,
        min_speech_seconds: float = MIN_SPEECH_SECONDS,
        max_speech_seconds: float = MAX_SPEECH_SECONDS,
    ) -> None:
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(model_dir / VAD_MODEL)
        cfg.silero_vad.threshold = threshold
        cfg.silero_vad.min_silence_duration = min_silence_seconds
        cfg.silero_vad.min_speech_duration = min_speech_seconds
        cfg.silero_vad.max_speech_duration = max_speech_seconds
        cfg.sample_rate = SAMPLE_RATE
        cfg.num_threads = 1
        self.window: int = cfg.silero_vad.window_size
        self._vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=max_speech_seconds + 10)

    def accept(self, samples: np.ndarray) -> None:
        self._vad.accept_waveform(samples)

    def speaking(self) -> bool:
        return bool(self._vad.is_speech_detected())

    def pop_segments(self) -> list[np.ndarray]:
        out = []
        while not self._vad.empty():
            out.append(np.asarray(self._vad.front.samples, dtype=np.float32))
            self._vad.pop()
        return out

    def reset(self) -> None:
        self._vad.reset()
