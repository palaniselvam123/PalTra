"""A bot's own book of real days on the Compare page (live_book.py), next to a backtest of the same day."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import backtest_compare
import backtest_detail
import live_book

DAY = "2026-11-16"


def test_ids_carry_the_bot_the_book_and_the_days():
    one = live_book.encode(1, "LIVE", DAY)
    assert one < 0 and live_book.is_live(one) and not live_book.is_live(12)
    assert live_book.decode(one) == {"bot": 1, "book": "LIVE", "start": DAY, "end": DAY, "days": [DAY]}
    week = live_book.decode(live_book.encode(3, "paper", "2026-11-13", "2026-11-17"))
    assert week["bot"] == 3 and week["book"] == "PAPER"
    assert week["days"] == ["2026-11-13", "2026-11-16", "2026-11-17"]          # weekends left out
    assert live_book.decode(live_book.encode(4, "RESEARCH", DAY))["bot"] == 0
    assert live_book.label(one) == "Bot 1 NSE live · 16 Nov"
    assert abs(live_book.encode(5, "LIVE", DAY, "2026-12-31")) < 2**53            # safe in JavaScript
    with pytest.raises(ValueError):
        live_book.encode(1, "LIVE", DAY, "2026-11-01")                            # last day before the first
    with pytest.raises(ValueError):
        live_book.encode(9, "LIVE", DAY)
    with pytest.raises(ValueError):
        live_book.decode(-7)


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/d.db")
    monkeypatch.setenv("OPEN_PNL_FILE", f"{tmp_path}/pnl.db")
    monkeypatch.setenv("SCAN_HISTORY_FILE", f"{tmp_path}/scans.db")
    import database
    from models import ReplayRun, TradeLog

    database.reset_engine()
    database.init_db()
    at = dt.datetime(2026, 11, 16, 9, 45)
    snap = json.dumps({"entry_mode": "COLOUR", "pattern_tf": 5, "qty": 10})

    def row(**kw):
        base = dict(date=DAY, symbol="TCS", direction="LONG", qty=10, entry_time=at, entry_price=100.0,
                    ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=99.0,
                    exit_time=at + dt.timedelta(minutes=20), exit_price=102.0, exit_reason="COLOUR_TURN",
                    gross_pnl=20.0, brokerage_and_taxes=5.0, net_pnl=15.0, entry_reason="CANDLE_COLOUR", strategy=snap)
        base.update(kw)
        return TradeLog(**base)

    with database.session_factory()() as db:
        db.add(ReplayRun(id=7, created_at=at, start_date=DAY, end_date=DAY, symbols="TCS,INFY",
                         settings=json.dumps({"bot": 1, "entry_mode": "COLOUR"}), status="FINISHED",
                         days_total=1, days_done=1, days=DAY))
        # Live: TCS 0.5 rupee worse fill, INFY only live; a Bot 2 trade and a PAPER trade that are not in this book.
        db.add(row(mode="LIVE", bot=1, entry_price=100.5, gross_pnl=15.0))
        db.add(row(mode="LIVE", bot=1, symbol="INFY", entry_time=at + dt.timedelta(hours=2),
                   exit_time=at + dt.timedelta(hours=2, minutes=5), gross_pnl=-30.0, entry_reason="NEWS"))
        db.add(row(mode="LIVE", bot=2, symbol="SBIN"))
        db.add(row(mode="PAPER", bot=1, symbol="SBIN"))
        # The backtest of the same day: TCS only.
        db.add(row(mode="REPLAY", bot=1, run_id=7))
        db.commit()
    yield live_book.encode(1, "LIVE", DAY)
    database.reset_engine()


def test_compare_page_reads_the_live_book_next_to_the_backtest(book):
    out = backtest_compare.compare([book, 7])
    live = next(r for r in out["runs"] if r["id"] == book)
    assert live["kind"] == "live" and live["label"] == "Bot 1 NSE live · 16 Nov"
    assert live["settings"]["entry_mode"] == "COLOUR" and live["settings"]["book"] == "LIVE"
    assert out["curve"]["runs"][book][-1] == -15.0            # TCS +15, INFY -30; Bot 2 and PAPER rows left out

    detail = backtest_detail.detail([book, 7], base=book)
    assert detail["stats"][book]["trades"] == 2 and detail["stats"][7]["trades"] == 1
    o = detail["overlap"][7]
    assert o["same"] == 1 and o["only_a"] == 1 and o["entry_worse_rs"] == -0.5    # the backtest filled 0.5 better
    live_note = next(t for t in detail["takeaways"] if t["title"] == "Live against the backtest")
    assert "₹0.50 a share worse" in live_note["text"] and "1 trades only live took" in live_note["text"]
    assert detail["labels"][book] == "Bot 1 NSE live · 16 Nov"

    days = live_book.days_with_trades(1, "LIVE")
    assert days == [{"day": DAY, "id": book, "trades": 2, "gross": -15.0}]
    day = backtest_detail.stock_day([book, 7], "TCS", DAY)
    assert [len(r["trades"]) for r in day["runs"]] == [1, 1]
