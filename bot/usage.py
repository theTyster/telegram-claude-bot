"""Per-user daily call limits.

Only ``(user_id, day, count)`` is stored: never any message content.
``UsageStore`` is the seam for swapping in another backend later (for
example a D1/Durable Object store behind a Cloudflare Worker).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


def day_key(tz: ZoneInfo, now: datetime | None = None) -> str:
    now = now or datetime.now(tz)
    return now.astimezone(tz).strftime("%Y-%m-%d")


def next_reset(tz: ZoneInfo, now: datetime | None = None) -> datetime:
    now = (now or datetime.now(tz)).astimezone(tz)
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=tz)


class UsageStore(Protocol):
    async def try_consume(self, user_id: int, day: str, limit: int) -> int | None:
        """Use one call. Returns calls left today, or None if over the limit."""

    async def refund(self, user_id: int, day: str) -> None: ...

    async def used(self, user_id: int, day: str) -> int: ...

    async def purge_before(self, day: str) -> None: ...


class SqliteUsageStore:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS usage ("
            " user_id INTEGER NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,"
            " PRIMARY KEY (user_id, day))"
        )
        # One asyncio lock is plenty for a family bot and keeps each
        # check-and-increment atomic.
        self._lock = asyncio.Lock()

    async def try_consume(self, user_id: int, day: str, limit: int) -> int | None:
        async with self._lock:
            row = self._db.execute(
                "INSERT INTO usage (user_id, day, count) VALUES (?, ?, 1)"
                " ON CONFLICT (user_id, day) DO UPDATE SET count = count + 1"
                " WHERE count < ? RETURNING count",
                (user_id, day, limit),
            ).fetchone()
            if row is None or row[0] > limit:
                return None
            return limit - row[0]

    async def refund(self, user_id: int, day: str) -> None:
        async with self._lock:
            self._db.execute(
                "UPDATE usage SET count = count - 1 WHERE user_id = ? AND day = ? AND count > 0",
                (user_id, day),
            )

    async def used(self, user_id: int, day: str) -> int:
        async with self._lock:
            row = self._db.execute(
                "SELECT count FROM usage WHERE user_id = ? AND day = ?", (user_id, day)
            ).fetchone()
            return row[0] if row else 0

    async def purge_before(self, day: str) -> None:
        async with self._lock:
            self._db.execute("DELETE FROM usage WHERE day < ?", (day,))

    def close(self) -> None:
        self._db.close()
