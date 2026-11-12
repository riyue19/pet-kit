from typing import Callable

from core.llm_config import LlmProfile
from core.voice_config import TtsConfig
from providers.base import TextProvider, TtsProvider
from providers.cosyvoice_tts import CosyVoiceTts
from providers.minimax_tts import MiniMaxTts
from providers.openai_tts import OpenAICompatTts
from providers.openai_compat import OpenAICompatProvider

TextProviderFactory = Callable[[LlmProfile, str | None], TextProvider]


def make_text_provider(profile: LlmProfile, api_key: str | None) -> TextProvider:
    return OpenAICompatProvider(profile, api_key)


def make_tts_provider(cfg: TtsConfig, api_key: str | None) -> TtsProvider:
    if cfg.provider == "cosyvoice":
        return CosyVoiceTts(cfg.cosyvoice, api_key)
    if cfg.provider == "openai":
        return OpenAICompatTts(cfg.openai, api_key)
    return MiniMaxTts(cfg.minimax, api_key)
