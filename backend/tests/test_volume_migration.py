"""Per-bar volume derivation.

The rule that matters most is what happens when the counter resets: a zero
written where the truth is unknown is indistinguishable downstream from a
genuinely quiet bar, and that is precisely the class of silent error that made
this migration necessary in the first place.
"""
from __future__ import annotations

import datetime as dt

from app.research.store import Candle, ResearchStore
from app.research.store import FIRST_BAR, OK, UNKNOWN, derive_session
from app.research.volume_migration import migrate

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class TestDeriveSession:
    def test_normal_session_differences(self):
        got = derive_session([100, 250, 400, 460])
        assert got == [(100, FIRST_BAR), (150, OK), (150, OK), (60, OK)]

    def test_first_bar_is_the_cumulative_and_is_flagged(self):
        vol, quality = derive_session([988614, 1266773])[0]
        assert vol == 988614
        assert quality == FIRST_BAR, "must not be reported as an ordinary derived bar"

    def test_flat_counter_gives_zero_volume(self):
        """No trades in the bar is a real zero, distinct from an unknown."""
        assert derive_session([100, 100, 100])[1] == (0, OK)

    def test_reset_bar_is_unknown_not_zero(self):
        got = derive_session([800_000, 12_000, 820_000])
        assert got[1] == (None, UNKNOWN)

    def test_bar_after_a_reset_is_also_unknown(self):
        """Its difference would be taken against the reset baseline and would
        report most of the day's volume as one bar."""
        got = derive_session([800_000, 12_000, 820_000])
        assert got[2] == (None, UNKNOWN)
        assert got[2][0] is not 808_000  # noqa: F632 — the wrong answer, spelled out

    def test_recovery_after_the_unknown_pair_resumes_normally(self):
        got = derive_session([800_000, 12_000, 820_000, 830_000])
        assert got[3] == (10_000, OK)

    def test_reset_to_exact_zero_is_handled(self):
        got = derive_session([833_647, 0, 16_560])
        assert got[1] == (None, UNKNOWN) and got[2] == (None, UNKNOWN)

    def test_consecutive_resets(self):
        got = derive_session([500, 10, 5, 700])
        assert [q for _, q in got] == [FIRST_BAR, UNKNOWN, UNKNOWN, UNKNOWN]

    def test_no_negative_volume_is_ever_emitted(self):
        for series in ([100, 50, 200], [10, 0, 5, 900], [5, 4, 3, 2]):
            assert all(v is None or v >= 0 for v, _ in derive_session(series))

    def test_single_bar_session(self):
        assert derive_session([1234]) == [(1234, FIRST_BAR)]

    def test_empty_session(self):
        assert derive_session([]) == []


class TestMigration:
    def _store(self, tmp_path):
        st = ResearchStore(tmp_path / "research.db")
        base = int(dt.datetime(2026, 6, 1, 9, 15, tzinfo=IST).timestamp())
        cumulative = [100, 250, 400, 380, 500]     # one reset at index 3
        st.upsert([
            Candle("AAA", "5m", base + i * 300, 10, 11, 9, 10.5, v, "live")
            for i, v in enumerate(cumulative)
        ])
        return st, base

    def test_raw_is_preserved_exactly(self, tmp_path):
        st, base = self._store(tmp_path)
        migrate(st)
        with st._conn() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT volume, raw_cumulative_volume FROM research_candles ORDER BY ts"
            ).fetchall()
        assert [r[0] for r in rows] == [100, 250, 400, 380, 500]
        assert [r[1] for r in rows] == [100, 250, 400, 380, 500]

    def test_derived_column_is_populated(self, tmp_path):
        st, _ = self._store(tmp_path)
        report = migrate(st)
        assert report["rows_updated"] == 5
        assert report["first_bar"] == 1
        assert report["unknown"] == 2       # the reset bar and the one after it
        assert report["ok"] == 2

    def test_read_defaults_to_derived_volume(self, tmp_path):
        st, _ = self._store(tmp_path)
        migrate(st)
        vols = [b.volume for b in st.read("AAA", "5m")]
        assert vols == [100, 150, 150, 0, 0]   # unknowns render as 0

    def test_raw_cumulative_is_still_reachable(self, tmp_path):
        """H001-H003 ran against this; their runs must stay reproducible."""
        st, _ = self._store(tmp_path)
        migrate(st)
        vols = [b.volume for b in st.read("AAA", "5m", volume="raw_cumulative")]
        assert vols == [100, 250, 400, 380, 500]

    def test_unknown_bars_are_nameable(self, tmp_path):
        """Rendering unknowns as 0 is only safe if callers can identify them."""
        st, base = self._store(tmp_path)
        migrate(st)
        unknown = st.unknown_volume_timestamps("AAA", "5m")
        assert unknown == {base + 3 * 300, base + 4 * 300}

    def test_migration_is_idempotent(self, tmp_path):
        st, _ = self._store(tmp_path)
        first = migrate(st)
        second = migrate(st)
        assert first == second

    def test_sessions_do_not_leak_into_each_other(self, tmp_path):
        """Day 2's first bar must be treated as a session opening, not as a
        difference against day 1's closing cumulative."""
        st = ResearchStore(tmp_path / "research.db")
        d1 = int(dt.datetime(2026, 6, 1, 9, 15, tzinfo=IST).timestamp())
        d2 = int(dt.datetime(2026, 6, 2, 9, 15, tzinfo=IST).timestamp())
        st.upsert([Candle("AAA", "5m", d1, 10, 11, 9, 10, 900_000, "live"),
                   Candle("AAA", "5m", d2, 10, 11, 9, 10, 120_000, "live")])
        migrate(st)
        assert [b.volume for b in st.read("AAA", "5m")] == [900_000, 120_000]
        assert st.unknown_volume_timestamps("AAA", "5m") == set()
