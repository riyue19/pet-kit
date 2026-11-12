"""Console API for chat history and long-term memories."""

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from api.auth import require_token
from core.llm_config import format_validation_error, load_backend_config, save_sections
from core.memory.factory import embed_dir, resolve_db_path
from core.memory.service import MemoryService
from core.memory_config import MemoryConfig
from core.redact import redact_sensitive

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

FETCH_COMMAND = "uv run python scripts/fetch_models.py"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_MEMORY_TEXT = 500


def _svc(request: Request) -> MemoryService:
    svc = getattr(request.app.state, "memory", None)
    if svc is None:
        raise HTTPException(503, "记忆功能未初始化")
    return svc


class SummaryOut(BaseModel):
    ok: bool
    added: int
    updated: int
    deleted: int
    lines: int
    error: str | None
    at: float | None = None


class MemoryStats(BaseModel):
    messages: int
    memories: int
    # Exchanges stored but not yet summarized.
    unsummarized_turns: int
    embed_ready: bool
    db_path: str
    last_summary: SummaryOut | None


class MemoryStateOut(BaseModel):
    config: MemoryConfig
    stats: MemoryStats
    fetch_command: str
    embed_model_dir: str
    # Set after a save that moved or switched the database.
    notice: str | None = None


class MemoryOut(BaseModel):
    id: int
    text: str
    pinned: bool
    source: str
    created: float
    updated: float
    # Similarity to the query (semantic search only).
    score: float | None = None


class MemoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_MEMORY_TEXT)
    pinned: bool = False

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("内容为空")
        return v


class MemoryUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    text: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_TEXT)
    pinned: bool | None = None


class IdIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int


class MessageOut(BaseModel):
    id: int
    ts: float
    role: str
    text: str
    source: str
    event_id: str | None
    expression: str | None
    motion: str | None
    summarized: bool


class HistoryOut(BaseModel):
    messages: list[MessageOut]
    # Pass the smallest id as before_id to load the next (older) page.
    has_more: bool


class DayOut(BaseModel):
    date: str
    count: int


class HistoryDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[int] | None = Field(default=None, max_length=1000)
    date: str | None = None
    all: bool = False
    # Delete messages already summarized into memories, except the newest `keep_recent` (never unsummarized ones).
    summarized: bool = False
    keep_recent: int = Field(default=0, ge=0, le=100000)

    @field_validator("date")
    @classmethod
    def _date(cls, v: str | None) -> str | None:
        if v is not None and not DATE_RE.match(v):
            raise ValueError("日期格式应为 YYYY-MM-DD")
        return v


class DeletedOut(BaseModel):
    deleted: int


def _mem_out(m, score=None) -> MemoryOut:
    return MemoryOut(
        id=m.id, text=m.text, pinned=m.pinned, source=m.source, created=m.created, updated=m.updated, score=score
    )


async def _state(request: Request) -> MemoryStateOut:
    svc = _svc(request)
    cfg = load_backend_config(request.app.state.config_path).memory
    store = svc.store
    last = svc.last_summary
    return MemoryStateOut(
        config=cfg,
        stats=MemoryStats(
            messages=await asyncio.to_thread(store.message_count),
            memories=await asyncio.to_thread(store.memory_count),
            unsummarized_turns=await asyncio.to_thread(store.count_unsummarized_replies),
            embed_ready=svc.embed_ready,
            db_path=str(store.path),
            last_summary=SummaryOut(**last.__dict__, at=svc.last_summary_at) if last else None,
        ),
        fetch_command=FETCH_COMMAND,
        embed_model_dir=str(embed_dir(cfg)),
    )


@router.get("/memory")
async def get_memory(request: Request) -> MemoryStateOut:
    svc = _svc(request)
    svc.reset_embedder()  # picks up a model downloaded since the last attempt
    await svc.warm_up()
    return await _state(request)


@router.put("/memory")
async def put_memory(request: Request, body: dict) -> MemoryStateOut:
    try:
        cfg = MemoryConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, format_validation_error(e)) from None
    svc = _svc(request)
    async with request.app.state.save_lock:
        old = load_backend_config(request.app.state.config_path).memory
        # Move the database first: if that fails, the config keeps pointing at the working location.
        note = None
        if resolve_db_path(cfg) != resolve_db_path(old):
            try:
                note = await svc.relocate(resolve_db_path(cfg))
            except Exception as e:
                log.exception("移动记忆数据库失败")
                raise HTTPException(500, f"无法使用新的保存位置：{type(e).__name__}: {e}") from None
        await asyncio.to_thread(save_sections, request.app.state.config_path, {"memory": cfg})
    log.info("记忆设置已保存")
    svc.reset_embedder(force=cfg.model_dir != old.model_dir)
    await svc.warm_up()
    state = await _state(request)
    state.notice = note
    return state


@router.post("/memory/summarize")
async def summarize_now(request: Request) -> SummaryOut:
    svc = _svc(request)
    r = await svc.summarize()
    return SummaryOut(**r.__dict__, at=svc.last_summary_at)


@router.get("/memories")
async def list_memories(
    request: Request,
    q: str | None = Query(None, max_length=200),
    semantic: bool = False,
    limit: int = Query(200, ge=1, le=1000),
) -> list[MemoryOut]:
    hits = await _svc(request).search_memories(q or "", semantic=semantic, limit=limit)
    return [_mem_out(m, s) for m, s in hits]


@router.post("/memories")
async def add_memory(request: Request, body: MemoryIn) -> MemoryOut:
    svc = _svc(request)
    mid = await svc.add_memory(redact_sensitive(body.text), pinned=body.pinned, source="manual")
    log.info("手动添加记忆 #%d", mid)
    return _mem_out((await asyncio.to_thread(svc.store.get_memories, [mid]))[0])


@router.put("/memories")
async def update_memory(request: Request, body: MemoryUpdateIn) -> MemoryOut:
    svc = _svc(request)
    text = redact_sensitive(body.text.strip()) if body.text is not None else None
    if text == "":
        raise HTTPException(422, "text: 内容为空")
    if not await svc.update_memory(body.id, text=text, pinned=body.pinned):
        raise HTTPException(404, f"记忆不存在：{body.id}")
    return _mem_out((await asyncio.to_thread(svc.store.get_memories, [body.id]))[0])


@router.delete("/memories")
async def delete_memory(request: Request, body: IdIn) -> DeletedOut:
    ok = await _svc(request).delete_memory(body.id)
    if ok:
        log.info("删除记忆 #%d", body.id)
    return DeletedOut(deleted=int(ok))


@router.get("/history")
async def get_history(
    request: Request,
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, max_length=200),
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> HistoryOut:
    store = _svc(request).store
    rows = await asyncio.to_thread(store.list_messages, before_id=before_id, limit=limit + 1, q=q or None, date=date)
    return HistoryOut(messages=[MessageOut(**r.__dict__) for r in rows[:limit]], has_more=len(rows) > limit)


@router.get("/history/days")
async def history_days(request: Request) -> list[DayOut]:
    days = await asyncio.to_thread(_svc(request).store.message_days)
    return [DayOut(date=d, count=n) for d, n in days]


@router.get("/history/summarized-count")
async def summarized_count(request: Request, keep_recent: int = Query(0, ge=0, le=100000)) -> DeletedOut:
    """How many messages a "delete summarized" run with this keep_recent would remove."""
    return DeletedOut(deleted=await _svc(request).count_summarized(keep_recent))


@router.post("/history/delete")
async def delete_history(request: Request, body: HistoryDeleteIn) -> DeletedOut:
    if body.summarized:
        if body.all or body.date or body.ids:
            raise HTTPException(422, "summarized 不能和 ids、date、all 同时使用")
        n = await _svc(request).delete_summarized(body.keep_recent)
        log.info("清理已总结的聊天记录 %d 条（保留最近 %d 条）", n, body.keep_recent)
        return DeletedOut(deleted=n)
    if not (body.all or body.date or body.ids):
        raise HTTPException(422, "要指定 ids、date、all 或 summarized 之一")
    n = await _svc(request).delete_messages(ids=body.ids, date=body.date, all=body.all)
    log.info("删除聊天记录 %d 条（%s）", n, "全部" if body.all else body.date or f"{len(body.ids or [])} 个 id")
    return DeletedOut(deleted=n)
