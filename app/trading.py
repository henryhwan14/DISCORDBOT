"""Trading engine that coordinates between the market, account service and positions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal

from .account_client import AccountServiceProtocol
from .exceptions import InsufficientFundsError, PositionNotFoundError, QuantityTooLargeError
from .market import StockMarket
from .positions import Portfolio, PortfolioUpdate, PositionStore

Side = Literal["BUY", "SELL"]


@dataclass
class TradeResult:
    user_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    total: float
    balance: float
    portfolio: Portfolio
    realized_change: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        payload = {
            "user_id": self.user_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": round(self.quantity, 4),
            "price": round(self.price, 2),
            "total": round(self.total, 2),
            "balance": round(self.balance, 2),
            "portfolio": self.portfolio.to_dict(),
            "realized_change": round(self.realized_change, 2),
        }
        return payload


class TradeManager:
    """High level component that performs trades for users."""

    def __init__(
        self,
        market: StockMarket,
        positions: PositionStore,
        accounts: AccountServiceProtocol,
    ) -> None:
        self._market = market
        self._positions = positions
        self._accounts = accounts

    async def buy(self, user_id: str, symbol: str, quantity: float) -> TradeResult:
        if quantity <= 0:
            raise ValueError("Quantity must be positive")
        price = await self._market.get_price(symbol)
        total_cost = price * quantity
        balance = await self._accounts.get_balance(user_id)
        if balance.balance < total_cost:
            raise InsufficientFundsError(
                f"Insufficient funds: required {total_cost:.2f}, available {balance.balance:.2f}"
            )
        new_balance = await self._accounts.create_transaction(
            user_id,
            amount=-total_cost,
            description=f"BUY {quantity} {symbol.upper()} @ {price:.2f}",
        )
        update: PortfolioUpdate = await self._positions.apply_buy(user_id, symbol, quantity, price)
        return TradeResult(
            user_id=user_id,
            symbol=symbol.upper(),
            side="BUY",
            quantity=quantity,
            price=price,
            total=total_cost,
            balance=new_balance.balance,
            portfolio=update.portfolio,
            realized_change=update.realized_change,
        )

    async def sell(self, user_id: str, symbol: str, quantity: float) -> TradeResult:
        if quantity <= 0:
            raise ValueError("Quantity must be positive")
        position = await self._positions.get_position(user_id, symbol)
        if position is None:
            raise PositionNotFoundError(f"User has no position in {symbol.upper()}")
        if quantity > position.quantity:
            raise QuantityTooLargeError(
                f"Cannot sell {quantity} shares, current position holds {position.quantity}"
            )
        price = await self._market.get_price(symbol)
        proceeds = price * quantity
        new_balance = await self._accounts.create_transaction(
            user_id,
            amount=proceeds,
            description=f"SELL {quantity} {symbol.upper()} @ {price:.2f}",
        )
        update = await self._positions.apply_sell(user_id, symbol, quantity, price)
        return TradeResult(
            user_id=user_id,
            symbol=symbol.upper(),
            side="SELL",
            quantity=quantity,
            price=price,
            total=proceeds,
            balance=new_balance.balance,
            portfolio=update.portfolio,
            realized_change=update.realized_change,
        )

    async def get_portfolio(self, user_id: str) -> Portfolio:
        return await self._positions.get_portfolio(user_id)

