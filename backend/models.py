"""SQLite tables for the SMA + ATR intraday terminal."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TradeLog(Base):
    __tablename__ = "trade_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String, index=True)  # IST session date YYYY-MM-DD
    symbol: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String)  # LONG | SHORT
    qty: Mapped[int] = mapped_column(Integer)
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime)
    entry_price: Mapped[float] = mapped_column(Float)
    ma_cross_price: Mapped[float] = mapped_column(Float)
    atr_at_entry: Mapped[float] = mapped_column(Float)
    sl_trigger_price: Mapped[float] = mapped_column(Float)
    exit_time: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # MA_CROSS | MA_APPROACH | ATR_SL_HIT | EOD_SQUARE_OFF | KILL_SWITCH | NOT_ON_GROWW | MANUAL_CLOSE
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    brokerage_and_taxes: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    mode: Mapped[str] = mapped_column(String, default="PAPER")  # PAPER | LIVE


class BotConfig(Base):
    __tablename__ = "bot_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, default="KIRLOSFER")
    # Comma-separated NSE names the bot may order. The symbol above is only
    # the chart on screen, so a Trade button can stay armed on another page.
    trade_symbols: Mapped[str] = mapped_column(String, default="")
    exchange: Mapped[str] = mapped_column(String, default="NSE")
    qty: Mapped[int] = mapped_column(Integer, default=1000)
    sma_fast: Mapped[int] = mapped_column(Integer, default=9)
    sma_slow: Mapped[int] = mapped_column(Integer, default=21)
    atr_period: Mapped[int] = mapped_column(Integer, default=14)
    atr_multiplier: Mapped[float] = mapped_column(Float, default=1.5)
    use_adx_filter: Mapped[bool] = mapped_column(Boolean, default=False)
    # When false, entries are sent without an exchange stop. Square-off, the
    # panic button, and an opposite crossover still close the position.
    use_stop: Mapped[bool] = mapped_column(Boolean, default=True)
    adx_threshold: Mapped[float] = mapped_column(Float, default=20.0)
    # Optional entry checks. Unchecked means the order ignores that reading.
    use_vwap: Mapped[bool] = mapped_column(Boolean, default=False)
    use_volume: Mapped[bool] = mapped_column(Boolean, default=False)
    volume_min_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    use_density: Mapped[bool] = mapped_column(Boolean, default=False)
    density_min_pct: Mapped[float] = mapped_column(Float, default=50.0)
    use_rsi: Mapped[bool] = mapped_column(Boolean, default=False)
    rsi_long_min: Mapped[float] = mapped_column(Float, default=40.0)
    rsi_long_max: Mapped[float] = mapped_column(Float, default=70.0)
    rsi_short_min: Mapped[float] = mapped_column(Float, default=30.0)
    rsi_short_max: Mapped[float] = mapped_column(Float, default=60.0)
    max_daily_loss: Mapped[float] = mapped_column(Float, default=5000.0)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=15)
    square_off_time: Mapped[str] = mapped_column(String, default="15:15")
    trading_mode: Mapped[str] = mapped_column(String, default="PAPER")
