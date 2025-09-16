"""FastAPI application that exposes the trading service and serves the dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .account_client import create_account_service
from .config import settings
from .exceptions import InsufficientFundsError, PositionNotFoundError, QuantityTooLargeError
from .market import StockMarket
from .positions import PositionStore
from .trading import TradeManager

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"


class TradeRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    quantity: float = Field(..., gt=0)
    side: Literal["BUY", "SELL"]


class TradeResponse(BaseModel):
    user_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    price: float
    total: float
    balance: float
    portfolio: dict
    realized_change: float


backend_settings = settings.backend
market = StockMarket(
    backend_settings.market_symbols,
    update_interval=backend_settings.market_update_interval,
    volatility=backend_settings.market_volatility,
    seed=backend_settings.market_seed,
)
position_store = PositionStore(backend_settings.positions_file)
account_service = create_account_service(
    backend_settings.account_service_base_url,
    backend_settings.account_service_api_key,
    backend_settings.account_service_timeout,
)
trade_manager = TradeManager(market=market, positions=position_store, accounts=account_service)


async def close_account_service() -> None:
    close = getattr(account_service, "aclose", None)
    if close:
        await close()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: D401  (FastAPI lifespan signature)
    """Manage background tasks for the FastAPI application."""

    market.start()
    try:
        yield
    finally:
        await market.stop()
        await close_account_service()


app = FastAPI(title="Discord Trading Bot Backend", lifespan=lifespan)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/stocks")
async def list_stocks():
    return await market.list_quotes()


@app.get("/api/stocks/{symbol}")
async def get_stock(symbol: str):
    try:
        return await market.get_quote(symbol)
    except KeyError:
        raise HTTPException(status_code=404, detail="Symbol not found") from None


@app.get("/api/users/{user_id}/portfolio")
async def get_portfolio(user_id: str):
    portfolio = await trade_manager.get_portfolio(user_id)
    return portfolio.to_dict()


@app.post("/api/trades", response_model=TradeResponse)
async def execute_trade(request: TradeRequest):
    try:
        if request.side == "BUY":
            result = await trade_manager.buy(request.user_id, request.symbol, request.quantity)
        else:
            result = await trade_manager.sell(request.user_id, request.symbol, request.quantity)
    except KeyError:
        raise HTTPException(status_code=404, detail="Symbol not found") from None
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PositionNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QuantityTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result.to_dict()


@app.websocket("/ws/quotes")
async def quotes_ws(websocket: WebSocket):
    await websocket.accept()
    queue = market.subscribe()
    try:
        snapshot = await market.list_quotes()
        await websocket.send_json({"type": "snapshot", "data": snapshot})
        while True:
            updates = await queue.get()
            await websocket.send_json({"type": "update", "data": updates})
    except WebSocketDisconnect:
        pass
    finally:
        market.unsubscribe(queue)


__all__ = [
    "app",
    "market",
    "trade_manager",
]

