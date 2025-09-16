"""Market data simulator that generates live prices for a small set of symbols."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import random
from typing import Dict, Iterable, List, Optional


@dataclass
class StockQuote:
    """Represents the latest quote for a particular stock symbol."""

    symbol: str
    price: float
    open: float
    high: float
    low: float
    previous_close: float
    change: float
    change_percent: float
    volume: int
    updated_at: datetime

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data["updated_at"] = self.updated_at.isoformat()
        return data


class StockMarket:
    """In-memory market that simulates price updates and notifies listeners."""

    def __init__(
        self,
        symbols: Iterable[str],
        *,
        update_interval: float = 2.0,
        volatility: float = 0.015,
        seed: Optional[int] = None,
    ) -> None:
        self._rng = random.Random(seed)
        self._quotes: Dict[str, StockQuote] = {
            symbol.upper(): self._create_initial_quote(symbol.upper())
            for symbol in symbols
        }
        self._update_interval = update_interval
        self._volatility = volatility
        self._listeners: List[asyncio.Queue[List[Dict[str, object]]]] = []
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task[None]] = None

    def _create_initial_quote(self, symbol: str) -> StockQuote:
        base_price = self._rng.uniform(15.0, 400.0)
        now = datetime.now(tz=timezone.utc)
        return StockQuote(
            symbol=symbol,
            price=round(base_price, 2),
            open=round(base_price, 2),
            high=round(base_price, 2),
            low=round(base_price, 2),
            previous_close=round(base_price, 2),
            change=0.0,
            change_percent=0.0,
            volume=self._rng.randint(1_000, 10_000),
            updated_at=now,
        )

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._update_interval)
            await self._tick()

    async def _tick(self) -> None:
        updates: List[Dict[str, object]] = []
        async with self._lock:
            for quote in self._quotes.values():
                updates.append(self._update_quote(quote))
        await self._broadcast(updates)

    def _update_quote(self, quote: StockQuote) -> Dict[str, object]:
        pct_move = self._rng.normalvariate(0, self._volatility)
        new_price = max(0.25, quote.price * (1 + pct_move))
        new_price = round(new_price, 2)
        quote.high = max(quote.high, new_price)
        quote.low = min(quote.low, new_price)
        quote.volume += self._rng.randint(0, 1_500)
        quote.previous_close = quote.price
        quote.price = new_price
        quote.change = round(quote.price - quote.previous_close, 2)
        if quote.previous_close:
            quote.change_percent = round((quote.change / quote.previous_close) * 100, 3)
        else:
            quote.change_percent = 0.0
        quote.updated_at = datetime.now(tz=timezone.utc)
        return quote.to_dict()

    async def _broadcast(self, payload: List[Dict[str, object]]) -> None:
        if not payload or not self._listeners:
            return
        for queue in list(self._listeners):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # If the listener is too slow we simply drop updates to avoid blocking the loop.
                pass

    def subscribe(self) -> asyncio.Queue[List[Dict[str, object]]]:
        queue: asyncio.Queue[List[Dict[str, object]]] = asyncio.Queue()
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[List[Dict[str, object]]]) -> None:
        if queue in self._listeners:
            self._listeners.remove(queue)

    async def list_quotes(self) -> List[Dict[str, object]]:
        async with self._lock:
            return [quote.to_dict() for quote in self._quotes.values()]

    async def get_quote(self, symbol: str) -> Dict[str, object]:
        key = symbol.upper()
        async with self._lock:
            if key not in self._quotes:
                raise KeyError(f"Unknown symbol: {symbol}")
            return self._quotes[key].to_dict()

    async def get_price(self, symbol: str) -> float:
        key = symbol.upper()
        async with self._lock:
            if key not in self._quotes:
                raise KeyError(f"Unknown symbol: {symbol}")
            return self._quotes[key].price


# Circular import guard
import contextlib  # noqa: E402  (placed at bottom to avoid circular import issues)

