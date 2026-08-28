"""Live candle volume lifecycle.

**Invariant under test: each trade's volume is counted exactly once.**

The defect these guard against was subtle because both halves were individually
correct. Backfill wrote a bar covering the whole bucket; ticks then added the
volume they observed, which was a *subset* of that same interval. Two correct
numbers, summed over overlapping ranges. Nothing crashed and nothing looked
obviously wrong — the bar was simply too big.
"""
from __future__ import annotations

import datetime as dt

from app.services.candle_store import CandleStore
from app.services.indicators import OHLCV
from app.services.volume_contract import BACKFILL, OK, RECONCILED, TICK, UNKNOWN, derive_rows

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def at(hh: int, mm: int, ss: int = 0, day: str = "2026-06-01") -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d")
               .replace(hour=hh, minute=mm, second=ss, tzinfo=IST).timestamp())


def seeded_store(bar_volume: int = 120_000, raw_cumulative: int = 500_000) -> tuple[CandleStore, int]:
    """A store whose 11:35 bucket was populated by backfill.

    The broker reported `bar_volume` traded so far in that bucket, with the
    session counter standing at `raw_cumulative` when it was fetched.
    """
    store = CandleStore()
    bucket = at(11, 35)
    store.seed(
        "AAA", "5m", "live",
        [OHLCV(bucket, 100.0, 101.0, 99.0, 100.0, bar_volume)],
        quality={bucket: OK},
        raw_cumulative={bucket: raw_cumulative},
    )
    return store, bucket


class TestBackfilledCandle:
    """A — a backfilled candle has correct canonical bar volume."""

    def test_backfill_volume_is_per_bar(self):
        rows = [(at(9, 15), 100.0, 101.0, 99.0, 100.0, 988_614),
                (at(9, 20), 100.0, 101.0, 99.0, 100.0, 1_266_773)]
        store = CandleStore()
        derived = derive_rows(rows)
        store.seed("AAA", "5m", "live",
                   [OHLCV(ts, o, h, lo, c, bv or 0) for ts, o, h, lo, c, bv, _r, _q in derived],
                   quality={r[0]: r[7] for r in derived},
                   raw_cumulative={r[0]: r[6] for r in derived})
        assert [b.volume for b in store.get("AAA", "5m", "live")] == [988_614, 278_159]

    def test_backfilled_bar_is_labelled(self):
        store, bucket = seeded_store()
        assert store.provenance("AAA", "5m", "live")[bucket] == BACKFILL
        assert store.get("AAA", "5m", "live")[0].volume == 120_000


class TestNoDoubleCounting:
    """B — tick ingestion must not re-count volume the bar already represents."""

    def test_tick_adds_only_genuinely_new_volume(self):
        """The bug: 120,000 + 30,000 of NEW volume must be 150,000, not
        120,000 + 530,000, and not 120,000 + 30,000 + 120,000."""
        store, bucket = seeded_store(bar_volume=120_000, raw_cumulative=500_000)
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        assert store.get("AAA", "5m", "live")[0].volume == 150_000

    def test_volume_never_exceeds_the_counter_movement(self):
        """A bar cannot contain more volume than the session counter advanced
        across its own span."""
        store, bucket = seeded_store(bar_volume=120_000, raw_cumulative=500_000)
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 37))
        store.on_tick("AAA", 100.0, 545_000, "live", now=at(11, 38))
        bar = store.get("AAA", "5m", "live")[0]
        # bucket opened at counter 380,000 (= 500,000 - 120,000)
        assert bar.volume == 545_000 - 380_000

    def test_many_ticks_do_not_inflate_the_bar(self):
        store, _ = seeded_store(bar_volume=120_000, raw_cumulative=500_000)
        for i in range(50):
            store.on_tick("AAA", 100.0, 500_000 + i * 100, "live", now=at(11, 36))
        assert store.get("AAA", "5m", "live")[0].volume == 120_000 + 49 * 100

    def test_pure_tick_bar_is_unaffected_by_the_reconciliation_path(self):
        store = CandleStore()
        store.on_tick("AAA", 100.0, 1_000, "live", now=at(11, 40))
        store.on_tick("AAA", 100.0, 1_800, "live", now=at(11, 41))
        store.on_tick("AAA", 100.0, 2_400, "live", now=at(11, 42))
        bar = store.get("AAA", "5m", "live")[0]
        assert bar.volume == 1_400
        assert store.provenance("AAA", "5m", "live")[bar.ts] == TICK


class TestTransition:
    """C — a candle moving from backfill to tick processing stays correct."""

    def test_provenance_becomes_reconciled(self):
        store, bucket = seeded_store()
        assert store.provenance("AAA", "5m", "live")[bucket] == BACKFILL
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        assert store.provenance("AAA", "5m", "live")[bucket] == RECONCILED

    def test_backfilled_volume_is_preserved_not_discarded(self):
        """Rebuilding the bar purely from ticks would silently drop the volume
        that traded before this process was watching."""
        store, _ = seeded_store(bar_volume=120_000, raw_cumulative=500_000)
        store.on_tick("AAA", 100.0, 500_000, "live", now=at(11, 39))
        assert store.get("AAA", "5m", "live")[0].volume == 120_000

    def test_ohlc_still_updates_across_the_transition(self):
        store, _ = seeded_store()
        store.on_tick("AAA", 107.5, 510_000, "live", now=at(11, 39))
        bar = store.get("AAA", "5m", "live")[0]
        assert bar.high == 107.5 and bar.close == 107.5

    def test_next_bucket_opens_as_a_pure_tick_bar(self):
        store, _ = seeded_store()
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        store.on_tick("AAA", 100.0, 560_000, "live", now=at(11, 41))
        bars = store.get("AAA", "5m", "live")
        assert len(bars) == 2
        assert bars[1].volume == 30_000
        assert store.provenance("AAA", "5m", "live")[bars[1].ts] == TICK


class TestIdempotence:
    """D — reprocessing the same tick data changes nothing."""

    def test_replaying_a_tick_is_a_no_op(self):
        store, _ = seeded_store()
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        once = store.get("AAA", "5m", "live")[0].volume
        for _ in range(5):
            store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39, 30))
        assert store.get("AAA", "5m", "live")[0].volume == once

    def test_replaying_a_pure_tick_sequence_is_a_no_op(self):
        def build(repeats: int) -> int:
            store = CandleStore()
            for _ in range(repeats):
                for cum in (1_000, 1_800, 2_400):
                    store.on_tick("AAA", 100.0, cum, "live", now=at(11, 40))
            return store.get("AAA", "5m", "live")[0].volume

        assert build(1) == build(3)

    def test_seeding_twice_does_not_change_a_reconciled_bar(self):
        store, bucket = seeded_store()
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        before = store.get("AAA", "5m", "live")[0].volume
        store.seed("AAA", "5m", "live", [OHLCV(bucket, 100.0, 101.0, 99.0, 100.0, 120_000)],
                   quality={bucket: OK}, raw_cumulative={bucket: 500_000})
        assert store.get("AAA", "5m", "live")[0].volume == before
        assert store.provenance("AAA", "5m", "live")[bucket] == RECONCILED


class TestMonotonicity:
    """E — canonical volume must not fall merely because the source changed."""

    def test_volume_does_not_drop_on_the_backfill_to_tick_transition(self):
        store, _ = seeded_store(bar_volume=120_000, raw_cumulative=500_000)
        before = store.get("AAA", "5m", "live")[0].volume
        store.on_tick("AAA", 100.0, 500_000, "live", now=at(11, 39))
        assert store.get("AAA", "5m", "live")[0].volume >= before

    def test_volume_is_non_decreasing_across_a_tick_sequence(self):
        store, _ = seeded_store()
        seen = []
        for cum in (505_000, 512_000, 512_000, 528_000, 540_000):
            store.on_tick("AAA", 100.0, cum, "live", now=at(11, 39))
            seen.append(store.get("AAA", "5m", "live")[0].volume)
        assert seen == sorted(seen)

    def test_a_counter_reset_marks_unknown_rather_than_going_backwards(self):
        """The one case where the source itself indicates something is wrong:
        the bar is flagged, never silently shrunk or negated."""
        store, bucket = seeded_store(bar_volume=120_000, raw_cumulative=500_000)
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        healthy = store.get("AAA", "5m", "live")[0].volume
        store.on_tick("AAA", 100.0, 12, "live", now=at(11, 39, 30))
        assert store.volume_quality("AAA", "5m", "live")[bucket] == UNKNOWN
        assert store.get("AAA", "5m", "live")[0].volume == healthy


class TestRawPreserved:
    """F — raw cumulative volume remains available."""

    def test_backfill_raw_is_kept(self):
        store, bucket = seeded_store(raw_cumulative=500_000)
        assert store.raw_cumulative("AAA", "5m", "live")[bucket] == 500_000

    def test_tick_updates_the_raw_counter(self):
        store, bucket = seeded_store(raw_cumulative=500_000)
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        assert store.raw_cumulative("AAA", "5m", "live")[bucket] == 530_000

    def test_raw_and_canonical_are_different_quantities(self):
        store, bucket = seeded_store(bar_volume=120_000, raw_cumulative=500_000)
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        assert store.raw_cumulative("AAA", "5m", "live")[bucket] == 530_000
        assert store.get("AAA", "5m", "live")[0].volume == 150_000


class TestConsumersUseCanonicalVolume:
    """G — indicators must read canonical bar volume, never the raw counter."""

    def test_candle_store_get_returns_canonical_volume(self):
        store, _ = seeded_store(bar_volume=120_000, raw_cumulative=500_000)
        store.on_tick("AAA", 100.0, 530_000, "live", now=at(11, 39))
        bar = store.get("AAA", "5m", "live")[0]
        assert bar.volume == 150_000
        assert bar.volume != store.raw_cumulative("AAA", "5m", "live")[bar.ts]

    def test_scanner_volume_ratio_reads_the_ohlcv_field(self):
        """Whatever `get()` returns is what every indicator sees; there is no
        second volume accessor for them to reach the raw counter through."""
        import inspect

        from app.services import scanner_engine

        src = inspect.getsource(scanner_engine)
        assert "raw_cumulative" not in src

    def test_no_consumer_reaches_for_the_raw_counter(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        allowed = {"candle_store.py", "store.py", "volume_migration.py",
                   "volume_contract.py", "routes_research.py", "backfill.py"}
        for path in root.rglob("*.py"):
            if path.name in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            assert "raw_cumulative" not in text, f"{path.name} reads the raw counter"
