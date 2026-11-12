"""Asks the chat LLM for the pet's move in a game. Any failure returns None: the caller plays a built-in move."""

import json
import logging
from pathlib import Path
from typing import Any

from core.credentials import KeyStore
from core.llm_chain import chat_with_fallback
from core.llm_config import ConfigError, load_backend_config
from core.persona import Persona, load_yaml_model
from providers.base import Message
from providers.factory import TextProviderFactory

log = logging.getLogger(__name__)

_DECODER = json.JSONDecoder(strict=False)


def first_object(text: str) -> dict[str, Any] | None:
    """First JSON object in a model reply (tolerates code fences and surrounding prose)."""
    start = text.find("{")
    while start >= 0:
        try:
            obj, _ = _DECODER.raw_decode(text, start)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return obj
        start = text.find("{", start + 1)
    return None


class GameBrain:
    def __init__(self, *, config_path: Path, persona_path: Path, keys: KeyStore, provider_factory: TextProviderFactory) -> None:
        self._config_path = config_path
        self._persona_path = persona_path
        self._keys = keys
        self._factory = provider_factory

    def available(self) -> bool:
        try:
            return bool(load_backend_config(self._config_path).llm.chain("chat"))
        except ConfigError:
            return False

    def pet_name(self) -> str:
        try:
            return load_yaml_model(self._persona_path, Persona).name
        except ConfigError:
            return Persona().name

    async def ask(self, rules: str, situation: str) -> dict[str, Any] | None:
        """One call on the chat profile chain: persona + the game's rules, then the situation as the pet sees it.
        Nothing of it is stored anywhere (no chat history, no memory)."""
        try:
            chain = load_backend_config(self._config_path).llm.chain("chat")
            persona = load_yaml_model(self._persona_path, Persona)
        except ConfigError as e:
            log.warning("配置有误，这一步由内置策略代打：%s", e)
            return None
        if not chain:
            return None
        messages: list[Message] = [
            {"role": "system", "content": f"{persona.system_prompt.strip()}\n\n{rules}"},
            {"role": "user", "content": situation},
        ]
        try:
            result, _ = await chat_with_fallback(chain, self._keys, self._factory, messages, role="chat")
        except Exception as e:  # noqa: BLE001 - the game goes on with a built-in move
            log.warning("小游戏问 LLM 失败，这一步由内置策略代打：%s", e)
            return None
        move = first_object(result.text)
        if move is None:
            log.warning("小游戏：模型回复不是 JSON，这一步由内置策略代打：%s", result.text[:120])
        return move
