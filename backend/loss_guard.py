"""Two loss guards (loss_guard.py). Both off by default, PAPER and LIVE, replays too. Neither opens a trade.

**Time stop** (`time_stop_candles`, 0 = off, 1-60; `time_stop_atr` 0.5, 0.1-5; per bot and per stock): a trade that has
gone nowhere is closed instead of waiting for its stop. Once `time_stop_candles` candles (the candles the bot's exits
read) have closed since the entry, the trade is closed (`TIME_STOP`) unless its best price since the entry was at least
`time_stop_atr` x the ATR at entry in its favour. A trade that once ran that far is never closed by this rule: the
stop, target and the other exits look after it. Judged once per closed candle by `StrategyEngine._watch_time_stop`.

**Pause after losses in a row** (`loss_streak_max`, 0 = off, 1-10; `loss_streak_pause_min` 30, 0 = the rest of the day,
at most 240; per bot, not per stock): after that many losing trades in a row on the bot today (before charges; a
winning trade starts the count again), the bot takes no new entry for the pause, so a bad patch is not traded through.
Open trades are untouched and every exit still works. After the pause the count starts again from 0.
"""
from __future__ import annotations

import datetime as dt

TIME_REASON = "TIME_STOP"
TIME_MAX_CANDLES = 60
TIME_ATR_DEFAULT, TIME_ATR_MIN, TIME_ATR_MAX = 0.5, 0.1, 5.0

STREAK_MAX = 10
PAUSE_DEFAULT, PAUSE_MAX = 30, 240
# Closed rows that were never a real trade do not count as a loss or a win.
NOT_TRADES = ("NOT_ON_GROWW", "REPLAY_STOPPED")


# ------------------------------------------------------------------ time stop
def time_candles(cfg) -> int:
    try:
        value = int(getattr(cfg, "time_stop_candles", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(TIME_MAX_CANDLES, value))


def time_atr(cfg) -> float:
    raw = getattr(cfg, "time_stop_atr", TIME_ATR_DEFAULT)
    try:
        value = TIME_ATR_DEFAULT if raw is None else float(raw)
    except (TypeError, ValueError):
        value = TIME_ATR_DEFAULT
    return max(TIME_ATR_MIN, min(TIME_ATR_MAX, value))


def time_on(cfg) -> bool:
    return time_candles(cfg) > 0


def best_move(direction: str, entry: float, high: float | None, low: float | None) -> float:
    """The most the price went in the trade's favour since the entry, in rupees a share (never below 0)."""
    if (direction or "").upper() == "LONG":
        return max(0.0, float(high if high is not None else entry) - float(entry))
    return max(0.0, float(entry) - float(low if low is not None else entry))


def time_verdict(cfg, candles_closed: int, best: float, atr: float | None) -> tuple[bool, str]:
    """(close it?, why) once `candles_closed` candles have closed since the entry."""
    need = time_candles(cfg)
    if need <= 0 or candles_closed < need:
        return False, ""
    if atr is None or atr <= 0:
        return False, "no ATR at entry to measure the move against"
    want = time_atr(cfg) * float(atr)
    if best >= want:
        return False, f"best move {best:.2f} reached {time_atr(cfg):g} ATR ({want:.2f})"
    return True, (
        f"{candles_closed} candle(s) since the entry and the best move was {best:.2f}, "
        f"under {time_atr(cfg):g} ATR ({want:.2f})"
    )


# ------------------------------------------------------------------ pause after losses in a row
def streak_max(cfg) -> int:
    try:
        value = int(getattr(cfg, "loss_streak_max", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(STREAK_MAX, value))


def pause_minutes(cfg) -> int:
    raw = getattr(cfg, "loss_streak_pause_min", PAUSE_DEFAULT)
    try:
        value = PAUSE_DEFAULT if raw is None else int(raw)
    except (TypeError, ValueError):
        value = PAUSE_DEFAULT
    return max(0, min(PAUSE_MAX, value))


def counts(reason: str | None) -> bool:
    return (reason or "") not in NOT_TRADES


def next_run(run: int, gross: float | None) -> int:
    """The losing run after one more closed trade: a loss adds one, a win starts again, a flat trade keeps it."""
    if gross is None or gross == 0:
        return run
    return run + 1 if gross < 0 else 0


def pause_until(cfg, last_loss_at: dt.datetime) -> dt.datetime | None:
    """When entries may start again; None = not today."""
    minutes = pause_minutes(cfg)
    return None if minutes <= 0 else last_loss_at + dt.timedelta(minutes=minutes)


def refusal(cfg, run: int, last_loss_at: dt.datetime | None, now: dt.datetime) -> str | None:
    """Why the bot takes no new entry now, or None. An ended pause is the caller's to reset (`pause_over`)."""
    need = streak_max(cfg)
    if need <= 0 or run < need or last_loss_at is None:
        return None
    until = pause_until(cfg, last_loss_at)
    if until is None:
        return f"{run} losing trades in a row: no new entries for the rest of the day"
    if now >= until:
        return None
    return f"{run} losing trades in a row: no new entries until {until.strftime('%H:%M')}"


def pause_over(cfg, run: int, last_loss_at: dt.datetime | None, now: dt.datetime) -> bool:
    """True once a pause that was running has ended: the count starts again from 0."""
    need = streak_max(cfg)
    if need <= 0 or run < need or last_loss_at is None:
        return False
    until = pause_until(cfg, last_loss_at)
    return until is not None and now >= until


def run_from_rows(cfg, rows: list[tuple[dt.datetime, float | None]]) -> tuple[int, dt.datetime | None]:
    """The losing run and its last loss from today's closed trades (exit time, gross), for a restart.

    A trade that closed after a pause had ended starts a new count, as it did live."""
    need = streak_max(cfg)
    run, last = 0, None
    for at, gross in sorted(rows, key=lambda r: r[0]):
        if need > 0 and run >= need and last is not None:
            until = pause_until(cfg, last)
            if until is not None and at >= until:
                run, last = 0, None
        new = next_run(run, gross)
        if new > run:
            last = at
        elif new == 0:
            last = None
        run = new
    return run, last
