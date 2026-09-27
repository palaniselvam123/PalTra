"""Settings for the SMA(9,21) + ATR stop terminal.

Loaded from the environment / `backend/.env`. The process always defaults to
PAPER. LIVE is refused unless the UI sends an explicit confirmation and a
Groww access token is present.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groww_access_token: str = ""
    default_symbol: str = "KIRLOSFER"
    default_qty: int = 1000
    trading_mode: str = "PAPER"  # PAPER | LIVE — PAPER is the only safe boot default
    sma_database_url: str = "sqlite:///./sma_terminal.db"
    # Limit orders sit this far through the touch so they fill without
    # sweeping the book the way a raw market order can.
    market_protection_pct: float = 0.20


@lru_cache
def get_settings() -> Settings:
    return Settings()
