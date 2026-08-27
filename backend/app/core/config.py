from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    encryption_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./trading.db"
    default_mode: str = "paper"
    frontend_origin: str = "http://localhost:3000"
    account_capital: float = 100_000.0

    # Risk engine defaults (all overridable per-user via /settings/risk)
    risk_per_trade_pct: float = 1.0
    daily_max_loss_pct: float = 2.0
    max_trades_per_day: int = 5
    max_spread_pct: float = 0.15
    square_off_time_ist: str = "15:30"
    paper_slippage_pct: float = 0.05


@lru_cache
def get_settings() -> Settings:
    return Settings()
