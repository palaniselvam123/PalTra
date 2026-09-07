from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Boolean, Text, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


class BrokerCredential(Base):
    __tablename__ = "broker_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker: Mapped[str] = mapped_column(String, unique=True)  # groww | zerodha | angelone
    api_key_encrypted: Mapped[str] = mapped_column(String)
    api_secret_encrypted: Mapped[str] = mapped_column(String)
    totp_secret_encrypted: Mapped[str] = mapped_column(String)
    access_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    token_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class AiCredential(Base):
    """LLM provider key, encrypted at rest with the same Fernet vault the
    broker credentials use. Kept in its own table rather than alongside the
    broker rows because this key buys analysis, not market access — sharing a
    table would imply the AI key can reach the broker, which it never does.
    """

    __tablename__ = "ai_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String, unique=True)  # openai
    api_key_encrypted: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class AiConfig(Base):
    """Singleton row (id=1) describing how the expert is used. Separate from
    the credential so deleting the key does not silently reset the gate.
    """

    __tablename__ = "ai_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String, default="gpt-5")
    web_search_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    gate_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    min_conviction: Mapped[int] = mapped_column(Integer, default=60)
    require_agreement: Mapped[bool] = mapped_column(Boolean, default=True)
    cache_ttl_sec: Mapped[int] = mapped_column(Integer, default=900)


class AiAnalysis(Base):
    """Every expert view is persisted — on-demand ones and the ones the bot
    gate consulted alike. Without this the gate is unauditable: you could see
    that an entry was skipped but never why.
    """

    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String)
    stance: Mapped[str] = mapped_column(String)  # BULLISH | BEARISH | NEUTRAL
    conviction: Mapped[int] = mapped_column(Integer, default=0)
    sentiment_label: Mapped[str] = mapped_column(String, default="NEUTRAL")
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)
    thesis: Mapped[str] = mapped_column(Text, default="")
    catalysts: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    risks: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    invalidation: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[str] = mapped_column(Text, default="[]")  # JSON array of {title,url}
    recency_note: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String, default="")
    web_search_used: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    requested_by: Mapped[str] = mapped_column(String, default="USER")  # USER | BOT_GATE | PREFETCH
    gate_side: Mapped[str | None] = mapped_column(String, nullable=True)  # side the bot wanted
    gate_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gate_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_capital: Mapped[float] = mapped_column(Float, default=100_000.0)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=1.0)
    daily_max_loss_pct: Mapped[float] = mapped_column(Float, default=2.0)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=5)
    max_spread_pct: Mapped[float] = mapped_column(Float, default=0.15)
    daily_profit_target_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_leverage: Mapped[float] = mapped_column(Float, default=5.0)
    min_edge_multiple: Mapped[float] = mapped_column(Float, default=1.5)
    square_off_time_ist: Mapped[str] = mapped_column(String, default="15:30")


class DailyRiskRecord(Base):
    __tablename__ = "daily_risk_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_date: Mapped[str] = mapped_column(String, unique=True)
    trades_taken: Mapped[int] = mapped_column(Integer, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_reason: Mapped[str] = mapped_column(String, default="")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String)  # paper | live
    symbol: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)  # BUY | SELL
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="OPEN")  # OPEN | CLOSED | CANCELLED
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy: Mapped[str] = mapped_column(String, default="ORB")
    # Reporting columns. Rows written before this schema read back as the
    # defaults below, so the reports layer must not present them as measured.
    source: Mapped[str] = mapped_column(String, default="MANUAL")  # MANUAL | BOT
    # Which virtual wallet this trade belongs to. AUTO is the original
    # dashboard/bot account; MANUAL is the separate discretionary desk,
    # which has its own capital, balance and reports.
    account: Mapped[str] = mapped_column(String, default="AUTO")  # AUTO | MANUAL
    # Which price series this position references. A simulated series does
    # not survive a restart, so a position opened against one cannot be
    # honestly closed against the next run's prices.
    feed_source: Mapped[str] = mapped_column(String, default="unknown")  # simulated | live | unknown
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_charges: Mapped[float] = mapped_column(Float, default=0.0)
    exit_charges: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class StrategyLog(Base):
    __tablename__ = "strategy_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String, default="INFO")
    message: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class WatchlistSymbol(Base):
    """A symbol the user searched for and added to live streaming, beyond the
    bot's fixed 20-name strategy universe. Persisted so an added symbol is
    still there after a restart — otherwise every search would have to be
    redone each session.
    """

    __tablename__ = "watchlist_symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, unique=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)



class ScannerConfig(Base):
    """Singleton (id=1) holding the scanner's strategy parameters.

    Separate from the ORB bot's StrategyConfig on purpose: the scanner only
    watches and alerts, it never places an order, so sharing a config would
    imply a coupling that must not exist.
    """

    __tablename__ = "scanner_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timeframe: Mapped[str] = mapped_column(String, default="5m")
    fast_period: Mapped[int] = mapped_column(Integer, default=9)
    fast_type: Mapped[str] = mapped_column(String, default="EMA")   # EMA | SMA
    slow_period: Mapped[int] = mapped_column(Integer, default=21)
    slow_type: Mapped[str] = mapped_column(String, default="EMA")
    signal_type: Mapped[str] = mapped_column(String, default="BOTH")  # GOLDEN_CROSS | DEATH_CROSS | BOTH
    trend_filter: Mapped[bool] = mapped_column(Boolean, default=True)
    trend_period: Mapped[int] = mapped_column(Integer, default=50)
    volume_filter: Mapped[bool] = mapped_column(Boolean, default=True)
    volume_multiplier: Mapped[float] = mapped_column(Float, default=1.5)
    volume_lookback: Mapped[int] = mapped_column(Integer, default=20)
    # Require a supporting candlestick pattern on the signal bar. A crossover
    # alone is a line-cross; a crossover confirmed by a reversal candle is a
    # line-cross the bar itself agrees with.
    pattern_filter: Mapped[bool] = mapped_column(Boolean, default=False)
    # How many recent bars may supply the confirming candle. Requiring it on
    # the signal bar exactly rejected ~96% of crossovers on real data —
    # technically correct, practically useless. Traders mean "a reversal
    # candle showed up recently", which is what a small window expresses.
    pattern_lookback: Mapped[int] = mapped_column(Integer, default=3)
    # Price band, in rupees. 0 means no limit on that side. A 1 lakh account
    # cannot meaningfully trade a 13,000-rupee stock, so scanning it only
    # produces alerts that cannot be acted on.
    # Trend-strength gate. A moving-average crossover is a trend-following
    # signal; in a range it sells the dip and buys the bounce (whipsaw).
    # ADX below the threshold means there is no trend to follow.
    adx_filter: Mapped[bool] = mapped_column(Boolean, default=True)
    adx_threshold: Mapped[float] = mapped_column(Float, default=20.0)
    rsi_filter: Mapped[bool] = mapped_column(Boolean, default=True)
    rsi_overbought: Mapped[float] = mapped_column(Float, default=70.0)
    min_price: Mapped[float] = mapped_column(Float, default=0.0)
    max_price: Mapped[float] = mapped_column(Float, default=0.0)
    # Judge the FORMING bar instead of waiting for it to close. Fires the
    # moment a cross happens, at the cost that a signal can vanish if price
    # crosses back before the bar completes.
    intrabar: Mapped[bool] = mapped_column(Boolean, default=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)
    once_per_session: Mapped[bool] = mapped_column(Boolean, default=True)
    universe: Mapped[str] = mapped_column(String, default="GAINERS")  # WATCHLIST | CORE | CUSTOM | GAINERS
    custom_symbols: Mapped[str] = mapped_column(Text, default="")       # comma-separated when CUSTOM


class ScannerSignal(Base):
    """Every signal the engine fires, with the numbers behind it and what
    happened to the alert. Persisted so a failed send is visible rather than
    vanishing, and so the log survives a restart.
    """

    __tablename__ = "scanner_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    timeframe: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)  # BUY | SELL
    price: Mapped[float] = mapped_column(Float)
    fast_label: Mapped[str] = mapped_column(String, default="")
    slow_label: Mapped[str] = mapped_column(String, default="")
    fast_value: Mapped[float] = mapped_column(Float, default=0.0)
    slow_value: Mapped[float] = mapped_column(Float, default=0.0)
    volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    pattern: Mapped[str | None] = mapped_column(String, nullable=True)          # the CONFIRMING pattern, if the filter was on
    candle_pattern: Mapped[str | None] = mapped_column(String, nullable=True)  # named pattern on the signal bar, if any
    candle_desc: Mapped[str] = mapped_column(String, default="")               # plain shape description, always present
    # The signal bar's own OHLC, so the log can draw the ACTUAL candle. A
    # generic icon per pattern name would show a textbook shape rather than
    # the bar that really fired.
    candle_open: Mapped[float] = mapped_column(Float, default=0.0)
    candle_high: Mapped[float] = mapped_column(Float, default=0.0)
    candle_low: Mapped[float] = mapped_column(Float, default=0.0)
    candle_close: Mapped[float] = mapped_column(Float, default=0.0)
    plain_english: Mapped[str] = mapped_column(Text, default="[]")            # JSON array of jargon-free lines
    candle_ts: Mapped[int] = mapped_column(Integer)
    # PENDING -> SENT | FAILED | SKIPPED (deduped/cooldown/no channel configured)
    alert_status: Mapped[str] = mapped_column(String, default="PENDING")
    alert_error: Mapped[str | None] = mapped_column(String, nullable=True)
    alert_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    feed_source: Mapped[str] = mapped_column(String, default="unknown")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class AlertChannel(Base):
    """WhatsApp delivery credentials, Fernet-encrypted like every other secret
    here. One row per provider so switching providers does not destroy the
    other's settings.
    """

    __tablename__ = "alert_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String, unique=True)  # callmebot | twilio
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # callmebot: phone + apikey. twilio: account_sid + auth_token + from/to.
    target_encrypted: Mapped[str] = mapped_column(String, default="")
    secret_encrypted: Mapped[str] = mapped_column(String, default="")
    extra_encrypted: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


_engine = create_async_engine(get_settings().database_url, echo=False)
async_session = async_sessionmaker(_engine, expire_on_commit=False)


# `create_all` creates missing TABLES but never adds a column to a table that
# already exists, so an upgraded install would keep its old `trades` table and
# every query naming a new column would fail. SQLite's ADD COLUMN is cheap and
# non-destructive, which is enough for this app's forward-only schema.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "scanner_config": {
        "pattern_filter": "BOOLEAN DEFAULT 0",
        "pattern_lookback": "INTEGER DEFAULT 3",
        "adx_filter": "BOOLEAN DEFAULT 1",
        "adx_threshold": "FLOAT DEFAULT 20",
        "rsi_filter": "BOOLEAN DEFAULT 1",
        "rsi_overbought": "FLOAT DEFAULT 70",
        "min_price": "FLOAT DEFAULT 0",
        "max_price": "FLOAT DEFAULT 0",
        "intrabar": "BOOLEAN DEFAULT 0",
    },
    "scanner_signals": {
        "pattern": "TEXT",
        "candle_pattern": "TEXT",
        "candle_desc": "TEXT DEFAULT ''",
        "candle_open": "FLOAT DEFAULT 0",
        "candle_high": "FLOAT DEFAULT 0",
        "candle_low": "FLOAT DEFAULT 0",
        "candle_close": "FLOAT DEFAULT 0",
        "plain_english": "TEXT DEFAULT '[]'",
    },
    "trades": {
        "source": "TEXT DEFAULT 'MANUAL'",
        "exit_reason": "TEXT",
        "entry_charges": "FLOAT DEFAULT 0.0",
        "exit_charges": "FLOAT DEFAULT 0.0",
        "account": "TEXT DEFAULT 'AUTO'",
        "feed_source": "TEXT DEFAULT 'unknown'",
    },
    "risk_settings": {
        "daily_profit_target_pct": "FLOAT DEFAULT 0.0",
        "max_leverage": "FLOAT DEFAULT 5.0",
        "min_edge_multiple": "FLOAT DEFAULT 1.5",
    },
}


async def _apply_additive_migrations(conn) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        rows = (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).fetchall()
        existing = {row[1] for row in rows}
        if not existing:
            continue  # table does not exist yet; create_all below makes it complete
        for name, ddl in columns.items():
            if name not in existing:
                await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


async def init_db() -> None:
    async with _engine.begin() as conn:
        await _apply_additive_migrations(conn)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "INSERT OR IGNORE INTO ai_config "
                "(id, model, web_search_enabled, gate_enabled, min_conviction, "
                "require_agreement, cache_ttl_sec) "
                "VALUES (1, 'gpt-5', 1, 0, 60, 1, 900)"
            )
        )
        # No raw seed for scanner_config: its defaults live on the ORM model,
        # not in the DDL, so an id-only INSERT violates NOT NULL and OR IGNORE
        # swallows it — a seed that silently does nothing. ScannerWorker
        # .load_config() creates the row through the ORM instead, which is the
        # one place those defaults are defined.


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
