"""Discord bot implementation that communicates with the backend trading service."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord.ext import commands
import httpx

from .config import settings

logger = logging.getLogger(__name__)


class TradingDiscordBot(commands.Bot):
    """Discord bot that forwards commands to the FastAPI backend."""

    def __init__(self, *, backend_url: str, command_prefix: str) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.backend_url = backend_url.rstrip("/")
        self.http: Optional[httpx.AsyncClient] = None

    async def setup_hook(self) -> None:
        self.http = httpx.AsyncClient(base_url=self.backend_url, timeout=10.0)
        await super().setup_hook()

    async def close(self) -> None:  # noqa: D401 (discord.py hook)
        if self.http:
            await self.http.aclose()
        await super().close()

    async def fetch_json(self, method: str, path: str, **kwargs):
        if not self.http:
            raise RuntimeError("HTTP client not initialized")
        response = await self.http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Request failed with {response.status_code}", request=response.request, response=response
            )
        return response.json()

    async def get_quote(self, symbol: str) -> dict:
        return await self.fetch_json("GET", f"/api/stocks/{symbol}")

    async def list_quotes(self) -> list:
        return await self.fetch_json("GET", "/api/stocks")

    async def get_portfolio(self, user_id: int) -> dict:
        return await self.fetch_json("GET", f"/api/users/{user_id}/portfolio")

    async def execute_trade(self, user_id: int, symbol: str, side: str, quantity: float) -> dict:
        payload = {"user_id": str(user_id), "symbol": symbol, "side": side, "quantity": quantity}
        return await self.fetch_json("POST", "/api/trades", json=payload)


bot_settings = settings.discord
bot = TradingDiscordBot(backend_url=bot_settings.backend_base_url, command_prefix=bot_settings.command_prefix)


@bot.event
async def on_ready():
    logger.info("Logged in as %s", bot.user)


def _format_quote(data: dict) -> str:
    return (
        f"**{data['symbol']}**\n"
        f"가격: {data['price']:.2f}\n"
        f"변동: {data['change']:+.2f} ({data['change_percent']:+.2f}%)\n"
        f"고가/저가: {data['high']:.2f} / {data['low']:.2f}\n"
        f"거래량: {data['volume']}"
    )


@bot.command(name="market", help="현재 시뮬레이션 중인 모든 종목의 요약을 표시합니다.")
async def market_command(ctx: commands.Context):
    try:
        quotes = await bot.list_quotes()
    except httpx.HTTPStatusError as exc:
        await ctx.send(f"시장 데이터를 불러오지 못했습니다: {exc.response.text}")
        return
    message = "\n\n".join(_format_quote(quote) for quote in quotes)
    await ctx.send(message)


@bot.command(name="price", help="특정 종목의 현재가를 조회합니다. 사용법: !price SYMBOL")
async def price_command(ctx: commands.Context, symbol: str):
    try:
        quote = await bot.get_quote(symbol)
    except httpx.HTTPStatusError:
        await ctx.send("알 수 없는 종목이거나 서버 오류가 발생했습니다.")
        return
    await ctx.send(_format_quote(quote))


def _parse_quantity(raw: str) -> Optional[float]:
    try:
        quantity = float(raw)
        if quantity <= 0:
            return None
        return quantity
    except ValueError:
        return None


async def _handle_trade(
    ctx: commands.Context,
    symbol: str,
    quantity: str,
    *,
    side: str,
) -> None:
    qty = _parse_quantity(quantity)
    if qty is None:
        await ctx.send("수량은 양의 숫자로 입력해주세요.")
        return
    try:
        result = await bot.execute_trade(ctx.author.id, symbol, side, qty)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.json().get("detail") if exc.response.headers.get("content-type", "").startswith("application/json") else exc.response.text
        await ctx.send(f"거래가 거부되었습니다: {detail}")
        return
    portfolio = result.get("portfolio", {})
    positions = portfolio.get("positions", [])
    holdings = ", ".join(f"{p['symbol']} {p['quantity']}주" for p in positions) or "보유 종목 없음"
    message = (
        f"{ctx.author.mention}님의 {side} 주문이 체결되었습니다!\n"
        f"{result['symbol']} {result['quantity']}주 @ {result['price']:.2f} → 총 {result['total']:.2f}\n"
        f"계좌 잔액: {result['balance']:.2f}\n"
        f"실현 손익 변화: {result.get('realized_change', 0.0):+.2f}\n"
        f"보유 종목: {holdings}"
    )
    await ctx.send(message)


@bot.command(name="buy", help="지정한 종목을 매수합니다. 사용법: !buy SYMBOL 수량")
async def buy_command(ctx: commands.Context, symbol: str, quantity: str):
    await _handle_trade(ctx, symbol, quantity, side="BUY")


@bot.command(name="sell", help="지정한 종목을 매도합니다. 사용법: !sell SYMBOL 수량")
async def sell_command(ctx: commands.Context, symbol: str, quantity: str):
    await _handle_trade(ctx, symbol, quantity, side="SELL")


@bot.command(name="portfolio", help="현재 포트폴리오와 손익을 확인합니다.")
async def portfolio_command(ctx: commands.Context):
    try:
        portfolio = await bot.get_portfolio(ctx.author.id)
    except httpx.HTTPStatusError as exc:
        await ctx.send(f"포트폴리오를 불러오지 못했습니다: {exc.response.text}")
        return
    positions = portfolio.get("positions", [])
    if not positions:
        await ctx.send("현재 보유한 종목이 없습니다.")
        return
    lines = [
        f"{pos['symbol']}: {pos['quantity']}주 (평단 {pos['average_price']:.2f})" for pos in positions
    ]
    lines.append(f"누적 실현 손익: {portfolio.get('realized_pnl', 0.0):+.2f}")
    await ctx.send("\n".join(lines))


def run_bot() -> None:
    token = bot_settings.token
    if not token:
        raise RuntimeError("DISCORD_TOKEN 환경 변수가 설정되어야 합니다.")
    logging.basicConfig(level=logging.INFO)
    bot.run(token)


if __name__ == "__main__":
    run_bot()

