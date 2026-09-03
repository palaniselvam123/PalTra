"""Price recording, movers ranking, fast-mover detection, and price questions.

The tests that matter most are the ones about honesty: that a minute with no
recorded price is reported as absent rather than filled in, that a lookup names
the minute it actually found, and that simulated prices can never be ranked
against live ones.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.research.snapshots import SnapshotStore, ist_date
from app.services.movers import AlertLedger, FastMover, format_alert
from app.services.price_questions import (
    find_day, find_symbol, find_time, looks_like_price_question,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 9, 3)


def ts_at(hh: int, mm: int, day: dt.date = DAY) -> int:
    return int(dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST).timestamp())


def when(hh: int, mm: int, day: dt.date = DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST)


@pytest.fixture()
def store(tmp_path):
    return SnapshotStore(tmp_path / "snap.db")


def seed(store: SnapshotStore, symbol: str, minutes: list[tuple[int, int, float]],
         open_price: float, source: str = "live") -> None:
    store.record([(symbol, ts_at(h, m), source, p, open_price, 1000) for h, m, p in minutes])


class TestRecording:
    def test_a_recorded_minute_reads_back(self, store):
        seed(store, "AAA", [(9, 15, 100.0)], 100.0)
        point = store.price_at("AAA", when(9, 15))
        assert point is not None and point.price == 100.0

    def test_re_recording_a_minute_replaces_it(self, store):
        """The recorder re-reads the forming minute each pass; the last write
        is the most complete one, not a duplicate row."""
        seed(store, "AAA", [(9, 15, 100.0)], 100.0)
        seed(store, "AAA", [(9, 15, 101.5)], 100.0)
        assert len(store.session_series("AAA", DAY)) == 1
        assert store.price_at("AAA", when(9, 15)).price == 101.5

    def test_recording_nothing_is_a_no_op(self, store):
        assert store.record([]) == 0

    def test_pct_from_open_is_derived_from_the_stored_open(self, store):
        seed(store, "AAA", [(10, 0, 110.0)], 100.0)
        assert store.price_at("AAA", when(10, 0)).pct_from_open == pytest.approx(10.0)


class TestPointInTimeLookup:
    def test_returns_the_nearest_earlier_minute_not_a_later_one(self, store):
        """Asking for 11:00 must never answer with 11:05 — that would be a
        price the user could not have known at the time."""
        seed(store, "AAA", [(10, 58, 99.0), (11, 5, 105.0)], 100.0)
        point = store.price_at("AAA", when(11, 0))
        assert point.price == 99.0
        assert point.time_ist == "10:58"

    def test_reports_the_minute_it_actually_found(self, store):
        seed(store, "AAA", [(10, 52, 99.0)], 100.0)
        assert store.price_at("AAA", when(11, 0)).time_ist == "10:52"

    def test_a_gap_beyond_tolerance_is_absent_not_filled(self, store):
        """No price is carried forward across a long gap; the answer is None."""
        seed(store, "AAA", [(9, 20, 99.0)], 100.0)
        assert store.price_at("AAA", when(11, 0), tolerance_min=15) is None

    def test_never_answers_from_a_previous_day(self, store):
        yesterday = DAY - dt.timedelta(days=1)
        store.record([("AAA", ts_at(15, 20, yesterday), "live", 90.0, 88.0, 10)])
        assert store.price_at("AAA", when(9, 20, DAY)) is None

    def test_unknown_symbol_is_none(self, store):
        seed(store, "AAA", [(10, 0, 100.0)], 100.0)
        assert store.price_at("NOSUCH", when(10, 0)) is None


class TestSourceIsolation:
    def test_simulated_and_live_prices_never_mix(self, store):
        """Ranking synthetic moves against real ones would be meaningless, so
        source is part of the primary key and every query filters on it."""
        seed(store, "AAA", [(10, 0, 200.0)], 100.0, source="simulated")
        seed(store, "AAA", [(10, 0, 101.0)], 100.0, source="live")
        assert store.price_at("AAA", when(10, 0), "live").price == 101.0
        assert store.price_at("AAA", when(10, 0), "simulated").price == 200.0

    def test_movers_are_scoped_to_one_source(self, store):
        seed(store, "AAA", [(10, 0, 200.0)], 100.0, source="simulated")
        seed(store, "BBB", [(10, 0, 101.0)], 100.0, source="live")
        assert [m.symbol for m in store.movers(DAY, "live")] == ["BBB"]


class TestMoversRanking:
    def test_ranked_best_first(self, store):
        seed(store, "UP", [(10, 0, 105.0)], 100.0)
        seed(store, "FLAT", [(10, 0, 100.0)], 100.0)
        seed(store, "DOWN", [(10, 0, 95.0)], 100.0)
        assert [m.symbol for m in store.movers(DAY)] == ["UP", "FLAT", "DOWN"]

    def test_pct_is_measured_from_the_session_open(self, store):
        seed(store, "AAA", [(9, 15, 100.0), (10, 0, 103.0)], 100.0)
        m = store.movers(DAY)[0]
        assert m.open_price == 100.0 and m.pct_from_open == pytest.approx(3.0)

    def test_as_of_gives_the_ranking_as_it_stood(self, store):
        """The reason for recording at all: reconstructing 10:30 at 16:00."""
        seed(store, "AAA", [(10, 0, 103.0), (11, 0, 90.0)], 100.0)
        early = store.movers(DAY, "live", when(10, 30))[0]
        late = store.movers(DAY, "live")[0]
        assert early.pct_from_open == pytest.approx(3.0)
        assert late.pct_from_open == pytest.approx(-10.0)

    def test_high_and_low_span_the_session(self, store):
        seed(store, "AAA", [(9, 15, 100.0), (10, 0, 108.0), (11, 0, 96.0)], 100.0)
        m = store.movers(DAY)[0]
        assert m.high_price == 108.0 and m.low_price == 96.0

    def test_a_baseline_from_the_open_is_labelled_as_such(self, store):
        seed(store, "AAA", [(9, 15, 100.0), (10, 0, 103.0)], 100.0)
        assert store.movers(DAY)[0].baseline_is_session_open is True

    def test_a_mid_session_start_is_not_called_the_open(self, store):
        """The recorder starting at 11:00 gives an 11:00 baseline. That is a
        fine reference point but it is not the session open, and saying so
        would misstate every percentage on the page."""
        seed(store, "AAA", [(11, 0, 100.0), (11, 30, 103.0)], 100.0)
        m = store.movers(DAY)[0]
        assert m.baseline_is_session_open is False
        assert m.first_time_ist == "11:00"

    def test_a_symbol_without_an_open_is_skipped(self, store):
        store.record([("AAA", ts_at(10, 0), "live", 100.0, None, 1)])
        assert store.movers(DAY) == []


class TestFastMoverSpeed:
    def test_speed_is_a_rate_not_a_level(self, store, monkeypatch):
        """A stock that gapped up then went flat must not read as fast."""
        from app.research import price_history as ph
        from app.services import movers as movers_mod

        seed(store, "GAP", [(9, 15, 105.0), (10, 0, 105.1), (10, 10, 105.2)], 100.0)
        seed(store, "FAST", [(9, 15, 100.0), (10, 0, 100.2), (10, 10, 103.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)

        out = movers_mod.fast_movers(DAY, "live", when(10, 10), window_min=10,
                                     min_speed=0.10, min_move=0.75)
        assert [f.symbol for f in out] == ["FAST"]

    def test_direction_follows_the_sign_of_the_move(self, store, monkeypatch):
        from app.research import price_history as ph
        from app.services import movers as movers_mod

        seed(store, "DROP", [(9, 15, 100.0), (10, 0, 99.8), (10, 10, 96.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        out = movers_mod.fast_movers(DAY, "live", when(10, 10), 10, 0.10, 0.75)
        assert out[0].direction == "DOWN" and out[0].speed_pct_per_min < 0

    def test_a_barely_moved_stock_is_ignored_despite_a_rate_spike(self, store, monkeypatch):
        from app.research import price_history as ph
        from app.services import movers as movers_mod

        seed(store, "TINY", [(9, 15, 100.0), (10, 9, 100.0), (10, 10, 100.3)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        assert movers_mod.fast_movers(DAY, "live", when(10, 10), 10, 0.10, 0.75) == []


    def test_fast_movers_work_on_a_historical_day(self, store, monkeypatch):
        """The whole point of storing old prices: asking what moved fast last
        June, not only what is moving now."""
        import datetime as dt2

        from app.research import price_history as ph
        from app.services import movers as movers_mod
        from app.services.indicators import OHLCV

        past = DAY - dt2.timedelta(days=30)
        # flat, then a sharp late move
        bars = [OHLCV(ts_at(9, 15, past) + i * 300, 100.0, 100.2, 99.8, 100.0, 10) for i in range(20)]
        bars += [OHLCV(ts_at(9, 15, past) + 20 * 300, 100.0, 104.0, 100.0, 103.5, 10)]

        monkeypatch.setattr(ph, "snapshot_store", store)          # no live record
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: bars if d == past else [])
        monkeypatch.setattr(ph.research_store, "symbols", lambda *a, **k: ["AAA"])

        out = movers_mod.fast_movers(past, "live", None, 10, 0.10, 0.75)
        assert [f.symbol for f in out] == ["AAA"]
        assert out[0].origin == ph.HISTORY
        assert out[0].resolution_min == 5


class TestAlertLedger:
    def test_the_same_symbol_and_direction_alerts_once_a_day(self):
        ledger = AlertLedger()
        assert ledger.should_send("AAA", "UP", DAY) is True
        assert ledger.should_send("AAA", "UP", DAY) is False

    def test_the_other_direction_is_a_separate_event(self):
        ledger = AlertLedger()
        ledger.should_send("AAA", "UP", DAY)
        assert ledger.should_send("AAA", "DOWN", DAY) is True

    def test_a_new_day_clears_the_ledger(self):
        ledger = AlertLedger()
        ledger.should_send("AAA", "UP", DAY)
        assert ledger.should_send("AAA", "UP", DAY + dt.timedelta(days=1)) is True

    def test_alert_text_carries_the_numbers_a_reader_needs(self):
        text = format_alert(FastMover("AAA", 2.5, 0.25, 2.5, 123.45, ts_at(10, 30), "UP"))
        assert "AAA" in text and "+2.50%" in text and "123.45" in text and "10:30" in text


class TestPriceQuestionParsing:
    def test_recognises_the_users_own_phrasing(self):
        assert looks_like_price_question("What is the stock price of RELIANCE at 11:00?")

    def test_a_question_without_a_time_is_not_one(self):
        assert not looks_like_price_question("What is the price of RELIANCE?")

    def test_a_question_without_a_price_word_is_not_one(self):
        assert not looks_like_price_question("What happened at 11:00?")

    def test_finds_a_tracked_symbol(self):
        assert find_symbol("what was RELIANCE doing") == "RELIANCE"

    def test_returns_none_for_an_unknown_symbol(self):
        assert find_symbol("what was NOTATICKER doing") is None

    @pytest.mark.parametrize("text,expected", [
        ("at 11:00", dt.time(11, 0)),
        ("at 11.30", dt.time(11, 30)),
        ("at 9:15", dt.time(9, 15)),
        ("at 3pm", dt.time(15, 0)),
        ("at 11am", dt.time(11, 0)),
    ])
    def test_time_forms(self, text, expected):
        assert find_time(text) == expected

    def test_a_bare_afternoon_hour_is_read_as_market_time(self):
        """The session runs 09:15-15:30, so "at 3" means 15:00, not 03:00."""
        assert find_time("what was it at 3") == dt.time(15, 0)

    def test_relative_days(self):
        assert find_day("price yesterday at 11:00", DAY) == DAY - dt.timedelta(days=1)
        assert find_day("price today at 11:00", DAY) == DAY
        assert find_day("price on 2026-06-01 at 11:00", DAY) == dt.date(2026, 6, 1)

    def test_an_unrecognised_day_falls_back_to_today(self):
        assert find_day("price at 11:00", DAY) == DAY


class TestStoreReporting:
    def test_stats_describe_what_is_held(self, store):
        seed(store, "AAA", [(9, 15, 100.0), (10, 0, 101.0)], 100.0)
        stats = store.stats("live")
        assert stats["points"] == 2 and stats["symbols"] == 1
        assert stats["first"] == DAY.isoformat() and stats["last_time_ist"] == "10:00"

    def test_empty_store_reports_zero_rather_than_failing(self, store):
        assert store.stats("live")["points"] == 0

    def test_recorded_days_lists_sessions(self, store):
        seed(store, "AAA", [(10, 0, 100.0)], 100.0)
        seed(store, "AAA", [(10, 0, 100.0)], 100.0)
        store.record([("AAA", ts_at(10, 0, DAY + dt.timedelta(days=1)), "live", 100.0, 100.0, 1)])
        assert store.recorded_days("live") == [DAY, DAY + dt.timedelta(days=1)]


class TestHistoricalFallback:
    """Old days are answered from stored broker history, and labelled as such."""

    def test_a_past_day_falls_back_to_broker_history(self, monkeypatch, store):
        import datetime as dt2

        from app.research import price_history as ph
        from app.services.indicators import OHLCV

        past = DAY - dt2.timedelta(days=30)
        bars = [OHLCV(ts_at(9, 15, past) + i * 300, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10)
                for i in range(20)]

        monkeypatch.setattr(ph, "snapshot_store", store)          # empty: no live record
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: bars if d == past else [])

        got = ph.price_at("AAA", when(10, 0, past))
        assert got is not None
        assert got.origin == ph.HISTORY
        assert got.resolution_min == 5

    def test_the_live_minute_record_wins_when_it_has_the_day(self, monkeypatch, store):
        """Finer resolution is preferred; history is only the fallback."""
        from app.research import price_history as ph

        seed(store, "AAA", [(10, 0, 123.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: [])

        got = ph.price_at("AAA", when(10, 0))
        assert got.origin == ph.LIVE and got.resolution_min == 1 and got.price == 123.0

    def test_history_is_not_used_for_today_on_the_simulated_feed(self, monkeypatch, store):
        """Answering a question about today's synthetic feed with real NSE data
        would silently swap one world for the other."""
        from app.research import price_history as ph
        from app.services.indicators import OHLCV

        today = ph.ist_date(int(dt.datetime.now(dt.timezone.utc).timestamp()))
        bars = [OHLCV(int(dt.datetime.combine(today, dt.time(10, 0), tzinfo=IST).timestamp()),
                      100.0, 101.0, 99.0, 100.5, 10)]
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: bars)

        at_ten = dt.datetime.combine(today, dt.time(10, 0), tzinfo=IST)
        assert ph.price_at("AAA", at_ten, source="simulated") is None
        assert ph.price_at("AAA", at_ten, source="live") is not None

    def test_a_past_day_is_answered_even_on_the_simulated_feed(self, monkeypatch, store):
        """Nobody asks what the simulator printed last June — a question about a
        past date is a question about the real market."""
        import datetime as dt2

        from app.research import price_history as ph
        from app.services.indicators import OHLCV

        past = DAY - dt2.timedelta(days=30)
        bars = [OHLCV(ts_at(10, 0, past), 100.0, 101.0, 99.0, 100.5, 10)]
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: bars)

        assert ph.price_at("AAA", when(10, 0, past), source="simulated") is not None

    def test_history_movers_baseline_is_always_the_session_open(self, monkeypatch, store):
        """Broker history starts at 09:15 by construction, so the caveat banner
        that applies to a late-starting recorder must not fire for it."""
        import datetime as dt2

        from app.research import price_history as ph
        from app.services.indicators import OHLCV

        past = DAY - dt2.timedelta(days=30)
        bars = [OHLCV(ts_at(9, 15, past) + i * 300, 100.0, 102.0, 99.0, 100.0 + i, 10)
                for i in range(10)]
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: bars)
        monkeypatch.setattr(ph.research_store, "symbols", lambda *a, **k: ["AAA"])

        rows, origin = ph.movers(past)
        assert origin == ph.HISTORY
        assert rows[0].baseline_is_session_open is True
        assert rows[0].open_price == 100.0


class TestLtpBatching:
    def test_the_batch_limit_matches_what_groww_accepts(self):
        """Groww rejects more than 50 instruments per quote request with
        'size must be between 1 and 50', which took the whole feed down rather
        than the excess names."""
        from app.brokers.groww_client import GrowwClient

        assert GrowwClient.LTP_BATCH_LIMIT == 50

    def test_a_wide_universe_is_split_into_accepted_chunks(self):
        import asyncio

        from app.brokers.groww_client import GrowwClient

        client = GrowwClient.__new__(GrowwClient)
        seen: list[int] = []

        async def fake_chunk(symbols):
            seen.append(len(symbols))
            return {s: 1.0 for s in symbols}

        client._ltp_chunk = fake_chunk  # noqa: SLF001
        out = asyncio.run(GrowwClient.get_ltp_batch(client, [f"S{i}" for i in range(125)]))
        assert seen == [50, 50, 25]
        assert len(out) == 125

    def test_one_failing_chunk_does_not_lose_the_others(self):
        """A partial quote set still updates most of the universe; an
        all-or-nothing failure freezes every price on the page."""
        import asyncio

        from app.brokers.groww_client import GrowwClient

        client = GrowwClient.__new__(GrowwClient)

        async def fake_chunk(symbols):
            if "S60" in symbols:
                raise RuntimeError("transient")
            return {s: 1.0 for s in symbols}

        client._ltp_chunk = fake_chunk  # noqa: SLF001
        out = asyncio.run(GrowwClient.get_ltp_batch(client, [f"S{i}" for i in range(125)]))
        assert len(out) == 75 and "S0" in out and "S60" not in out


class TestMissDiagnosis:
    """A miss should say WHICH cause applies, not list every possibility.

    "One of three things went wrong" is not something a reader can act on;
    "recording started at 16:42" is.
    """

    def test_a_symbol_the_app_does_not_store_is_named_as_such(self, monkeypatch, store):
        from app.research import price_history as ph

        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: [])
        d = ph.diagnose_miss("NOTATICKER", when(11, 0), "simulated")
        assert d["in_live_universe"] is False and d["in_history_universe"] is False
        assert "not a symbol this app stores" in d["reason"]

    def test_a_time_before_recording_started_says_so_with_the_window(self, monkeypatch, store):
        """The exact case on screen: asking for 11:00 when the recorder began
        at 16:42."""
        from app.research import price_history as ph

        seed(store, "AAA", [(16, 42, 100.0), (17, 4, 101.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: [])
        monkeypatch.setattr("app.services.market_data.market_data.symbols", ["AAA"])

        d = ph.diagnose_miss("AAA", when(11, 0), "live")
        assert "16:42" in d["reason"] and "17:04" in d["reason"]
        assert "before recording started" in d["reason"]
        assert d["live_first_ist"] == "16:42" and d["live_last_ist"] == "17:04"

    def test_a_time_after_the_last_record_is_distinguished(self, monkeypatch, store):
        from app.research import price_history as ph

        seed(store, "AAA", [(9, 15, 100.0), (10, 0, 101.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: [])
        monkeypatch.setattr("app.services.market_data.market_data.symbols", ["AAA"])

        assert "after the last recorded minute" in ph.diagnose_miss("AAA", when(15, 0), "live")["reason"]

    def test_a_weekend_is_named_rather_than_blamed_on_the_recorder(self, monkeypatch, store):
        import datetime as dt2

        from app.research import price_history as ph

        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: [])
        monkeypatch.setattr("app.research.cross_sectional.SECTOR_OF", {"AAA": "X"})

        saturday = dt2.date(2026, 6, 13)
        assert saturday.weekday() == 5
        d = ph.diagnose_miss("AAA", when(11, 0, saturday), "live")
        assert "weekend" in d["reason"]

    def test_the_diagnosis_reports_the_coverage_it_used(self, monkeypatch, store):
        """The numbers behind the sentence are returned too, so a UI can show
        the user what times would have worked."""
        from app.research import price_history as ph

        seed(store, "AAA", [(16, 42, 100.0), (17, 4, 101.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: [])
        d = ph.diagnose_miss("AAA", when(11, 0), "live")
        assert d["live_points"] == 2 and d["asked_for"] == "11:00" and d["symbol"] == "AAA"
