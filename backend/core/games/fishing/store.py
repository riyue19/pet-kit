"""SQLite persistence for fishing (inventory, codex, catch log, bottles, key/value state).

Every write goes through `tx()`: one cast = one transaction, so a crash neither loses nor repeats a cast.
Species ids that are no longer in fish.yaml stay in the tables; callers show them as "未知鱼".
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory (
  id INTEGER PRIMARY KEY, species_id TEXT NOT NULL, size_cm REAL NOT NULL,
  trophy INTEGER NOT NULL DEFAULT 0, caught_ts INTEGER NOT NULL, location TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bait_stock (bait_id TEXT PRIMARY KEY, qty INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS codex (
  species_id TEXT PRIMARY KEY, first_ts INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0,
  max_size REAL NOT NULL, min_size REAL, total_size REAL NOT NULL DEFAULT 0,
  released INTEGER NOT NULL DEFAULT 0, story TEXT, story_ts INTEGER
);
CREATE TABLE IF NOT EXISTS catch_log (
  id INTEGER PRIMARY KEY, seq INTEGER NOT NULL, ts INTEGER NOT NULL, outcome TEXT NOT NULL,
  species_id TEXT, junk_id TEXT, size_cm REAL, escaped INTEGER DEFAULT 0,
  location TEXT, weather TEXT, time_slot TEXT, bait TEXT
);
CREATE TABLE IF NOT EXISTS junk_stats (junk_id TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS bottles (
  id INTEGER PRIMARY KEY, sender TEXT NOT NULL, body TEXT NOT NULL,
  created_ts INTEGER NOT NULL, read_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_catch_log_ts ON catch_log(ts);
"""
MAX_LOG_ROWS = 5000


class Store:
    def __init__(self, path: Path | None) -> None:
        """`path` None = in memory (tests)."""
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path else ":memory:", isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._depth = 0

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def tx(self) -> Iterator[None]:
        """All-or-nothing; nested use joins the outer transaction."""
        if self._depth:
            yield
            return
        self._db.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")
        finally:
            self._depth = 0

    def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # --- key/value state -----------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set(self, key: str, value: Any) -> None:
        with self.tx():
            self._db.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def add_coins(self, delta: int) -> int:
        coins = max(0, int(self.get("coins", 0)) + delta)
        self.set("coins", coins)
        return coins

    # --- bait ----------------------------------------------------------------------------------

    def bait_qty(self, bait_id: str) -> int:
        row = self._db.execute("SELECT qty FROM bait_stock WHERE bait_id=?", (bait_id,)).fetchone()
        return int(row["qty"]) if row else 0

    def bait_stock(self) -> dict[str, int]:
        return {r["bait_id"]: r["qty"] for r in self._rows("SELECT bait_id, qty FROM bait_stock WHERE qty > 0")}

    def add_bait(self, bait_id: str, delta: int) -> int:
        qty = max(0, self.bait_qty(bait_id) + delta)
        with self.tx():
            self._db.execute(
                "INSERT INTO bait_stock(bait_id, qty) VALUES(?, ?) ON CONFLICT(bait_id) DO UPDATE SET qty=excluded.qty",
                (bait_id, qty),
            )
        return qty

    # --- inventory -----------------------------------------------------------------------------

    def inventory(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM inventory ORDER BY caught_ts DESC, id DESC")

    def add_fish(self, species_id: str, size_cm: float, trophy: bool, ts: float, location: str) -> int:
        with self.tx():
            cur = self._db.execute(
                "INSERT INTO inventory(species_id, size_cm, trophy, caught_ts, location) VALUES(?,?,?,?,?)",
                (species_id, size_cm, int(trophy), int(ts), location),
            )
        return int(cur.lastrowid or 0)

    def remove_fish(self, ids: list[int]) -> None:
        with self.tx():
            self._db.executemany("DELETE FROM inventory WHERE id=?", [(i,) for i in ids])

    # --- codex ---------------------------------------------------------------------------------

    def codex(self) -> dict[str, dict[str, Any]]:
        return {r["species_id"]: r for r in self._rows("SELECT * FROM codex")}

    def codex_entry(self, species_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM codex WHERE species_id=?", (species_id,))
        return rows[0] if rows else None

    def record_catch(self, species_id: str, size_cm: float, ts: float) -> tuple[bool, bool]:
        """Returns (first time, new personal record: beat the old max once caught 3+ times)."""
        old = self.codex_entry(species_id)
        with self.tx():
            if old is None:
                self._db.execute(
                    "INSERT INTO codex(species_id, first_ts, count, max_size, min_size, total_size) VALUES(?,?,?,?,?,?)",
                    (species_id, int(ts), 1, size_cm, size_cm, size_cm),
                )
                return True, False
            self._db.execute(
                "UPDATE codex SET count=count+1, max_size=MAX(max_size, ?), min_size=MIN(COALESCE(min_size, ?), ?), "
                "total_size=total_size+? WHERE species_id=?",
                (size_cm, size_cm, size_cm, size_cm, species_id),
            )
        return False, size_cm > old["max_size"] and old["count"] >= 3

    def add_released(self, species_id: str) -> None:
        with self.tx():
            self._db.execute("UPDATE codex SET released=released+1 WHERE species_id=?", (species_id,))

    def set_story(self, species_id: str, story: str, ts: float) -> None:
        with self.tx():
            self._db.execute("UPDATE codex SET story=?, story_ts=? WHERE species_id=?", (story, int(ts), species_id))

    # --- log / junk ----------------------------------------------------------------------------

    def log_cast(self, result: Any, *, escaped: bool = False) -> None:
        c = result.ctx
        with self.tx():
            self._db.execute(
                "INSERT INTO catch_log(seq, ts, outcome, species_id, junk_id, size_cm, escaped, location, weather, time_slot, bait)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (result.seq, int(result.ts), result.outcome, result.species_id, result.junk_id, result.size_cm,
                 int(escaped), c.get("location"), c.get("weather"), c.get("time_slot"), c.get("bait")),
            )
            self._db.execute(
                "DELETE FROM catch_log WHERE id <= (SELECT MAX(id) FROM catch_log) - ?", (MAX_LOG_ROWS,)
            )

    def set_escaped(self, seq: int, escaped: bool = True) -> None:
        with self.tx():
            self._db.execute("UPDATE catch_log SET escaped=? WHERE seq=?", (int(escaped), seq))

    def recent_log(self, limit: int = 30) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM catch_log ORDER BY id DESC LIMIT ?", (limit,))

    def add_junk(self, junk_id: str) -> None:
        with self.tx():
            self._db.execute(
                "INSERT INTO junk_stats(junk_id, count) VALUES(?, 1) ON CONFLICT(junk_id) DO UPDATE SET count=count+1",
                (junk_id,),
            )

    def junk_stats(self) -> dict[str, int]:
        return {r["junk_id"]: r["count"] for r in self._rows("SELECT junk_id, count FROM junk_stats")}

    def totals(self) -> dict[str, int]:
        row = self._db.execute(
            "SELECT COUNT(*) AS casts, COALESCE(SUM(outcome='fish' AND escaped=0), 0) AS fish,"
            " COALESCE(SUM(escaped), 0) AS escaped FROM catch_log"
        ).fetchone()
        return {"casts": row["casts"], "fish": row["fish"], "escaped": row["escaped"]}

    # --- bottles -------------------------------------------------------------------------------

    def add_bottles(self, letters: list[tuple[str, str]], ts: float) -> None:
        with self.tx():
            self._db.executemany(
                "INSERT INTO bottles(sender, body, created_ts) VALUES(?,?,?)", [(s, b, int(ts)) for s, b in letters]
            )

    def bottle_counts(self) -> tuple[int, int]:
        """(total, unread)"""
        row = self._db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(read_ts IS NULL), 0) AS unread FROM bottles").fetchone()
        return int(row["n"]), int(row["unread"])

    def take_bottle(self, ts: float) -> dict[str, Any] | None:
        """A random unread letter (any letter once all were read), marked read."""
        rows = self._rows("SELECT * FROM bottles WHERE read_ts IS NULL ORDER BY RANDOM() LIMIT 1") or self._rows(
            "SELECT * FROM bottles ORDER BY RANDOM() LIMIT 1"
        )
        if not rows:
            return None
        with self.tx():
            self._db.execute("UPDATE bottles SET read_ts=? WHERE id=?", (int(ts), rows[0]["id"]))
        return rows[0]

    def read_bottles(self, limit: int = 30) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM bottles WHERE read_ts IS NOT NULL ORDER BY read_ts DESC LIMIT ?", (limit,))
