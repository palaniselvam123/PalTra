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
    (`indicators._direction_reason`, colour rule; Heikin Ashi colours with the bot's `ha_colours`).

A size "says exit" while its latest closed candle shows any ticked condition (`OpenPosition.small_state`). The trade
closes (`SMALL_CANDLE_EXIT`, via `_exit_now`, which cancels a LIVE stop first) once at least `small_exit_agree` (1)
ticked sizes say so at the same time, and, with `small_exit_loss_pct` > 0, only while the trade is at least that %
under water from its entry (a winner is left to the bot's own exits). No reverse: the next entry waits for its own
signal on the bot's own candle. Entries are unchanged. A LIVE position restored without its stop id is left to that
stop, as with the other candle exits. The stock research baseline keeps it off.

**Look only when the trade is in trouble** (`small_exit_when` `ALWAYS` default | `TROUBLE`; `small_exit_giveback` 30 %,
1-100): with `TROUBLE` the sizes are judged only while the trade is in a loss (the price on the wrong side of the entry)
or has fallen back from its best price since the entry by at least that % of the best profit (`in_trouble`); while the
price keeps going the trade's way the smaller candles are not looked at. Judged again on every pass while it holds.

**Bigger candles too** (`small_exit_upper`, CSV of 5 / 10 / 15 / 30 / 60 bigger than the bot's candle, "" = none;
`small_exit_upper_must` off): the same three readings on bigger candles, counted with the smaller sizes toward
`small_exit_agree`; with `small_exit_upper_must` the trade closes only while at least one bigger size also says exit.
"""
from __future__ import annotations

import pandas as pd

import exit_candle
import heikin_ashi
import indicators
import slope_exit

REASON = "SMALL_CANDLE_EXIT"
SIZES = (1, 2, 3, 5, 10)
MAX_COUNTER = 10
MAX_AGREE = 5
UPPER_SIZES = (5, 10, 15, 30, 60)
WHENS = ("ALWAYS", "TROUBLE")


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


def upper_sizes(cfg) -> list[int]:
    """The ticked bigger candle sizes in minutes, smallest first; empty = none."""
    raw = getattr(cfg, "small_exit_upper", None) or ""
    out: list[int] = []
    for part in str(raw).replace(";", ",").split(","):
        try:
            n = int(float(part.strip()))
        except ValueError:
            continue
        if n in UPPER_SIZES and n not in out:
            out.append(n)
    return sorted(out)


def clean_upper(raw) -> str:
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(x) for x in raw)
    return ",".join(str(n) for n in upper_sizes(type("C", (), {"small_exit_upper": raw})()))


def watched_upper(cfg, own_minutes: int) -> list[int]:
    """The ticked bigger sizes that are bigger than the bot's own candle."""
    return [s for s in upper_sizes(cfg) if s > int(own_minutes or 1)]


def upper_must(cfg) -> bool:
    return bool(getattr(cfg, "small_exit_upper_must", False))


def when(cfg) -> str:
    value = str(getattr(cfg, "small_exit_when", None) or "ALWAYS").upper()
    return value if value in WHENS else "ALWAYS"


def giveback(cfg) -> float:
    try:
        v = float(getattr(cfg, "small_exit_giveback", 30) or 30)
    except (TypeError, ValueError):
        return 30.0
    return max(1.0, min(100.0, v))


def in_trouble(direction: str, entry: float, price: float, best: float | None, cfg) -> tuple[bool, str]:
    """(in trouble, why): the trade is in a loss, or has given back `giveback` % of its best profit."""
    if not entry or not price:
        return False, ""
    sign = 1.0 if direction == "LONG" else -1.0
    now = (price - entry) * sign
    if now < 0:
        return True, f"in a loss ({now:+.2f} a share)"
    top = (best - entry) * sign if best else 0.0
    if top <= 0:
        return False, ""
    back = (top - now) / top * 100.0
    if back >= giveback(cfg):
        return True, f"gave back {back:.0f}% of the best profit ({top:.2f} → {now:.2f} a share)"
    return False, ""


def clean_sizes(raw) -> str:
    """A settings value (CSV string or list) as the stored CSV, sizes outside SIZES dropped."""
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(x) for x in raw)
    return ",".join(str(n) for n in sizes(type("C", (), {"small_exit_minutes": raw})()))


def on(cfg, own_minutes: int) -> bool:
    """Any ticked size smaller than the bot's own candle, or bigger one ticked under "bigger candles too"."""
    return any(s < int(own_minutes or 1) for s in sizes(cfg)) or bool(watched_upper(cfg, own_minutes))


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
        # Heikin Ashi colours (heikin_ashi.py) when the bot reads them.
        if not indicators._direction_reason(heikin_ashi.view(frame, cfg, "exit"), other, n, "COLOUR"):  # noqa: SLF001
            size_note.append(f"{n} candle{'s' if n > 1 else ''} in a row against the trade")
    return "; ".join(size_note) if size_note else None


def decide(
    state: dict[int, str | None],
    cfg,
    direction: str,
    entry: float,
    price: float,
    best: float | None = None,
    upper: tuple[int, ...] | list[int] = (),
) -> tuple[bool, str]:
    """Whether enough sizes say exit now and the gates allow it: (exit, note). `upper` = the bigger sizes in `state`."""
    saying = {size: note for size, note in state.items() if note}
    need = agree(cfg)
    if len(saying) < need:
        return False, ""
    if upper and upper_must(cfg) and not any(size in saying for size in upper):
        return False, ""
    gate = loss_pct(cfg)
    under = losing_pct(direction, entry, price)
    if gate > 0 and under < gate:
        return False, ""
    trouble = ""
    if when(cfg) == "TROUBLE":
        bad, trouble = in_trouble(direction, entry, price, best, cfg)
        if not bad:
            return False, ""
    parts = [f"{size}-min{' (bigger)' if size in upper else ''}: {note}" for size, note in sorted(saying.items())]
    text = " · ".join(parts)
    if gate > 0:
        text += f" · {under:.2f}% under water"
    if trouble:
        text += f" · {trouble}"
    return True, text
