from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import config
from providers import BinLookup


@dataclass
class BinResult:
    bin: str
    data: dict = field(default_factory=dict)
    live: Optional[bool] = None
    three_ds: Optional[bool] = None
    error: Optional[str] = None
    cached: bool = False

    @property
    def status(self) -> str:
        if self.error:
            return "ERROR"
        if self.live is True:
            return "LIVE"
        if self.live is False:
            return "DEAD"
        return "UNKNOWN"


@dataclass
class Stats:
    total: int = 0
    done: int = 0
    live: int = 0
    dead: int = 0
    unknown: int = 0
    errors: int = 0
    tds_yes: int = 0
    tds_no: int = 0
    tds_unknown: int = 0
    cached: int = 0
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    _rate: float = 0.0

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.done)

    @property
    def rate(self) -> float:
        if self.done == 0:
            return 0.0
        instant = self.done / max(0.001, self.elapsed)
        self._rate = instant if self._rate == 0 else (self._rate * 0.7 + instant * 0.3)
        return self._rate

    @property
    def eta(self) -> Optional[float]:
        r = self.rate
        return self.remaining / r if r > 0 else None

    @property
    def percent(self) -> float:
        return (self.done / self.total * 100) if self.total else 0.0


class ProxyPool:
    """Round-robin rotation over the alive proxy list."""

    def __init__(self, proxies: list[str]) -> None:
        self.proxies = list(dict.fromkeys(proxies))
        self._index = 0

    def __bool__(self) -> bool:
        return bool(self.proxies)

    def next(self) -> Optional[str]:
        if not self.proxies:
            return None
        proxy = self.proxies[self._index % len(self.proxies)]
        self._index += 1
        return proxy


ProgressCb = Callable[[Stats], Awaitable[None]]


class BulkRunner:
    def __init__(self, lookup: BinLookup, bins: list[str],
                 concurrency: int = config.DEFAULT_CONCURRENCY,
                 proxies: list[str] | None = None) -> None:
        self.lookup = lookup
        self.bins = bins
        self.concurrency = max(1, min(concurrency, config.MAX_CONCURRENCY))
        self.sem = asyncio.Semaphore(self.concurrency)
        self.pool = ProxyPool(proxies or [])
        self.stats = Stats(total=len(bins))
        self.results: list[BinResult] = []
        self.cancelled = False

    async def _one(self, bin_value: str) -> None:
        async with self.sem:
            if self.cancelled:
                return
            proxy = self.pool.next()
            result = await self.lookup.check(bin_value, proxy=proxy)
            self.results.append(result)

            s = self.stats
            s.done += 1
            if result.cached:
                s.cached += 1
            if result.error:
                s.errors += 1
            elif result.live is True:
                s.live += 1
            elif result.live is False:
                s.dead += 1
            else:
                s.unknown += 1

            if result.three_ds is True:
                s.tds_yes += 1
            elif result.three_ds is False:
                s.tds_no += 1
            else:
                s.tds_unknown += 1

    async def run(self, on_progress: ProgressCb | None = None,
                  interval: float = config.PROGRESS_INTERVAL) -> Stats:
        async def ticker() -> None:
            while True:
                await asyncio.sleep(interval)
                if on_progress:
                    await on_progress(self.stats)

        tick = asyncio.create_task(ticker())
        try:
            await asyncio.gather(*[self._one(b) for b in self.bins])
        finally:
            tick.cancel()
            try:
                await tick
            except asyncio.CancelledError:
                pass
            self.stats.finished = time.time()
            if on_progress:
                await on_progress(self.stats)
        return self.stats

    def sorted_results(self) -> list[BinResult]:
        return sorted(
            self.results,
            key=lambda r: (0 if r.live else 1, 0 if r.three_ds else 1, r.bin),
        )