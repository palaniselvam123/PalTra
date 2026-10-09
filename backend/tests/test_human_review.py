"""1-minute human review of an uncertain SMA position (review.py, review_on).

The bot's own candle stays the strategy. A review only reads closed candles,
records what it showed, and waits: EXIT closes the reviewed trade through the
normal close path (USER_REVIEW_EXIT), WAIT and no answer change nothing.
All order paths here are PAPER or local replay fills.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import review
from tests.test_gap_mode import _settings, db  # noqa: F401
from tests.test_gap_mode import _frame as _gap_day_frame

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _sma_frame(pairs: list[tuple[float, float]]) -> pd.DataFrame:
    """Enriched-looking frame: one row per (sma_9, sma_21), plus a forming row last."""
    rows = [{"ts": 1_000_000 + 300 * i, "sma_9": f, "sma_21": s} for i, (f, s) in enumerate(pairs)]
    rows.append({"ts": rows[-1]["ts"] + 300, "sma_9": 9_999.0, "sma_21": 1.0})  # forming: never read
    return pd.DataFrame(rows)


# --- the trigger --------------------------------------------------------------

def test_tcs_gap_of_0_029_is_uncertain_at_0_03_and_not_at_0_02():
    # TCS 08-Oct-2026: 5-min SMA9 2104.96, SMA21 2104.35 -> +0.029%, narrowing from the candle before.
    frame = _sma_frame([(2105.30, 2104.40), (2104.96, 2104.35)])
    hit = review.uncertainty(frame, "LONG", 0.03, cross_exit=True)
    assert hit is not None
    assert hit.gap_pct == pytest.approx((2104.96 - 2104.35) / 2104.35 * 100)
    assert abs(hit.gap_pct) <= 0.03 and not hit.crossed
    assert review.uncertainty(frame, "LONG", 0.02, cross_exit=True) is None


def test_a_widening_gap_or_a_gap_on_the_other_side_is_not_a_review():
    widening = _sma_frame([(2104.90, 2104.35), (2104.96, 2104.35)])
    assert review.uncertainty(widening, "LONG", 0.03, cross_exit=True) is None
    # A SHORT whose lines sit the LONG way round has already crossed; the cross exit handles it.
    narrowing = _sma_frame([(2105.30, 2104.40), (2104.96, 2104.35)])
    assert review.uncertainty(narrowing, "SHORT", 0.03, cross_exit=True) is None


def test_with_the_cross_exit_off_a_fresh_cross_inside_the_band_is_a_review():
    crossed = _sma_frame([(2104.50, 2104.40), (2104.30, 2104.35)])  # fast just went under slow
    assert review.uncertainty(crossed, "LONG", 0.03, cross_exit=True) is None
    hit = review.uncertainty(crossed, "LONG", 0.03, cross_exit=False)
    assert hit is not None and hit.crossed


def test_the_forming_candle_is_never_read():
    # Only the forming row is inside the band: no review.
    frame = _sma_frame([(2106.0, 2104.0), (2106.5, 2104.0)])
    frame.loc[frame.index[-1], ["sma_9", "sma_21"]] = [2104.01, 2104.0]
    assert review.uncertainty(frame, "LONG", 0.03, cross_exit=True) is None


def test_an_episode_ends_only_well_clear_of_the_band():
    assert not review.episode_over(0.04, 0.03)
    assert review.episode_over(0.046, 0.03)
    assert not review.episode_over(None, 0.03)


def _tape(closes: list[float]) -> pd.DataFrame:
    start = dt.datetime(2026, 10, 8, 9, 15, tzinfo=IST)
    rows, cum, prev = [], 0, closes[0]
    for i, c in enumerate(closes):
        cum += 1000
        rows.append({"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": prev, "high": max(prev, c) + 0.1,
                     "low": min(prev, c) - 0.1, "close": c, "volume": cum})
        prev = c
    return pd.DataFrame(rows)


def test_one_minute_read_out_uses_closed_minutes_only():
    closes = [2100 + i * 0.2 for i in range(40)] + [2108.0, 2107.0, 2106.0, 2105.0]
    tape = _tape(closes + [2500.0])  # a forming minute far away
    one = review.one_minute_evidence(tape, 9, 21)
    assert one is not None
    assert one["close"] == pytest.approx(2105.0)
    assert [c["close"] for c in one["candles"]][-3:] == [2107.0, 2106.0, 2105.0]
    assert [c["colour"] for c in one["candles"]][-3:] == ["RED", "RED", "RED"]
    assert one["fast_slope"] == "falling"
    assert one["vs_fast"] == "below"
    assert all(key in one for key in ("vwap", "vs_vwap", "rsi14", "volume_ratio", "gap_pct", "gap_trend"))
    text = review.review_text(symbol="TCS", mode="PAPER", direction="LONG", qty=10, entry=2109.8, minutes=5,
                              five={"candle_ts": 0, "sma_fast": 2104.96, "sma_slow": 2104.35, "gap_pct": 0.029},
                              one=one, sma_fast=9, sma_slow=21, band=0.03)
    assert "TCS" in text and "+0.029%" in text and "UNCERTAIN" in text and "No answer = keep holding" in text


# --- the 1-minute trend verdict (information only) ------------------------------

def _evidence(**over):
    one = {
        "candles": [{"close": c} for c in (2105.2, 2104.6, 2104.1, 2103.2, 2102.6)],
        "fast_slope": "falling", "vs_fast": "below", "vs_slow": "below", "vs_vwap": "below",
    }
    one.update(over)
    return one


def test_falling_minutes_are_against_a_long_and_with_a_short():
    long_v = review.verdict(_evidence(), "LONG")
    assert long_v["label"] == review.AGAINST and (long_v["against"], long_v["with"], long_v["of"]) == (5, 0, 5)
    short_v = review.verdict(_evidence(), "SHORT")
    assert short_v["label"] == review.WITH and short_v["with"] == 5
    assert long_v["checks"]["price vs VWAP"] == "against" and short_v["checks"]["price vs VWAP"] == "with"


def test_the_verdict_needs_a_net_score_of_three_either_way():
    # 2 with, 3 against: net -1, so a split read.
    split = review.verdict(_evidence(fast_slope="rising", vs_fast="above"), "LONG")
    assert (split["with"], split["against"], split["score"], split["label"]) == (2, 3, -1, review.MIXED)
    # 1 with, 4 against: net -3, enough.
    lean = review.verdict(_evidence(fast_slope="rising"), "LONG")
    assert (lean["with"], lean["against"], lean["score"], lean["label"]) == (1, 4, -3, review.AGAINST)
    # Flat readings count as neither.
    flat = review.verdict(_evidence(fast_slope="flat", vs_fast="at", vs_slow="above", vs_vwap="above"), "LONG")
    assert (flat["flat"], flat["score"], flat["label"]) == (2, 1, review.MIXED)


def test_too_little_data_gives_no_verdict_and_never_raises():
    assert review.verdict(None, "LONG") is None
    assert review.verdict({"candles": [{"close": 1}]}, "LONG") is None
    assert review.verdict(_evidence(fast_slope=None, vs_fast=None, vs_slow=None, vs_vwap=None), "LONG") is None
    assert "not enough" in review.verdict_line(None, "LONG")


def test_the_verdict_is_in_the_alert_text_and_says_it_is_information_only():
    one = _evidence(candle_ts=0, vwap=2118.52, gap_trend="widening", slow_slope="flat", rsi14=38.0, volume_ratio=1.4)
    one["verdict"] = review.verdict(one, "LONG")
    one["candles"] = [dict(c, colour="RED") for c in one["candles"]]
    text = review.review_text(symbol="TCS", mode="PAPER", direction="LONG", qty=10, entry=2109.8, minutes=5,
                              five={"candle_ts": 0, "sma_fast": 2104.96, "sma_slow": 2104.35, "gap_pct": 0.029},
                              one=one, sma_fast=9, sma_slow=21, band=0.03)
    assert "1-min trend is AGAINST this LONG" in text and "Information only" in text


# --- in the engine: replays on local fills -------------------------------------

async def _replay(database, **over):
    from models import ReviewLog
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(_gap_day_frame(), "SIMG", [dt.date(2026, 9, 22)], _settings(database, **over))
    with database.session_factory()() as s:
        reviews = [
            {"trade_id": r.trade_id, "created_at": r.created_at, "status": r.status, "five": r.five_min, "one": r.one_min}
            for r in s.query(ReviewLog).order_by(ReviewLog.id).all()
        ]
        s.query(ReviewLog).delete()
        s.commit()
    return trades, reviews


def _key(trades):
    return [(t["entry_time"], t["direction"], t["exit_time"], t["exit_reason"], round(t["exit_price"], 2)) for t in trades]


@pytest.mark.asyncio
async def test_no_answer_changes_nothing_and_each_review_is_recorded(db):  # noqa: F811
    five = dict(candle_minutes=5)
    plain, none = await _replay(db, **five)
    assert none == []  # off by default
    watched, reviews = await _replay(db, review_on=True, review_gap_pct=0.05, review_cooldown_min=15, **five)
    # Reviews were raised, nobody answered: every trade is exactly as without them.
    assert reviews, "the fading 5-minute gap should have raised a review"
    assert _key(watched) == _key(plain)
    assert not any(t["exit_reason"] == "USER_REVIEW_EXIT" for t in watched)
    # Unanswered reviews end as NO_RESPONSE once their trade has closed.
    assert {r["status"] for r in reviews} == {review.NO_RESPONSE}
    assert all('"candles"' in r["one"] for r in reviews)


@pytest.mark.asyncio
async def test_one_review_per_uncertain_stretch_and_the_cooldown_holds(db):  # noqa: F811
    _, reviews = await _replay(db, review_on=True, review_gap_pct=0.05, review_cooldown_min=30, candle_minutes=5)
    by_trade: dict[int, list] = {}
    for r in reviews:
        by_trade.setdefault(r["trade_id"], []).append(r["created_at"])
    for times in by_trade.values():
        for a, b in zip(times, times[1:]):
            assert (b - a).total_seconds() >= 30 * 60


@pytest.mark.asyncio
async def test_a_one_minute_strategy_is_never_reviewed(db):  # noqa: F811
    _, reviews = await _replay(db, review_on=True, review_gap_pct=0.5, candle_minutes=1)
    assert reviews == []


# --- answers ------------------------------------------------------------------

def _engine_with_trade(database, symbol: str = "TCS", on_review=None):
    from strategy_engine import OpenPosition, StrategyEngine

    eng = StrategyEngine()  # PAPER client: local fills only
    eng.on_review = on_review
    cfg = eng.load_config()
    cfg = type(cfg)(**{c.name: getattr(cfg, c.name) for c in type(cfg).__table__.columns})
    cfg.symbol = symbol
    now = dt.datetime(2026, 10, 8, 13, 40, tzinfo=IST)
    tid = eng._insert_open_trade(cfg=cfg, direction="LONG", fill=2109.8, cross_price=2109.5, atr=2.0, sl=2100.0, now=now, qty=10)
    eng.positions[symbol] = OpenPosition(
        direction="LONG", qty=10, entry_price=2109.8, ma_cross_price=2109.5, atr_at_entry=2.0, sl_trigger=2100.0,
        sl_order_id="", entry_order_id="PAPER-1", entry_time=now, trade_id=tid, mode="PAPER", stop_active=False,
    )
    eng._ltps[symbol] = 2104.0
    eng._reviews_loaded = True
    hit = review.Uncertain(1_000_000, 2104.96, 2104.35, 0.029, 0.04, False)
    eng._raise_review(symbol, eng.positions[symbol], cfg, 5, hit, now + dt.timedelta(minutes=20))
    return eng, tid


def _trade(database, tid):
    from models import TradeLog

    with database.session_factory()() as s:
        row = s.get(TradeLog, tid)
        return row.exit_reason, row.exit_time


@pytest.mark.asyncio
async def test_exit_closes_the_reviewed_trade_once(db):  # noqa: F811
    eng, tid = _engine_with_trade(db)
    item = eng.reviews[-1]
    assert item["status"] == review.PENDING and item["trade_id"] == tid
    assert "UNCERTAIN" in item["message"]
    out = await eng.answer_review(item["id"], "EXIT")
    assert out["status"] == review.EXIT
    assert "TCS" not in eng.positions
    assert _trade(db, tid)[0] == "USER_REVIEW_EXIT"
    # A second tap (or another screen) sends nothing.
    again = await eng.answer_review(item["id"], "EXIT")
    assert again["result"].startswith("Already answered")
    from models import ReviewLog

    with db.session_factory()() as s:
        row = s.get(ReviewLog, item["id"])
        assert row.status == review.EXIT and row.action_at is not None
        assert '"gap_pct": 0.029' in row.five_min


@pytest.mark.asyncio
async def test_a_review_sends_one_alert_and_tells_the_replay_to_pause(db, _no_alert_delivery):  # noqa: F811
    import asyncio

    paused: list[dict] = []
    eng, _ = _engine_with_trade(db, on_review=paused.append)
    await asyncio.sleep(0.05)
    assert [m for m in _no_alert_delivery if "SMA REVIEW" in m] and len(paused) == 1
    assert paused[0]["status"] == review.PENDING


@pytest.mark.asyncio
async def test_the_saved_review_carries_the_verdict_and_never_changes_the_trade(db):  # noqa: F811
    from models import ReviewLog

    eng, tid = _engine_with_trade(db)
    item = eng.reviews[-1]
    with db.session_factory()() as s:
        saved = s.get(ReviewLog, item["id"]).one_min
    # No 1-minute tape in this test, so there is nothing to read and no verdict; the trade is untouched.
    assert "verdict" not in saved or '"verdict": null' in saved
    assert eng.positions["TCS"].trade_id == tid and _trade(db, tid) == (None, None)


@pytest.mark.asyncio
async def test_wait_and_no_answer_keep_the_trade_open(db):  # noqa: F811
    eng, tid = _engine_with_trade(db)
    item = eng.reviews[-1]
    out = await eng.answer_review(item["id"], "WAIT")
    assert out["status"] == review.WAIT
    assert eng.positions["TCS"].trade_id == tid
    assert _trade(db, tid) == (None, None)


@pytest.mark.asyncio
async def test_an_answer_after_the_trade_closed_sends_nothing(db):  # noqa: F811
    eng, tid = _engine_with_trade(db)
    item = eng.reviews[-1]
    await eng.close_symbol("TCS")  # closed some other way first
    assert _trade(db, tid)[0] == "MANUAL_CLOSE"
    out = await eng.answer_review(item["id"], "EXIT")
    assert out["status"] == review.ALREADY_CLOSED and "nothing sent" in out["result"]
    assert _trade(db, tid)[0] == "MANUAL_CLOSE"


@pytest.mark.asyncio
async def test_exit_never_closes_a_newer_trade_on_the_same_stock(db):  # noqa: F811
    eng, tid = _engine_with_trade(db)
    item = eng.reviews[-1]
    eng.positions["TCS"].trade_id = tid + 1000  # the stock now holds another trade
    out = await eng.answer_review(item["id"], "EXIT")
    assert out["status"] == review.ALREADY_CLOSED
    assert "TCS" in eng.positions


@pytest.mark.asyncio
async def test_a_pending_review_ends_unanswered_when_its_trade_closes(db):  # noqa: F811
    eng, _ = _engine_with_trade(db)
    item = eng.reviews[-1]
    eng.positions.pop("TCS")
    eng._expire_reviews()
    assert item["status"] == review.NO_RESPONSE


def test_api_saves_the_settings_and_answers_need_a_real_review(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"review_on", "review_gap_pct", "review_cooldown_min"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        cfg = client.get("/api/config").json()
        assert (cfg["review_on"], cfg["review_gap_pct"], cfg["review_cooldown_min"]) == (False, 0.03, 15)
        saved = client.put("/api/config", json={"review_on": True, "review_gap_pct": 0.05, "review_cooldown_min": 20}).json()
        assert (saved["review_on"], saved["review_gap_pct"], saved["review_cooldown_min"]) == (True, 0.05, 20)
        own = client.put("/api/config/stock/TCS", json={"review_gap_pct": 0.02})
        assert own.status_code == 200 and own.json()["own"] == {"review_gap_pct": 0.02}
        assert client.put("/api/config", json={"review_gap_pct": 0}).status_code == 422
        assert client.post("/api/bot/review/999/exit").status_code == 400
        assert client.post("/api/bot/review/1/close").status_code == 404
        assert "reviews" in client.get("/api/state").json()
    database.reset_engine()
