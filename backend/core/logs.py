"""Structured logging for troubleshooting.

- Every record gets the id of the event being handled (a ContextVar set by the event bus), so one
  conversation can be followed end to end: event -> LLM -> TTS -> pet.
- Records go to a JSON-lines file (rotated at midnight, 14 days kept) and to an in-memory ring that the
  console's 日志 page polls. Secrets are redacted before either sees the record.
"""

import json
import logging
import threading
import time
from collections import deque
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from core.redact import RedactingFilter

current_event: ContextVar[str | None] = ContextVar("current_event", default=None)

LOG_FILE = "backend.jsonl"
KEEP_DAYS = 14
RING_SIZE = 3000
MAX_QUERY = 1000
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_BACKEND_DIR = Path(__file__).resolve().parents[1]


class EventIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "event_id", None):
            record.event_id = current_event.get()
        return True


def _source_file(path: str) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(_BACKEND_DIR).as_posix()
    except ValueError:
        # Library code: keep the tail, which is what identifies it.
        return "/".join(p.parts[-2:])


def to_entry(record: logging.LogRecord) -> dict[str, Any]:
    exc = record.exc_text
    if not exc and record.exc_info:
        exc = logging.Formatter().formatException(record.exc_info)
    return {
        "ts": round(record.created, 3),
        "level": record.levelname,
        "logger": record.name,
        "file": _source_file(record.pathname),
        "line": record.lineno,
        "func": record.funcName,
        "msg": record.getMessage(),
        "event_id": getattr(record, "event_id", None),
        "exc": exc or None,
    }


class SafeRotatingFileHandler(TimedRotatingFileHandler):
    """If rotation fails (Windows: another process holds the file), keep appending and retry next midnight."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:
                self.stream = self._open()
            self.rolloverAt = self.computeRollover(int(time.time()))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(to_entry(record), ensure_ascii=False)


class RingHandler(logging.Handler):
    """Keeps the latest records with increasing sequence numbers for incremental polling."""

    def __init__(self, size: int = RING_SIZE) -> None:
        super().__init__()
        self._entries: deque[dict[str, Any]] = deque(maxlen=size)
        self._seq = 0
        self._ring_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = to_entry(record)
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)
            return
        with self._ring_lock:
            self._seq += 1
            entry["seq"] = self._seq
            self._entries.append(entry)

    @property
    def last_seq(self) -> int:
        return self._seq

    def query(
        self,
        *,
        after: int = 0,
        level: str = "DEBUG",
        logger: str | None = None,
        event_id: str | None = None,
        q: str | None = None,
        limit: int = 200,
    ) -> tuple[list[dict[str, Any]], int]:
        """(newest `limit` matching entries with seq > after, oldest first; seq of the snapshot).

        The seq is taken under the same lock as the snapshot, so polling with after=seq never skips lines.
        """
        floor = LEVELS.get(level.upper(), 0)
        needle = q.lower() if q else None
        with self._ring_lock:
            entries = list(self._entries)
            snapshot_seq = self._seq
        out = []
        for e in reversed(entries):
            if e["seq"] <= after:
                break
            if LEVELS.get(e["level"], 0) < floor:
                continue
            if logger and not e["logger"].startswith(logger):
                continue
            if event_id and e["event_id"] != event_id:
                continue
            if needle and needle not in e["msg"].lower() and needle not in (e["exc"] or "").lower():
                continue
            out.append(e)
            if len(out) >= min(limit, MAX_QUERY):
                break
        out.reverse()
        return out, snapshot_seq


def setup_logging(log_dir: Path | None, *, level: int = logging.INFO) -> RingHandler:
    """Configures the root logger: console (stderr), JSON file (if log_dir) and the console ring."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    redact, event_id = RedactingFilter(), EventIdFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(event_id)s]: %(message)s"))
    handlers: list[logging.Handler] = [stream]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file = SafeRotatingFileHandler(
            log_dir / LOG_FILE, when="midnight", backupCount=KEEP_DAYS, encoding="utf-8", delay=True
        )
        file.setFormatter(JsonFormatter())
        handlers.append(file)
    ring = RingHandler()
    handlers.append(ring)
    for h in handlers:
        # Redaction first: it rewrites the record that every later handler sees.
        h.addFilter(redact)
        h.addFilter(event_id)
        root.addHandler(h)
    # Chatty per-request logs from HTTP clients add nothing when debugging the pet.
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return ring
