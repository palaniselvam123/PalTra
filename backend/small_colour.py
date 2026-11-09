"""Smaller-candle colours (small_colour.py): read the colour of the last N candles of smaller sizes.

A 15-minute bot judges a candle's colour once every 15 minutes. With sizes ticked here (`sc_minutes`, CSV of
1 / 2 / 3 / 5 / 10, smaller than the bot's own candle; "" = off), the stock's 1-minute Groww tape (`_tapes`) is
grouped into candles of each ticked size from 09:15 (`exit_candle.closed_frame`, closed candles only, with Heikin
Ashi colours when the bot's `ha_colours` is on) and each size is asked whether its last `sc_count` (1-5, default 2)
closed candles were all green or all red (`run_colour`). How the sizes combine (`sc_join`): `ALL` every ticked size
agrees (default), `ANY` one size is enough, `AT_LEAST` at least `sc_need` sizes. A size with too few candles never
agrees.

Two uses, each its own tick box (per bot and per stock, PAPER and LIVE, replays too; off by default):

* **Entry check** (`sc_entry`): read in `StrategyEngine.apply_signal` next to the other entry checks: a buy needs
  the smaller candles green, a sell red (the signal's side, like every entry check); else the entry is refused
  ("smaller candles: …"), which late entry can wait out.
* **Confirm a colour turn** (`sc_confirm`): a colour-turn exit (`COLOUR_TURN`: the SMA 9 turn trade's and the
  reversed trade's "close on the other colour", `StrategyEngine._watch_turn_colour`) first waits until the smaller
  candles agree with the turn (red for a long, green for a short); it closes on the first pass they do, and the wait
  is dropped when they show the trade's own colour instead (the dip is over). Candle-colour entry mode keeps its own
  turns.

Nothing here places an order; it only says yes / no to an entry or an exit the bot already asked for.
"""
from __future__ import annotations

import pandas as pd

import exit_candle
import heikin_ashi

SIZES = (1, 2, 3, 5, 10)
JOINS = ("ALL", "ANY", "AT_LEAST")
MAX_COUNT = 5
MAX_NEED = 5
CHECK = "small_colour"


def sizes(cfg) -> list[int]:
    raw = getattr(cfg, "sc_minutes", None) if not isinstance(cfg, dict) else cfg.get("sc_minutes")
    out: list[int] = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(float(part))
        except ValueError:
            continue
        if n in SIZES and n not in out:
            out.append(n)
    return sorted(out)


def clean_sizes(raw) -> str:
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(x) for x in raw)
    return ",".join(str(n) for n in sizes({"sc_minutes": raw}))


def watched(cfg, own_minutes: int) -> list[int]:
    """Ticked sizes smaller than the bot's own candle (a bigger one says nothing new)."""
    return [s for s in sizes(cfg) if s < int(own_minutes or 1)]


def count(cfg) -> int:
    try:
        n = int(getattr(cfg, "sc_count", 2) or 2)
    except (TypeError, ValueError):
        return 2
    return max(1, min(MAX_COUNT, n))


def join(cfg) -> str:
    value = str(getattr(cfg, "sc_join", None) or "ALL").upper()
    return value if value in JOINS else "ALL"


def need(cfg) -> int:
    try:
        n = int(getattr(cfg, "sc_need", 1) or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_NEED, n))


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "sc_entry", False))


def confirm_on(cfg) -> bool:
    return bool(getattr(cfg, "sc_confirm", False))


def run_colour(frame: pd.DataFrame | None, n: int) -> str | None:
    """'G' when the last n candles all closed green, 'R' all red, else None (mixed, a doji or too few)."""
    if frame is None or len(frame) < n:
        return None
    last = frame.iloc[-n:]
    body = last["close"].astype(float) - last["open"].astype(float)
    if (body > 0).all():
        return "G"
    if (body < 0).all():
        return "R"
    return None


def read(tape: pd.DataFrame | None, cfg, own_minutes: int) -> dict[int, str | None]:
    """Each watched size's colour run now: {size: 'G' | 'R' | None}."""
    out: dict[int, str | None] = {}
    n = count(cfg)
    for size in watched(cfg, own_minutes):
        frame = exit_candle.closed_frame(tape, size, int(getattr(cfg, "sma_fast", 9) or 9), int(getattr(cfg, "sma_slow", 21) or 21))
        if frame is not None and not frame.empty and heikin_ashi.on(cfg):
            frame = heikin_ashi.view(frame, cfg)
        out[size] = run_colour(frame, n)
    return out


def agree(runs: dict[int, str | None], want: str, cfg) -> tuple[bool, str]:
    """Whether enough sizes show `want` ('G' / 'R') under `sc_join`, and a short note."""
    if not runs:
        return True, "no smaller size ticked"
    yes = [s for s, c in runs.items() if c == want]
    rule = join(cfg)
    goal = len(runs) if rule == "ALL" else 1 if rule == "ANY" else min(need(cfg), len(runs))
    word = "green" if want == "G" else "red"
    n = count(cfg)
    shown = ", ".join(f"{s}m {'green' if c == 'G' else 'red' if c == 'R' else 'mixed'}" for s, c in sorted(runs.items()))
    note = f"last {n} candle{'s' if n > 1 else ''} {word} on {len(yes)} of {len(runs)} size(s), need {goal} ({shown})"
    return len(yes) >= goal, note


def entry_reason(tape: pd.DataFrame | None, direction: str, cfg, own_minutes: int) -> str | None:
    """None when the smaller candles agree with a `direction` entry (or nothing is ticked), else why not."""
    if not entry_on(cfg) or not watched(cfg, own_minutes):
        return None
    ok, note = agree(read(tape, cfg, own_minutes), "G" if direction == "LONG" else "R", cfg)
    return None if ok else f"smaller candles: {note}"


def confirms_turn(tape: pd.DataFrame | None, signal_direction: str, cfg, own_minutes: int) -> tuple[str, str]:
    """For a colour turn against a `signal_direction` trade: 'yes' (smaller candles agree, close), 'back' (they
    show the trade's own colour, drop the wait) or 'wait', with a note."""
    runs = read(tape, cfg, own_minutes)
    against = "R" if signal_direction == "LONG" else "G"
    ok, note = agree(runs, against, cfg)
    if ok:
        return "yes", note
    back, back_note = agree(runs, "G" if against == "R" else "R", cfg)
    return ("back", back_note) if back else ("wait", note)
