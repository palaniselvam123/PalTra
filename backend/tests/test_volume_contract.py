"""The canonical volume contract, across both ingestion paths.

The contract is one sentence — `OHLCV.volume` is the volume traded during that
candle, always — and the whole point of these tests is that *both* paths obey
it. A per-path test would have passed before this fix: the tick path was already
correct and the backfill path was already self-consistent. What was missing was
anything asserting they agreed.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.services.candle_store import CandleStore
from app.services.indicators import OHLCV
from app.services.volume_contract import (
    BACKFILL, FIRST_BAR, OK, TICK, UNKNOWN, derive_rows, derive_session,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def ts_at(day: str, hh: int, mm: int) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=IST).timestamp())


def broker_rows(day: str, cumulative: list[int]) -> list[tuple]:
    """Broker history as returned: volume cumulative from the session open."""
    base = ts_at(day, 9, 15)
    return [(base + i * 300, 100.0, 101.0, 99.0, 100.5, v) for i, v in enumerate(cumulative)]


class TestDerivationRule:
    """D — reset handling is deterministic."""

    def test_ordinary_differences(self):
        assert derive_session([100, 250, 400]) == [(100, FIRST_BAR), (150, OK), (150, OK)]

    def test_reset_bar_and_its_successor_are_unknown(self):
        got = derive_session([800_000, 12_000, 820_000, 830_000])
        assert [q for _, q in got] == [FIRST_BAR, UNKNOWN, UNKNOWN, OK]

    def test_negative_difference_is_never_clipped_to_zero(self):
        """A fabricated zero is indistinguishable downstream from a quiet bar."""
        vols = [v for v, _ in derive_session([500, 10, 700])]
        assert vols[1] is None and 0 not in vols[1:]

    def test_flat_counter_is_a_real_zero(self):
        assert derive_session([100, 100])[1] == (0, OK)

    def test_deterministic(self):
        series = [100, 250, 240, 400, 401]
        assert derive_session(series) == derive_session(series)

    def test_rows_never_difference_across_a_session_boundary(self):
        rows = broker_rows("2026-06-01", [900_000]) + broker_rows("2026-06-02", [120_000])
        out = derive_rows(rows)
        assert [r[5] for r in out] == [900_000, 120_000]
        assert [r[7] for r in out] == [FIRST_BAR, FIRST_BAR]

    def test_rows_preserve_the_raw_cumulative(self):
        """C — raw cumulative value remains preserved."""
        out = derive_rows(broker_rows("2026-06-01", [100, 250, 400]))
        assert [r[6] for r in out] == [100, 250, 400]
        assert [r[5] for r in out] == [100, 150, 150]

    def test_unsorted_rows_are_ordered_first(self):
        rows = broker_rows("2026-06-01", [100, 250, 400])
        out = derive_rows([rows[2], rows[0], rows[1]])
        assert [r[0] for r in out] == [r[0] for r in rows]
        assert [r[5] for r in out] == [100, 150, 150]


class TestBackfillPath:
    """A — backfill candles expose canonical per-bar volume."""

    def setup_method(self):
        self.store = CandleStore()
        rows = broker_rows("2026-06-01", [988_614, 1_266_773, 1_476_664])
        derived = derive_rows(rows)
        self.store.seed(
            "AAA", "5m", "live",
            [OHLCV(ts, o, h, lo, c, bv or 0) for ts, o, h, lo, c, bv, _r, _q in derived],
            quality={r[0]: r[7] for r in derived},
            raw_cumulative={r[0]: r[6] for r in derived},
        )

    def test_seeded_volume_is_per_bar_not_cumulative(self):
        vols = [b.volume for b in self.store.get("AAA", "5m", "live")]
        assert vols == [988_614, 278_159, 209_891]

    def test_seeded_volume_is_not_monotonically_rising(self):
        """The symptom that exposed the defect: a cumulative column never falls."""
        vols = [b.volume for b in self.store.get("AAA", "5m", "live")]
        assert any(vols[i] < vols[i - 1] for i in range(1, len(vols)))

    def test_raw_cumulative_is_recoverable(self):
        raw = self.store.raw_cumulative("AAA", "5m", "live")
        assert sorted(raw.values()) == [988_614, 1_266_773, 1_476_664]

    def test_provenance_is_recorded_as_backfill(self):
        assert set(self.store.provenance("AAA", "5m", "live").values()) == {BACKFILL}

    def test_first_bar_is_flagged(self):
        q = self.store.volume_quality("AAA", "5m", "live")
        assert sorted(q.values()) == [FIRST_BAR, OK, OK]


class TestTickPath:
    """B — tick candles expose canonical per-bar volume."""

    def setup_method(self):
        self.store = CandleStore()
        base = ts_at("2026-06-01", 10, 0)
        # A cumulative day counter, as the feed reports it.
        for offset, cum in ((0, 1_000), (60, 1_500), (120, 1_800), (300, 2_400)):
            self.store.on_tick("AAA", 100.0, cum, "live", now=base + offset)

    def test_tick_volume_is_the_delta_not_the_counter(self):
        bars = self.store.get("AAA", "5m", "live")
        assert [b.volume for b in bars] == [800, 600]   # 1000 is the seeding tick

    def test_provenance_is_recorded_as_tick(self):
        assert set(self.store.provenance("AAA", "5m", "live").values()) == {TICK}

    def test_tick_bars_are_quality_ok(self):
        assert set(self.store.volume_quality("AAA", "5m", "live").values()) == {OK}


class TestBothPathsAgree:
    """E — both ingestion paths satisfy the same contract."""

    def test_same_cumulative_series_yields_the_same_bar_volumes(self):
        """The heart of the contract: feed identical underlying data through
        both paths and the canonical field must match."""
        cumulative = [1_000, 1_800, 2_400, 3_100]
        base = ts_at("2026-06-01", 10, 0)

        tick_store = CandleStore()
        for i, cum in enumerate(cumulative):
            tick_store.on_tick("AAA", 100.0, cum, "live", now=base + i * 300)
        tick_vols = [b.volume for b in tick_store.get("AAA", "5m", "live")]

        rows = [(base + i * 300, 100.0, 101.0, 99.0, 100.0, v) for i, v in enumerate(cumulative)]
        derived = derive_rows(rows)
        backfill_vols = [r[5] for r in derived]

        # The first tick only seeds the counter (delta 0), so both paths are
        # compared from their second bar onward.
        assert tick_vols[1:] == backfill_vols[1:]
        assert tick_vols[1:] == [800, 600, 700]

    def test_tick_bars_win_over_backfill_on_a_collision(self):
        """A bar this process actually observed is better evidence than history
        fetched for the same stamp, and its provenance must survive."""
        store = CandleStore()
        base = ts_at("2026-06-01", 10, 0)
        store.on_tick("AAA", 100.0, 1_000, "live", now=base)
        store.on_tick("AAA", 100.0, 1_900, "live", now=base + 60)

        bucket = store.get("AAA", "5m", "live")[0].ts
        store.seed("AAA", "5m", "live", [OHLCV(bucket, 1, 2, 0.5, 1.5, 999_999)],
                   quality={bucket: OK}, raw_cumulative={bucket: 999_999})

        assert store.get("AAA", "5m", "live")[0].volume == 900
        assert store.provenance("AAA", "5m", "live")[bucket] == TICK


class TestNoSilentFallback:
    """G — no path silently falls back to the wrong semantic."""

    def test_backfill_module_derives_rather_than_seeding_raw(self):
        import inspect

        from app.services import backfill

        src = inspect.getsource(backfill)
        assert "derive_rows(rows)" in src, "backfill must derive before seeding"
        assert "volume=r[5]" not in src, "raw broker volume must not reach OHLCV.volume"

    def test_research_store_and_live_share_one_derivation_rule(self):
        """Two copies of this rule would drift apart, which is how the two
        paths disagreed in the first place."""
        from app.research import store as research_store
        from app.services import volume_contract

        assert research_store.derive_session is volume_contract.derive_session

    def test_research_store_default_read_is_bar_volume(self, tmp_path):
        from app.research.store import Candle, ResearchStore

        st = ResearchStore(tmp_path / "r.db")
        base = ts_at("2026-06-01", 9, 15)
        st.upsert([Candle("AAA", "5m", base + i * 300, 10, 11, 9, 10, v, "live")
                   for i, v in enumerate([100, 250, 400])])
        assert [b.volume for b in st.read("AAA", "5m")] == [100, 150, 150]
        assert [b.volume for b in st.read("AAA", "5m", volume="raw_cumulative")] == [100, 250, 400]

    def test_unknown_volume_bars_are_identifiable_not_just_zero(self, tmp_path):
        from app.research.store import Candle, ResearchStore

        st = ResearchStore(tmp_path / "r.db")
        base = ts_at("2026-06-01", 9, 15)
        st.upsert([Candle("AAA", "5m", base + i * 300, 10, 11, 9, 10, v, "live")
                   for i, v in enumerate([500, 900, 10, 950])])
        vols = [b.volume for b in st.read("AAA", "5m")]
        unknown = st.unknown_volume_timestamps("AAA", "5m")
        assert 0 in vols                      # rendered as zero
        assert len(unknown) == 2              # but nameable, so excludable
        assert base + 2 * 300 in unknown


class TestRvolConsumesBarVolume:
    """F — RVOL must be built on canonical bar volume."""

    def test_entry_diagnostics_rvol_uses_the_candle_volume_field(self, tmp_path):
        """`observe()` divides this bar's volume by a trailing average. On a
        cumulative column that ratio is ~1.0 for every bar regardless of
        activity, which is what made the old RVOL diagnostic meaningless."""
        from app.research.store import Candle, ResearchStore
        from app.services.entry_diagnostics import observe

        st = ResearchStore(tmp_path / "r.db")
        base = ts_at("2026-06-01", 9, 15)
        # Steady 1,000/bar, then one bar with 5,000.
        cumulative, running = [], 0
        for i in range(40):
            running += 5_000 if i == 35 else 1_000
            cumulative.append(running)
        st.upsert([
            Candle("AAA", "5m", base + i * 300, 100.0, 101.0, 99.0, 100.0 + i * 0.01, v, "live")
            for i, v in enumerate(cumulative)
        ])

        bars = st.read("AAA", "5m")
        obs = observe("AAA", bars, 35, "BUY", horizon_bars=2)
        assert obs is not None
        assert obs.rvol == pytest.approx(5.0, abs=0.2), "RVOL must see the 5x bar"

        raw_bars = st.read("AAA", "5m", volume="raw_cumulative")
        raw_obs = observe("AAA", raw_bars, 35, "BUY", horizon_bars=2)
        # On a cumulative column every bar is close to its trailing average, so
        # the ratio is compressed towards 1 and a genuine 5x burst barely
        # registers. That compression is what made the old RVOL diagnostic
        # uninformative.
        assert raw_obs.rvol < raw_obs.rvol * 0 + 2.0
        assert raw_obs.rvol < obs.rvol / 2, "cumulative volume hides the burst"
