"""Persistence layer that stores user positions managed by the trading service."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Position:
    symbol: str
    quantity: float
    average_price: float

    def to_dict(self) -> Dict[str, float]:
        data = asdict(self)
        data["quantity"] = round(data["quantity"], 4)
        data["average_price"] = round(data["average_price"], 2)
        return data


@dataclass
class Portfolio:
    positions: List[Position]
    realized_pnl: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "positions": [position.to_dict() for position in self.positions],
            "realized_pnl": round(self.realized_pnl, 2),
        }


@dataclass
class PortfolioUpdate:
    """Return type for store mutations."""

    portfolio: Portfolio
    realized_change: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        payload = self.portfolio.to_dict()
        payload["realized_change"] = round(self.realized_change, 2)
        return payload


class PositionStore:
    """Stores positions in a JSON file with an asyncio lock for safe concurrent access."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._data: Dict[str, Dict[str, object]] = {}
        self._ensure_file()
        self._load()

    def _ensure_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text(json.dumps({}, indent=2))

    def _load(self) -> None:
        try:
            self._data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def _portfolio_from_record(self, record: Dict[str, object]) -> Portfolio:
        positions = [
            Position(symbol=symbol, quantity=values["quantity"], average_price=values["average_price"])
            for symbol, values in record.get("positions", {}).items()
        ]
        realized = float(record.get("realized_pnl", 0.0))
        return Portfolio(positions=positions, realized_pnl=realized)

    async def get_portfolio(self, user_id: str) -> Portfolio:
        async with self._lock:
            record = self._data.get(user_id, {"positions": {}, "realized_pnl": 0.0})
            return self._portfolio_from_record(record)

    async def get_position(self, user_id: str, symbol: str) -> Optional[Position]:
        portfolio = await self.get_portfolio(user_id)
        for position in portfolio.positions:
            if position.symbol == symbol.upper():
                return position
        return None

    async def apply_buy(
        self, user_id: str, symbol: str, quantity: float, price: float
    ) -> PortfolioUpdate:
        async with self._lock:
            record = self._data.setdefault(
                user_id, {"positions": {}, "realized_pnl": 0.0}
            )
            positions = record.setdefault("positions", {})
            symbol = symbol.upper()
            entry = positions.get(symbol)
            if entry:
                current_qty = entry["quantity"]
                current_avg = entry["average_price"]
                new_qty = current_qty + quantity
                if new_qty <= 0:
                    positions.pop(symbol, None)
                else:
                    total_cost = current_qty * current_avg + quantity * price
                    entry["quantity"] = new_qty
                    entry["average_price"] = total_cost / new_qty
            else:
                positions[symbol] = {"quantity": quantity, "average_price": price}
            self._path.write_text(json.dumps(self._data, indent=2, sort_keys=True))
            portfolio = self._portfolio_from_record(record)
        return PortfolioUpdate(portfolio=portfolio, realized_change=0.0)

    async def apply_sell(
        self, user_id: str, symbol: str, quantity: float, price: float
    ) -> PortfolioUpdate:
        async with self._lock:
            record = self._data.setdefault(
                user_id, {"positions": {}, "realized_pnl": 0.0}
            )
            positions = record.setdefault("positions", {})
            symbol = symbol.upper()
            entry = positions.get(symbol)
            if not entry:
                raise KeyError(symbol)
            current_qty = entry["quantity"]
            if quantity > current_qty:
                raise ValueError("Quantity exceeds current position size")
            entry["quantity"] = current_qty - quantity
            realized = (price - entry["average_price"]) * quantity
            record["realized_pnl"] = record.get("realized_pnl", 0.0) + realized
            if entry["quantity"] <= 0:
                positions.pop(symbol, None)
            self._path.write_text(json.dumps(self._data, indent=2, sort_keys=True))
            portfolio = self._portfolio_from_record(record)
        return PortfolioUpdate(portfolio=portfolio, realized_change=realized)

