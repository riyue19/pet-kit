"""SQLite storage for chat history and long-term memories (blocking; MemoryService calls it off-loop).

messages: every exchange that went to the LLM (user line + pet reply), kept forever unless deleted.
memories: facts distilled from messages by summarization, or added by hand in the console.
Embeddings are float32 blobs; NULL until computed (e.g. the model wasn't downloaded yet).
"""

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    event_id TEXT,
    expression TEXT,
    motion TEXT,
    summarized INTEGER NOT NULL DEFAULT 0,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS messages_ts ON messages(ts);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    text TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'summary' CHECK (source IN ('summary', 'manual')),
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Message:
    id: int
    ts: float
    role: str
    text: str
    source: str
    event_id: str | None
    expression: str | None
    motion: str | None
    summarized: bool
    # The spoken line when it differed from the bubble text (tts.speech_language); "" otherwise.
    speech: str = ""


@dataclass
class Memory:
    id: int
    created: float
    updated: float
    text: str
    pinned: bool
    source: str


def _blob(v: np.ndarray | None) -> bytes | None:
    return None if v is None else np.asarray(v, dtype=np.float32).tobytes()


def _local_day_bounds(date: str) -> tuple[float, float]:
    d = datetime.strptime(date, "%Y-%m-%d")
    return d.timestamp(), (d + timedelta(days=1)).timestamp()


class MemoryStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        # Rollback journal, not WAL: everything lives in this one file (a WAL setup keeps recent writes in
        # memory.db-wal, so the .db looks empty to someone browsing or copying it). One connection anyway.
        self._db.execute("PRAGMA journal_mode=DELETE")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        # Added later: older databases get the column on first open.
        if "speech" not in {r["name"] for r in self._db.execute("PRAGMA table_info(messages)")}:
            self._db.execute("ALTER TABLE messages ADD COLUMN speech TEXT")
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- meta ---------------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    # --- messages -----------------------------------------------------------------------

    def add_message(
        self,
        role: str,
        text: str,
        *,
        source: str = "",
        event_id: str | None = None,
        expression: str | None = None,
        motion: str | None = None,
        ts: float | None = None,
        speech: str = "",
    ) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO messages(ts, role, text, source, event_id, expression, motion, speech) VALUES (?,?,?,?,?,?,?,?)",
                (ts if ts is not None else time.time(), role, text, source, event_id, expression, motion, speech or None),
            )
            return int(cur.lastrowid)

    @staticmethod
    def _msg(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            ts=row["ts"],
            role=row["role"],
            text=row["text"],
            source=row["source"],
            event_id=row["event_id"],
            expression=row["expression"],
            motion=row["motion"],
            summarized=bool(row["summarized"]),
            speech=row["speech"] or "",
        )

    def recent_messages(self, n: int) -> list[Message]:
        """Last n messages, oldest first."""
        if n <= 0:
            return []
        with self._lock:
            rows = self._db.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [self._msg(r) for r in reversed(rows)]

    def list_messages(
        self, *, before_id: int | None = None, limit: int = 50, q: str | None = None, date: str | None = None
    ) -> list[Message]:
        """Newest-first page of messages, optionally filtered by substring and/or local date (YYYY-MM-DD)."""
        sql, args = "SELECT * FROM messages WHERE 1=1", []
        if before_id is not None:
            sql += " AND id < ?"
            args.append(before_id)
        if q:
            sql += " AND text LIKE ? ESCAPE '\\'"
            args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        if date:
            start, end = _local_day_bounds(date)
            sql += " AND ts >= ? AND ts < ?"
            args += [start, end]
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._msg(r) for r in rows]

    def message_days(self) -> list[tuple[str, int]]:
        """(local date, message count), newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT date(ts, 'unixepoch', 'localtime') AS d, COUNT(*) AS n FROM messages GROUP BY d ORDER BY d DESC"
            ).fetchall()
        return [(r["d"], r["n"]) for r in rows]

    def get_messages(self, ids: list[int]) -> list[Message]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM messages WHERE id IN ({marks}) ORDER BY id", ids).fetchall()
        return [self._msg(r) for r in rows]

    def unsummarized(self, limit: int = 400) -> list[Message]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM messages WHERE summarized=0 ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
        return [self._msg(r) for r in rows]

    def count_unsummarized_replies(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE summarized=0 AND role='assistant'"
            ).fetchone()
        return int(row["n"])

    def mark_summarized(self, up_to_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE messages SET summarized=1 WHERE id <= ? AND summarized=0", (up_to_id,))

    def delete_messages(self, *, ids: list[int] | None = None, date: str | None = None, all: bool = False) -> int:
        with self._lock:
            if all:
                cur = self._db.execute("DELETE FROM messages")
            elif date:
                start, end = _local_day_bounds(date)
                cur = self._db.execute("DELETE FROM messages WHERE ts >= ? AND ts < ?", (start, end))
            elif ids:
                marks = ",".join("?" * len(ids))
                cur = self._db.execute(f"DELETE FROM messages WHERE id IN ({marks})", ids)
            else:
                return 0
            return cur.rowcount

    def delete_messages_returning(
        self, *, ids: list[int] | None = None, date: str | None = None, all: bool = False
    ) -> list[int]:
        """Like delete_messages, but returns the ids that were actually deleted (atomically)."""
        with self._lock:
            if all:
                where, args = "1=1", []
            elif date:
                start, end = _local_day_bounds(date)
                where, args = "ts >= ? AND ts < ?", [start, end]
            elif ids:
                where, args = f"id IN ({','.join('?' * len(ids))})", list(ids)
            else:
                return []
            gone = [r[0] for r in self._db.execute(f"SELECT id FROM messages WHERE {where}", args).fetchall()]
            self._db.execute(f"DELETE FROM messages WHERE {where}", args)
            return gone

    # Rows a "clean up summarized" run may delete: already distilled into memories, and not among the newest
    # `keep_recent` messages (those stay as the conversation context). Unsummarized rows are never touched.
    _CLEANUP_WHERE = "summarized=1 AND id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT ?)"

    def count_summarized(self, keep_recent: int = 0) -> int:
        with self._lock:
            row = self._db.execute(
                f"SELECT COUNT(*) FROM messages WHERE {self._CLEANUP_WHERE}", (max(0, keep_recent),)
            ).fetchone()
        return int(row[0])

    def delete_summarized_returning(self, keep_recent: int = 0) -> list[int]:
        """Deletes summarized messages except the newest `keep_recent`; returns the deleted ids (atomically)."""
        args = (max(0, keep_recent),)
        with self._lock:
            gone = [r[0] for r in self._db.execute(f"SELECT id FROM messages WHERE {self._CLEANUP_WHERE}", args)]
            self._db.execute(f"DELETE FROM messages WHERE {self._CLEANUP_WHERE}", args)
            return gone

    def vacuum(self) -> None:
        """SQLite keeps deleted pages in the file; VACUUM gives the space back."""
        with self._lock:
            self._db.execute("VACUUM")

    def existing_ids(self, table: str, ids: list[int]) -> list[int]:
        assert table in ("messages", "memories")
        if not ids:
            return []
        with self._lock:
            rows = self._db.execute(f"SELECT id FROM {table} WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        return [r[0] for r in rows]

    def message_count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    # --- memories -----------------------------------------------------------------------

    @staticmethod
    def _mem(row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            created=row["created"],
            updated=row["updated"],
            text=row["text"],
            pinned=bool(row["pinned"]),
            source=row["source"],
        )

    def add_memory(self, text: str, *, pinned: bool = False, source: str = "summary", embedding: np.ndarray | None = None) -> int:
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO memories(created, updated, text, pinned, source, embedding) VALUES (?,?,?,?,?,?)",
                (now, now, text, int(pinned), source, _blob(embedding)),
            )
            return int(cur.lastrowid)

    def update_memory(
        self, mid: int, *, text: str | None = None, pinned: bool | None = None, embedding: np.ndarray | None = None
    ) -> bool:
        sets, args = ["updated=?"], [time.time()]
        if text is not None:
            sets += ["text=?", "embedding=?"]
            args += [text, _blob(embedding)]
        if pinned is not None:
            sets.append("pinned=?")
            args.append(int(pinned))
        args.append(mid)
        with self._lock:
            cur = self._db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", args)
            return cur.rowcount > 0

    def delete_memory(self, mid: int) -> bool:
        with self._lock:
            return self._db.execute("DELETE FROM memories WHERE id=?", (mid,)).rowcount > 0

    def list_memories(self, q: str | None = None) -> list[Memory]:
        sql, args = "SELECT * FROM memories", []
        if q:
            sql += " WHERE text LIKE ? ESCAPE '\\'"
            args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY pinned DESC, updated DESC"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._mem(r) for r in rows]

    def get_memories(self, ids: list[int]) -> list[Memory]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM memories WHERE id IN ({marks})", ids).fetchall()
        by_id = {r["id"]: self._mem(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def memory_count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    # --- embeddings ---------------------------------------------------------------------

    def vectors(self, table: str, dim: int) -> tuple[np.ndarray, np.ndarray]:
        """(ids, matrix) of rows that have an embedding of the right size."""
        assert table in ("messages", "memories")
        with self._lock:
            rows = self._db.execute(f"SELECT id, embedding FROM {table} WHERE embedding IS NOT NULL").fetchall()
        ids, vecs = [], []
        for r in rows:
            v = np.frombuffer(r["embedding"], dtype=np.float32)
            if v.shape[0] == dim:
                ids.append(r["id"])
                vecs.append(v)
        if not vecs:
            return np.zeros(0, dtype=np.int64), np.zeros((0, dim), dtype=np.float32)
        return np.array(ids, dtype=np.int64), np.vstack(vecs)

    def missing_embeddings(self, table: str, limit: int = 256) -> list[tuple[int, str]]:
        assert table in ("messages", "memories")
        with self._lock:
            rows = self._db.execute(
                f"SELECT id, text FROM {table} WHERE embedding IS NULL ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
        return [(r["id"], r["text"]) for r in rows]

    def set_embeddings(self, table: str, items: list[tuple[int, np.ndarray]]) -> None:
        assert table in ("messages", "memories")
        with self._lock:
            self._db.executemany(f"UPDATE {table} SET embedding=? WHERE id=?", [(_blob(v), i) for i, v in items])

    def clear_embeddings(self) -> None:
        """Embedding model changed: old vectors are incomparable with new ones."""
        with self._lock:
            self._db.execute("UPDATE messages SET embedding=NULL")
            self._db.execute("UPDATE memories SET embedding=NULL")
