"""SQLite tables for the SMA + ATR intraday terminal."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
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
    # MA_CROSS | MA_APPROACH | ATR_SL_HIT | GAP_SL_HIT | TARGET_HIT | BB_TARGET | BB_MIDDLE | GAP_FADE | EOD_SQUARE_OFF | KILL_SWITCH | NOT_ON_GROWW | MANUAL_CLOSE | SL_REJECTED
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    brokerage_and_taxes: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    mode: Mapped[str] = mapped_column(String, default="PAPER")  # PAPER | LIVE
    # False when the trade was entered with no stop (use_stop off). The
    # sl_trigger_price on such a row is only the level a stop would have used.
    stop_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The replay run (ReplayRun.id) a REPLAY trade belongs to. None otherwise.
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # JSON copy of the strategy settings at entry (strategy_engine.SNAPSHOT_FIELDS).
    # None on trades booked before this was recorded.
    strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Highest / lowest price while the trade was open (ticks, entry, exit).
    # Written at the close; None on trades booked before this was recorded.
    max_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Trade number inside its own book: PAPER and LIVE count separately, and
    # each replay / backtest run counts from 1. See trade_ref().
    book_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)


BOOK_PREFIX = {"LIVE": "N", "PAPER": "P", "RESEARCH": "Q"}


def trade_ref(mode: str | None, run_id: int | None, seq: int | None, fallback: int | None = None) -> str:
    """The trade id shown to people, unique within its book.

    NSE live: N-12 · Simulation (PAPER): P-12 · Research desk: Q-12 ·
    a replay / backtest run 7: R7-12.
    """
    book = (mode or "PAPER").upper()
    if seq is None:
        return f"#{fallback}" if fallback is not None else "—"
    if book == "REPLAY":
        return f"R{run_id}-{seq}" if run_id is not None else f"R-{seq}"
    return f"{BOOK_PREFIX.get(book, book[:1])}-{seq}"


class ReplayRun(Base):
    """One replay over one or more past days, with the settings it used."""

    __tablename__ = "replay_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    start_date: Mapped[str] = mapped_column(String)  # YYYY-MM-DD
    end_date: Mapped[str] = mapped_column(String)
    start_time: Mapped[str] = mapped_column(String, default="09:15")
    symbols: Mapped[str] = mapped_column(String, default="")  # comma-separated
    settings: Mapped[str] = mapped_column(Text, default="{}")  # JSON snapshot of BotConfig
    status: Mapped[str] = mapped_column(String, default="RUNNING")  # RUNNING | FINISHED | STOPPED
    days_total: Mapped[int] = mapped_column(Integer, default=0)
    days_done: Mapped[int] = mapped_column(Integer, default=0)
    days: Mapped[str] = mapped_column(String, default="")  # comma-separated trading days played


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
    # ATR = fixed stop at atr_multiplier × ATR (default). SMA_GAP = moving
    # stop and target from the SMA 9/21 gap %, PAPER only (gap_trail.py).
    # TSL = trailing stop in ₹ steps, PAPER and LIVE (tsl.py).
    stop_type: Mapped[str] = mapped_column(String, default="ATR")
    gap_sl_mult: Mapped[float] = mapped_column(Float, default=1.0)
    gap_tp_mult: Mapped[float] = mapped_column(Float, default=2.0)
    gap_min_pct: Mapped[float] = mapped_column(Float, default=0.2)
    # TSL = Groww-style trailing stop (tsl.py): stop ₹ from entry, moved ₹ step
    # by step as price gains; optional ₹ target (0 = none).
    tsl_sl_points: Mapped[float] = mapped_column(Float, default=20.0)
    tsl_trail_points: Mapped[float] = mapped_column(Float, default=10.0)
    tsl_target_points: Mapped[float] = mapped_column(Float, default=0.0)
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
    # Bollinger entry filter: skip a stretched cross (closed outside the band
    # on its own side) and a squeeze (bands narrower than bb_min_width_pct).
    use_bollinger: Mapped[bool] = mapped_column(Boolean, default=False)
    bb_period: Mapped[int] = mapped_column(Integer, default=20)
    bb_std: Mapped[float] = mapped_column(Float, default=2.0)
    bb_min_width_pct: Mapped[float] = mapped_column(Float, default=0.15)
    # Bollinger exit (PAPER and LIVE): OFF | BAND (take profit on a close at
    # the far band) | MIDDLE (close back across the middle band) | BOTH.
    # Uses bb_period / bb_std. Exits are BB_TARGET / BB_MIDDLE.
    bb_exit: Mapped[str] = mapped_column(String, default="OFF")
    # SMA gap range entry filter: (SMA fast - SMA slow) / SMA slow x 100 on the
    # closed cross candle, signed. Buys and sells are switched on separately;
    # an unticked side is not checked.
    use_gap_long: Mapped[bool] = mapped_column(Boolean, default=False)
    gap_long_min: Mapped[float] = mapped_column(Float, default=0.02)
    gap_long_max: Mapped[float] = mapped_column(Float, default=0.5)
    use_gap_short: Mapped[bool] = mapped_column(Boolean, default=False)
    gap_short_min: Mapped[float] = mapped_column(Float, default=-0.5)
    gap_short_max: Mapped[float] = mapped_column(Float, default=-0.02)
    # Candle direction entry filter: the last candle_dir_count closed candles
    # move the trade's way. CLOSES: each close beyond the one before (up for a
    # buy, down for a sell); COLOUR: each candle green / red; BOTH.
    # "Reset today's trade count": trades up to this TradeLog id on that day no
    # longer count toward max_trades_per_day (kept so a restart remembers it).
    trade_count_reset_id: Mapped[int] = mapped_column(Integer, default=0)
    trade_count_reset_date: Mapped[str | None] = mapped_column(String, nullable=True)
    use_candle_dir: Mapped[bool] = mapped_column(Boolean, default=False)
    candle_dir_count: Mapped[int] = mapped_column(Integer, default=2)
    candle_dir_rule: Mapped[str] = mapped_column(String, default="CLOSES")
    # SMA gap mode (gap_mode.py): a cross arms the trade and the signed gap %
    # fires it (>= gap_entry_long / <= gap_entry_short), optionally after
    # gap_entry_delay_min more minutes and within gap_entry_window_min of the
    # cross (0 = until the next cross). The trade closes when the gap fades
    # back to gap_exit_long / gap_exit_short, or gives back gap_giveback_pct
    # of its widest (0 = off). Exit reason GAP_FADE.
    use_gap_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    gap_entry_long: Mapped[float] = mapped_column(Float, default=0.05)
    gap_exit_long: Mapped[float] = mapped_column(Float, default=0.02)
    gap_entry_short: Mapped[float] = mapped_column(Float, default=-0.05)
    gap_exit_short: Mapped[float] = mapped_column(Float, default=-0.02)
    gap_giveback_pct: Mapped[float] = mapped_column(Float, default=0.0)
    # Fade exit confirmations (gap_mode.fade_confirmed): close beyond the slow
    # SMA, and/or the gap narrowing on N candles in a row (0 = off).
    gap_fade_confirm_sma: Mapped[bool] = mapped_column(Boolean, default=False)
    gap_fade_min_candles: Mapped[int] = mapped_column(Integer, default=0)
    gap_entry_delay_min: Mapped[int] = mapped_column(Integer, default=0)
    gap_entry_window_min: Mapped[int] = mapped_column(Integer, default=0)
    max_daily_loss: Mapped[float] = mapped_column(Float, default=5000.0)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=40)
    # 1 after the one-time raise from the old default of 15. A later edit to
    # 15 is kept.
    max_trades_bumped: Mapped[int] = mapped_column(Integer, default=1)
    square_off_time: Mapped[str] = mapped_column(String, default="15:15")
    # No new position from this time. Open ones still close on a cross,
    # the stop, or the square-off.
    entry_cutoff_time: Mapped[str] = mapped_column(String, default="15:00")
    trading_mode: Mapped[str] = mapped_column(String, default="PAPER")
    # Per-stock strategy overrides as JSON: {"TCS": {"qty": 50, "stop_type": "TSL"}}.
    # Only STOCK_FIELDS (strategy_engine.py) are read from it; the account-wide
    # risk limits, cut-off, square-off and mode always come from this row.
    stock_settings: Mapped[str] = mapped_column(String, default="{}")
