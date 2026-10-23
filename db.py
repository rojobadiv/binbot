from __future__ import annotations

import json
import time
from typing import Any, Optional

import aiosqlite

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    role        TEXT NOT NULL DEFAULT 'user',
    checks      INTEGER NOT NULL DEFAULT 0,
    bins        INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proxies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL UNIQUE,
    owner_id    INTEGER NOT NULL DEFAULT 0,
    is_global  INTEGER NOT NULL DEFAULT 0,
    alive       INTEGER NOT NULL DEFAULT 1,
    latency_ms  INTEGER,
    fails       INTEGER NOT NULL DEFAULT 0,
    added_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bin_cache (
    bin         TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    live        INTEGER,
    three_ds    INTEGER,
    source      TEXT,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    live        INTEGER NOT NULL DEFAULT 0,
    dead        INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'running',
    created_at  INTEGER NOT NULL
);
"""


class DB:
    def __init__(self, path: str = config.DB_PATH) -> None:
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        for url in config.GLOBAL_PROXIES:
            await self.add_proxy(url, owner_id=0, is_global=True, silent=True)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ── users ─────────────────────────────────────────
    async def get_user(self, user_id: int):
        async with self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None):
        now = int(time.time())
        status = "approved" if config.FREE_MODE else "pending"
        await self.conn.execute(
            """INSERT INTO users (user_id, username, first_name, status, role, created_at, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   username = excluded.username,
                   first_name = excluded.first_name,
                   last_seen = excluded.last_seen""",
            (user_id, username, first_name, status, "user", now, now),
        )
        await self.conn.commit()
        return await self.get_user(user_id)

    async def set_status(self, user_id: int, status: str) -> None:
        await self.conn.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
        await self.conn.commit()

    async def list_users(self, offset: int = 0, limit: int = 10):
        async with self.conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ) as cur:
            return list(await cur.fetchall())

    async def count_users(self) -> dict[str, int]:
        async with self.conn.execute("SELECT status, COUNT(*) c FROM users GROUP BY status") as cur:
            rows = await cur.fetchall()
        out = {"total": 0, "approved": 0, "pending": 0, "banned": 0}
        for row in rows:
            out["total"] += row["c"]
            if row["status"] in out:
                out[row["status"]] = row["c"]
        return out

    async def bump_user_stats(self, user_id: int, bins: int) -> None:
        await self.conn.execute(
            "UPDATE users SET checks = checks + 1, bins = bins + ? WHERE user_id = ?", (bins, user_id)
        )
        await self.conn.commit()

    # ── proxies ───────────────────────────────────────
    async def add_proxy(self, url: str, owner_id: int, is_global: bool, silent: bool = False) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO proxies (url, owner_id, is_global, added_at) VALUES (?, ?, ?, ?)",
                (url, owner_id, int(is_global), int(time.time())),
            )
            if not silent:
                await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def list_proxies(self, owner_id: int, is_global: bool):
        async with self.conn.execute(
            "SELECT * FROM proxies WHERE is_global = ? OR owner_id = ? ORDER BY is_global DESC, id",
            (int(is_global), owner_id),
        ) as cur:
            return list(await cur.fetchall())

    async def alive_proxies(self, owner_id: int) -> list[str]:
        async with self.conn.execute(
            "SELECT url FROM proxies WHERE alive = 1 AND (is_global = 1 OR owner_id = ?)",
            (owner_id,),
        ) as cur:
            return [r["url"] for r in await cur.fetchall()]

    async def update_proxy(self, proxy_id: int, alive: bool, latency_ms: int | None = None) -> None:
        await self.conn.execute(
            """UPDATE proxies SET alive = ?, latency_ms = ?,
               fails = CASE WHEN ? = 0 THEN fails + 1 ELSE 0 END
               WHERE id = ?""",
            (int(alive), latency_ms, int(alive), proxy_id),
        )
        await self.conn.commit()

    async def delete_proxy(self, proxy_id: int) -> None:
        await self.conn.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
        await self.conn.commit()

    async def clear_proxies(self, owner_id: int, only_user: bool) -> int:
        if only_user:
            cur = await self.conn.execute(
                "DELETE FROM proxies WHERE owner_id = ? AND is_global = 0", (owner_id,)
            )
        else:
            cur = await self.conn.execute(
                "DELETE FROM proxies WHERE is_global = 0 AND owner_id = ?", (owner_id,)
            )
        await self.conn.commit()
        return cur.rowcount

    # ── cache ─────────────────────────────────────────
    async def cache_get(self, bin_value: str) -> Optional[dict]:
        cutoff = int(time.time()) - config.CACHE_TTL_HOURS * 3600
        async with self.conn.execute(
            "SELECT * FROM bin_cache WHERE bin = ? AND updated_at > ?", (bin_value, cutoff)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return {
            "data": json.loads(row["payload"]),
            "live": None if row["live"] is None else bool(row["live"]),
            "three_ds": None if row["three_ds"] is None else bool(row["three_ds"]),
            "source": row["source"],
        }

    async def cache_set(self, bin_value: str, payload: dict, live: bool | None,
                        three_ds: bool | None, source: str) -> None:
        await self.conn.execute(
            """INSERT INTO bin_cache (bin, payload, live, three_ds, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(bin) DO UPDATE SET
                   payload = excluded.payload, live = excluded.live,
                   three_ds = excluded.three_ds, source = excluded.source,
                   updated_at = excluded.updated_at""",
            (bin_value, json.dumps(payload), None if live is None else int(live),
             None if three_ds is None else int(three_ds), source, int(time.time())),
        )
        await self.conn.commit()

    # ── jobs ──────────────────────────────────────────
    async def create_job(self, user_id: int, total: int) -> int:
        cur = await self.conn.execute(
            "INSERT INTO jobs (user_id, total, created_at) VALUES (?, ?, ?)",
            (user_id, total, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
        await self.conn.commit()

    async def global_stats(self) -> dict[str, int]:
        async with self.conn.execute(
            "SELECT COUNT(*) jobs, COALESCE(SUM(total),0) bins, COALESCE(SUM(live),0) live FROM jobs"
        ) as cur:
            row = await cur.fetchone()
        return {"jobs": row["jobs"], "bins": row["bins"], "live": row["live"]}