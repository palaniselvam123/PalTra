"""Watch smaller candles while in a trade (`small_exit_minutes`): exit before the bot's own candle can.

Off by default (no sizes ticked); per bot and per stock, PAPER and LIVE, replays too. A bot on 15-minute
candles only learns that a trade has turned when a 15-minute candle closes, 45 minutes after a 1-minute
chart already showed it. With sizes ticked here (1, 2, 3, 5, 10 minutes), `StrategyEngine._watch_small_candles`
groups the stock's 1-minute Groww tape (`_tapes`) into candles of each ticked size from 09:15
(`exit_candle.closed_frame`, closed candles only) and, once per newly closed candle of that size, reads on it:

  * `small_exit_cross` (on): SMA fast / slow crossed against the trade on that candle (`exit_candle.crossed_against`),
    or are already against it when the trade was opened against that size's trend (`exit_candle.side_against`
    on the first candle read after the entry);
  * `small_exit_slope` (on): the SMA fast slope on that size (`slope_exit.slope`, % per candle over
    `slope_lookback`) is at least `slope_turn_pct` against the trade;
  * `small_exit_counter` (N, 0 = off): the last N closed candles of that size all went against the trade
    (`indicators._direction_reason`, colour rule).

A size "says exit" while its latest closed candle shows any ticked condition (`OpenPosition.small_state`). The trade
closes (`SMALL_CANDLE_EXIT`, via `_exit_now`, which cancels a LIVE stop first) once at least `small_exit_agree` (1)
ticked sizes say so at the same time, and, with `small_exit_loss_pct` > 0, only while the trade is at least that %
under water from its entry (a winner is left to the bot's own exits). No reverse: the next entry waits for its own
signal on the bot's own candle. Entries are unchanged. A LIVE position restored without its stop id is left to that
stop, as with the other candle exits. The stock research baseline keeps it off.
"""
from __future__ import annotations

import pandas as pd

import exit_candle
import indicators
import slope_exit

REASON = "SMALL_CANDLE_EXIT"
SIZES = (1, 2, 3, 5, 10)
MAX_COUNTER = 10
MAX_AGREE = 5


def sizes(cfg) -> list[int]:
    """The ticked candle sizes in minutes, smallest first; empty = off."""
    raw = getattr(cfg, "small_exit_minutes", None) or ""
    out: list[int] = []
    for part in str(raw).replace(";", ",").split(","):
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
    """A settings value (CSV string or list) as the stored CSV, sizes outside SIZES dropped."""
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(x) for x in raw)
    return ",".join(str(n) for n in sizes(type("C", (), {"small_exit_minutes": raw})()))


def on(cfg, own_minutes: int) -> bool:
    """Any ticked size smaller than the bot's own candle."""
    return any(s < int(own_minutes or 1) for s in sizes(cfg))


def watched(cfg, own_minutes: int) -> list[int]:
    """The ticked sizes that are smaller than the bot's own candle (a bigger one cannot be sooner)."""
    return [s for s in sizes(cfg) if s < int(own_minutes or 1)]


def cross_on(cfg) -> bool:
    return getattr(cfg, "small_exit_cross", None) is not False


def slope_on(cfg) -> bool:
    return getattr(cfg, "small_exit_slope", None) is not False


def counter(cfg) -> int:
    try:
        n = int(getattr(cfg, "small_exit_counter", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_COUNTER, n))


def loss_pct(cfg) -> float:
    try:
        v = float(getattr(cfg, "small_exit_loss_pct", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(50.0, v))


def agree(cfg) -> int:
    try:
        n = int(getattr(cfg, "small_exit_agree", 1) or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_AGREE, n))


def losing_pct(direction: str, entry: float, price: float) -> float:
    """How far the trade is under water, in % of its entry (negative = in profit)."""
    if not entry or not price:
        return 0.0
    move = (price - entry) / entry * 100.0
    return -move if direction == "LONG" else move


def verdict(frame: pd.DataFrame | None, signal_direction: str, cfg, first_after_entry: bool) -> str | None:
    """What this size's latest closed candle says against the trade, or None.

    `frame` is `exit_candle.closed_frame` output (close / fast / slow, forming candle dropped);
    `first_after_entry` marks the first candle of this size read after the entry, on which SMAs already
    against the trade count like a cross (the trade was opened against this size's trend).
    """
    if frame is None or getattr(frame, "empty", True):
        return None
    size_note: list[str] = []
    if cross_on(cfg):
        hit, note = exit_candle.crossed_against(frame, signal_direction)
        if hit:
            size_note.append(note)
        elif first_after_entry:
            last = frame.iloc[-1]
            if exit_candle.side_against(last["fast"], last["slow"], signal_direction):
                size_note.append(f"SMA fast {last['fast']:.2f} already against the trade (slow {last['slow']:.2f})")
    if slope_on(cfg):
        value = slope_exit.slope(frame, int(getattr(cfg, "sma_fast", 9) or 9), slope_exit.lookback(cfg))
        if value is not None:
            signed = value if signal_direction == "LONG" else -value
            floor = slope_exit.turn_pct(cfg)
            if signed < 0 and -signed >= max(floor, 1e-9):
                size_note.append(f"SMA fast slope {value:+.3f}%/candle against the trade")
    n = counter(cfg)
    if n > 0 and len(frame) >= n:
        other = "SHORT" if signal_direction == "LONG" else "LONG"
        # No reason against the *other* side = every one of the last n candles went against this trade.
        if not indicators._direction_reason(frame, other, n, "COLOUR"):  # noqa: SLF001
            size_note.append(f"{n} candle{'s' if n > 1 else ''} in a row against the trade")
    return "; ".join(size_note) if size_note else None


def decide(state: dict[int, str | None], cfg, direction: str, entry: float, price: float) -> tuple[bool, str]:
    """Whether enough sizes say exit now and the loss gate allows it: (exit, note)."""
    saying = {size: note for size, note in state.items() if note}
    need = agree(cfg)
    if len(saying) < need:
        return False, ""
    gate = loss_pct(cfg)
    under = losing_pct(direction, entry, price)
    if gate > 0 and under < gate:
        return False, ""
    parts = [f"{size}-min: {note}" for size, note in sorted(saying.items())]
    text = " · ".join(parts)
    if gate > 0:
        text += f" · {under:.2f}% under water"
    return True, text
