"""SQLite session for the SMA terminal. Separate file from the ORB desk DB."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings
from models import Base, BotConfig

_engine = None
_Session: sessionmaker[Session] | None = None


def reset_engine() -> None:
    """Drop the cached engine so tests can point at a fresh database."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None
    get_settings.cache_clear()


def database_url() -> str:
    """Persist the terminal on the Fly volume when that volume is mounted."""
    configured = os.environ.get("SMA_DATABASE_URL")
    if configured:
        return configured
    if os.path.isfile("/data/trading.db"):
        return "sqlite:////data/sma_terminal.db"
    return get_settings().sma_database_url


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = database_url()
        _engine = create_engine(url, connect_args={"check_same_thread": False})
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _Session is not None
    return _Session


def _ensure_bot_config_columns(engine) -> None:
    """create_all does not add a column to a table that already exists."""
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(bot_config)").fetchall()
        if not rows:
            return
        names = {row[1] for row in rows}
        if "use_stop" not in names:
            conn.exec_driver_sql("ALTER TABLE bot_config ADD COLUMN use_stop BOOLEAN DEFAULT 1")


def init_db() -> BotConfig:
    """Create tables and seed a single BotConfig row from settings."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_bot_config_columns(engine)
    SessionLocal = session_factory()
    settings = get_settings()
    with SessionLocal() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            mode = (settings.trading_mode or "PAPER").upper()
            if mode not in ("PAPER", "LIVE"):
                mode = "PAPER"
            # Never seed LIVE just because the env var says so — boot PAPER.
            # LIVE is an explicit runtime confirmation.
            row = BotConfig(
                id=1,
                symbol=(settings.default_symbol or "KIRLOSFER").upper(),
                exchange="NSE",
                qty=int(settings.default_qty or 1000),
                sma_fast=9,
                sma_slow=21,
                atr_period=14,
                atr_multiplier=1.5,
                use_adx_filter=False,
                adx_threshold=20.0,
                max_daily_loss=5000.0,
                max_trades_per_day=15,
                square_off_time="15:15",
                trading_mode="PAPER",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        elif (row.trading_mode or "").upper() == "LIVE" and not settings.groww_access_token:
            # A restart must not resume live routing without a token.
            row.trading_mode = "PAPER"
            db.commit()
            db.refresh(row)
        return row
