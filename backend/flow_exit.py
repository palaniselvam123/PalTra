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
WINDOWS = (0, 1, 2, 3, 5, 10, 15)
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
    return value if value in WINDOWS else DEFAULT_WINDOW


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
