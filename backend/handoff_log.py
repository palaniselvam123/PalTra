"""Hand-off trail and trend-at-entry bookkeeping (display only; no order path, nothing in the strategy reads it).

* `record` keeps one `HandoffLog` row per hand-off of a stock between the SMA bots, whether it went through or not:
  recovery and losing V-turn exits to Bot 2 (choppy), breakout to Bot 3 (trend), fast moves to Bot 5 and back. In a
  replay the row carries the run and the replayed time, so a backtest shows where each stock went.
* `rows` reads them back for the blotter (`GET /api/handoffs`) and a backtest run (`replay.get_run`).
* `regime_json` / `parse` store the choppy / trending read (`stock_regime`) on a trade at its entry
  (`TradeLog.entry_regime`); `by_regime` totals a list of trades by it.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

log = logging.getLogger(__name__)

KINDS = ("RECOVERY", "VTURN_LOSS", "CHOP_LOSSES", "BREAKOUT", "TREND_PROFIT", "STOP_EXIT", "FAST_MOVE", "FAST_BACK")
KIND_WORDS = {
    "RECOVERY": "recovered loss → Bot 2 (choppy)",
    "VTURN_LOSS": "V-turn exit at a loss → Bot 2 (choppy)",
    "CHOP_LOSSES": "losing trades today → Bot 2 (choppy)",
    "BREAKOUT": "trend breakout → Bot 3 (trend)",
    "TREND_PROFIT": "trend while the trade is in profit → Bot 3 (trend)",
    "STOP_EXIT": "stop-loss exit → the bot set for it",
    "FAST_MOVE": "fast move → Bot 5",
    "FAST_BACK": "back from Bot 5",
}


def regime_json(regime: dict | None) -> str | None:
    """The fields of a `stock_regime` read worth keeping on a trade or hand-off (no per-candle phase list)."""
    if not regime:
        return None
    keep = {k: regime.get(k) for k in ("phase", "score", "crosses", "phase_since", "phase_move_pct", "minutes")}
    return json.dumps(keep)


def parse(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record(
    *,
    at: dt.datetime,
    mode: str,
    run_id: int | None,
    from_bot: int,
    to_bot: int,
    symbol: str,
    kind: str,
    why: str | None,
    result: str,
    trade_id: int | None = None,
    regime: dict | None = None,
) -> None:
    """Keep one hand-off. Never raises: bookkeeping must not stop a hand-off."""
    from database import session_factory
    from models import HandoffLog

    try:
        when = at.replace(tzinfo=None) if at.tzinfo else at
        with session_factory()() as db:
            db.add(
                HandoffLog(
                    at=when,
                    day=when.date().isoformat(),
                    mode=(mode or "PAPER").upper(),
                    run_id=run_id,
                    from_bot=int(from_bot),
                    to_bot=int(to_bot),
                    symbol=(symbol or "").upper(),
                    kind=kind,
                    why=(why or "")[:400] or None,
                    result=(result or "")[:400],
                    moved=moved(result),
                    trade_id=trade_id,
                    regime=regime_json(regime),
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("hand-off log failed for %s", symbol)


def moved(result: str | None) -> bool:
    text = (result or "").lower()
    return text.startswith("moved") or text.startswith("armed on bot") or text.startswith("back on bot")


def _dict(row) -> dict:
    return {
        "id": row.id,
        "at": row.at.isoformat() if row.at else None,
        "day": row.day,
        "mode": row.mode,
        "run_id": row.run_id,
        "from_bot": row.from_bot,
        "to_bot": row.to_bot,
        "symbol": row.symbol,
        "kind": row.kind,
        "kind_words": KIND_WORDS.get(row.kind, row.kind),
        "why": row.why,
        "result": row.result,
        "moved": bool(row.moved),
        "trade_id": row.trade_id,
        "regime": parse(row.regime),
    }


def rows(
    *, run_id: int | None = None, day: str | None = None, mode: str | None = None, bot: int | None = None, limit: int = 500
) -> list[dict]:
    """Hand-offs, newest first: a run's, or a day's outside replays (optionally one mode, one bot either end)."""
    from database import session_factory
    from models import HandoffLog

    with session_factory()() as db:
        q = db.query(HandoffLog)
        if run_id is not None:
            q = q.filter(HandoffLog.run_id == int(run_id))
        else:
            q = q.filter(HandoffLog.run_id.is_(None))
            if day:
                q = q.filter(HandoffLog.day == day)
            if mode:
                q = q.filter(HandoffLog.mode == mode.upper())
        if bot is not None:
            q = q.filter((HandoffLog.from_bot == int(bot)) | (HandoffLog.to_bot == int(bot)))
        found = q.order_by(HandoffLog.at.desc(), HandoffLog.id.desc()).limit(max(1, min(int(limit), 2000))).all()
        return [_dict(r) for r in found]


def delete_run(run_id: int, days: list[str] | None = None) -> None:
    """Drop a run's hand-offs (all, or only those days: a resume plays them again)."""
    from database import session_factory
    from models import HandoffLog

    try:
        with session_factory()() as db:
            q = db.query(HandoffLog).filter(HandoffLog.run_id == int(run_id))
            if days:
                q = q.filter(HandoffLog.day.in_(days))
            q.delete(synchronize_session=False)
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("hand-off log delete failed for run %s", run_id)


def trend_label(regime: dict | None, direction: str | None) -> str:
    """UP / DOWN / CHOPPY / UNKNOWN for the phase at entry, plus WITH / AGAINST when it is a trend."""
    if not regime or not regime.get("phase"):
        return "UNKNOWN"
    phase = regime.get("phase")
    if phase == "CHOP":
        return "CHOPPY"
    long = (direction or "").upper() in ("LONG", "BUY")
    side = "UP" if phase == "BULL" else "DOWN"
    with_it = (phase == "BULL") == long
    return f"{side}_{'WITH' if with_it else 'AGAINST'}"


GROUP_WORDS = {
    "UP_WITH": "Up trend, traded with it",
    "DOWN_WITH": "Down trend, traded with it",
    "UP_AGAINST": "Up trend, traded against it",
    "DOWN_AGAINST": "Down trend, traded against it",
    "CHOPPY": "Choppy",
    "UNKNOWN": "Not read (older trade or too few candles)",
}


# A cut trade closes within this many seconds of its hand-off row (the replay notes the row right after the close).
MATCH_SEC = 120


def _naive(when):
    if when is None:
        return None
    if isinstance(when, str):
        try:
            when = dt.datetime.fromisoformat(when)
        except ValueError:
            return None
    return when.replace(tzinfo=None) if when.tzinfo else when


def _pnl(t) -> dict:
    return {
        "trade_id": int(t.id),
        "side": t.direction,
        "entry_time": t.entry_time.isoformat() if t.entry_time else None,
        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        "exit_reason": t.exit_reason,
        "gross": round(float(t.gross_pnl or 0), 2),
        "net": round(float(t.net_pnl or 0), 2),
    }


def with_pnl(rows: list[dict], trades: list) -> list[dict]:
    """A run's hand-off rows with the P&L on each side (display only): `from_trade` = the trade the sending bot
    closed for it (its `trade_id`, else for a fast move the FAST_MOVE_HANDOFF exit on that stock within MATCH_SEC),
    `to_trade` = the first closed trade the receiving bot's settings opened on that stock at or after the hand-off
    (a FAST_MOVE entry for Bot 5, else a trade played as that bot, "…@B2"). Either is None when not found."""
    from fast_bot import ENTRY_REASON, EXIT_REASON
    from handoff_chain import played_as

    closed = sorted((t for t in trades if t.exit_time is not None and t.entry_time is not None), key=lambda t: t.entry_time)
    by_id = {int(t.id): t for t in closed}
    used_from: set[int] = set()
    used_to: set[int] = set()
    out = []
    # Oldest first, so two hand-offs of one stock each take their own trades.
    for row in sorted(rows, key=lambda r: r.get("at") or ""):
        at = _naive(row.get("at"))
        sym = row.get("symbol")
        src = None
        if row.get("trade_id") is not None:
            src = by_id.get(int(row["trade_id"]))
        elif at is not None and row.get("kind") == "FAST_MOVE":
            near = [
                t for t in closed
                if t.symbol == sym and t.exit_reason == EXIT_REASON and int(t.id) not in used_from
                and abs((_naive(t.exit_time) - at).total_seconds()) <= MATCH_SEC
            ]
            src = min(near, key=lambda t: abs((_naive(t.exit_time) - at).total_seconds()), default=None)
        dst = None
        if row.get("moved") and at is not None:
            for t in closed:
                if t.symbol != sym or int(t.id) in used_to or _naive(t.entry_time) < at - dt.timedelta(seconds=MATCH_SEC):
                    continue
                reason = t.entry_reason or ""
                if (reason.startswith(ENTRY_REASON) if row.get("to_bot") == 5 else played_as(reason) == row.get("to_bot")):
                    dst = t
                    break
        if src is not None:
            used_from.add(int(src.id))
        if dst is not None:
            used_to.add(int(dst.id))
        out.append({**row, "from_trade": _pnl(src) if src else None, "to_trade": _pnl(dst) if dst else None})
    keyed = {r.get("id"): r for r in out}
    return [keyed[r.get("id")] for r in rows] if len(keyed) == len(rows) else out


def by_regime(trades: list) -> list[dict]:
    """Closed trades totalled by the trend at entry: count, wins, P&L before and after charges."""
    groups: dict[str, dict] = {}
    for t in trades:
        if getattr(t, "exit_price", None) is None:
            continue
        key = trend_label(parse(getattr(t, "entry_regime", None)), getattr(t, "direction", None))
        g = groups.setdefault(key, {"group": key, "label": GROUP_WORDS[key], "trades": 0, "wins": 0, "gross": 0.0, "net": 0.0})
        gross = float(getattr(t, "gross_pnl", None) or 0.0)
        g["trades"] += 1
        g["wins"] += 1 if gross > 0 else 0
        g["gross"] += gross
        net = getattr(t, "net_pnl", None)
        g["net"] += float(net if net is not None else gross)
    order = list(GROUP_WORDS)
    out = sorted(groups.values(), key=lambda g: order.index(g["group"]))
    for g in out:
        g["gross"] = round(g["gross"], 2)
        g["net"] = round(g["net"], 2)
        g["win_pct"] = round(100 * g["wins"] / g["trades"], 1) if g["trades"] else None
    return out


def trend_text(regime: dict | None, direction: str | None) -> str:
    """One line for a CSV / PDF cell, e.g. "Up since 12:10 (+0.80%), with it · trending, 1 cross"."""
    if not regime or not regime.get("phase"):
        return ""
    label = trend_label(regime, direction)
    phase = {"BULL": "Up", "BEAR": "Down", "CHOP": "Choppy"}.get(regime.get("phase"), "")
    since = f" since {regime['phase_since']}" if regime.get("phase_since") else ""
    move = regime.get("phase_move_pct")
    move_txt = f" ({move:+.2f}%)" if isinstance(move, (int, float)) and label != "CHOPPY" else ""
    side = ", with it" if label.endswith("_WITH") else ", against it" if label.endswith("_AGAINST") else ""
    crosses = int(regime.get("crosses") or 0)
    score = str(regime.get("score") or "").lower()
    return f"{phase}{since}{move_txt}{side} · {score}, {crosses} cross{'' if crosses == 1 else 'es'}"
