"""Executed volume exit (`flow_exit`, off by default; per bot and per stock, PAPER and LIVE, replays too; owner's request
after the SOLARINDS case: buyers held most of the *waiting* orders while sellers kept hitting the bids).

A trade is closed once the other side did at least `flow_exit_pct` % (60) of the shares traded at market over a
window: a buy when sellers did, a sell when buyers did. The buy / sell split is the estimate of trade_flow.py (the
per-second prices the bots record, read with the tick rule, else the 1-minute candle's shape), so it reads *executed*
trades, unlike the buyers / sellers fade exit (pressure_exit.py), which reads the orders still waiting.

- **Window** (`flow_exit_window`): the last N closed 1-minute candles (1, 2, 3, 5, 10, 15), judged once per newly
  closed minute, or 0 = the candle the bot exits on (its own candle, or the colour / pattern candle) just closed.
  Only minutes from the entry on count (the entry minute itself when the trade opened in its first 15 seconds), and a
  window is judged only once it lies wholly after the entry.
- **Only while losing** (`flow_exit_loss`): closes only a trade under water at the last price.
- **Waiting orders agree** (`flow_exit_book`): also needs the latest Most active read of the stock (at most
  pressure_exit.MAX_AGE_SEC old) to show the other side holding more than half of the waiting orders; no fresh read
  = no close, so one burst of selling against a strong order book does not close a trade.

**Use it to confirm the other exits** (`flow_exit_mode` `EXIT` default | `CONFIRM`; owner's request: the executed volume
as a check, like the entry checks, instead of an exit of its own): with `CONFIRM` the rule never closes a trade itself.
Instead every signal exit in `CONFIRM_REASONS` (candle colour turn, counter candles, SMA cross, the SMA 9 exits,
smaller candles, exit candle cross, Supertrend, Bollinger, EMA, MACD, cumulative delta, slow SMA) closes only when the
other side also did at least `flow_exit_pct` % of the shares traded over the window (`confirm_span`: the last N minutes,
or 0 = the last exit candle that closed, cut at the entry); otherwise the trade is held and that exit is judged again the
next time it fires (`StrategyEngine._flow_hold`). Too little volume to read lets the exit go. Stops, targets, the
square-off, recovery, the gap fade and the kill switch are never held. "Only while losing" and "waiting orders agree"
belong to the exit mode and are not read here.

Too little volume to read (fewer than half the window's minutes with volume) closes nothing. Exits go through
`StrategyEngine._exit_now` as `FLOW_EXIT` (a LIVE stop is cancelled first; a LIVE position restored without its stop
id is left to that stop). The stops, targets and every other exit stay; whichever comes first closes the trade. No
order path of its own.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

EXIT_REASON = "FLOW_EXIT"
DEFAULT_PCT = 60.0
MIN_PCT = 50.0
MAX_PCT = 95.0
DEFAULT_WINDOW = 3
MODES = ("EXIT", "CONFIRM")
# The signal exits a CONFIRM-mode executed volume check must agree with (never stops, targets or the square-off).
CONFIRM_REASONS = frozenset({
    "MA_CROSS", "PROFITABLE_TREND_EXHAUSTION", "COLOUR_TURN", "SMA9_VTURN", "SMA9_TURN", "SMA9_TURN_AGAINST",
    "SMA9_BEND", "SMA9_DECEL", "COUNTER_CANDLES", "SMALL_CANDLE_EXIT", "EXIT_CANDLE_CROSS", "SUPERTREND",
    "BB_MIDDLE", "BB_TARGET", "EMA_CROSS", "MACD_CROSS", "CVD_DIVERGENCE", "SLOW_SMA_BREACH",
})
WINDOWS = (0, 1, 2, 3, 5, 10, 15)  # the ready-made choices on the Settings card
MAX_WINDOW = 60  # a custom window: any whole number of minutes from 1 to this (0 = the exit candle)
ENTRY_GRACE_SEC = 15       # an entry this close to its minute's open counts that minute as the trade's own


def on(cfg) -> bool:
    return bool(getattr(cfg, "flow_exit", False))


def pct(cfg) -> float:
    value = getattr(cfg, "flow_exit_pct", None)
    try:
        value = float(DEFAULT_PCT if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_PCT
    return min(MAX_PCT, max(MIN_PCT, value))


def window(cfg) -> int:
    value = getattr(cfg, "flow_exit_window", None)
    try:
        value = int(DEFAULT_WINDOW if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_WINDOW
    return value if 0 <= value <= MAX_WINDOW else DEFAULT_WINDOW


def mode(cfg) -> str:
    """EXIT (closes the trade itself, the default) or CONFIRM (only lets the other exits close)."""
    value = str(getattr(cfg, "flow_exit_mode", None) or "EXIT").strip().upper()
    return value if value in MODES else "EXIT"


def confirms(cfg) -> bool:
    return on(cfg) and mode(cfg) == "CONFIRM"


def closes(cfg) -> bool:
    return on(cfg) and mode(cfg) == "EXIT"


def confirm_span(now_ts: float, entry_ts: float, win: int, exit_minutes: int) -> tuple[int, int] | None:
    """(start, end) of the closed minutes a CONFIRM check reads now, or None when there is nothing yet.

    The last `win` minutes, or with 0 the last exit candle (of `exit_minutes` from 09:15 IST) that has closed; never
    minutes before the entry. When that candle closed before the entry's first minute, the minutes since the entry."""
    end = int(now_ts) - int(now_ts) % 60
    first = first_minute(entry_ts)
    if win > 0:
        start = end - win * 60
    else:
        size = max(1, int(exit_minutes)) * 60
        day = dt.datetime.fromtimestamp(end - 1, IST)
        open_ts = int(dt.datetime.combine(day.date(), dt.time(9, 15), tzinfo=IST).timestamp())
        closed = open_ts + max(0, (end - open_ts) // size) * size
        if closed > open_ts:
            start, end = closed - size, closed
        else:
            start = end - size
    start = max(start, first)
    if start >= end:
        start, end = first, int(now_ts) - int(now_ts) % 60
    if start >= end:
        return None
    return start, end


def loss_only(cfg) -> bool:
    return bool(getattr(cfg, "flow_exit_loss", False))


def book_agree(cfg) -> bool:
    return bool(getattr(cfg, "flow_exit_book", False))


def first_minute(entry_ts: float) -> int:
    """The first 1-minute candle (its open, epoch seconds) that counts as the trade's own."""
    start = int(entry_ts) - int(entry_ts) % 60
    return start if entry_ts - start <= ENTRY_GRACE_SEC else start + 60


def span(now_ts: float, entry_ts: float, win: int, exit_minutes: int) -> tuple[int, int] | None:
    """(start, end) epoch seconds of the minutes to read now, or None when nothing new is due.

    `end` is the close of the last closed 1-minute candle. With a window of N minutes the span is the N minutes before
    it; with 0 it is the exit candle that closed at `end` (candles of `exit_minutes` from 09:15 IST), else None."""
    end = int(now_ts) - int(now_ts) % 60
    first = first_minute(entry_ts)
    if win > 0:
        start = end - win * 60
    else:
        size = max(1, int(exit_minutes)) * 60
        day = dt.datetime.fromtimestamp(end - 1, IST)
        open_ts = int(dt.datetime.combine(day.date(), dt.time(9, 15), tzinfo=IST).timestamp())
        if end <= open_ts or (end - open_ts) % size:
            return None
        start = end - size
    if start < first:
        return None
    return start, end


def judge(points: list[dict], direction: str, level: float, minutes: int) -> tuple[bool | None, str]:
    """(close?, plain note) from the window's executed volume points; None = too little volume to read."""
    with_volume = [p for p in points if p["buy"] + p["sell"] > 0]
    if len(with_volume) < max(1, minutes // 2):
        return None, f"too little volume to read in the last {minutes} min"
    buy = sum(p["buy"] for p in with_volume)
    sell = sum(p["sell"] for p in with_volume)
    total = buy + sell
    other = "sellers" if direction == "LONG" else "buyers"
    share = round(100.0 * (sell if direction == "LONG" else buy) / total, 1)
    kinds = {p.get("src") for p in with_volume}
    src = "ticks" if kinds == {"T"} else "candles" if kinds == {"C"} else "ticks + candles"
    note = f"{other} did {share:g}% of the shares traded in {minutes} min ({src}; exit at {level:g}%)"
    return share >= level, note


def book_note(symbol: str, direction: str, now_ts: float) -> tuple[bool, str]:
    """(the waiting orders agree?, note): the other side holds more than half in a fresh Most active read."""
    import pressure_exit

    rows = pressure_exit._rows(symbol, now_ts)
    if not rows:
        return False, "no Most active read of the waiting orders"
    at, share = rows[-1]
    if now_ts - at > pressure_exit.MAX_AGE_SEC:
        return False, f"last Most active read is {int((now_ts - at) // 60)} min old"
    other = 100.0 - pressure_exit.side_share(share, direction)
    who = "sellers" if direction == "LONG" else "buyers"
    if other > 50.0:
        return True, f"{who} hold {other:g}% of the waiting orders"
    return False, f"{who} hold only {other:g}% of the waiting orders"


# ---- the same estimate as an entry check (flow_entry) -----------------------------------------------------------------
# An entry (any path through StrategyEngine.apply_signal: cross, colour, join, late / early-reversal entry, turn, re-entry)
# is refused unless the real order's own side did at least `flow_entry_pct` % (55) of the shares traded at market over
# `flow_entry_window` closed minutes (3; 0 = the length of the candle the bot exits on, ending at the last closed minute).
# Too little volume to read refuses too, like the other checks when a line is not ready. Direct orders (news, hand-offs,
# Force, fast-mover) never read it.

DEFAULT_ENTRY_PCT = 55.0


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "flow_entry", False))


def entry_pct(cfg) -> float:
    value = getattr(cfg, "flow_entry_pct", None)
    try:
        value = float(DEFAULT_ENTRY_PCT if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_ENTRY_PCT
    return min(MAX_PCT, max(MIN_PCT, value))


def entry_window(cfg) -> int:
    value = getattr(cfg, "flow_entry_window", None)
    try:
        value = int(DEFAULT_WINDOW if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_WINDOW
    return value if 0 <= value <= MAX_WINDOW else DEFAULT_WINDOW


def entry_span(now_ts: float, win: int, exit_minutes: int) -> tuple[int, int]:
    """(start, end) of the closed minutes an entry reads: the last `win` minutes, or with 0 the last closed exit candle
    (candles of `exit_minutes` from 09:15 IST): the very candle the chart draws at that size, so its hover % is what the
    bot read (owner's request)."""
    end = int(now_ts) - int(now_ts) % 60
    if win > 0:
        return end - win * 60, end
    size = max(1, int(exit_minutes)) * 60
    day = dt.datetime.fromtimestamp(end - 1, IST)
    open_ts = int(dt.datetime.combine(day.date(), dt.time(9, 15), tzinfo=IST).timestamp())
    closed = open_ts + max(0, (end - open_ts) // size) * size
    if closed > open_ts:
        return closed - size, closed
    return end - size, end


def entry_refusal(points: list[dict], side: str, level: float, minutes: int) -> str | None:
    """None when the order's own side did at least `level` % of the executed volume, else the plain refusal."""
    with_volume = [p for p in points if p["buy"] + p["sell"] > 0]
    if len(with_volume) < max(1, minutes // 2):
        return f"executed volume: too little volume to read in the last {minutes} min"
    buy = sum(p["buy"] for p in with_volume)
    sell = sum(p["sell"] for p in with_volume)
    own = round(100.0 * (buy if side == "LONG" else sell) / (buy + sell), 1)
    if own >= level:
        return None
    who = "buyers" if side == "LONG" else "sellers"
    return f"executed volume: {who} did only {own:g}% of the shares traded in {minutes} min (need {level:g}%)"


# ---- executed volume starts the trade (flow_trigger) ------------------------------------------------------------------
# The other way round from the Most active entry (pressure_exit AT_ARM: the waiting orders start the trade): a flat armed
# stock is entered once buyers did at least `flow_trigger_pct` % (60) of the shares traded at market over the window
# (`flow_trigger_window`, 3 closed minutes; 0 = one exit candle's length) — a buy — or sellers did — a sell. The waiting
# orders are then the check (`flow_trigger_book`, 55; 0 = not checked): the order's side must hold at least that % of the
# waiting orders in a Most active read at most pressure_exit.MAX_AGE_SEC old; no fresh read = no order. Judged once per
# closed window end; every entry check, the stop, flip, cut-off, caps and one LIVE bot per stock apply (apply_signal).

TRIGGER_REASON = "EXECUTED_VOLUME"
DEFAULT_TRIGGER_PCT = 60.0
DEFAULT_TRIGGER_BOOK = 55.0


def trigger_on(cfg) -> bool:
    return bool(getattr(cfg, "flow_trigger", False))


def trigger_pct(cfg) -> float:
    value = getattr(cfg, "flow_trigger_pct", None)
    try:
        value = float(DEFAULT_TRIGGER_PCT if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_TRIGGER_PCT
    return min(MAX_PCT, max(MIN_PCT, value))


def trigger_window(cfg) -> int:
    value = getattr(cfg, "flow_trigger_window", None)
    try:
        value = int(DEFAULT_WINDOW if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_WINDOW
    return value if 0 <= value <= MAX_WINDOW else DEFAULT_WINDOW


def trigger_book(cfg) -> float:
    """The waiting-orders check in %, 0 = not checked."""
    value = getattr(cfg, "flow_trigger_book", None)
    try:
        value = float(DEFAULT_TRIGGER_BOOK if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_TRIGGER_BOOK
    return 0.0 if value <= 0 else min(MAX_PCT, max(30.0, value))


def split(points: list[dict], minutes: int) -> tuple[float, str] | None:
    """(buyers' % of the executed volume, source word) over the points, or None with too little volume to read."""
    with_volume = [p for p in points if p["buy"] + p["sell"] > 0]
    if not with_volume or len(with_volume) < max(1, minutes // 2):
        return None
    buy = sum(p["buy"] for p in with_volume)
    sell = sum(p["sell"] for p in with_volume)
    kinds = {p.get("src") for p in with_volume}
    src = "ticks" if kinds == {"T"} else "candles" if kinds == {"C"} else "ticks + candles"
    return round(100.0 * buy / (buy + sell), 1), src


def trigger_side(buy_pct: float, level: float) -> str | None:
    """LONG when buyers did at least `level` %, SHORT when sellers did, else None."""
    if buy_pct >= level:
        return "LONG"
    if 100.0 - buy_pct >= level:
        return "SHORT"
    return None


def latest_wait(symbol: str, now_ts: float) -> tuple[float, float] | None:
    """(buyers' % of the waiting orders, read time) of the latest Most active read at most MAX_AGE_SEC old, else None."""
    import pressure_exit

    rows = pressure_exit._rows(symbol, now_ts)
    if not rows:
        return None
    at, share = rows[-1]
    if now_ts - at > pressure_exit.MAX_AGE_SEC:
        return None
    return round(share * 100.0, 1), at


def book_refusal(symbol: str, side: str, now_ts: float, level: float) -> str | None:
    """None when the order's side holds at least `level` % of the waiting orders in a fresh read, else why not."""
    if level <= 0:
        return None
    wait = latest_wait(symbol, now_ts)
    who = "buyers" if side == "LONG" else "sellers"
    if wait is None:
        return f"no fresh Most active read of the waiting orders (needs {who} at {level:g}%)"
    buy, at = wait
    own = buy if side == "LONG" else round(100.0 - buy, 1)
    if own >= level:
        return None
    clock = dt.datetime.fromtimestamp(at, IST).strftime("%H:%M")
    return f"{who} hold only {own:g}% of the waiting orders (read {clock}, need {level:g}%)"


def entry_reads(symbol: str, tape, now_ts: float, minutes: int, exit_minutes: int = 0) -> dict:
    """What a trade saw at its entry (display only, saved on the trade): the executed split over the `minutes` closed
    minutes before it and the latest fresh waiting-orders read."""
    import trade_flow

    out: dict = {}
    start, end = entry_span(now_ts, minutes, exit_minutes or minutes)
    try:
        got = split(trade_flow.minutes(symbol, tape, start, end), (end - start) // 60)
    except Exception:  # noqa: BLE001 - bookkeeping only
        got = None
    if got is not None:
        out.update({"buy": got[0], "src": got[1], "from": start, "to": end})
    wait = latest_wait(symbol, now_ts)
    if wait is not None:
        out.update({"wait_buy": wait[0], "read_at": int(wait[1])})
    return out
