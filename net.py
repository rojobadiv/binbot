from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiohttp

try:
    from aiohttp_socks import ProxyConnector
except ImportError:
    ProxyConnector = None  # type: ignore

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class Net:
    """Async HTTP client. One pooled session per proxy, created lazily."""

    def __init__(self, timeout: float = 12.0) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout, connect=6)
        self._sessions: dict[str, aiohttp.ClientSession] = {}
        self._lock = asyncio.Lock()

    async def _session(self, proxy: Optional[str]) -> aiohttp.ClientSession:
        key = proxy or "__direct__"
        session = self._sessions.get(key)
        if session and not session.closed:
            return session

        async with self._lock:
            session = self._sessions.get(key)
            if session and not session.closed:
                return session

            if proxy and ProxyConnector and proxy.startswith("socks"):
                connector: aiohttp.BaseConnector = ProxyConnector.from_url(proxy, limit=64)
            else:
                connector = aiohttp.TCPConnector(limit=128, ttl_dns_cache=600, ssl=False)

            session = aiohttp.ClientSession(
                connector=connector,
                timeout=self._timeout,
                headers={"User-Agent": UA, "Accept": "application/json, */*"},
            )
            self._sessions[key] = session
            return session

    async def get_json(self, url: str, proxy: Optional[str] = None,
                       retries: int = 2, headers: dict | None = None) -> Any | None:
        for attempt in range(retries + 1):
            try:
                session = await self._session(proxy)
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        try:
                            return await resp.json(content_type=None)
                        except Exception:
                            return None
                    if resp.status not in (429, 500, 502, 503, 504):
                        return None
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass
            await asyncio.sleep(0.35 * (attempt + 1))
        return None

    async def get_text(self, url: str, proxy: Optional[str] = None) -> str | None:
        try:
            session = await self._session(proxy)
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.text()
        except Exception:
            return None
        return None

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            if not session.closed:
                await session.close()
        self._sessions.clear()