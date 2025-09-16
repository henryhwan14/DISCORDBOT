"""Configuration utilities for the Discord trading bot project."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

load_dotenv()


def _parse_symbol_list(value: Optional[str], default: Iterable[str]) -> List[str]:
    if not value:
        return [symbol.upper() for symbol in default]
    return [item.strip().upper() for item in value.split(",") if item.strip()]


class BackendSettings(BaseModel):
    """Settings that configure the FastAPI backend service."""

    account_service_base_url: Optional[str] = Field(
        default=None,
        description="Base URL for the external account service managed by another bot.",
    )
    account_service_api_key: Optional[str] = Field(
        default=None, description="Optional API key passed to the external account service."
    )
    account_service_timeout: float = Field(
        default=5.0,
        gt=0.0,
        description="Timeout for HTTP requests to the external account service.",
    )
    market_symbols: List[str] = Field(
        default_factory=lambda: ["ACME", "BNB", "CRYPTO", "DXL"],
        description="Symbols that will be simulated by the in-memory market engine.",
    )
    market_update_interval: float = Field(
        default=2.0,
        gt=0.0,
        description="Interval in seconds between market price updates.",
    )
    market_volatility: float = Field(
        default=0.015,
        gt=0.0,
        description="Volatility factor that controls the magnitude of simulated price swings.",
    )
    market_seed: Optional[int] = Field(
        default=None,
        description="Optional random seed applied to the market simulator for reproducibility.",
    )
    positions_file: Path = Field(
        default=Path("data/positions.json"),
        description="File used to persist user positions maintained by this service.",
    )
    backend_host: str = Field(default="0.0.0.0", description="Host interface for the FastAPI app.")
    backend_port: int = Field(default=8000, ge=1, le=65535, description="Port for the FastAPI app.")

    @classmethod
    def from_env(cls) -> "BackendSettings":
        import os

        data = {
            "account_service_base_url": os.getenv("ACCOUNT_SERVICE_BASE_URL"),
            "account_service_api_key": os.getenv("ACCOUNT_SERVICE_API_KEY"),
        }
        timeout = os.getenv("ACCOUNT_SERVICE_TIMEOUT")
        if timeout:
            data["account_service_timeout"] = float(timeout)

        symbols = os.getenv("MARKET_SYMBOLS")
        if symbols:
            data["market_symbols"] = _parse_symbol_list(symbols, default=[])

        interval = os.getenv("MARKET_UPDATE_INTERVAL")
        if interval:
            data["market_update_interval"] = float(interval)

        volatility = os.getenv("MARKET_VOLATILITY")
        if volatility:
            data["market_volatility"] = float(volatility)

        seed = os.getenv("MARKET_RANDOM_SEED")
        if seed:
            data["market_seed"] = int(seed)

        positions_file = os.getenv("POSITIONS_FILE")
        if positions_file:
            data["positions_file"] = Path(positions_file)

        host = os.getenv("BACKEND_HOST")
        if host:
            data["backend_host"] = host

        port = os.getenv("BACKEND_PORT")
        if port:
            data["backend_port"] = int(port)

        try:
            return cls(**data)
        except ValidationError as exc:
            raise RuntimeError(f"Invalid backend configuration: {exc}") from exc


class DiscordSettings(BaseModel):
    """Settings dedicated to the Discord bot process."""

    token: Optional[str] = Field(
        default=None,
        alias="DISCORD_TOKEN",
        description="Token of the Discord bot that executes trading commands.",
    )
    backend_base_url: str = Field(
        default="http://localhost:8000",
        alias="BACKEND_BASE_URL",
        description="Base URL of the backend service that exposes market and trading endpoints.",
    )
    command_prefix: str = Field(
        default="!",
        alias="COMMAND_PREFIX",
        description="Prefix used to invoke commands inside Discord.",
        min_length=1,
    )

    @classmethod
    def from_env(cls) -> "DiscordSettings":
        import os

        raw = {key: os.getenv(key) for key in ["DISCORD_TOKEN", "BACKEND_BASE_URL", "COMMAND_PREFIX"]}
        try:
            return cls(**raw)
        except ValidationError as exc:
            raise RuntimeError(f"Invalid Discord bot configuration: {exc}") from exc


@dataclass(frozen=True)
class Settings:
    """Container that groups Discord and backend settings together."""

    backend: BackendSettings
    discord: DiscordSettings

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from the current environment and optional `.env` file."""

        backend = BackendSettings.from_env()
        discord = DiscordSettings.from_env()
        return cls(backend=backend, discord=discord)


settings = Settings.load()
"""Singleton-like settings object that modules can import directly."""

