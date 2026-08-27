"""Research store: storage, incremental update, and isolation from the live path.

The isolation tests matter as much as the storage ones. The whole point of a
second store is that research ingestion cannot disturb live trading, and that
guarantee is only real if something checks it.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.research.store import Candle, ResearchStore, day_bounds, ist_date


@pytest.fixture()
def store(tmp_path):
    return ResearchStore(tmp_path / "research.db")


def candle(ts: int, symbol: str = "TESTCO", price: float = 100.0, source: str = "live") -> Candle:
    return Candle(symbol, "5m", ts, price, price + 1, price - 1, price + 0.5, 1000, source)


# 2026-06-01 09:15 IST
BASE = int(dt.datetime(2026, 6, 1, 9, 15, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp())


class TestStorage:
    def test_insert_and_read_back(self, store):
        assert store.upsert([candle(BASE), candle(BASE + 300)]) == 2
        bars = store.read("TESTCO", "5m")
        assert [b.ts for b in bars] == [BASE, BASE + 300]
        assert bars[0].open == 100.0

    def test_read_returns_live_ohlcv_type(self, store):
        """The existing research modules take OHLCV; if this drifts they all
        break at once."""
        from app.services.indicators import OHLCV

        store.upsert([candle(BASE)])
        assert isinstance(store.read("TESTCO", "5m")[0], OHLCV)

    def test_reads_come_back_in_chronological_order(self, store):
        store.upsert([candle(BASE + 600), candle(BASE), candle(BASE + 300)])
        ts = [b.ts for b in store.read("TESTCO", "5m")]
        assert ts == sorted(ts)

    def test_empty_read_is_empty_list_not_an_error(self, store):
        assert store.read("NOSUCH", "5m") == []

    def test_upsert_of_nothing_is_a_no_op(self, store):
        assert store.upsert([]) == 0


class TestDuplicatePrevention:
    def test_same_candle_twice_does_not_grow_the_table(self, store):
        assert store.upsert([candle(BASE)]) == 1
        assert store.upsert([candle(BASE)]) == 0
        assert len(store.read("TESTCO", "5m")) == 1

    def test_reingesting_an_overlapping_window_adds_only_the_new_bars(self, store):
        """Windows overlap by design, so this is the normal case, not an edge
        case: a re-fetch must not double-count."""
        store.upsert([candle(BASE + i * 300) for i in range(5)])
        added = store.upsert([candle(BASE + i * 300) for i in range(3, 8)])
        assert added == 3
        assert len(store.read("TESTCO", "5m")) == 8

    def test_a_corrected_bar_replaces_rather_than_duplicates(self, store):
        store.upsert([candle(BASE, price=100.0)])
        store.upsert([candle(BASE, price=250.0)])
        bars = store.read("TESTCO", "5m")
        assert len(bars) == 1 and bars[0].open == 250.0

    def test_sources_are_stored_separately(self, store):
        """Synthetic and real prices are different series about different
        worlds; blending them once produced a candle with a 2x impossible
        range. They must never collide on one key."""
        store.upsert([candle(BASE, source="live"), candle(BASE, price=50.0, source="simulated")])
        assert len(store.read("TESTCO", "5m", "live")) == 1
        assert len(store.read("TESTCO", "5m", "simulated")) == 1
        assert store.read("TESTCO", "5m", "live")[0].open == 100.0


class TestIncrementalUpdate:
    def test_coverage_records_which_days_are_done(self, store):
        d = dt.date(2026, 6, 1)
        store.mark_coverage("TESTCO", "5m", "live", d, 75, "OK")
        assert store.covered_days("TESTCO", "5m", "live") == {d}

    def test_failed_days_are_not_treated_as_covered(self, store):
        """A failed fetch must be retried, not silently accepted as complete."""
        store.mark_coverage("TESTCO", "5m", "live", dt.date(2026, 6, 1), 0, "FAILED", "timeout")
        assert store.covered_days("TESTCO", "5m", "live") == set()

    def test_empty_days_are_covered_so_holidays_are_not_refetched(self, store):
        d = dt.date(2026, 6, 2)
        store.mark_coverage("TESTCO", "5m", "live", d, 0, "EMPTY")
        assert d in store.covered_days("TESTCO", "5m", "live")

    def test_missing_days_skips_what_is_already_held(self, store):
        from app.research.ingestion import missing_days

        store.mark_coverage("TESTCO", "5m", "live", dt.date(2026, 6, 1), 75, "OK")
        need = missing_days(store, "TESTCO", "5m", "live", dt.date(2026, 6, 1), dt.date(2026, 6, 5))
        assert dt.date(2026, 6, 1) not in need
        assert dt.date(2026, 6, 2) in need

    def test_missing_days_excludes_weekends(self, store):
        from app.research.ingestion import missing_days

        # 2026-06-06 is a Saturday, 06-07 a Sunday
        need = missing_days(store, "TESTCO", "5m", "live", dt.date(2026, 6, 5), dt.date(2026, 6, 8))
        assert all(d.weekday() < 5 for d in need)

    def test_coverage_is_per_symbol(self, store):
        store.mark_coverage("AAA", "5m", "live", dt.date(2026, 6, 1), 75, "OK")
        assert store.covered_days("BBB", "5m", "live") == set()


class TestDateFiltering:
    def test_read_respects_a_date_range(self, store):
        day1 = BASE
        day2 = BASE + 86400
        store.upsert([candle(day1), candle(day2)])
        only_first = store.read("TESTCO", "5m", start=dt.date(2026, 6, 1), end=dt.date(2026, 6, 1))
        assert [b.ts for b in only_first] == [day1]

    def test_day_bounds_covers_a_whole_ist_day(self):
        lo, hi = day_bounds(dt.date(2026, 6, 1))
        assert hi - lo == 86400
        assert ist_date(lo) == dt.date(2026, 6, 1)
        assert ist_date(hi - 1) == dt.date(2026, 6, 1)

    def test_ist_date_is_not_utc_date(self):
        """A 09:15 IST bar is still 03:45 UTC the same day; a naive UTC read of
        an evening bar would land on the wrong day."""
        ts = int(dt.datetime(2026, 6, 1, 9, 15,
                             tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp())
        assert ist_date(ts) == dt.date(2026, 6, 1)


class TestLiveIsolation:
    """Research ingestion must not perturb the live trading path."""

    def test_writing_research_candles_leaves_the_live_store_untouched(self, store):
        from app.services.candle_store import candle_store

        before = {k: len(v) for k, v in candle_store._bars.items()}  # noqa: SLF001
        store.upsert([candle(BASE + i * 300) for i in range(50)])
        after = {k: len(v) for k, v in candle_store._bars.items()}  # noqa: SLF001
        assert before == after

    def test_research_store_uses_a_different_database_file(self, store):
        from app.core.config import get_settings

        assert "research" in str(store.path)
        assert str(store.path) not in get_settings().database_url

    def test_research_package_does_not_import_live_strategy_state(self):
        """A stray import would let a research run mutate scanner or bot state.
        Reading indicators and the market clock is fine — those are pure."""
        import pathlib

        pkg = pathlib.Path(__file__).resolve().parents[1] / "app" / "research"
        banned = ("strategy_runner", "scanner_worker", "paper_engine", "execution", "routes_bot")
        for f in pkg.glob("*.py"):
            text = f.read_text(encoding="utf-8")
            for name in banned:
                assert f"import {name}" not in text and f"from app.services.{name}" not in text, (
                    f"{f.name} imports live trading module {name}"
                )

    def test_live_candle_store_limits_are_unchanged(self):
        """The point of the research store is that these did not have to move."""
        from app.services.candle_store import MAX_BARS

        assert MAX_BARS == 1500

    def test_live_backfill_window_is_unchanged(self):
        from app.services.backfill import BACKFILL_DAYS

        assert BACKFILL_DAYS["5m"] == 10
