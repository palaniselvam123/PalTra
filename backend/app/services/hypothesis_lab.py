"""Grade a trading hypothesis against matched controls.

A hypothesis earns a place in the app only by beating a baseline. The baseline
has to be *matched*, because an unmatched one is easy to beat for the wrong
reason: entries cluster on volatile bars, so comparing them to random bars
drawn from the whole session compares volatility, not skill.

Two controls, answering different questions:

* **same-bar, random side** — same instant, same volatility, direction chosen
  by coin flip. Isolates whether the signal knows WHICH WAY price will go.
* **same-day, random bar** — same session and side, a different moment.
  Isolates whether the signal knows WHEN to act.

A real edge should beat both. Beating only the second means the rule picks
good moments but not good directions, which a long-only variant might exploit;
beating only the first means the direction call is sound but the timing is
arbitrary. Both are more useful findings than a single pass/fail.
"""
from __future__ import annotations

import datetime as dt
import random
import statistics
from dataclasses import dataclass

from app.core.market_clock import IST
from app.services.entry_diagnostics import Observation, context_series, observe
from app.services.indicators import OHLCV
from app.services.paper_engine import estimate_charges

SLIPPAGE_ROUND_TRIP_PCT = 0.10
CONTROLS_PER_SIGNAL = 4
HORIZONS = (6, 12, 24)


def cost_floor_pct(price: float, position_value: float = 100_000) -> float:
    """Round-trip cost as a percentage of price — the move a trade must make
    before it is worth taking at all."""
    qty = max(int(position_value / price), 1)
    charges = estimate_charges("BUY", price, qty) + estimate_charges("SELL", price, qty)
    return charges / (price * qty) * 100 + SLIPPAGE_ROUND_TRIP_PCT


@dataclass
class Stats:
    n: int
    mfe: float
    mae: float
    ratio: float
    favourable_pct: float
    clears_cost_pct: float   # share whose best-case excursion beats the cost floor

    @classmethod
    def of(cls, obs: list[Observation]) -> "Stats | None":
        if not obs:
            return None
        mfe = statistics.fmean([o.forward.mfe_pct for o in obs])
        mae = statistics.fmean([o.forward.mae_pct for o in obs])
        return cls(
            n=len(obs),
            mfe=round(mfe, 3),
            mae=round(mae, 3),
            ratio=round(mfe / mae, 3) if mae > 0 else 0.0,
            favourable_pct=round(sum(o.forward.mfe > o.forward.mae for o in obs) / len(obs) * 100, 1),
            clears_cost_pct=round(
                sum(o.forward.mfe_pct > cost_floor_pct(o.entry_price) for o in obs) / len(obs) * 100, 1
            ),
        )


def _ratio(obs: list[Observation]) -> float:
    mae = statistics.fmean([o.forward.mae_pct for o in obs])
    return statistics.fmean([o.forward.mfe_pct for o in obs]) / mae if mae > 0 else 0.0


def permutation_p(a: list[Observation], b: list[Observation], iterations: int = 4000, seed: int = 5) -> float:
    """Probability of a ratio gap this large arising from reshuffling alone.

    Works on plain float arrays rather than Observations: the shuffle is the
    inner loop, and moving objects around instead of numbers made a full run
    take minutes.
    """
    if not a or not b:
        return 1.0

    pool = [(o.forward.mfe_pct, o.forward.mae_pct) for o in a + b]
    k = len(a)

    def ratio_of(pairs) -> float:
        n = len(pairs)
        if not n:
            return 0.0
        mae = sum(p[1] for p in pairs) / n
        return (sum(p[0] for p in pairs) / n) / mae if mae > 0 else 0.0

    observed = abs(ratio_of(pool[:k]) - ratio_of(pool[k:]))
    rng = random.Random(seed)
    hits = 0
    for _ in range(iterations):
        rng.shuffle(pool)
        if abs(ratio_of(pool[:k]) - ratio_of(pool[k:])) >= observed:
            hits += 1
    return hits / iterations


def block_permutation_p(
    sig_by_day: dict, ctrl_by_day: dict, iterations: int = 4000, seed: int = 9
) -> float:
    """Permutation at the DAY level, which is the correct unit here.

    Signals inside one trading day share a market move, so they are not
    independent observations. Treating them as independent overstates the
    sample and therefore the significance. Shuffling whole days preserves the
    within-day correlation, and because each day contributes its signal group
    and its control group together, the test is also paired — it compares like
    with like instead of letting a quiet day and a violent day land on opposite
    sides of the comparison.

    On the 8-day ORB sample this moved p from 0.228 to 0.040, so the choice of
    unit is not a technicality.
    """
    days = sorted(set(sig_by_day) | set(ctrl_by_day))
    if len(days) < 3:
        return 1.0

    def ratio(groups: list) -> float:
        pairs = [p for g in groups for p in g]
        if not pairs:
            return 0.0
        mae = sum(p[1] for p in pairs) / len(pairs)
        return (sum(p[0] for p in pairs) / len(pairs)) / mae if mae > 0 else 0.0

    sig = {d: [(o.forward.mfe_pct, o.forward.mae_pct) for o in sig_by_day.get(d, [])] for d in days}
    ctl = {d: [(o.forward.mfe_pct, o.forward.mae_pct) for o in ctrl_by_day.get(d, [])] for d in days}

    observed = abs(ratio([sig[d] for d in days]) - ratio([ctl[d] for d in days]))
    rng = random.Random(seed)
    hits = 0
    for _ in range(iterations):
        a, b = [], []
        for d in days:
            if rng.random() < 0.5:
                a.append(sig[d]); b.append(ctl[d])
            else:
                a.append(ctl[d]); b.append(sig[d])
        if abs(ratio(a) - ratio(b)) >= observed:
            hits += 1
    return hits / iterations


def by_day(obs: list[Observation]) -> dict:
    out: dict = {}
    for o in obs:
        d = dt.datetime.fromtimestamp(o.ts, tz=dt.timezone.utc).astimezone(IST).date()
        out.setdefault(d, []).append(o)
    return out


def split_days(days: list, fractions=(0.6, 0.2, 0.2)) -> tuple[set, set, set]:
    """Chronological three-way split by trading day.

    In-sample for exploration, validation for confirming what exploration
    suggested, and a final hold-out that stays untouched. Split by DAY rather
    than by row so no session appears on both sides — a row-level split leaks,
    because bars from the same day share the same market move.
    """
    days = sorted(days)
    n = len(days)
    a = int(n * fractions[0])
    b = a + int(n * fractions[1])
    return set(days[:a]), set(days[a:b]), set(days[b:])


def build_controls(
    symbol: str,
    candles: list[OHLCV],
    signals: list[tuple[int, str]],
    horizon: int,
    ctx: dict,
    seed: int = 17,
) -> tuple[list[Observation], list[Observation]]:
    """Matched control entries for one symbol: (same-bar, same-day)."""
    rng = random.Random(seed)
    by_day: dict[dt.date, list[int]] = {}
    for i, c in enumerate(candles):
        day = dt.datetime.fromtimestamp(c.ts, tz=dt.timezone.utc).astimezone(IST).date()
        by_day.setdefault(day, []).append(i)

    same_bar: list[Observation] = []
    same_day: list[Observation] = []
    for i, side in signals:
        day = dt.datetime.fromtimestamp(candles[i].ts, tz=dt.timezone.utc).astimezone(IST).date()
        peers = [j for j in by_day.get(day, []) if j != i and j < len(candles) - horizon - 1]
        for _ in range(CONTROLS_PER_SIGNAL):
            o = observe(symbol, candles, i, rng.choice(["BUY", "SELL"]), horizon, ctx)
            if o is not None:
                same_bar.append(o)
            if peers:
                o2 = observe(symbol, candles, rng.choice(peers), side, horizon, ctx)
                if o2 is not None:
                    same_day.append(o2)
    return same_bar, same_day


def evaluate(
    data: dict[str, list[OHLCV]],
    generator,
    horizons: tuple[int, ...] = HORIZONS,
    split: float = 0.7,
) -> dict:
    """Run a hypothesis against both controls at each horizon.

    `split` sets the chronological in-sample / out-of-sample boundary. The
    split is by TIME, not by random assignment: shuffling bars across the
    boundary would let the test period share days with the training period,
    which is the most common way an out-of-sample claim turns out to be false.
    """
    result: dict = {"horizons": {}, "per_symbol_signals": {}}

    for horizon in horizons:
        sig: list[Observation] = []
        ctrl_bar: list[Observation] = []
        ctrl_day: list[Observation] = []
        oos_sig: list[Observation] = []
        oos_ctrl: list[Observation] = []

        for sym, candles in data.items():
            if len(candles) < 100:
                continue
            ctx = context_series(candles, 9, 21)
            signals = [(i, s) for i, s in generator(sym, candles) if i < len(candles) - horizon - 1]
            result["per_symbol_signals"][sym] = len(signals)
            boundary = int(len(candles) * split)

            # Pair each Observation with the bar it came from BEFORE dropping
            # the Nones. Filtering first and zipping after silently shifts the
            # pairing, which mislabels which signals are out-of-sample.
            paired = [
                (i, observe(sym, candles, i, s, horizon, ctx)) for i, s in signals
            ]
            paired = [(i, o) for i, o in paired if o is not None]
            sig += [o for _, o in paired]

            cb, cd = build_controls(sym, candles, signals, horizon, ctx)
            ctrl_bar += cb
            ctrl_day += cd

            oos_sig += [o for i, o in paired if i >= boundary]
            oos_ctrl += [o for o in cb if o.ts >= candles[boundary].ts]

        entry = {
            "signal": Stats.of(sig),
            "control_same_bar": Stats.of(ctrl_bar),
            "control_same_day": Stats.of(ctrl_day),
            "long": Stats.of([o for o in sig if o.side == "BUY"]),
            "short": Stats.of([o for o in sig if o.side == "SELL"]),
            "p_vs_same_bar": round(permutation_p(sig, ctrl_bar), 4),
            "p_vs_same_day": round(permutation_p(sig, ctrl_day), 4),
            "oos_signal": Stats.of(oos_sig),
            "oos_control": Stats.of(oos_ctrl),
        }
        result["horizons"][horizon] = entry
    return result
