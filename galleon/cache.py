"""SQLite cache with TTL for navigator results."""

import asyncio
import json
import time
from pathlib import Path

import aiosqlite

DEFAULT_TTL = 86400  # 24 hours
DB_PATH = Path(__file__).resolve().parent.parent / "galleon_cache.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cache (
    source TEXT NOT NULL,
    key    TEXT NOT NULL,
    data   TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (source, key)
)
"""


async def _db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_PATH)
    await conn.execute(_CREATE_TABLE)
    await conn.commit()
    return conn


async def get(source: str, key: str) -> dict | None:
    """Return cached data or None if missing/expired."""
    conn = await _db()
    try:
        cursor = await conn.execute(
            "SELECT data, expires_at FROM cache WHERE source = ? AND key = ?",
            (source, key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        data, expires_at = row
        if time.time() > expires_at:
            await conn.execute(
                "DELETE FROM cache WHERE source = ? AND key = ?", (source, key)
            )
            await conn.commit()
            return None
        return json.loads(data)
    finally:
        await conn.close()


async def put(source: str, key: str, data: dict, ttl: float = DEFAULT_TTL) -> None:
    """Store data with TTL."""
    conn = await _db()
    try:
        await conn.execute(
            "INSERT OR REPLACE INTO cache (source, key, data, expires_at) VALUES (?, ?, ?, ?)",
            (source, key, json.dumps(data), time.time() + ttl),
        )
        await conn.commit()
    finally:
        await conn.close()


async def cleanup() -> int:
    """Delete all expired entries. Returns count deleted."""
    conn = await _db()
    try:
        cursor = await conn.execute(
            "DELETE FROM cache WHERE expires_at < ?", (time.time(),)
        )
        await conn.commit()
        return cursor.rowcount
    finally:
        await conn.close()


if __name__ == "__main__":
    async def _test():
        await put("test", "key1", {"hello": "world"}, ttl=10)
        result = await get("test", "key1")
        print(json.dumps(result))

    asyncio.run(_test())
