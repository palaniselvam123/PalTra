"""Derive per-bar volume from the broker's cumulative counter, preserving raw.

The research store's `volume` column holds the broker's **cumulative volume
since the session open**, not the volume traded in that bar. Verified across the
whole dataset: no symbol's volume ever falls within a session except at the
session tail, and successive differences are plausible five-minute volumes.

This adds two derived columns and never destroys the original:

* `raw_cumulative_volume` — the value exactly as the broker returned it.
* `bar_volume` — the causal first difference within a session, NULL where it
  cannot be derived honestly.
* `volume_quality` — why a bar's derived value is what it is.

**First bar of a session.** `bar_volume[first] = cumulative[first]`, because the
counter starts from zero before the open. Flagged `FIRST_BAR` rather than `OK`:
no session in the dataset opens at zero, and the median first-bar value is 3.15x
a typical later bar, which is consistent with a genuine opening burst but also
with pre-open auction volume being folded in. That ambiguity cannot be resolved
from this data, so the flag records it instead of hiding it.

**Session-tail resets.** 791 bars (0.43%) show the counter falling within a
session, every one of them in the 15:00 hour, and 98.5% falling to under a tenth
of the previous value. These are counter resets near the close, not gradual
revisions. Both the reset bar and the bar after it are marked `UNKNOWN`: the
reset bar's difference is negative, and the following bar's difference is
computed against a reset baseline, so it would report most of the day's volume
as a single bar. Neither is clipped to zero — a fabricated zero is
indistinguishable from a real quiet bar downstream, which is exactly the kind of
silent error that produced this investigation.
"""
from __future__ import annotations

from app.research.store import (
    FIRST_BAR, OK, UNKNOWN, ResearchStore, ist_date, store as default_store,
)

MIGRATION_SQL = [
    "ALTER TABLE research_candles ADD COLUMN raw_cumulative_volume INTEGER",
    "ALTER TABLE research_candles ADD COLUMN bar_volume INTEGER",
    "ALTER TABLE research_candles ADD COLUMN volume_quality TEXT",
]


def migrate(store: ResearchStore | None = None) -> dict:
    """Add the derived columns to an existing database and populate them.

    Only needed for stores written before `upsert` maintained these columns
    itself; new writes derive their own. Idempotent, so re-running is safe and
    doubles as a repair if a session is ever suspected of being stale.
    """
    st = store or default_store

    with st._conn() as conn:  # noqa: SLF001 — same package
        existing = {r[1] for r in conn.execute("PRAGMA table_info(research_candles)")}
        for sql in MIGRATION_SQL:
            column = sql.split("ADD COLUMN ")[1].split()[0]
            if column not in existing:
                conn.execute(sql)
        rows = conn.execute(
            "SELECT DISTINCT symbol, interval, source, ts FROM research_candles"
        ).fetchall()

    sessions = {(symbol, interval, source, ist_date(ts)) for symbol, interval, source, ts in rows}
    updated = st.rederive_volume(sessions)

    with st._conn() as conn:  # noqa: SLF001
        counts = dict(
            conn.execute(
                "SELECT volume_quality, COUNT(*) FROM research_candles GROUP BY volume_quality"
            ).fetchall()
        )
    total = sum(counts.values()) or 1
    return {
        "rows_updated": updated,
        "sessions": len(sessions),
        "ok": counts.get(OK, 0),
        "first_bar": counts.get(FIRST_BAR, 0),
        "unknown": counts.get(UNKNOWN, 0),
        "unknown_pct": round(counts.get(UNKNOWN, 0) / total * 100, 3),
    }
