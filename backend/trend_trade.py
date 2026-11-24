"""Trade on the trend reading (owner's request: Trend quality auto-armed stocks entered and closed on the trend score /
trend phase, not on an SMA cross). Per bot and per stock, PAPER and LIVE, replays too; off by default.

The reading is the Trend quality card's, on the candles the bot trades (`StrategyEngine._exit_minutes`) from the
1-minute tape it already holds (`stock_regime.read`, no Groww call): the **phase now** (`BULL` / `BEAR` / `CHOP`,
`trend_phases`) and the **trend score** 0-100 (`trend_strength.read`: the share of its 7 checks passed for the side
the SMAs are on, `trend_side`).

* **Entry** (`tq_entry`, `tq_entry_score` 70, `tq_entry_phase` on): a flat armed stock is entered on the trend's side
  once the score is at least the level and, with the phase check, the phase points the same way (UP for a buy). Asked
  once per closed candle through `apply_signal(..., why=ENTRY_REASON)`, so every entry check, the stop, flip, cut-off,
  caps, wait after an exit and one LIVE bot per stock apply. With `tq_only` (on) the bot's own signals (SMA cross, colour,
  turn…) no longer open trades on these stocks: a cross still closes a trade, it does not reverse it (`blocks`).
* **Exit** (`tq_exit`, `tq_exit_score` 40, `tq_exit_phase` on): once per candle closed after the entry, the trade closes
  (`EXIT_REASON`) when the phase is CHOP or the other way, or the score for the trade's side is under the level (the
  SMAs on the other side count as 0). "Not enough candles yet" (`phase_early`) never closes or opens on the phase.
* **Skip a doji** (`tq_skip_doji`, off; `tq_doji_pct` 10, 1-90): the entry also waits while the newest closed candle
  the score was read on (`stock_regime.read`'s `last_bar`, real candles) has a body under that % of its high-low range
  (owner's GLENMARK case: bought at 10:25 on a doji closing ₹1.6 above SMA 9). Asked again on the next candle.
* **Which stocks** (`tq_all`, off): only the ones the Trend quality auto-arm armed (source chip "Auto-arm: Trend
  quality", and the same picks in an auto-arm backtest), or with it on every armed stock.
"""
from __future__ import annotations

ENTRY_REASON = "TREND_QUALITY"
EXIT_REASON = "TREND_FADE"
SOURCE_WORD = "auto-arm: trend quality"
DEFAULT_ENTRY_SCORE = 70
DEFAULT_EXIT_SCORE = 40
DEFAULT_DOJI_PCT = 10
# Entries that still go through with "only the trend opens trades": orders a person or another bot asked for.
PASS_REASONS = (ENTRY_REASON, "FORCE_ORDER", "MANUAL_REVERSE", "NEWS", "HANDOFF", "FAST_MOVE", "BREAKOUT_ARRIVAL",
                "AUTO_REVIEW_REVERSE", "MOST_ACTIVE")


def _score(cfg, name: str, default: int) -> int:
    try:
        value = int(round(float(getattr(cfg, name, default) if getattr(cfg, name, None) is not None else default)))
    except (TypeError, ValueError):
        value = default
    return max(0, min(100, value))


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "tq_entry", False))


def exit_on(cfg) -> bool:
    return bool(getattr(cfg, "tq_exit", False))


def entry_score(cfg) -> int:
    return _score(cfg, "tq_entry_score", DEFAULT_ENTRY_SCORE)


def exit_score(cfg) -> int:
    return _score(cfg, "tq_exit_score", DEFAULT_EXIT_SCORE)


def entry_phase(cfg) -> bool:
    return getattr(cfg, "tq_entry_phase", True) is not False


def exit_phase(cfg) -> bool:
    return getattr(cfg, "tq_exit_phase", True) is not False


def skip_doji(cfg) -> bool:
    return bool(getattr(cfg, "tq_skip_doji", False))


def doji_pct(cfg) -> int:
    try:
        value = int(round(float(getattr(cfg, "tq_doji_pct", None) or DEFAULT_DOJI_PCT)))
    except (TypeError, ValueError):
        value = DEFAULT_DOJI_PCT
    return max(1, min(90, value))


def doji_note(read: dict | None, cfg) -> str | None:
    """Why the entry waits on a doji (the newest closed candle's body under `tq_doji_pct` % of its range), or None."""
    if not skip_doji(cfg):
        return None
    bar = (read or {}).get("last_bar")
    if not bar:
        return None
    rng = float(bar["high"]) - float(bar["low"])
    body = abs(float(bar["close"]) - float(bar["open"]))
    share = 0.0 if rng <= 0 else body / rng * 100
    need = doji_pct(cfg)
    if rng > 0 and share >= need:
        return None
    return f"last candle a doji (body {share:.0f}% of its range, needs {need}%)"


def only_trend(cfg) -> bool:
    return getattr(cfg, "tq_only", True) is not False


def every_stock(cfg) -> bool:
    return bool(getattr(cfg, "tq_all", False))


def from_trend_quality(source: str | None) -> bool:
    return SOURCE_WORD in str(source or "").lower()


def applies(cfg, source: str | None) -> bool:
    """This stock is traded on the trend reading (auto-armed by Trend quality, or every stock with `tq_all`)."""
    return every_stock(cfg) or from_trend_quality(source)


def blocks(cfg, source: str | None, why: str | None) -> str | None:
    """Why the bot's own signal may not open a trade here ("only the trend opens trades"), or None."""
    if not entry_on(cfg) or not only_trend(cfg) or not applies(cfg, source):
        return None
    reason = str(why or "SMA_CROSS").split(":", 1)[0].split("@", 1)[0]
    if reason.startswith(PASS_REASONS):
        return None
    return "only the trend reading opens trades on this stock (Trend quality entry)"


def _side_word(side: str) -> str:
    return "up" if side == "LONG" else "down"


def _trend_side(read: dict | None) -> str | None:
    word = (read or {}).get("trend_side")
    return "LONG" if word == "BULLISH" else "SHORT" if word == "BEARISH" else None


def score_for(read: dict | None, side: str) -> int:
    """The trend score for `side`: the read's score when the SMAs point that way, else 0."""
    if _trend_side(read) != side:
        return 0
    try:
        return int((read or {}).get("trend_score") or 0)
    except (TypeError, ValueError):
        return 0


def _phase_side(read: dict | None) -> str | None:
    phase = (read or {}).get("phase")
    return "LONG" if phase == "BULL" else "SHORT" if phase == "BEAR" else None


def entry_call(read: dict | None, cfg) -> tuple[str | None, str]:
    """(LONG / SHORT to enter, or None; a plain note of what was read)."""
    if not read:
        return None, "trend not readable yet (too few candles)"
    side = _trend_side(read)
    if side is None:
        return None, "no trend side yet"
    score = score_for(read, side)
    need = entry_score(cfg)
    text = f"trend {_side_word(side)}, score {score} (needs {need})"
    if score < need:
        return None, text + " — waiting"
    if entry_phase(cfg):
        if read.get("phase_early"):
            return None, text + ", phase: not enough candles yet — waiting"
        if _phase_side(read) != side:
            phase = read.get("phase") or "?"
            return None, text + f", phase {phase} (needs {'UP' if side == 'LONG' else 'DOWN'}) — waiting"
        text += f", phase {'UP' if side == 'LONG' else 'DOWN'} since {read.get('phase_since') or '?'}"
    doji = doji_note(read, cfg)
    if doji:
        return None, text + f", {doji} — waiting"
    return side, text


def exit_call(read: dict | None, side: str, cfg) -> tuple[bool, str]:
    """(close the trade?, a plain note) for a trade on `side` (its signal side)."""
    if not read:
        return False, "trend not readable — held"
    notes: list[str] = []
    if exit_phase(cfg) and not read.get("phase_early"):
        phase = read.get("phase")
        if phase == "CHOP":
            notes.append(f"phase turned choppy (since {read.get('phase_since') or '?'})")
        elif _phase_side(read) not in (None, side):
            notes.append(f"phase turned {'DOWN' if side == 'LONG' else 'UP'}")
    score = score_for(read, side)
    level = exit_score(cfg)
    if score < level:
        why = f"trend score {score} under {level}"
        if _trend_side(read) not in (None, side):
            why += f" (the SMAs point {_side_word(_trend_side(read))})"
        notes.append(why)
    if notes:
        return True, "; ".join(notes)
    return False, f"trend holds: score {score}, phase {read.get('phase') or '?'}"
