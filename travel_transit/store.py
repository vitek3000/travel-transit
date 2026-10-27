"""A small SQLite file next to the service: the requests sent to Transitous (for the pace and the daily limit) and the
answers kept for a few hours.

Every request books its moment in the table before it is sent, inside one write transaction, so two threads or two
processes of the service sharing the file still keep the pace together. Rows older than two days are deleted, and an
answer is deleted once it has expired.
"""

import sqlite3
import threading
import time
import zlib
from collections.abc import Callable
from pathlib import Path

DAY_S = 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS calls_at ON calls (at);
CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, expires REAL NOT NULL, body BLOB NOT NULL);
"""


class DailyLimitReached(Exception):
    def __init__(self, retry_after_s: float):
        self.retry_after_s = retry_after_s
        super().__init__("daily limit reached")


class Busy(Exception):
    def __init__(self, retry_after_s: float):
        self.retry_after_s = retry_after_s
        super().__init__("too many requests are waiting for their turn")


class Store:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # autocommit: the transactions below are opened by hand (BEGIN IMMEDIATE takes the write lock at once)
        return sqlite3.connect(self._path, timeout=10, isolation_level=None)

    # --- the pace and the daily limit ---

    def book(self, *, min_interval_s: float, daily_limit: int, max_wait_s: float) -> float:
        """Book the moment the next request to Transitous may start (seconds since the epoch, maybe in the future).

        Raises DailyLimitReached when `daily_limit` requests went out in the last 24 hours, and Busy when the turn is
        more than `max_wait_s` away (many requests queueing at once)."""
        with self._lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                now = self._clock()
                db.execute("DELETE FROM calls WHERE at < ?", (now - 2 * DAY_S,))
                count, oldest = db.execute(
                    "SELECT COUNT(*), MIN(at) FROM calls WHERE at > ?", (now - DAY_S,)
                ).fetchone()
                if count >= daily_limit:
                    db.execute("COMMIT")
                    raise DailyLimitReached(max(1.0, oldest + DAY_S - now))
                (last,) = db.execute("SELECT MAX(at) FROM calls").fetchone()
                slot = now if last is None else max(now, last + min_interval_s)
                if slot - now > max_wait_s:
                    db.execute("COMMIT")
                    raise Busy(slot - now)
                db.execute("INSERT INTO calls (at) VALUES (?)", (slot,))
                db.execute("COMMIT")
                return slot
            finally:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                db.close()

    def calls_last_day(self) -> int:
        """The requests sent (or booked) in the last 24 hours."""
        now = self._clock()
        with self._lock:
            db = self._connect()
            try:
                (count,) = db.execute("SELECT COUNT(*) FROM calls WHERE at > ?", (now - DAY_S,)).fetchone()
                return int(count)
            finally:
                db.close()

    # --- the answers ---

    def get(self, key: str) -> bytes | None:
        """A fresh answer kept under `key`, or None."""
        now = self._clock()
        with self._lock:
            db = self._connect()
            try:
                row = db.execute("SELECT expires, body FROM answers WHERE key = ?", (key,)).fetchone()
                if row is None:
                    return None
                expires, body = row
                if expires <= now:
                    db.execute("DELETE FROM answers WHERE key = ?", (key,))
                    return None
                return zlib.decompress(body)
            finally:
                db.close()

    def put(self, key: str, body: bytes, ttl_s: float) -> None:
        now = self._clock()
        with self._lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM answers WHERE expires <= ?", (now,))
                db.execute(
                    "INSERT OR REPLACE INTO answers (key, expires, body) VALUES (?, ?, ?)",
                    (key, now + ttl_s, zlib.compress(body, 6)),
                )
                db.execute("COMMIT")
            finally:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                db.close()
