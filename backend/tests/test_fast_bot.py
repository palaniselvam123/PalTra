"""Fast-mover hand-off (fast_bot.py): a held stock running its trade's way moves to Bot 5 at its size."""
from __future__ import annotations

import datetime as dt
from collections import deque
from types import SimpleNamespace

import pytest

from tests.test_sma_atr_terminal import IST, api, engine  # noqa: F401

NOW = dt.datetime(2026, 10, 30, 11, 0, tzinfo=IST).timestamp()


def _hist(*points):
    """(seconds before NOW, price) pairs, oldest first."""
    return deque((NOW - ago, price) for ago, price in points)


def _s(**over):
    import fast_bot

    return fast_bot.clean({"on": True, **over})


def test_move_rule_reads_the_trade_side_only():
    import fast_bot

    up = _hist((170, 100.0), (120, 100.2), (60, 100.4), (0, 100.6))
    s = _s(rule="MOVE", pct=0.5, minutes=3)
    assert "+0.60%" in fast_bot.fast(up, "LONG", NOW, s)
    assert fast_bot.fast(up, "SHORT", NOW, s) is None  # running against a short is not its way
    down = _hist((100, 200.0), (0, 198.8))
    assert fast_bot.fast(down, "SHORT", NOW, s) is not None
    # A move older than the window does not count.
    old = _hist((400, 100.0), (170, 100.5), (0, 100.6))
    assert fast_bot.fast(old, "LONG", NOW, s) is None


def test_speed_rule_and_either():
    import fast_bot

    hist = _hist((300, 100.0), (150, 100.4), (0, 100.8))  # 0.8% in 5 min = 0.16%/min
    assert fast_bot.fast(hist, "LONG", NOW, _s(rule="SPEED", speed=0.15, speed_minutes=5)) is not None
    assert fast_bot.fast(hist, "LONG", NOW, _s(rule="SPEED", speed=0.2, speed_minutes=5)) is None
    assert fast_bot.fast(hist, "LONG", NOW, _s(rule="MOVE", pct=1.0, minutes=5)) is None
    assert fast_bot.fast(hist, "LONG", NOW, _s(rule="EITHER", pct=1.0, minutes=5, speed=0.15, speed_minutes=5)) is not None


def test_settings_are_clamped_and_bot5_is_never_a_source():
    import fast_bot

    s = fast_bot.clean({"rule": "nonsense", "pct": 99, "minutes": 0, "sources": [1, 5, "3", 3, "x"]})
    assert s.rule == "MOVE" and s.pct == fast_bot.MAX_PCT and s.minutes == 1
    assert s.sources == [1, 3]
    assert fast_bot.Settings().on is False


class _Source:
    def __init__(self, bot=1, mode="PAPER", direction="LONG", price=100.6):
        self.bot_id = bot
        self.positions = {"ACME": SimpleNamespace(direction=direction, mode=mode, stop_active=True, sl_order_id="SL1", entry_price=100.0)}
        self._ltps = {"ACME": price}
        self.closed: list[str] = []
        self.alerts: list[str] = []
        self.net = 0

    async def close_symbol(self, symbol, reason="MANUAL_CLOSE", trade_id=None):
        self.closed.append(reason)
        self.positions.pop(symbol, None)
        return f"{symbol} closed"

    async def _groww_net(self, symbol):
        return self.net

    def _alert(self, text, **_):
        self.alerts.append(text)


class _Target:
    def __init__(self, mode="PAPER", status="RUNNING", trades=0):
        self.status = status
        self.positions = {}
        self.trades_today = trades
        self.orders: list[tuple] = []
        self.alerts: list[str] = []
        self._signals = {}
        self.cfg = SimpleNamespace(
            trading_mode=mode, square_off_time="15:15", entry_cutoff_time="15:00", max_trades_per_day=20
        )

    def load_config(self):
        return self.cfg

    def _now(self):
        return dt.datetime.fromtimestamp(NOW, IST)

    async def direct_order(self, symbol, signal, why, note=""):
        self.orders.append((symbol, signal, why))
        return f"{symbol} {signal} ordered"

    def _alert(self, text, **_):
        self.alerts.append(text)


@pytest.fixture
def moved(monkeypatch, tmp_path):
    import fast_bot
    import recovery

    monkeypatch.setenv("FAST_BOT_FILE", str(tmp_path / "fast_bot.json"))
    monkeypatch.setattr(fast_bot, "market_is_open", lambda *_a, **_k: True)
    calls = []

    def handoff(from_bot, symbol, source_live, to_bot=2):
        calls.append((from_bot, symbol, source_live, to_bot))
        return f"moved to Bot {to_bot} — waits for its next cross"

    monkeypatch.setattr(recovery, "handoff", handoff)
    return calls


async def _run(src, target, **settings):
    import fast_bot

    fast_bot.save({"on": True, "rule": "MOVE", "pct": 0.5, "minutes": 3, **settings})
    bot = fast_bot.FastBot()
    for ago, price in ((170, 100.0), (60, 100.3)):
        bot.note("ACME", price, NOW - ago, fast_bot.load())
    return bot, await bot.pass_once({src.bot_id: src, 5: target}, now=NOW)


@pytest.mark.asyncio
async def test_a_fast_long_is_cut_and_bot5_buys_at_once(moved):
    src, target = _Source(bot=2), _Target()
    bot, done = await _run(src, target, sources=[2])
    assert src.closed == ["FAST_MOVE_HANDOFF"]
    assert moved == [(2, "ACME", False, 5)]
    assert target.orders == [("ACME", "BULLISH", "FAST_MOVE")]
    assert "Bot 5 buys at once" in done[0]
    assert bot.moved_today == ["ACME"]
    # The same stock is not moved twice a day.
    src.positions["ACME"] = SimpleNamespace(direction="LONG", mode="PAPER", stop_active=True, sl_order_id="SL1", entry_price=100.0)
    again = await bot.pass_once({2: src, 5: target}, now=NOW + 5)
    assert again == [] and len(target.orders) == 1


@pytest.mark.asyncio
async def test_a_fast_short_sells_on_bot5(moved):
    import fast_bot

    src, target = _Source(direction="SHORT", price=99.4), _Target()
    fast_bot.save({"on": True, "rule": "MOVE", "pct": 0.5, "minutes": 3})
    bot = fast_bot.FastBot()
    for ago, price in ((170, 100.0), (60, 99.7)):
        bot.note("ACME", price, NOW - ago, fast_bot.load())
    done = await bot.pass_once({1: src, 5: target}, now=NOW)
    assert target.orders == [("ACME", "BEARISH", "FAST_MOVE")] and "sells at once" in done[0]


@pytest.mark.asyncio
async def test_nothing_is_cut_when_bot5_cannot_take_it(moved):
    src = _Source()
    for target, word in (
        (_Target(status="STOPPED"), "not RUNNING"),
        (_Target(mode="LIVE"), "practice money"),
        (_Target(trades=20), "trade cap"),
    ):
        _bot, done = await _run(src, target)
        assert word in done[0] and "not moved" in done[0]
        assert src.closed == [] and target.orders == [] and moved == []


@pytest.mark.asyncio
async def test_a_live_trade_moves_only_once_groww_is_flat(moved, monkeypatch):
    import fast_bot

    monkeypatch.setattr(fast_bot, "FLAT_WAIT_SEC", 1)
    src, target = _Source(mode="LIVE"), _Target(mode="LIVE")
    src.net = 1  # Groww still shows the share
    _bot, done = await _run(src, target)
    assert src.closed == ["FAST_MOVE_HANDOFF"] and moved == [] and target.orders == []
    assert "Groww still shows shares" in done[0]
    src2, target2 = _Source(mode="LIVE"), _Target(mode="LIVE")
    _bot, done = await _run(src2, target2)
    assert moved == [(1, "ACME", True, 5)] and target2.orders == [("ACME", "BULLISH", "FAST_MOVE")]


@pytest.mark.asyncio
async def test_off_or_slow_or_not_a_source_does_nothing(moved):
    src, target = _Source(), _Target()
    _bot, done = await _run(src, target, on=False)
    assert done == [] and src.closed == []
    _bot, done = await _run(src, target, pct=2.0)
    assert done == [] and src.closed == []
    _bot, done = await _run(src, target, sources=[3])
    assert done == [] and src.closed == []


def test_settings_route_needs_confirm_while_bot5_is_live(api, monkeypatch, tmp_path):
    import database
    from bots import config_id_for, ensure_bot_config
    from models import BotConfig

    monkeypatch.setenv("FAST_BOT_FILE", str(tmp_path / "fast_bot.json"))
    got = api.get("/api/bots/fast-mover").json()
    assert got["on"] is False and got["target_bot"] == 5 and got["rule"] == "MOVE"
    saved = api.put("/api/bots/fast-mover", json={"on": True, "rule": "EITHER", "speed": 0.2, "sources": [1, 2]})
    assert saved.status_code == 200 and saved.json()["rule"] == "EITHER" and saved.json()["sources"] == [1, 2]
    ensure_bot_config(5)
    with database.session_factory()() as db:
        db.get(BotConfig, config_id_for(5)).trading_mode = "LIVE"
        db.commit()
    try:
        assert api.put("/api/bots/fast-mover", json={"on": True}).status_code == 409
        assert api.put("/api/bots/fast-mover", json={"on": True, "confirm_live": True}).status_code == 200
        assert api.put("/api/bots/fast-mover", json={"on": False}).status_code == 200
    finally:
        with database.session_factory()() as db:
            db.get(BotConfig, config_id_for(5)).trading_mode = "PAPER"
            db.commit()


@pytest.mark.asyncio
async def test_real_bots_paper_trade_moves_to_bot5_at_its_qty(engine, monkeypatch, tmp_path):  # noqa: F811
    """End to end on the real engines in PAPER: Bot 1's 1-share long is closed, Bot 5 buys 50 at once."""
    import database
    import fast_bot
    from bots import BotEngine, config_id_for, ensure_bot_config
    from models import BotConfig, TradeLog
    from strategy_engine import trade_names
    from tests.test_sma_atr_terminal import _FakeBroker, _instant, _tape

    now = dt.datetime.fromtimestamp(NOW, IST)
    monkeypatch.setenv("FAST_BOT_FILE", str(tmp_path / "fast_bot.json"))
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: True)
    monkeypatch.setattr(fast_bot, "market_is_open", lambda *_a, **_k: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: now)
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols, row.qty, row.trading_mode, row.use_vwap = "ACME", 1, "PAPER", True
        db.commit()
    ensure_bot_config(5)
    with database.session_factory()() as db:
        row = db.get(BotConfig, config_id_for(5))
        row.qty, row.trade_symbols, row.use_vwap, row.use_rsi = 50, "", True, True
        db.commit()
    rising = _tape([100.0 + i * 0.05 for i in range(40)])
    engine._frames["ACME"] = rising
    engine._ltps["ACME"] = 102.0
    engine.status = "RUNNING"
    await engine.direct_order("ACME", "BULLISH", why="FORCE_ORDER")
    assert engine.positions["ACME"].qty == 1

    bot5 = BotEngine(5, _FakeBroker())
    bot5._sleep = _instant
    bot5._frames["ACME"] = rising
    bot5._ltps["ACME"] = 102.6
    bot5.status = "RUNNING"

    fast_bot.save({"on": True, "rule": "MOVE", "pct": 0.5, "minutes": 3, "sources": [1]})
    watcher = fast_bot.FastBot()
    watcher.note("ACME", 102.0, NOW - 120, fast_bot.load())
    engine._ltps["ACME"] = 102.6
    done = await watcher.pass_once({1: engine, 5: bot5}, now=NOW)
    assert "Bot 5 buys at once" in done[0], done

    assert "ACME" not in engine.positions
    pos = bot5.positions["ACME"]
    assert pos.direction == "LONG" and pos.qty == 50
    with database.session_factory()() as db:
        assert "ACME" not in trade_names(db.get(BotConfig, 1))
        assert "ACME" in trade_names(db.get(BotConfig, config_id_for(5)))
        rows = db.query(TradeLog).filter(TradeLog.symbol == "ACME").order_by(TradeLog.id).all()
        assert rows[0].exit_reason == "FAST_MOVE_HANDOFF" and rows[0].qty == 1
        assert rows[1].bot == 5 and rows[1].entry_reason == "FAST_MOVE" and rows[1].qty == 50


def test_a_live_trade_is_never_cut_for_a_paper_bot5():
    import fast_bot

    target = _Target(mode="PAPER")
    assert "Bot 5 is PAPER" in fast_bot.ready(target, "ACME", source_live=True)
    assert fast_bot.ready(_Target(mode="LIVE"), "ACME", source_live=True) is None
    assert fast_bot.ready(target, "ACME", source_live=False) is None


@pytest.mark.asyncio
async def test_live_source_and_paper_bot5_cuts_nothing(moved):
    src, target = _Source(mode="LIVE"), _Target(mode="PAPER")
    _bot, done = await _run(src, target)
    assert "not moved" in done[0] and src.closed == [] and target.orders == [] and moved == []


def test_replay_snapshot_carries_bot5_settings_only_when_on_and_a_source(monkeypatch, tmp_path):
    import fast_bot

    monkeypatch.setenv("FAST_BOT_FILE", str(tmp_path / "fast_bot.json"))
    monkeypatch.setattr(fast_bot, "_bot5_row", lambda: SimpleNamespace(qty=50, use_vwap=True, stock_settings='{"ACME": {"qty": 70}}'))
    assert fast_bot.replay_snapshot(1) is None  # off
    fast_bot.save({"on": True, "sources": [1, 2]})
    snap = fast_bot.replay_snapshot(1)
    assert snap["settings"]["on"] and snap["bot5"]["qty"] == 50 and snap["bot5"]["use_vwap"] is True
    assert fast_bot.bot5_overrides(snap, "acme")["qty"] == 70 and fast_bot.bot5_overrides(snap, "TCS")["qty"] == 50
    assert fast_bot.replay_snapshot(3) is None  # Bot 3 is not a source
    assert fast_bot.replay_snapshot(5) is None


@pytest.mark.asyncio
async def test_a_replay_cuts_the_fast_mover_and_plays_it_on_with_bot5s_size(tmp_path, monkeypatch):
    """A replayed day of Bot 1 (qty 10): a held stock moving fast its way is cut and opened again at once
    with Bot 5's quantity (50), and the run's totals show that part."""
    import database
    import fast_bot
    from models import BotConfig, TradeLog
    from replay import ReplayEngine, ReplayFeed, _fast_move_part
    from tests.test_replay import DAY, _frames

    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/replay.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda msg: None)
    database.reset_engine()
    database.init_db()
    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.symbol, row.trade_symbols, row.qty = "TCS", "TCS", 10
        s.commit()
    snap = {
        "settings": {"on": True, "rule": "MOVE", "pct": 0.1, "minutes": 3, "sources": [1], "max_day": 10},
        "bot5": {"qty": 50},
        "bot5_stocks": {},
    }
    clock = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    engine = ReplayEngine(ReplayFeed(_frames(), clock), ["TCS"], fast=snap)
    engine.load_config()
    await engine.tick(clock)
    engine.hold_for_next_cross(["TCS"])
    engine.status = "RUNNING"
    end = dt.datetime.combine(DAY, dt.time(15, 20), tzinfo=IST)
    while engine.feed.clock < end:
        engine.feed.clock += dt.timedelta(seconds=10)
        await engine.tick(engine.feed.clock)
    with database.session_factory()() as s:
        rows = s.query(TradeLog).order_by(TradeLog.id).all()
        cut = [r for r in rows if r.exit_reason == fast_bot.EXIT_REASON]
        opened = [r for r in rows if (r.entry_reason or "").startswith(fast_bot.ENTRY_REASON)]
        assert cut and cut[0].qty == 10
        assert opened and opened[0].qty == 50 and opened[0].direction == cut[0].direction
        # Each stock moves once a day: after the hand-off it trades on Bot 5's settings.
        assert len(cut) == 1
        assert all(r.qty == 50 for r in rows if r.id > opened[0].id)
        part = _fast_move_part(rows)
    assert part["cut"] == 1 and part["trades"] >= 1
    database.reset_engine()


def test_only_a_trade_already_in_profit_moves():
    import fast_bot

    s = fast_bot.clean({"on": True, "min_profit_pct": 0.3})
    long = SimpleNamespace(direction="LONG", entry_price=100.0)
    short = SimpleNamespace(direction="SHORT", entry_price=100.0)
    assert fast_bot.in_profit(long, 100.4, s) and not fast_bot.in_profit(long, 100.2, s)
    assert not fast_bot.in_profit(long, 99.0, s)  # a loser bouncing off its low never moves
    assert fast_bot.in_profit(short, 99.6, s) and not fast_bot.in_profit(short, 100.5, s)
    assert fast_bot.in_profit(long, 99.0, fast_bot.clean({"min_profit_pct": 0}))  # 0 = any trade
    assert fast_bot.Settings().min_profit_pct == 0.3


@pytest.mark.asyncio
async def test_a_losing_trade_that_bounces_fast_stays(moved):
    """A long bought at 102 that bounced 0.6% off its low (100 → 100.6) is still in loss: it stays on its bot."""
    src, target = _Source(price=100.6), _Target()
    src.positions["ACME"].entry_price = 102.0
    _bot, done = await _run(src, target)
    assert done == [] and src.closed == [] and target.orders == [] and moved == []


@pytest.mark.asyncio
async def test_a_replay_moves_nothing_until_the_trade_is_far_enough_in_profit(tmp_path, monkeypatch):
    import database
    import fast_bot
    from models import BotConfig, TradeLog
    from replay import ReplayEngine, ReplayFeed
    from tests.test_replay import DAY, _frames

    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/replay.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda msg: None)
    database.reset_engine()
    database.init_db()
    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.symbol, row.trade_symbols, row.qty = "TCS", "TCS", 10
        s.commit()
    snap = {
        "settings": {"on": True, "rule": "MOVE", "pct": 0.1, "minutes": 3, "sources": [1], "min_profit_pct": 5.0},
        "bot5": {"qty": 50},
        "bot5_stocks": {},
    }
    clock = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    engine = ReplayEngine(ReplayFeed(_frames(), clock), ["TCS"], fast=snap)
    engine.load_config()
    await engine.tick(clock)
    engine.hold_for_next_cross(["TCS"])
    engine.status = "RUNNING"
    end = dt.datetime.combine(DAY, dt.time(15, 20), tzinfo=IST)
    while engine.feed.clock < end:
        engine.feed.clock += dt.timedelta(seconds=10)
        await engine.tick(engine.feed.clock)
    with database.session_factory()() as s:
        rows = s.query(TradeLog).all()
        assert rows and not any(r.exit_reason == fast_bot.EXIT_REASON for r in rows)
        assert all(r.qty == 10 for r in rows)
    database.reset_engine()
