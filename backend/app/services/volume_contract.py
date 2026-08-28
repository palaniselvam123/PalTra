"""The canonical volume contract, shared by every ingestion path.

**Contract.** A candle's `volume` field means exactly one thing:

    bar_volume = the volume traded during that candle

Nothing may put a cumulative counter in that field. The broker's raw value stays
available separately; it is never destroyed and never silently substituted.

**Why this module exists.** Two paths feed the same candle representation and
they disagreed:

* the tick path differenced a cumulative counter per tick, producing per-bar
  volume — correct;
* the backfill path took the broker's historical rows verbatim, which are
  cumulative-since-session-open — wrong, and indistinguishable from the other
  once written.

A chart could therefore show 10,698,449 for one bar and 220,437 for the next
purely because one came from history and the other from ticks. Any RVOL built
on that is meaningless. Rather than fix each path separately and hope they stay
in agreement, both now derive through the one function here.

Lives in `services/` rather than `research/` so the live path never has to
import from the research package — the isolation between them is deliberate and
tested.

**Source semantics.** Groww reports historical candle volume as cumulative from
the session open. The live tick feed likewise reports a cumulative day counter,
which `candle_store.on_tick` differences per tick.

**Derivation rule.** `bar_volume[t] = cumulative[t] - cumulative[t-1]` within a
session; the first bar's cumulative is its own volume.

**Session resets.** The counter resets near the close. Both the reset bar and
the bar after it are UNKNOWN, never zero — see `derive_session`.
"""
from __future__ import annotations

import datetime as dt

from app.core.market_clock import IST

OK = "OK"
FIRST_BAR = "FIRST_BAR"
UNKNOWN = "UNKNOWN"
TICK = "TICK"              # provenance: built entirely from observed ticks
BACKFILL = "BACKFILL"      # provenance: derived from broker history
RECONCILED = "RECONCILED"  # provenance: seeded by backfill, then continued from ticks
#
# RECONCILED exists because the two are genuinely a third thing, not a label of
# convenience: such a bar's early volume comes from the broker's history and its
# later volume from ticks observed here, joined at a recovered bucket-start
# anchor. A reader comparing ingestion paths must be able to exclude it, since
# it belongs wholly to neither.


def ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def derive_session(cumulative: list[int]) -> list[tuple[int | None, str]]:
    """Per-bar volumes for one session's cumulative series, in order.

    Returns `(bar_volume, quality)` per bar. Two cases are not ordinary
    differences:

    * **First bar** has no predecessor. Its cumulative IS its own volume, since
      the counter starts from zero at the open. Flagged `FIRST_BAR` rather than
      `OK` because it may also include pre-open auction volume, which candle
      data alone cannot settle.
    * **Counter resets** near the close. The reset bar's true volume is
      unrecoverable — the value may be a fresh counter or a partial snapshot,
      and one bar cannot distinguish them. The bar *after* a reset is equally
      unusable: differencing it against the reset baseline would report most of
      the day's volume as a single bar.

    Both reset cases return None. They are never clipped to zero: a fabricated
    zero is indistinguishable downstream from a genuinely quiet bar, which is
    exactly the silent error this rule exists to prevent.
    """
    out: list[tuple[int | None, str]] = []
    reset_at: set[int] = set()
    for i, cum in enumerate(cumulative):
        if i == 0:
            out.append((cum, FIRST_BAR))
        elif cum < cumulative[i - 1]:
            out.append((None, UNKNOWN))
            reset_at.add(i)
        elif (i - 1) in reset_at:
            out.append((None, UNKNOWN))
        else:
            out.append((cum - cumulative[i - 1], OK))
    return out


def derive_rows(
    rows: list[tuple[int, float, float, float, float, int]],
) -> list[tuple[int, float, float, float, float, int | None, int, str]]:
    """Convert broker history rows to the canonical contract.

    Input rows are `(ts, o, h, l, c, cumulative_volume)`; output adds the
    derived per-bar volume, preserves the raw cumulative, and carries the
    quality flag:

        (ts, o, h, l, c, bar_volume | None, raw_cumulative, quality)

    Sessions are split on the IST calendar day, so a day boundary is never
    differenced across.
    """
    if not rows:
        return []

    ordered = sorted(rows, key=lambda r: r[0])
    by_day: dict[dt.date, list[int]] = {}
    for idx, r in enumerate(ordered):
        by_day.setdefault(ist_date(r[0]), []).append(idx)

    out: list[tuple[int, float, float, float, float, int | None, int, str]] = [None] * len(ordered)  # type: ignore[list-item]
    for _day, idxs in by_day.items():
        derived = derive_session([ordered[i][5] for i in idxs])
        for i, (bar_volume, quality) in zip(idxs, derived):
            ts, o, h, low, c, raw = ordered[i]
            out[i] = (ts, o, h, low, c, bar_volume, raw, quality)
    return out
