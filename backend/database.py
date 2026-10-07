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
        if "trade_symbols" not in names:
            conn.exec_driver_sql("ALTER TABLE bot_config ADD COLUMN trade_symbols TEXT DEFAULT ''")
            conn.exec_driver_sql(
                "UPDATE bot_config SET trade_symbols = symbol "
                "WHERE trade_symbols IS NULL OR trade_symbols = ''"
            )
        additions = {
            "use_vwap": "BOOLEAN DEFAULT 0",
            "use_volume": "BOOLEAN DEFAULT 0",
            "volume_min_ratio": "FLOAT DEFAULT 1",
            "use_density": "BOOLEAN DEFAULT 0",
            "density_min_pct": "FLOAT DEFAULT 50",
            "use_rsi": "BOOLEAN DEFAULT 0",
            "rsi_long_min": "FLOAT DEFAULT 40",
            "rsi_long_max": "FLOAT DEFAULT 70",
            "rsi_short_min": "FLOAT DEFAULT 30",
            "rsi_short_max": "FLOAT DEFAULT 60",
            "use_bollinger": "BOOLEAN DEFAULT 0",
            "bb_period": "INTEGER DEFAULT 20",
            "bb_std": "FLOAT DEFAULT 2",
            "bb_min_width_pct": "FLOAT DEFAULT 0.15",
            "bb_exit": "VARCHAR DEFAULT 'OFF'",
            "use_gap_long": "BOOLEAN DEFAULT 0",
            "gap_long_min": "FLOAT DEFAULT 0.02",
            "gap_long_max": "FLOAT DEFAULT 0.5",
            "use_gap_short": "BOOLEAN DEFAULT 0",
            "gap_short_min": "FLOAT DEFAULT -0.5",
            "gap_short_max": "FLOAT DEFAULT -0.02",
            "use_candle_dir": "BOOLEAN DEFAULT 0",
            "trade_count_reset_id": "INTEGER DEFAULT 0",
            "trade_count_reset_date": "VARCHAR",
            "candle_dir_count": "INTEGER DEFAULT 2",
            "candle_dir_rule": "VARCHAR DEFAULT 'CLOSES'",
            "use_gap_mode": "BOOLEAN DEFAULT 0",
            "gap_entry_long": "FLOAT DEFAULT 0.05",
            "gap_exit_long": "FLOAT DEFAULT 0.02",
            "gap_entry_short": "FLOAT DEFAULT -0.05",
            "gap_exit_short": "FLOAT DEFAULT -0.02",
            "gap_giveback_pct": "FLOAT DEFAULT 0",
            "gap_fade_confirm_sma": "BOOLEAN DEFAULT 0",
            "gap_fade_min_candles": "INTEGER DEFAULT 0",
            "gap_fade_intrabar": "BOOLEAN DEFAULT 0",
            "second_ticks": "BOOLEAN DEFAULT 1",
            "bot_name": "VARCHAR",
            "flip_orders": "BOOLEAN DEFAULT 0",
            "gap_entry_delay_min": "INTEGER DEFAULT 0",
            "gap_entry_window_min": "INTEGER DEFAULT 0",
            "entry_cutoff_time": "TEXT DEFAULT '15:00'",
            "stop_type": "TEXT DEFAULT 'ATR'",
            "gap_sl_mult": "FLOAT DEFAULT 1",
            "gap_tp_mult": "FLOAT DEFAULT 2",
            "gap_min_pct": "FLOAT DEFAULT 0.2",
            "tsl_sl_points": "FLOAT DEFAULT 20",
            "tsl_trail_points": "FLOAT DEFAULT 10",
            "tsl_target_points": "FLOAT DEFAULT 0",
            "stock_settings": "TEXT DEFAULT '{}'",
        }
        for column, decl in additions.items():
            if column not in names:
                conn.exec_driver_sql(f"ALTER TABLE bot_config ADD COLUMN {column} {decl}")
        if "max_trades_bumped" not in names:
            conn.exec_driver_sql("ALTER TABLE bot_config ADD COLUMN max_trades_bumped INTEGER DEFAULT 0")
        conn.exec_driver_sql(
            "UPDATE bot_config SET max_trades_per_day = 40, max_trades_bumped = 1 "
            "WHERE (max_trades_bumped IS NULL OR max_trades_bumped = 0) AND max_trades_per_day = 15"
        )
        conn.exec_driver_sql(
            "UPDATE bot_config SET max_trades_bumped = 1 "
            "WHERE max_trades_bumped IS NULL OR max_trades_bumped = 0"
        )


def _ensure_trade_log_columns(engine) -> None:
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(trade_log)").fetchall()
        if not rows:
            return
        names = {row[1] for row in rows}
        if "stop_active" not in names:
            conn.exec_driver_sql("ALTER TABLE trade_log ADD COLUMN stop_active BOOLEAN DEFAULT 1")
        if "flipped" not in names:
            conn.exec_driver_sql("ALTER TABLE trade_log ADD COLUMN flipped BOOLEAN DEFAULT 0")
        if "bot" not in names:
            conn.exec_driver_sql("ALTER TABLE trade_log ADD COLUMN bot INTEGER DEFAULT 1")
        if "run_id" not in names:
            conn.exec_driver_sql("ALTER TABLE trade_log ADD COLUMN run_id INTEGER")
        if "strategy" not in names:
            conn.exec_driver_sql("ALTER TABLE trade_log ADD COLUMN strategy TEXT")
        if "max_high" not in names:
            conn.exec_driver_sql("ALTER TABLE trade_log ADD COLUMN max_high FLOAT")
        if "max_low" not in names:
            conn.exec_driver_sql("ALTER TABLE trade_log ADD COLUMN max_low FLOAT")
        if "book_seq" not in names:
            conn.exec_driver_sql("ALTER TABLE trade_log ADD COLUMN book_seq INTEGER")
        # Number older trades inside their own book (mode, and run for replays).
        conn.exec_driver_sql(
            "UPDATE trade_log SET book_seq = ("
            " SELECT COUNT(*) FROM trade_log t2"
            " WHERE t2.mode = trade_log.mode AND IFNULL(t2.run_id, -1) = IFNULL(trade_log.run_id, -1)"
            " AND t2.id <= trade_log.id"
            ") WHERE book_seq IS NULL"
        )


def init_db() -> BotConfig:
    """Create tables and seed a single BotConfig row from settings."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_bot_config_columns(engine)
    _ensure_trade_log_columns(engine)
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
                trade_symbols=(settings.default_symbol or "KIRLOSFER").upper(),
                exchange="NSE",
                qty=int(settings.default_qty or 1000),
                sma_fast=9,
                sma_slow=21,
                atr_period=14,
                atr_multiplier=1.5,
                use_adx_filter=False,
                adx_threshold=20.0,
                max_daily_loss=5000.0,
                max_trades_per_day=40,
                max_trades_bumped=1,
                square_off_time="15:15",
                entry_cutoff_time="15:00",
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
