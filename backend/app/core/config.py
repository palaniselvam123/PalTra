from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    encryption_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./trading.db"
    default_mode: str = "paper"
    # Comma-separated. Next.js hops to 3001/3002/3003 when a port is taken, and
    # a mismatch here fails every request at the CORS preflight — with a browser
    # error that looks nothing like a config problem.
    frontend_origin: str = "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003"

    @property
    def frontend_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]
    account_capital: float = 100_000.0

    # Risk engine defaults (all overridable per-user via /settings/risk)
    risk_per_trade_pct: float = 1.0
    daily_max_loss_pct: float = 2.0
    max_trades_per_day: int = 5
    max_spread_pct: float = 0.15
    square_off_time_ist: str = "15:30"
    paper_slippage_pct: float = 0.05

    # Course coach — RAG over the user's own uploaded trading curriculum.
    # Kept in env rather than the encrypted vault (unlike the OpenAI key)
    # because this key authenticates to a service the user runs themselves; it
    # buys nothing and bills nothing. Advisory only: it has no veto and no
    # path to the order engine.
    course_rag_enabled: bool = True
    course_rag_url: str = ""
    course_rag_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
