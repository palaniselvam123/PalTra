"""Trend readings of a backtest (trend_log.py): kept once per candle with the bot's decision, saved per run, dropped
with the run / an unfinished day, and worked out again for runs saved before."""

import datetime as dt

import pandas as pd
import pytest

import trend_log
from tests.test_trend_trade import _cfg, _engine, _weekday, read


@pytest.fixture(autouse=True)
def _own_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_READS_FILE", str(tmp_path / "trend_reads.db"))


def test_row_keeps_the_reading_and_the_decision():
    got = dict(read("BEARISH", 86, "CHOP"), trend_checks={"stack": True, "hold": False, "zzz": True}, crosses=2,
               score="MIXED", trend_mode="MIDDAY")
    row = trend_log.row_from(got, ts=1_800_000_000, price=1428.123, minutes=5, decision="waiting")
    assert row["side"] == "BEARISH" and row["score"] == 86 and row["phase"] == "CHOP"
    assert row["checks"] == {"stack": True, "hold": False}  # only the known checks, in order
    assert row["price"] == 1428.12 and row["minutes"] == 5 and row["label"] == "MIXED" and row["decision"] == "waiting"


def test_save_read_and_drop_per_run_and_day():
    day1 = int(dt.datetime(2026, 11, 18, 10, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp())
    day2 = day1 + 86_400
    rows = [("CDSL", trend_log.row_from(read(), ts=day1, price=1, minutes=5, decision="a")),
            ("CDSL", trend_log.row_from(read(), ts=day2, price=1, minutes=5, decision="b")),
            ("BSE", trend_log.row_from(read(), ts=day1, price=1, minutes=5, decision="c"))]
    assert trend_log.save(7, 4, rows) == 3
    trend_log.save(8, 4, rows[:1])
    assert [r["decision"] for r in trend_log.for_run(7, "cdsl")] == ["a", "b"]
    assert [r["decision"] for r in trend_log.for_run(7, day="2026-11-18")] == ["c", "a"]  # by symbol, then time
    assert {(s["day"], s["symbol"]) for s in trend_log.summary(7)} == {("2026-11-18", "BSE"), ("2026-11-18", "CDSL"),
                                                                       ("2026-11-19", "CDSL")}
    trend_log.delete_run(7, ["2026-11-19"])               # a resume / rewind drops the unfinished day
    assert [r["decision"] for r in trend_log.for_run(7)] == ["c", "a"]
    trend_log.delete_run(7)
    assert trend_log.for_run(7) == [] and len(trend_log.for_run(8)) == 1


@pytest.mark.asyncio
async def test_engine_keeps_one_reading_a_candle_with_the_decision(monkeypatch, tmp_path):
    import database

    eng = _engine(monkeypatch, tmp_path)
    reads = {"now": read("BEARISH", 86, "CHOP")}
    monkeypatch.setattr(eng, "_trend_read", lambda s, c: reads["now"])

    async def fake_apply(signal, *a, **k):
        return f"opened {signal}"

    monkeypatch.setattr(eng, "apply_signal", fake_apply)
    eng.trend_rows = []
    cfg = _cfg("Auto-arm: Trend quality", tq_entry_score=80)

    async def at(hour, minute):
        now = _weekday(hour, minute)
        eng._now = lambda: now
        await eng._trend_entry_pass("ACME", object(), cfg, now)
        eng._record_trend("ACME", cfg)

    await at(10, 46)
    await at(10, 50)                      # same 15-minute candle: kept once
    reads["now"] = read("BEARISH", 100, "BEAR")
    await at(11, 1)
    kept = eng._tq_log["ACME"]
    assert len(kept) == 2 and len(eng.trend_rows) == 2
    assert "phase CHOP (needs DOWN)" in kept[0]["decision"] and kept[0]["score"] == 86
    assert kept[1]["decision"].startswith("opened BEARISH") and kept[1]["phase"] == "BEAR"
    # Not a Trend quality stock: nothing kept.
    eng._record_trend("OTHER", _cfg("Manual", symbol="OTHER"))
    assert "OTHER" not in eng._tq_log
    database.reset_engine()


@pytest.mark.asyncio
async def test_engine_notes_the_trend_exit(monkeypatch, tmp_path):
    import database

    eng = _engine(monkeypatch, tmp_path)
    monkeypatch.setattr(eng, "_trend_read", lambda s, c: read(phase="CHOP"))

    async def fake_exit(cfg, price, reason):
        eng.positions.pop("ACME", None)

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    opened = _weekday(10, 30).replace(second=10)
    pos = type("P", (), {"entry_time": opened, "trade_id": 7, "signal_direction": "LONG", "direction": "LONG",
                         "mode": "PAPER", "stop_active": True, "sl_order_id": None})()
    eng.positions["ACME"] = pos
    eng.position = pos
    eng.ltp = 100.0
    now = opened.replace(hour=11, minute=1)
    eng._now = lambda: now
    cfg = _cfg("Auto-arm: Trend quality")
    await eng._watch_trend_exit(cfg)
    eng._record_trend("ACME", cfg, cfg)
    assert eng._tq_log["ACME"][-1]["decision"].startswith("closed the trade: phase turned choppy")
    database.reset_engine()


def test_work_out_reads_every_candle_of_the_day(monkeypatch):
    calls = []

    def fake_read(part, fast, slow, minutes, now):
        calls.append(now.strftime("%H:%M"))
        return read("BEARISH", 86, "BEAR" if now.hour >= 12 else "CHOP")

    monkeypatch.setattr("stock_regime.read", fake_read)
    day = dt.date(2026, 11, 18)
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    start = int(dt.datetime.combine(day, dt.time(9, 15), tzinfo=ist).timestamp())
    frame = pd.DataFrame({"ts": [start + 60 * i for i in range(375)], "close": [100.0 + i for i in range(375)]})
    cfg = _cfg("x", tq_entry_score=80)
    rows = trend_log.work_out(frame, day, cfg, minutes=15, fast=9, slow=21)
    assert calls[0] == "09:30" and calls[-1] == "15:30" and len(rows) == 25
    assert rows[0]["source"] == "after" and "phase CHOP" in rows[0]["decision"]
    noon = next(r for r in rows if dt.datetime.fromtimestamp(r["ts"], ist).strftime("%H:%M") == "12:00")
    assert noon["decision"].startswith("would enter SHORT")
    assert rows[0]["price"] == 114.0  # the last close before 09:30
