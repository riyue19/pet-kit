import asyncio
import itertools
import logging
from collections import OrderedDict, deque
from typing import Any, Awaitable, Callable

from core.events import Event
from core.logs import current_event

Handler = Callable[[Event], Awaitable[None]]

log = logging.getLogger(__name__)

HISTORY = 500


class EventBus:
    """In-process pub/sub. publish() returns immediately; handlers run as tasks so a slow LLM call
    never blocks the producer (e.g. the WebSocket receive loop).

    Each handler runs with core.logs.current_event set, so every log line it (or any task it spawns)
    writes carries the event id. Recent events and their outcome are kept for the console.
    """

    def __init__(self, history: int = HISTORY) -> None:
        self._handlers: list[Handler] = []
        self._tasks: set[asyncio.Task] = set()
        self._seq = itertools.count(1)
        # (seq, event)
        self.recent: deque[tuple[int, Event]] = deque(maxlen=history)
        # event id -> outcome (agent output or error), bounded like `recent`.
        self._results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._history = history

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        self._handlers.append(handler)
        return lambda: self._handlers.remove(handler)

    def publish(self, event: Event) -> None:
        self.recent.append((next(self._seq), event))
        log.info("事件 %s %s", event.id, event.type, extra={"event_id": event.id})
        for handler in list(self._handlers):
            task = asyncio.create_task(self._run(handler, event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def record_result(self, event_id: str, result: dict[str, Any]) -> None:
        self._results[event_id] = result
        self._results.move_to_end(event_id)
        while len(self._results) > self._history:
            self._results.popitem(last=False)

    @property
    def last_seq(self) -> int:
        return self.recent[-1][0] if self.recent else 0

    def history(self, *, after: int = 0, type: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Events with seq > after (newest `limit`), oldest first, each with its outcome if any.

        Call on the event loop thread only (publish() mutates `recent` there).
        """
        out = []
        for seq, e in reversed(list(self.recent)):
            if seq <= after:
                break
            if type and e.type != type:
                continue
            out.append({"seq": seq, **e.model_dump(), "result": self._results.get(e.id)})
            if len(out) >= limit:
                break
        out.reverse()
        return out

    async def _run(self, handler: Handler, event: Event) -> None:
        current_event.set(event.id)
        try:
            await handler(event)
        except Exception:
            log.exception("处理事件失败 %s", event.id)
            self.record_result(event.id, {"error": "处理失败，详见日志"})

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
