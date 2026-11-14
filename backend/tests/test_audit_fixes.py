"""Fixes from the Nov 2026 audit outside the exit path (see test_live_exit_safety.py for that). No network."""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _day(day: dt.date, start: dt.time, n: int, per_minute: int) -> list[dict]:
    t = dt.datetime.combine(day, start, tzinfo=IST)
    first = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    cum = int((t - first).total_seconds() // 60) * per_minute  # Groww's running total from the open
    rows = []
    for _ in range(n):
        cum += per_minute
        rows.append({"ts": int(t.timestamp()), "volume": cum, "close": 100.0})
        t += dt.timedelta(minutes=1)
    return rows


def test_volume_vs_usual_compares_the_same_time_of_day():
    """The oldest day of a frame starts mid-session; matching candles by position read 2× days as 1.5×."""
    import nth_candle

    rows = (
        _day(dt.date(2026, 11, 9), dt.time(12, 0), 210, 1000)  # partial: from 12:00
        + _day(dt.date(2026, 11, 10), dt.time(9, 15), 375, 1000)
        + _day(dt.date(2026, 11, 11), dt.time(9, 15), 30, 2000)  # today: twice the usual pace
    )
    closed = pd.DataFrame(rows)
    days = pd.to_datetime(closed["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    assert nth_candle.rvol_at(closed, days, dt.date(2026, 11, 11), 10) == 2.0


class _Eng:
    def __init__(self, bot_id, closed, opened, price):
        self.bot_id, self.closed, self.opened = bot_id, closed, opened
        self.positions = {"SBIN": object()}
        self._ltps = {"SBIN": price} if price else {}
        self.status, self.halt_reason, self.killed = "RUNNING", "", []

    def load_config(self):
        return SimpleNamespace(trading_mode="PAPER")

    def _kpis(self, mode):  # noqa: ARG002
        return {"actual_gross": self.closed, "net": self.closed}

    def _open_pnl(self, name):  # noqa: ARG002
        return {"gross": self.opened, "net": self.opened} if self._ltps else None

    async def kill(self, reason):
        self.killed.append(reason)
        self.status, self.positions = "HALTED", {}


@pytest.mark.asyncio
async def test_the_all_bots_guard_does_not_trip_on_a_position_with_no_price_yet():
    """Right after a restart: closed +5,500, open −1,500 (no price yet). Counting the open as ₹0 hit the ₹5,000 target."""
    import all_bots_guard as guard

    bots = [_Eng(1, 5500.0, -1500.0, price=None)]
    cfg = SimpleNamespace(all_bots_target=5000, all_bots_stop=0, all_bots_squareoff="")
    task = asyncio.create_task(guard.run(lambda: bots, lambda: cfg, lambda: True, poll=0.01))
    await asyncio.sleep(0.08)
    assert bots[0].killed == []
    bots[0]._ltps["SBIN"] = 99.0  # the price arrives: +4,000 in all, under the target
    await asyncio.sleep(0.05)
    task.cancel()
    assert bots[0].killed == []


@pytest.mark.asyncio
async def test_the_all_bots_guard_panics_every_bot_together():
    import all_bots_guard as guard

    started: list[int] = []

    class Slow(_Eng):
        async def kill(self, reason):
            started.append(self.bot_id)
            await asyncio.sleep(0.05)
            await super().kill(reason)

    bots = [Slow(i, 0.0, 0.0, price=100.0) for i in (1, 2, 3)]
    task = asyncio.create_task(guard.trip(bots, "stop", -6000, 5000, 5000))
    await asyncio.sleep(0.02)
    assert started == [1, 2, 3]  # all started before the first finished
    await task


def test_panic_all_bots_can_be_restarted_like_a_single_panic():
    from strategy_engine import StrategyEngine

    eng = StrategyEngine.__new__(StrategyEngine)
    eng.status, eng.halt_reason = "HALTED", "Manual PANIC SQUARE-OFF (all bots)"
    assert StrategyEngine.release_manual_panic(eng) is True and eng.status == "STOPPED"
    eng.status, eng.halt_reason = "HALTED", "max_daily_loss ₹5000 breached"
    assert StrategyEngine.release_manual_panic(eng) is False


def test_an_rsi_low_of_zero_is_kept():
    import strategy_engine as se

    assert se._or_default(0.0, 40.0) == 0.0
    assert se._or_default(None, 40.0) == 40.0


@pytest.mark.asyncio
async def test_a_delivery_holding_is_not_the_bots_intraday_position(monkeypatch):
    from groww_client import GrowwClient

    client = GrowwClient(mode="LIVE")

    async def positions():
        return [
            {"trading_symbol": "SBIN", "product": "CNC", "net_quantity": 10},
            {"trading_symbol": "SBIN", "product": "MIS", "net_quantity": -5},
        ]

    monkeypatch.setattr(client, "get_positions", positions)
    assert await client.net_quantity("SBIN") == -5


def test_the_fast_mover_checks_bot_5s_trade_list_before_cutting_the_trade(monkeypatch):
    import fast_bot
    import strategy_engine as se

    full = ",".join(f"S{i:02d}" for i in range(se.MAX_TRADE_SYMBOLS))
    target = SimpleNamespace(
        status="RUNNING", positions={}, trades_today=0,
        load_config=lambda: SimpleNamespace(
            trading_mode="PAPER", square_off_time="15:15", entry_cutoff_time="15:00", max_trades_per_day=20,
            trade_symbols=full,
        ),
        _now=lambda: dt.datetime(2026, 11, 11, 11, 0, tzinfo=IST),
    )
    monkeypatch.setattr(fast_bot, "market_is_open", lambda now=None: True)
    assert "Trade list is full" in fast_bot.ready(target, "ACME", source_live=False)


@pytest.mark.asyncio
async def test_auto_arm_counts_only_the_stocks_it_armed(tmp_path, monkeypatch):
    import auto_arm
    import scalp_alerts

    monkeypatch.setenv("SCALP_ALERTS_FILE", str(tmp_path / "alerts.json"))
    for book in (*auto_arm._DONE.values(), *auto_arm._SEEN.values()):
        book.clear()
    s = scalp_alerts.load()
    s.arm_strong_on, s.arm_strong_bot, s.arm_max_day = True, 2, 2
    monkeypatch.setattr(scalp_alerts, "load", lambda: s)
    already = ["AAA", "BBB"]
    rows = [{"symbol": x} for x in ("AAA", "BBB", "CCC")]
    monkeypatch.setattr(auto_arm, "candidates", lambda kind, rows, s, now: rows)
    armed: list[str] = []

    async def bots():
        return {2: {"mode": "PAPER", "armed": already, "name": "Bot 2"}}

    async def arm(bot, sym, source):  # noqa: ARG001
        armed.append(sym)

    now = dt.datetime(2026, 11, 11, 11, 0, tzinfo=IST)
    await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=now, check_market=False)
    assert armed == ["CCC"]  # the two already armed did not use up the limit of 2
