"""Replay the unchanged SMA 9/21 strategy on past days, one stock at a time.

This is ``replay.ReplayEngine`` (the Backtests tab's engine: local fills, no
order path) with two research-only shortcuts that do not change any trade:

* the chart frame is not built (``_view_frame``); orders never read it;
* the settings are read once instead of from SQLite on every tick.

and one stepping rule: while the stock is flat the clock moves a whole
minute at a time (entries only happen when a candle closes, on the minute),
and while a position is open it moves on the usual 10-second grid so stops
see each candle's path. ``tests/test_sma_research.py`` checks that this gives
the same trades as stepping every 10 seconds.

Each stock is replayed in its own engine, so stocks never affect each other.
The daily loss limit is off: in the app it is shared by all armed stocks,
and a per-stock run cannot reproduce that, so leaving it on would cut trades
for reasons unrelated to the stock.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from database import session_factory
from groww_client import IST
from models import BotConfig, TradeLog
from replay import SESSION_END, SESSION_OPEN, ReplayEngine, ReplayFeed, _to_next_grid

NO_LIMIT = 1e12
# Entry filters and exits that are not the core SMA 9/21 strategy. The
# baseline turns them off so every stock is judged on the crossover alone.
BASELINE_OFF = {
    "use_vwap": False,
    "use_volume": False,
    "use_density": False,
    "use_rsi": False,
    "use_adx_filter": False,
    "use_bollinger": False,
    "bb_exit": "OFF",
    "flip_orders": False,
    "cross_exit": True,
    "entry_mode": "SMA",
    "candle_minutes": 1,
    "review_on": False,
    "stock_settings": "{}",
}


def baseline_settings(row: dict) -> dict:
    """The user's saved settings with the extras off, for a clean crossover baseline."""
    data = dict(row)
    data.update(BASELINE_OFF)
    data["max_daily_loss"] = NO_LIMIT
    return data


class ResearchEngine(ReplayEngine):
    """ReplayEngine with fixed settings and no chart work."""

    uses_wallet = False  # offline research never touches the practice wallet

    def __init__(self, feed: ReplayFeed, symbol: str, settings: dict):
        self._settings = dict(settings)
        super().__init__(feed, [symbol])

    def load_config(self) -> BotConfig:
        cfg = getattr(self, "_fixed_cfg", None)
        if cfg is None:
            data = {c.name: self._settings.get(c.name) for c in BotConfig.__table__.columns if c.name in self._settings}
            data["trading_mode"] = "REPLAY"
            data["max_trades_per_day"] = 1_000_000
            data["trade_symbols"] = ",".join(self.replay_symbols)
            data["symbol"] = self.replay_symbols[0]
            cfg = BotConfig(**data)
            self._fixed_cfg = cfg
        self._cfg_cache = cfg
        return cfg

    def _view_frame(self, view, frame, cfg):  # noqa: ARG002
        return frame, False


def _trade_rows(min_id: int) -> list[dict]:
    with session_factory()() as db:
        rows = (
            db.query(TradeLog)
            .filter(TradeLog.id > min_id, TradeLog.mode == "REPLAY")
            .order_by(TradeLog.id)
            .all()
        )
    out = []
    for r in rows:
        out.append(
            {
                "symbol": r.symbol,
                "date": r.date,
                "direction": r.direction,
                "qty": r.qty,
                "entry_time": r.entry_time,
                "exit_time": r.exit_time,
                "entry_price": r.entry_price,
                "exit_price": r.exit_price,
                "cross_price": r.ma_cross_price,
                "exit_reason": r.exit_reason,
                "gross": float(r.gross_pnl or 0.0),
                "charges": float(r.brokerage_and_taxes or 0.0),
                "net": float(r.net_pnl or 0.0),
            }
        )
    return out


async def replay_day(
    feed: ReplayFeed, symbol: str, day: dt.date, settings: dict, *, every_10s: bool = False
) -> list[dict]:
    """Play one stock through one past session; return its trades."""
    clock = dt.datetime.combine(day, SESSION_OPEN, tzinfo=IST)
    end = dt.datetime.combine(day, SESSION_END, tzinfo=IST)
    feed.clock = clock
    engine = ResearchEngine(feed, symbol, settings)
    first_id = engine._min_trade_id
    engine.load_config()
    # As ReplaySession does: read the tape, skip the cross already on it, run.
    await engine.tick(clock)
    engine.hold_for_next_cross([symbol])
    engine.status = "RUNNING"
    while feed.clock < end:
        if every_10s or engine.positions:
            step = _to_next_grid(feed.clock)
        else:
            step = 60 - (feed.clock.second + feed.clock.microsecond / 1e6)
        feed.clock = min(end, feed.clock + dt.timedelta(seconds=step))
        await engine.tick(feed.clock)
    if engine.positions:
        await engine._square_off("EOD_SQUARE_OFF")
    return _trade_rows(first_id)


def trading_days(frame: pd.DataFrame) -> list[dt.date]:
    if frame is None or frame.empty:
        return []
    days = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    return sorted(d for d in days.unique() if d.weekday() < 5)


async def replay_symbol(
    frame: pd.DataFrame, symbol: str, days: list[dt.date], settings: dict, *, every_10s: bool = False
) -> list[dict]:
    """All requested days of one stock, oldest first."""
    feed = ReplayFeed({symbol: frame}, dt.datetime.combine(days[0], SESSION_OPEN, tzinfo=IST))
    trades: list[dict] = []
    for day in days:
        trades.extend(await replay_day(feed, symbol, day, settings, every_10s=every_10s))
    return trades
