"""Price recording, movers ranking, fast-mover detection, and price questions.

The tests that matter most are the ones about honesty: that a minute with no
recorded price is reported as absent rather than filled in, that a lookup names
the minute it actually found, and that simulated prices can never be ranked
against live ones.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.research import universe_extra

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
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: (bars, ph.HISTORY, 5) if d == past else ([], ph.HISTORY, 5))
        monkeypatch.setattr(ph.research_store, "symbols", lambda *a, **k: ["AAA"])
        monkeypatch.setattr(universe_extra, "movers_universe", lambda: {"AAA"})

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
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: (bars, ph.HISTORY, 5) if d == past else ([], ph.HISTORY, 5))

        got = ph.price_at("AAA", when(10, 0, past))
        assert got is not None
        assert got.origin == ph.HISTORY
        assert got.resolution_min == 5

    def test_the_live_minute_record_wins_when_it_has_the_day(self, monkeypatch, store):
        """Finer resolution is preferred; history is only the fallback."""
        from app.research import price_history as ph

        seed(store, "AAA", [(10, 0, 123.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: ([], ph.HISTORY, 5))

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
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: (bars, ph.HISTORY, 5))

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
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: (bars, ph.HISTORY, 5))

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
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: (bars, ph.HISTORY, 5))
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
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: ([], ph.HISTORY, 5))
        d = ph.diagnose_miss("NOTATICKER", when(11, 0), "simulated")
        assert d["in_live_universe"] is False and d["in_history_universe"] is False
        assert "not a symbol this app stores" in d["reason"]

    def test_a_time_before_recording_started_says_so_with_the_window(self, monkeypatch, store):
        """The exact case on screen: asking for 11:00 when the recorder began
        at 16:42."""
        from app.research import price_history as ph

        seed(store, "AAA", [(16, 42, 100.0), (17, 4, 101.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: ([], ph.HISTORY, 5))
        monkeypatch.setattr("app.services.market_data.market_data.symbols", ["AAA"])

        d = ph.diagnose_miss("AAA", when(11, 0), "live")
        assert "16:42" in d["reason"] and "17:04" in d["reason"]
        assert "before recording started" in d["reason"]
        assert d["live_first_ist"] == "16:42" and d["live_last_ist"] == "17:04"

    def test_a_time_after_the_last_record_is_distinguished(self, monkeypatch, store):
        from app.research import price_history as ph

        seed(store, "AAA", [(9, 15, 100.0), (10, 0, 101.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: ([], ph.HISTORY, 5))
        monkeypatch.setattr("app.services.market_data.market_data.symbols", ["AAA"])

        assert "after the last recorded minute" in ph.diagnose_miss("AAA", when(15, 0), "live")["reason"]

    def test_a_weekend_is_named_rather_than_blamed_on_the_recorder(self, monkeypatch, store):
        import datetime as dt2

        from app.research import price_history as ph

        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: ([], ph.HISTORY, 5))
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
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: ([], ph.HISTORY, 5))
        d = ph.diagnose_miss("AAA", when(11, 0), "live")
        assert d["live_points"] == 2 and d["asked_for"] == "11:00" and d["symbol"] == "AAA"


class TestFetchOnMiss:
    """A miss should trigger a fetch, not a shrug — the broker has the data."""

    def test_a_fetch_is_refused_for_a_future_date(self):
        import datetime as dt2

        from app.research import price_fetch

        tomorrow = dt2.datetime.now(IST).date() + dt2.timedelta(days=1)
        assert "future" in price_fetch.fetch_blocked_reason("AAA", tomorrow)

    def test_a_fetch_is_refused_for_a_weekend(self):
        from app.research import price_fetch

        saturday = dt.date(2026, 6, 13)
        assert saturday.weekday() == 5
        assert "weekend" in price_fetch.fetch_blocked_reason("AAA", saturday)

    def test_a_fetch_is_refused_for_todays_simulated_prices(self):
        """Answering a question about the synthetic feed with real NSE data
        would swap one world for the other."""
        import datetime as dt2

        from app.research import price_fetch

        today = dt2.datetime.now(IST).date()
        reason = price_fetch.fetch_blocked_reason("AAA", today, source="simulated")
        assert "SIMULATED" in reason

    def test_no_broker_session_is_reported_as_such(self, monkeypatch):
        """'Not connected to Groww' is actionable; 'no record' is not."""
        from app.research import price_fetch

        monkeypatch.setattr(price_fetch, "broker_client", lambda: None)
        past = dt.date(2026, 6, 15)
        assert past.weekday() < 5
        assert "Not connected to Groww" in price_fetch.fetch_blocked_reason("AAA", past)

    def test_a_stored_day_is_not_refetched(self, monkeypatch):
        """The fetch is a cache fill, not a per-request call."""
        import asyncio

        from app.research import price_fetch

        monkeypatch.setattr(price_fetch, "already_stored", lambda *a, **k: True)
        called = {"n": 0}

        async def boom(*a, **k):
            called["n"] += 1

        monkeypatch.setattr(price_fetch, "_do_fetch", boom)
        out = asyncio.run(price_fetch.ensure_day("AAA", dt.date(2026, 6, 15)))
        assert out["cached"] is True and out["fetched"] is False and called["n"] == 0

    def test_resolve_returns_the_stored_price_without_fetching(self, monkeypatch, store):
        import asyncio

        from app.research import price_history as ph

        seed(store, "AAA", [(11, 0, 123.0)], 100.0)
        monkeypatch.setattr(ph, "snapshot_store", store)
        point, note = asyncio.run(ph.resolve_price("AAA", when(11, 0), "live"))
        assert point.price == 123.0 and note["cached"] is True and note["fetched"] is False

    def test_resolve_surfaces_the_fetch_reason_when_it_cannot_run(self, monkeypatch, store):
        """The blocked reason replaces the generic store diagnosis, because it
        is the one the reader can do something about."""
        import asyncio

        from app.research import price_fetch
        from app.research import price_history as ph

        monkeypatch.setattr(ph, "snapshot_store", store)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: ([], ph.HISTORY, 5))
        monkeypatch.setattr(price_fetch, "broker_client", lambda: None)

        point, note = asyncio.run(ph.resolve_price("AAA", when(11, 0, dt.date(2026, 6, 15)), "live"))
        assert point is None
        assert "Not connected to Groww" in note["reason"]
        assert note["fetch_attempted"] is True


class TestStoredMinuteBarsArePreferred:
    def test_one_minute_history_beats_five_minute(self, monkeypatch, store):
        """An on-demand fetch stores 1-minute bars; answering from a 5-minute
        bucket when those exist would discard precision the app has."""
        from app.research import price_history as ph
        from app.services.indicators import OHLCV

        monkeypatch.setattr(ph, "snapshot_store", store)
        minute = [OHLCV(ts_at(11, 0), 100.0, 101.0, 99.0, 111.0, 5)]
        monkeypatch.setattr(
            ph.research_store, "read",
            lambda sym, iv, src, start=None, end=None: minute if iv == "1m" else [],
        )
        got = ph.price_at("AAA", when(11, 0))
        assert got.origin == ph.HISTORY_1M and got.resolution_min == 1 and got.price == 111.0


class TestEmptyResultsExplainThemselves:
    """An empty movers table has several causes that look identical from outside.

    Each needs a different action from the user — change the as-of, switch the
    feed, or fetch the day — so returning [] for all three was a real gap.
    """

    def test_as_of_before_the_recording_started_says_when_it_ran(self, tmp_path, monkeypatch):
        from app.research import price_history as ph
        from app.research.snapshots import SnapshotStore

        s = SnapshotStore(tmp_path / "s.db")
        monkeypatch.setattr(ph, "snapshot_store", s)
        day = dt.date(2026, 3, 4)
        base = int(dt.datetime.combine(day, dt.time(11, 42), tzinfo=IST).timestamp())
        s.record([("ACME", base + i * 60, "live", 100.0 + i, 100.0, None) for i in range(5)])

        reason = ph.why_empty(day, "live", dt.datetime.combine(day, dt.time(10, 0), tzinfo=IST))
        assert reason is not None
        assert "11:42" in reason, "the reason must name the window that WAS recorded"
        assert "as it stood at" in reason.lower()

    def test_simulated_feed_on_today_says_so_rather_than_looking_empty(self, monkeypatch):
        from app.research import price_history as ph

        today = dt.datetime.now(IST).date()
        reason = ph.why_empty(today, "simulated", None)
        assert reason is not None and "SIMULATED" in reason
        assert "LIVE" in reason, "must say what to change, not just what is wrong"

    def test_as_of_no_longer_silently_relabels_the_store(self, tmp_path, monkeypatch):
        """The bug: an as-of that emptied the live slice fell to history, and a
        one-symbol history table was presented with no hint that the as-of was
        the cause. The rows may still come from history; the reason must be
        available alongside them."""
        from app.research import price_history as ph
        from app.research.snapshots import SnapshotStore

        s = SnapshotStore(tmp_path / "s.db")
        monkeypatch.setattr(ph, "snapshot_store", s)
        day = dt.date(2026, 3, 4)
        base = int(dt.datetime.combine(day, dt.time(11, 42), tzinfo=IST).timestamp())
        s.record([("ACME", base, "live", 100.0, 100.0, None)])

        cov = ph.live_coverage(day, "live")
        assert cov["covered"] and cov["first_ist"] == "11:42" and cov["in_session"]


class TestPostCloseRecordIsNotSessionCoverage:
    """A recorder started after 15:30 writes rows that describe nothing.

    The broker keeps serving the last close, so every symbol lands at an
    identical price and a move of exactly 0.00%. Those rows exist, so a check
    for "is there a live record for this day" says yes — and the finer
    resolution then wins over real broker history, producing a table of stocks
    all flat at zero. Coverage has to mean the session, not the date.
    """

    def _post_close_store(self, tmp_path, day):
        from app.research.snapshots import SnapshotStore

        s = SnapshotStore(tmp_path / "s.db")
        base = int(dt.datetime.combine(day, dt.time(17, 7), tzinfo=IST).timestamp())
        rows = []
        for sym, price in (("ACME", 100.0), ("BETA", 250.0)):
            for i in range(7):
                rows.append((sym, base + i * 600, "live", price, price, None))
        s.record(rows)
        return s

    def test_post_close_rows_do_not_count_as_coverage(self, tmp_path):
        from app.research import price_history as ph

        day = dt.date(2026, 3, 4)
        s = self._post_close_store(tmp_path, day)
        rows = s.movers(day, "live")
        assert len(rows) == 2, "the rows are really there"
        assert all(r.pct_from_open == 0.0 for r in rows), "and every one is flat"
        assert not ph._covers_session(rows, day), "but none of it is session data"

    def test_in_session_rows_do_count(self, tmp_path):
        from app.research import price_history as ph
        from app.research.snapshots import SnapshotStore

        day = dt.date(2026, 3, 4)
        s = SnapshotStore(tmp_path / "t.db")
        base = int(dt.datetime.combine(day, dt.time(9, 20), tzinfo=IST).timestamp())
        s.record([("ACME", base + i * 60, "live", 100.0 + i, 100.0, None) for i in range(5)])
        assert ph._covers_session(s.movers(day, "live"), day)

    def test_a_post_close_record_falls_through_to_history(self, tmp_path, monkeypatch):
        from app.research import price_history as ph

        day = dt.date(2026, 3, 4)
        monkeypatch.setattr(ph, "snapshot_store", self._post_close_store(tmp_path, day))
        monkeypatch.setattr(ph.research_store, "symbols", lambda *a, **k: ["ZULU"])
        monkeypatch.setattr(universe_extra, "movers_universe", lambda: {"ZULU"})
        bar_ts = int(dt.datetime.combine(day, dt.time(9, 20), tzinfo=IST).timestamp())
        bars = [
            SimpleNamespace(ts=bar_ts, open=100.0, high=101.0, low=99.0, close=100.0),
            SimpleNamespace(ts=bar_ts + 300, open=100.0, high=106.0, low=100.0, close=105.0),
        ]
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: (bars, ph.HISTORY, 5))

        out, origin = ph.movers(day, "live")
        assert origin == ph.HISTORY, "real session data must win over frozen quotes"
        assert [m.symbol for m in out] == ["ZULU"]
        assert out[0].pct_from_open == pytest.approx(5.0)

    def test_the_reason_names_the_post_close_window(self, tmp_path, monkeypatch):
        from app.research import price_history as ph

        day = dt.date(2026, 3, 4)
        monkeypatch.setattr(ph, "snapshot_store", self._post_close_store(tmp_path, day))
        monkeypatch.setattr(ph.research_store, "symbols", lambda *a, **k: [])
        monkeypatch.setattr(universe_extra, "movers_universe", lambda: set())
        reason = ph.why_empty(day, "live", None)
        assert reason and "17:07" in reason and "15:30" in reason


class TestExplicitWindow:
    """Ranking a chosen window, not always the whole day up to a cutoff.

    `as_of` alone answers "how far has it come since the open, as of 11:00".
    That is a different question from "what moved between 10:00 and 11:00", and
    only the second isolates an hour. The baseline has to move with the window
    or the number describes one span while the label claims another.
    """

    def _day_bars(self, day):
        base = int(dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST).timestamp())
        # rises to 110 by 10:00, falls back to 100 by 11:00: up on the day at
        # 10:00, down over the 10:00-11:00 hour.
        prices = {0: 100.0, 45: 110.0, 105: 100.0}
        return [
            SimpleNamespace(ts=base + m * 60, open=p, high=p, low=p, close=p)
            for m, p in sorted(prices.items())
        ]

    def _patched(self, monkeypatch, day):
        from app.research import price_history as ph

        monkeypatch.setattr(ph, "snapshot_store", SimpleNamespace(movers=lambda *a, **k: []))
        monkeypatch.setattr(ph.research_store, "symbols", lambda *a, **k: ["ZULU"])
        monkeypatch.setattr(universe_extra, "movers_universe", lambda: {"ZULU"})
        bars = self._day_bars(day)
        monkeypatch.setattr(ph, "_history_bars", lambda sym, d: (bars, ph.HISTORY, 5))
        return ph

    def test_whole_session_measures_from_the_open(self, monkeypatch):
        day = dt.date(2026, 3, 4)
        ph = self._patched(monkeypatch, day)
        out, _ = ph.movers(day, "live")
        assert out[0].pct_from_open == pytest.approx(0.0), "100 -> 100 over the day"
        assert out[0].baseline_is_session_open

    def test_as_of_alone_still_measures_from_the_open(self, monkeypatch):
        day = dt.date(2026, 3, 4)
        ph = self._patched(monkeypatch, day)
        at_ten = dt.datetime.combine(day, dt.time(10, 0), tzinfo=IST)
        out, _ = ph.movers(day, "live", at_ten)
        assert out[0].pct_from_open == pytest.approx(10.0), "up 10% from the open by 10:00"

    def test_a_window_measures_from_its_own_start(self, monkeypatch):
        day = dt.date(2026, 3, 4)
        ph = self._patched(monkeypatch, day)
        out, _ = ph.movers(
            day,
            "live",
            dt.datetime.combine(day, dt.time(11, 0), tzinfo=IST),
            dt.datetime.combine(day, dt.time(10, 0), tzinfo=IST),
        )
        assert out[0].pct_from_open == pytest.approx(-9.0909, abs=1e-3), "110 -> 100 in the hour"
        assert not out[0].baseline_is_session_open, "this is not a from-open number"
        assert out[0].first_time_ist == "10:00"

    def test_the_window_excludes_a_bar_that_started_before_it(self, monkeypatch):
        """A 5-minute bar stamped 09:55 covers 09:55-10:00. Letting it in would
        put pre-window price into a window that says it starts at 10:00."""
        day = dt.date(2026, 3, 4)
        ph = self._patched(monkeypatch, day)
        out, _ = ph.movers(
            day, "live", None, dt.datetime.combine(day, dt.time(10, 0), tzinfo=IST)
        )
        assert out[0].first_time_ist == "10:00"

    def test_live_record_honours_the_same_window(self, tmp_path, monkeypatch):
        from app.research.snapshots import SnapshotStore

        day = dt.date(2026, 3, 4)
        s = SnapshotStore(tmp_path / "w.db")
        base = int(dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST).timestamp())
        s.record([
            ("ACME", base, "live", 100.0, 100.0, None),
            ("ACME", base + 45 * 60, "live", 110.0, 100.0, None),
            ("ACME", base + 105 * 60, "live", 100.0, 100.0, None),
        ])
        whole = s.movers(day, "live")
        assert whole[0].pct_from_open == pytest.approx(0.0)

        hour = s.movers(
            day,
            "live",
            dt.datetime.combine(day, dt.time(11, 0), tzinfo=IST),
            dt.datetime.combine(day, dt.time(10, 0), tzinfo=IST),
        )
        assert hour[0].pct_from_open == pytest.approx(-9.0909, abs=1e-3)
        assert hour[0].open_price == pytest.approx(110.0), "baseline is the 10:00 price"

    def test_a_since_at_the_open_keeps_the_true_session_open(self, tmp_path):
        """09:15 is the default, so it must behave exactly like no window at all
        rather than substituting the first recorded price for the real open."""
        from app.research.snapshots import SnapshotStore

        day = dt.date(2026, 3, 4)
        s = SnapshotStore(tmp_path / "o.db")
        base = int(dt.datetime.combine(day, dt.time(9, 20), tzinfo=IST).timestamp())
        s.record([("ACME", base, "live", 105.0, 100.0, None)])
        at_open = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
        assert s.movers(day, "live", None, at_open)[0].open_price == pytest.approx(100.0)


class TestRangeFilters:
    """Price and percentage bounds narrow the ranking, not the store.

    The totals beside each table are what the user reads to judge whether a
    short list means a quiet day or a narrow filter, so a filtered total must
    never be reported as the size of the universe.
    """

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_price_bounds_exclude_by_last_price(self, monkeypatch):
        from app.api import routes_movers as rm

        rows = [
            SimpleNamespace(symbol="CHEAP", last_price=20.0, pct_from_open=5.0,
                            open_price=19.0, high_price=21.0, low_price=19.0,
                            last_ts=0, last_time_ist="10:00", points=3,
                            first_time_ist="09:15", baseline_is_session_open=True,
                            resolution_min=5, first_ts=0),
            SimpleNamespace(symbol="MID", last_price=800.0, pct_from_open=2.0,
                            open_price=784.0, high_price=810.0, low_price=780.0,
                            last_ts=0, last_time_ist="10:00", points=3,
                            first_time_ist="09:15", baseline_is_session_open=True,
                            resolution_min=5, first_ts=0),
            SimpleNamespace(symbol="DEAR", last_price=40000.0, pct_from_open=1.0,
                            open_price=39600.0, high_price=40100.0, low_price=39500.0,
                            last_ts=0, last_time_ist="10:00", points=3,
                            first_time_ist="09:15", baseline_is_session_open=True,
                            resolution_min=5, first_ts=0),
        ]
        monkeypatch.setattr(rm.price_history, "movers", lambda *a, **k: (rows, rm.price_history.HISTORY))

        r = self._client().get("/api/movers", params={"min_price": 100, "max_price": 5000, "source": "live"})
        assert r.status_code == 200
        body = r.json()
        assert body["symbols_tracked"] == 3, "the universe is unchanged by a filter"
        assert body["symbols_after_filter"] == 1
        assert [m["symbol"] for m in body["gainers"]] == ["MID"]

    def test_percentage_bounds_are_signed(self, monkeypatch):
        """-1% is BELOW -0.5%, so a min of -1 must keep a -0.5% stock and drop a
        -2% one. Comparing magnitudes would invert the loser side."""
        from app.api import routes_movers as rm

        def row(sym, pct):
            return SimpleNamespace(symbol=sym, last_price=100.0, pct_from_open=pct,
                                   open_price=100.0, high_price=101.0, low_price=99.0,
                                   last_ts=0, last_time_ist="10:00", points=3,
                                   first_time_ist="09:15", baseline_is_session_open=True,
                                   resolution_min=5, first_ts=0)

        rows = [row("UP", 3.0), row("FLATISH", -0.5), row("DOWN", -2.0)]
        monkeypatch.setattr(rm.price_history, "movers", lambda *a, **k: (rows, rm.price_history.HISTORY))

        body = self._client().get(
            "/api/movers", params={"min_pct": -1, "max_pct": 1, "source": "live"}
        ).json()
        assert body["symbols_after_filter"] == 1
        assert [m["symbol"] for m in body["losers"]] == ["FLATISH"]

    def test_filtering_everything_out_says_so(self, monkeypatch):
        """An empty table because of a filter must not read like an empty store —
        the actions that fix them are completely different."""
        from app.api import routes_movers as rm

        rows = [SimpleNamespace(symbol="ONE", last_price=100.0, pct_from_open=1.0,
                                open_price=99.0, high_price=101.0, low_price=99.0,
                                last_ts=0, last_time_ist="10:00", points=3,
                                first_time_ist="09:15", baseline_is_session_open=True,
                                resolution_min=5, first_ts=0)]
        monkeypatch.setattr(rm.price_history, "movers", lambda *a, **k: (rows, rm.price_history.HISTORY))

        body = self._client().get(
            "/api/movers", params={"min_price": 90000, "source": "live"}
        ).json()
        assert body["symbols_after_filter"] == 0
        assert "filters" in body["empty_reason"]
        assert "1 symbols have prices" in body["empty_reason"]

    def test_the_data_range_is_reported_for_the_slider_bounds(self, monkeypatch):
        from app.api import routes_movers as rm

        rows = [SimpleNamespace(symbol="A", last_price=22.5, pct_from_open=-2.4,
                                open_price=23.0, high_price=23.0, low_price=22.0,
                                last_ts=0, last_time_ist="10:00", points=3,
                                first_time_ist="09:15", baseline_is_session_open=True,
                                resolution_min=5, first_ts=0),
                SimpleNamespace(symbol="B", last_price=47000.0, pct_from_open=3.1,
                                open_price=45600.0, high_price=47100.0, low_price=45000.0,
                                last_ts=0, last_time_ist="10:00", points=3,
                                first_time_ist="09:15", baseline_is_session_open=True,
                                resolution_min=5, first_ts=0)]
        monkeypatch.setattr(rm.price_history, "movers", lambda *a, **k: (rows, rm.price_history.HISTORY))

        body = self._client().get("/api/movers", params={"source": "live"}).json()
        assert body["price_range"] == {"min": 22.5, "max": 47000.0}
        assert body["pct_range"]["min"] == pytest.approx(-2.4)


class TestWatchlistNamesAreRanked:
    """A watchlist symbol must be ranked, not absent.

    ANTELOPUS sat in the persisted watchlist since August and appeared nowhere
    in the tables. The universe-wide fetch covered the research map only, so no
    history was ever pulled for it — and a symbol with no bars is dropped
    silently, which on screen is indistinguishable from one that did not move.
    """

    def test_the_universe_includes_what_the_feed_polls(self, monkeypatch):
        from app.research import universe_extra
        from app.research.cross_sectional import SECTOR_OF

        monkeypatch.setattr(
            universe_extra, "movers_universe", universe_extra.movers_universe
        )
        from app.services.market_data import market_data

        market_data.add_symbol("ZZTESTONLY")
        try:
            uni = universe_extra.movers_universe()
            assert "ZZTESTONLY" in uni, "a polled symbol must be considered for ranking"
            assert set(SECTOR_OF) <= uni, "and the research map stays included"
        finally:
            if "ZZTESTONLY" in market_data.symbols:
                market_data.symbols.remove("ZZTESTONLY")
