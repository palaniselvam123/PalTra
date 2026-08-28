"""Signal-episode diagnostics.

A hypothesis reporting 2,571 signals sounds like 2,571 pieces of evidence. It
usually is not. Signals fire on overlapping input windows, so a single market
event can emit a run of nominal signals that are near-copies of one another —
and a sample of correlated near-copies supports far weaker conclusions than its
count suggests, even when the statistics already block by day.

This measures that clustering. It is diagnostic only: nothing here filters
signals, changes a hypothesis, or revises a recorded verdict.

**Episode rule, and why it is causal.** Two signals belong to the same episode
when they share a symbol, a trading day and a direction, and are separated by
fewer than `min_separation_bars`. The default separation is the hypothesis's own
lookback window: signals closer together than that were computed from
overlapping input data and therefore cannot be independent observations of
distinct events. That argument is about the inputs alone — no forward return is
consulted, and the separation must never be chosen by looking at performance.

Direction is part of the key because a long and a short minutes apart are
different events, not a continuation of one.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from dataclasses import dataclass

from app.core.market_clock import IST

DEFAULT_MIN_SEPARATION_BARS = 12


@dataclass(frozen=True)
class SignalPoint:
    """The minimum a hypothesis must expose for episode analysis."""

    symbol: str
    index: int
    ts: int
    side: str


@dataclass
class Episode:
    symbol: str
    day: dt.date
    side: str
    indices: list[int]

    @property
    def size(self) -> int:
        return len(self.indices)

    @property
    def duration_bars(self) -> int:
        """Bars spanned from first to last signal. A lone signal spans 0."""
        return self.indices[-1] - self.indices[0]


def ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def group_episodes(
    points: list[SignalPoint], min_separation_bars: int = DEFAULT_MIN_SEPARATION_BARS
) -> list[Episode]:
    """Collapse signals into episodes of correlated market events."""
    by_key: dict[tuple[str, dt.date, str], list[int]] = defaultdict(list)
    for p in points:
        by_key[(p.symbol, ist_date(p.ts), p.side)].append(p.index)

    episodes: list[Episode] = []
    for (symbol, day, side), indices in by_key.items():
        indices.sort()
        run = [indices[0]]
        for i in indices[1:]:
            if i - run[-1] < min_separation_bars:
                run.append(i)
            else:
                episodes.append(Episode(symbol, day, side, run))
                run = [i]
        episodes.append(Episode(symbol, day, side, run))
    return episodes


def episode_report(
    points: list[SignalPoint], min_separation_bars: int = DEFAULT_MIN_SEPARATION_BARS
) -> dict:
    """The seven clustering measures, plus the size distribution behind them."""
    if not points:
        return {"signals": 0, "episodes": 0}

    episodes = group_episodes(points, min_separation_bars)
    days = {ist_date(p.ts) for p in points}
    sizes = [e.size for e in episodes]
    durations = [e.duration_bars for e in episodes]

    return {
        "signals": len(points),
        "signal_days": len(days),
        "signals_per_signal_day": round(len(points) / len(days), 2),
        "episodes": len(episodes),
        "signals_per_episode": round(len(points) / len(episodes), 2),
        "median_episode_duration_bars": round(statistics.median(durations), 1),
        "max_episode_duration_bars": max(durations),
        # Context for the averages above: a mean of 2 signals per episode means
        # something different when most episodes are singletons than when they
        # are uniformly pairs.
        "episodes_per_signal_day": round(len(episodes) / len(days), 2),
        "singleton_episodes_pct": round(sum(s == 1 for s in sizes) / len(sizes) * 100, 1),
        "largest_episode_signals": max(sizes),
        "min_separation_bars": min_separation_bars,
    }


def format_report(name: str, report: dict) -> str:
    if not report.get("signals"):
        return f"{name}: no signals"
    r = report
    return "\n".join([
        f"{name}",
        f"  total signals                {r['signals']:,}",
        f"  signal-days                  {r['signal_days']}",
        f"  signals per signal-day       {r['signals_per_signal_day']}",
        f"  distinct episodes            {r['episodes']:,}",
        f"  signals per episode          {r['signals_per_episode']}",
        f"  median episode duration      {r['median_episode_duration_bars']} bars",
        f"  max episode duration         {r['max_episode_duration_bars']} bars",
        f"  (episodes per signal-day     {r['episodes_per_signal_day']};  "
        f"singletons {r['singleton_episodes_pct']}%;  "
        f"largest episode {r['largest_episode_signals']} signals;  "
        f"separation {r['min_separation_bars']} bars)",
    ])
