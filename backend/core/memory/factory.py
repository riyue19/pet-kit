"""Builds the MemoryService used by the app: SQLite file, local embedder, and the summarization LLM."""

import logging
from pathlib import Path
from typing import Any

from core.credentials import KeyStore
from core.llm_chain import chat_with_fallback
from core.llm_config import ConfigError, load_backend_config
from core.memory.service import MemoryService
from core.memory.store import MemoryStore
from core.memory_config import MemoryConfig
from providers.base import Embedder, ProviderError
from providers.factory import TextProviderFactory

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_FILE = "memory.db"
EMBED_THREADS = 2


def _memory_config(config_path: Path) -> MemoryConfig:
    try:
        return load_backend_config(config_path).memory
    except ConfigError:
        return MemoryConfig()


def default_db_path() -> Path:
    from core.paths import runtime_dir

    return runtime_dir() / DB_FILE


def resolve_db_path(cfg: MemoryConfig) -> Path:
    if not cfg.db_path:
        return default_db_path()
    p = Path(cfg.db_path)
    return p if p.suffix.lower() == ".db" else p / DB_FILE


def embed_dir(cfg: MemoryConfig) -> Path:
    p = Path(cfg.model_dir)
    return p if p.is_absolute() else BACKEND_DIR / p


def build_memory_service(
    *, config_path: Path, db_path: Path, keys: KeyStore, provider_factory: TextProviderFactory
) -> MemoryService:
    def embedder() -> Embedder | None:
        # Imported lazily: onnxruntime is only needed once the model exists.
        from providers.bge_embed import BgeEmbedder, embed_model_ready

        d = embed_dir(_memory_config(config_path))
        return BgeEmbedder(d, EMBED_THREADS) if embed_model_ready(d) else None

    async def summarize(messages: list[dict[str, Any]]) -> str:
        llm = load_backend_config(config_path).llm
        chain = llm.chain("memory")
        if not chain:
            raise ProviderError("没有可用于总结记忆的 LLM（在「AI 模型」页指定「记忆」或「聊天」用途）")
        result, _ = await chat_with_fallback(chain, keys, provider_factory, messages, role="memory")
        return result.text

    return MemoryService(
        MemoryStore(db_path),
        config=lambda: _memory_config(config_path),
        embedder=embedder,
        summarize=summarize,
    )
