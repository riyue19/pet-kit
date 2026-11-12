"""`memory` section of config/backend.yaml (edited from the console's 记忆 page)."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Off: conversations are not stored; the pet only remembers the current session (persona.history_turns).
    enabled: bool = True
    # Summarize into long-term memories after this many new exchanges (one user line + one reply).
    summarize_every_turns: int = Field(default=20, ge=5, le=200)
    # Long-term memories added to the prompt per reply (pinned ones are always added on top).
    recall_memories: int = Field(default=5, ge=0, le=20)
    # Older conversation lines (outside the recent history window) added per reply.
    recall_messages: int = Field(default=3, ge=0, le=10)
    # Cosine similarity a memory/message needs to be recalled (bge-small-zh: related ~0.5, loosely related ~0.4, unrelated ~0.3).
    min_similarity: float = Field(default=0.45, ge=0.1, le=0.95)
    # Relative to backend/; filled by scripts/fetch_models.py.
    model_dir: str = Field(default="models/embed", min_length=1, max_length=260)
    # Where chat history + memories are stored: an absolute folder (memory.db is created inside) or a .db file.
    # Empty = %LOCALAPPDATA%/pet-kit/memory.db. Changing it moves the existing database there.
    db_path: str = Field(default="", max_length=260)

    @field_validator("db_path")
    @classmethod
    def _db_path(cls, v: str) -> str:
        v = v.strip().strip('"')
        if v and not re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/)", v):
            raise ValueError("请填写完整路径，例如 E:\\pet-kit-data（留空则使用默认位置）")
        return v
