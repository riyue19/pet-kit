import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.auth import require_token
from api.probe_image import QUESTION, red_square_data_url
from core.credentials import MAX_KEY_LENGTH, KeyStore
from core.llm_config import (
    ConfigError,
    LlmConfig,
    LlmProfile,
    LlmFallbacks,
    LlmRoles,
    format_validation_error,
    load_backend_config,
    save_llm_config,
)
from providers.base import ProviderError, user_image, user_text
from providers.factory import TextProviderFactory

PING = "这是一条连通性测试，请只回复“pong”。"
REPLY_PREVIEW_CHARS = 200

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm", dependencies=[Depends(require_token)])


class KeyStatus(BaseModel):
    set: bool
    hint: str | None


class ProfileOut(LlmProfile):
    key: KeyStatus


class LlmStateOut(BaseModel):
    profiles: list[ProfileOut]
    roles: LlmRoles
    fallbacks: LlmFallbacks
    web_search: bool


class ProfileIn(LlmProfile):
    # Set when the console renamed a saved profile, so its stored key follows the new name.
    renamed_from: str | None = None


class LlmSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[ProfileIn] = Field(default_factory=list)
    roles: LlmRoles = Field(default_factory=LlmRoles)
    # Omitted by older consoles: keep the saved value then.
    fallbacks: LlmFallbacks | None = None
    web_search: bool | None = None


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    api_key: str = Field(max_length=MAX_KEY_LENGTH)


class TestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["chat", "vision"]
    profile: LlmProfile
    # Name under which the key is stored, if it differs from profile.name (unsaved rename).
    key_profile: str | None = None


class TestOut(BaseModel):
    ok: bool
    latency_ms: int | None = None
    model: str | None = None
    reply: str | None = None
    error: str | None = None


def _keys(request: Request) -> KeyStore:
    return request.app.state.keys


def _load(request: Request) -> LlmConfig:
    try:
        return load_backend_config(request.app.state.config_path).llm
    except ConfigError as e:
        log.warning("读取 backend.yaml 失败：%s", e)
        raise HTTPException(500, f"config/backend.yaml 有误：{e}") from None


def _state(request: Request, llm: LlmConfig) -> LlmStateOut:
    keys = _keys(request)
    return LlmStateOut(
        profiles=[ProfileOut(**p.model_dump(), key=KeyStatus(**keys.status(p.name))) for p in llm.profiles],
        roles=llm.roles,
        fallbacks=llm.fallbacks,
        web_search=llm.web_search,
    )


@router.get("")
def get_llm(request: Request) -> LlmStateOut:
    return _state(request, _load(request))


@router.put("")
async def put_llm(request: Request, body: LlmSaveIn) -> LlmStateOut:
    async with request.app.state.save_lock:
        old = _load(request)
        try:
            def keep_unsent(p: ProfileIn) -> LlmProfile:
                data = p.model_dump(exclude={"renamed_from"})
                # A console that doesn't know web_search yet must not reset it to "off".
                prev = old.profile(p.renamed_from or p.name)
                if "web_search" not in p.model_fields_set and prev is not None:
                    data["web_search"] = prev.web_search
                return LlmProfile(**data)

            new = LlmConfig(
                profiles=[keep_unsent(p) for p in body.profiles],
                roles=body.roles,
                fallbacks=old.fallbacks if body.fallbacks is None else body.fallbacks,
                web_search=old.web_search if body.web_search is None else body.web_search,
            )
        except ValidationError as e:
            raise HTTPException(422, format_validation_error(e)) from None
        old_names = {p.name for p in old.profiles}
        # A profile without a saved origin starts keyless, even if it reuses a deleted profile's name.
        sources = {p.name: p.renamed_from if p.renamed_from in old_names else None for p in body.profiles}
        await asyncio.to_thread(save_llm_config, request.app.state.config_path, new)
        failed = await asyncio.to_thread(_keys(request).remap, sources, old_names)
        if failed:
            raise HTTPException(500, f"配置已保存，但以下配置的 API Key 迁移失败，请重新填写：{'、'.join(sorted(failed))}")
        return _state(request, new)


@router.put("/key")
async def put_key(request: Request, body: KeyIn) -> KeyStatus:
    async with request.app.state.save_lock:
        if _load(request).profile(body.profile) is None:
            raise HTTPException(404, f"配置不存在：{body.profile}（请先保存配置）")
        try:
            await asyncio.to_thread(_keys(request).set, body.profile, body.api_key)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return KeyStatus(**await asyncio.to_thread(_keys(request).status, body.profile))


class KeyDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str


@router.delete("/key")
async def delete_key(request: Request, body: KeyDeleteIn) -> KeyStatus:
    async with request.app.state.save_lock:
        await asyncio.to_thread(_keys(request).delete, body.profile)
    return KeyStatus(set=False, hint=None)


@router.post("/test")
async def test_profile(request: Request, body: TestIn) -> TestOut:
    # Only a saved profile has a key; never fall back to profile.name, which may collide with another profile.
    key = await asyncio.to_thread(_keys(request).get, body.key_profile) if body.key_profile else None
    factory: TextProviderFactory = request.app.state.provider_factory
    messages = [user_image(QUESTION, red_square_data_url())] if body.kind == "vision" else [user_text(PING)]
    try:
        provider = factory(body.profile, key)
    except Exception:  # noqa: BLE001
        log.exception("测试连接：无法创建客户端（%s）", body.profile.name)
        return TestOut(ok=False, error="无法创建客户端，请检查 base_url 等参数")
    try:
        result = await provider.chat(messages)
    except ProviderError as e:
        log.warning("测试连接失败（%s，%s）：%s", body.kind, body.profile.name, e)
        return TestOut(ok=False, error=str(e))
    finally:
        await provider.aclose()
    return TestOut(ok=True, latency_ms=result.latency_ms, model=result.model, reply=result.text[:REPLY_PREVIEW_CHARS])
