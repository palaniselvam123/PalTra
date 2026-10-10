"""1-minute human review of an uncertain SMA position (review_on, off by default).

The bot's own candle (e.g. 5 minutes, candles.py) stays the strategy. This
module only notices when an open trade's SMA fast/slow gap on that candle has
narrowed into a small band around a cross (`review_gap_pct`), and builds a
read-out of the closed 1-minute candles for a person to look at. The person
answers EXIT (the engine closes the trade with USER_REVIEW_EXIT) or WAIT (only
recorded). No answer changes nothing: the strategy carries on as before.

Everything here is a pure function of closed candles: the forming candle of
either size is never read, so a replay sees exactly what the live desk saw.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from indicators import closed_technical_snapshot, enrich, sma_gap_pct

DEFAULT_GAP_PCT = 0.03
DEFAULT_COOLDOWN_MIN = 15
# An episode ends once the gap is this many times the band away again.
RESET_FACTOR = 1.5
# 1-minute candles shown on the review.
SHOW_CANDLES = 5

PENDING = "PENDING"
EXIT = "USER_REVIEW_EXIT"
WAIT = "USER_REVIEW_WAIT"
NO_RESPONSE = "NO_RESPONSE"
ALREADY_CLOSED = "ALREADY_CLOSED"


def review_on(cfg) -> bool:
    return bool(getattr(cfg, "review_on", False))


def gap_band(cfg) -> float:
    try:
        value = float(getattr(cfg, "review_gap_pct", None) or DEFAULT_GAP_PCT)
    except (TypeError, ValueError):
        return DEFAULT_GAP_PCT
    return value if value > 0 else DEFAULT_GAP_PCT


def cooldown(cfg) -> dt.timedelta:
    try:
        value = int(getattr(cfg, "review_cooldown_min", None) if getattr(cfg, "review_cooldown_min", None) is not None else DEFAULT_COOLDOWN_MIN)
    except (TypeError, ValueError):
        value = DEFAULT_COOLDOWN_MIN
    return dt.timedelta(minutes=max(0, value))


# Smart-review knobs.
CHECK_MINUTES_CHOICES = (1, 2, 3, 5, 10, 15)
# When the check candle fires the prompt: ALWAYS = like today; ONLY_IF_AGAINST
# = only when the 1-minute verdict reads against the trade; ONLY_IF_NOT_WITH =
# also when it reads mixed (anything but with the trade). An unreadable verdict
# (too little tape) always prompts — safer than silently skipping.
CHECK_MODES = ("ALWAYS", "ONLY_IF_AGAINST", "ONLY_IF_NOT_WITH")
# What happens when a prompt would fire and nobody is around: PROMPT = today's
# behaviour, EXIT = close the trade now (reason AUTO_REVIEW_EXIT), CONTINUE =
# log the uncertain stretch but never message.
DEFAULT_ANSWERS = ("PROMPT", "EXIT", "CONTINUE")
AUTO_EXIT = "AUTO_REVIEW_EXIT"
AUTO_CONTINUE = "AUTO_REVIEW_CONTINUE"


def check_minutes_list(cfg) -> list[int]:
    """One or more check candle sizes. The settings column stores them as "1,15";
    an older row with a plain integer still parses cleanly."""
    raw = getattr(cfg, "review_check_minutes", None)
    if raw is None:
        return [1]
    if isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        parts = str(raw).split(",")
    out: list[int] = []
    for part in parts:
        try:
            value = int(str(part).strip())
        except (TypeError, ValueError):
            continue
        if value in CHECK_MINUTES_CHOICES and value not in out:
            out.append(value)
    return out or [1]


def check_minutes(cfg) -> int:
    """First check-candle size — kept for callers that only need one."""
    return check_minutes_list(cfg)[0]


def check_mode(cfg) -> str:
    raw = str(getattr(cfg, "review_check_mode", None) or "ALWAYS").upper()
    return raw if raw in CHECK_MODES else "ALWAYS"


def default_answer(cfg) -> str:
    raw = str(getattr(cfg, "review_default_answer", None) or "PROMPT").upper()
    return raw if raw in DEFAULT_ANSWERS else "PROMPT"


def check_candle_evidence(tape: pd.DataFrame | None, minutes: int, sma_fast: int, sma_slow: int, atr_period: int = 14) -> dict | None:
    """`one_minute_evidence` read on the check candle (1-min tape resampled to `minutes`).

    Reuses the same SMAs, VWAP and verdict as the 1-minute readout, so the
    smart check reads the market the same way the card already shows it.
    """
    if minutes <= 1:
        return one_minute_evidence(tape, sma_fast, sma_slow, atr_period)
    # Imported here to keep review.py's import graph the same at module load.
    from candles import resample

    resampled = resample(tape, minutes)
    return one_minute_evidence(resampled, sma_fast, sma_slow, atr_period)


def check_candles_evidence(
    tape: pd.DataFrame | None,
    minutes_list: list[int],
    sma_fast: int,
    sma_slow: int,
    atr_period: int = 14,
) -> dict[int, dict | None]:
    """Evidence per requested check-candle size, keyed by minutes."""
    return {
        m: check_candle_evidence(tape, m, sma_fast, sma_slow, atr_period) for m in minutes_list
    }


def _gap(row) -> float | None:
    return sma_gap_pct(row.get("sma_9"), row.get("sma_21"))


@dataclass
class Uncertain:
    """The bot's candle that made the trade uncertain."""

    candle_ts: int
    sma_fast: float
    sma_slow: float
    gap_pct: float  # signed: (fast - slow) / slow x 100
    prev_gap_pct: float
    crossed: bool  # the lines have crossed against the trade (cross exit off)

    def as_dict(self) -> dict:
        return {
            "candle_ts": self.candle_ts,
            "sma_fast": round(self.sma_fast, 4),
            "sma_slow": round(self.sma_slow, 4),
            "gap_pct": round(self.gap_pct, 4),
            "prev_gap_pct": round(self.prev_gap_pct, 4),
            "narrowing": abs(self.gap_pct) < abs(self.prev_gap_pct),
            "crossed": self.crossed,
        }


def last_gap(frame: pd.DataFrame | None) -> float | None:
    """Signed gap % on the last closed candle of an enriched frame."""
    if frame is None or len(frame) < 2:
        return None
    return _gap(frame.iloc[-2])


def uncertainty(frame: pd.DataFrame | None, signal_direction: str, band: float, cross_exit: bool) -> Uncertain | None:
    """Is the trade's SMA relationship uncertain on the last closed candle?

    `frame` is enriched, oldest first, with the forming candle last (never read).
    Uncertain means |gap| <= band and either the gap is on the trade's side and
    narrowed since the candle before (heading for a cross), or, with the cross
    exit off, the lines have just crossed against the trade inside the band.
    """
    if frame is None or len(frame) < 3:
        return None
    bar, before = frame.iloc[-2], frame.iloc[-3]
    now, prev = _gap(bar), _gap(before)
    if now is None or prev is None or abs(now) > band:
        return None
    side = 1.0 if signal_direction == "LONG" else -1.0
    favour, favour_prev = now * side, prev * side
    approaching = favour >= 0 and favour < favour_prev
    crossed = favour < 0 and not cross_exit
    if not (approaching or crossed):
        return None
    try:
        ts = int(bar.get("ts"))
    except (TypeError, ValueError):
        return None
    return Uncertain(ts, float(bar.get("sma_9")), float(bar.get("sma_21")), float(now), float(prev), crossed)


def episode_over(gap_pct: float | None, band: float) -> bool:
    """The gap has moved well clear of the band: the next narrowing is a new event."""
    return gap_pct is not None and abs(gap_pct) > band * RESET_FACTOR


def _slope_word(values: list[float]) -> str:
    if len(values) < 2:
        return "unknown"
    change = values[-1] - values[0]
    scale = abs(values[-1]) * 0.00005  # 0.005% of price counts as flat
    if change > scale:
        return "rising"
    if change < -scale:
        return "falling"
    return "flat"


def one_minute_evidence(tape: pd.DataFrame | None, sma_fast: int, sma_slow: int, atr_period: int = 14) -> dict | None:
    """Read-out of the closed 1-minute candles. The forming minute is dropped first.

    Reuses the strategy's own indicators: SMA fast/slow, session VWAP, RSI(14)
    and the minute volume ratio from closed_technical_snapshot.
    """
    if tape is None or len(tape) < 3:
        return None
    frame = enrich(tape, sma_fast, sma_slow, atr_period)
    snap = closed_technical_snapshot(frame)
    closed = frame.iloc[:-1]
    if closed.empty or snap is None:
        return None
    last = closed.iloc[-SHOW_CANDLES:]
    candles = []
    for _, row in last.iterrows():
        o, c = float(row.get("open")), float(row.get("close"))
        candles.append(
            {
                "ts": int(row.get("ts")),
                "open": round(o, 2),
                "high": round(float(row.get("high")), 2),
                "low": round(float(row.get("low")), 2),
                "close": round(c, 2),
                "colour": "GREEN" if c > o else "RED" if c < o else "DOJI",
            }
        )
    tail = closed.iloc[-3:]
    fast = [float(v) for v in tail["sma_9"].tolist() if pd.notna(v)]
    slow = [float(v) for v in tail["sma_21"].tolist() if pd.notna(v)]
    gaps = [g for g in (_gap(row) for _, row in tail.iterrows()) if g is not None]
    close = snap.close

    def side_of(level: float | None) -> str | None:
        if close is None or level is None:
            return None
        return "above" if close > level else "below" if close < level else "at"

    return {
        "candle_ts": snap.timestamp,
        "candles": candles,
        "close": close,
        "sma_fast": snap.sma9,
        "sma_slow": snap.sma21,
        "gap_pct": snap.sma_gap_pct,
        "gap_trend": (
            "narrowing" if len(gaps) >= 2 and abs(gaps[-1]) < abs(gaps[0])
            else "widening" if len(gaps) >= 2 and abs(gaps[-1]) > abs(gaps[0])
            else "steady"
        ),
        "fast_slope": _slope_word(fast),
        "slow_slope": _slope_word(slow),
        "vs_fast": side_of(snap.sma9),
        "vs_slow": side_of(snap.sma21),
        "vwap": snap.vwap,
        "vs_vwap": side_of(snap.vwap),
        "rsi14": snap.rsi14,
        "volume_ratio": snap.volume_ratio,
    }


# Verdict labels: how the closed 1-minute candles read against the open trade.
WITH, AGAINST, MIXED = "WITH", "AGAINST", "MIXED"
# The net score (with minus against) needed for WITH / AGAINST, and the fewest readable checks.
VERDICT_MIN = 3


def verdict(one: dict | None, direction: str) -> dict | None:
    """How the closed 1-minute candles read for an open trade. Information only.

    Five plain checks, each +1 (with the trade), -1 (against it) or 0 (flat):
    the 1-minute fast SMA slope, price against the fast SMA, against the slow
    SMA, against VWAP, and the net move of the last three closes. The score is
    checks with the trade minus checks against it: VERDICT_MIN or more is WITH,
    -VERDICT_MIN or less is AGAINST, anything between is MIXED. Nothing
    reads this to trade: the person decides, and no answer keeps the trade.
    Returns None when fewer than three checks could be read.
    """
    if not one:
        return None
    side = 1 if direction == "LONG" else -1

    def word(value: str | None, up: str, down: str) -> int | None:
        if value is None or value == "unknown":
            return None
        return 1 if value == up else -1 if value == down else 0

    closes = [c["close"] for c in (one.get("candles") or [])]
    move = None
    if len(closes) >= 3:
        net = closes[-1] - closes[-3]
        move = 1 if net > 0 else -1 if net < 0 else 0
    readings = [
        ("SMA slope", word(one.get("fast_slope"), "rising", "falling")),
        ("price vs fast SMA", word(one.get("vs_fast"), "above", "below")),
        ("price vs slow SMA", word(one.get("vs_slow"), "above", "below")),
        ("price vs VWAP", word(one.get("vs_vwap"), "above", "below")),
        ("last 3 closes", move),
    ]
    read = [(name, v * side) for name, v in readings if v is not None]
    if len(read) < VERDICT_MIN:
        return None
    score = sum(v for _, v in read)
    behind = sum(1 for _, v in read if v > 0)
    against = sum(1 for _, v in read if v < 0)
    flat = len(read) - behind - against
    label = WITH if score >= VERDICT_MIN else AGAINST if score <= -VERDICT_MIN else MIXED
    return {
        "label": label,
        "score": score,
        "of": len(read),
        "with": behind,
        "against": against,
        "flat": flat,
        "checks": {name: ("with" if v > 0 else "against" if v < 0 else "flat") for name, v in read},
    }


def combined_verdict(evidence_by_minutes: dict[int, dict | None], direction: str) -> dict | None:
    """One verdict rolled up from several check candles. AGAINST if any says AGAINST;
    WITH only when every readable one says WITH; MIXED otherwise. None when nothing could be read."""
    if not evidence_by_minutes:
        return None
    labels: list[str] = []
    readable: list[tuple[int, dict]] = []
    for m, ev in evidence_by_minutes.items():
        if ev is None:
            continue
        v = verdict(ev, direction)
        if v is None:
            continue
        labels.append(v.get("label", MIXED))
        readable.append((m, v))
    if not readable:
        return None
    if AGAINST in labels:
        label = AGAINST
    elif all(l == WITH for l in labels):
        label = WITH
    else:
        label = MIXED
    totals = {k: sum(v.get(k, 0) for _, v in readable) for k in ("score", "of", "with", "against", "flat")}
    return {"label": label, "by_candle": [{"minutes": m, **v} for m, v in readable], **totals}


def verdict_line(v: dict | None, direction: str) -> str:
    if not v:
        return "Verdict: not enough 1-minute data to read a trend."
    tally = f"{v['with']} with · {v['against']} against · {v['flat']} flat of {v['of']}"
    if v["label"] == AGAINST:
        head = f"1-min trend is AGAINST this {direction}"
    elif v["label"] == WITH:
        head = f"1-min trend is WITH this {direction}"
    else:
        head = f"1-min trend is MIXED for this {direction}"
    return f"Verdict: {head} ({tally}). Information only — you decide."


_DOT = {"GREEN": "🟢", "RED": "🔴", "DOJI": "⚪"}


def _num(value, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _clock(ts: int | None) -> str:
    if ts is None:
        return "—"
    return (dt.datetime.fromtimestamp(int(ts), dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)).strftime("%H:%M")


def review_text(
    *,
    symbol: str,
    mode: str,
    direction: str,
    qty: int,
    entry: float,
    minutes: int,
    five: dict,
    one: dict | None,
    sma_fast: int,
    sma_slow: int,
    band: float,
) -> str:
    """The alert, short enough to read on a phone mid-trade."""
    gap = five.get("gap_pct") or 0.0
    lines = [
        f"⚠️ {symbol} — {minutes}-MIN SMA REVIEW · {mode}",
        f"{direction} {qty} @ ₹{entry:,.2f}",
        "",
        f"{minutes}-min (candle closed {_clock((five.get('candle_ts') or 0) + minutes * 60)}):",
        f"  SMA{sma_fast} {_num(five.get('sma_fast'))} · SMA{sma_slow} {_num(five.get('sma_slow'))}",
        f"  Gap {gap:+.3f}% ({'crossed' if five.get('crossed') else 'narrowing'}, band ±{band:g}%) · UNCERTAIN",
    ]
    if one:
        closes = " → ".join(_num(c["close"]) for c in one["candles"][-4:])
        dots = " ".join(_DOT.get(c["colour"], "⚪") for c in one["candles"][-4:])
        lines += [
            "",
            f"1-min review (to {_clock((one.get('candle_ts') or 0) + 60)}):",
            f"  Last closes: {closes}",
            f"  Candles: {dots}",
            f"  SMA{sma_fast} {one['fast_slope']} · SMA{sma_slow} {one['slow_slope']} · gap {one['gap_trend']}",
            f"  Price {one['vs_fast'] or '—'} SMA{sma_fast}, {one['vs_slow'] or '—'} SMA{sma_slow}",
            f"  VWAP {_num(one['vwap'])} (price {one['vs_vwap'] or '—'})"
            + (f" · RSI {one['rsi14']:.0f}" if one.get("rsi14") is not None else "")
            + (f" · vol {one['volume_ratio']:.1f}× avg" if one.get("volume_ratio") is not None else ""),
        ]
        lines.append(verdict_line(one.get("verdict"), direction))
    else:
        lines += ["", "1-min review: not enough closed 1-minute candles yet."]
    lines += [
        "",
        f"The {minutes}-min trend is uncertain. EXIT or WAIT in the terminal.",
        "No answer = keep holding.",
    ]
    return "\n".join(lines)
