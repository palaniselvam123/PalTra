"""Where a backtest run came from (display only; nothing in the strategy reads it).

Owner's request: tag each backtest with where it was started (the Most active page, Trend quality auto-arm, the
Strategy lab, ...). The pick backtests already say it in their saved rule (`scalp_pick.kind`, the auto-arm's
`source`); every start path also saves `origin` (ORIGINS) on the run's settings, so plain replays started from the
Strategy lab, the Scalp page's Arm ticked, a stock ranking, the Compare page, the queue or an AI tool are told apart.
Runs saved before `origin` existed read from their rule, else as a plain replay.
"""
from __future__ import annotations

#: The start paths that save an `origin` on the run's settings, and how each reads on the screens.
ORIGINS: dict[str, str] = {
    "replay": "Replay bar",
    "scalp_arm": "Scalp page: Backtest these",
    "strategy_lab": "Strategy lab",
    "scan_trade": "Most active: Trade a saved scan",
    "stock_rank": "Stock ranking",
    "pick_compare": "Compare pick times",
    "daily": "Daily strong-trend run",
    "queue": "Backtest queue",
    "queue_schedule": "Backtest queue (daily schedule)",
    "live_compare": "Compare: Backtest the same days",
    "mcp": "AI tool (MCP)",
}

#: Origins that only say how a pick backtest was started; the pick rule itself names what it is.
_STARTERS = {"daily", "queue", "queue_schedule", "pick_compare", "mcp", "stock_rank"}

_AUTOARM = {
    "TREND": ("AUTOARM_TREND", "Auto-arm · Trend quality"),
    "ACTIVE": ("AUTOARM_ACTIVE", "Auto-arm · Most active"),
    "BOTH": ("AUTOARM_BOTH", "Auto-arm · Trend quality + Most active"),
}
_PICKS = {
    "TREND": ("TREND_PICKS", "Trend quality picks"),
    "ACTIVE": ("ACTIVE_PICKS", "Most active picks"),
    "SAVED": ("SAVED_SCAN", "Most active saved scan"),
}


def clean(origin: str | None) -> str | None:
    """A known origin code, or None (an unknown one is not saved)."""
    code = (origin or "").strip().lower()
    return code if code in ORIGINS else None


def describe(settings: dict | None) -> dict:
    """{"code", "label", "via"}: what the run is (its pick rule, else where it was started) and, for a pick
    backtest started by something else (the daily run, the queue, a comparison), that starter in `via`."""
    s = settings if isinstance(settings, dict) else {}
    origin = clean(s.get("origin") if isinstance(s.get("origin"), str) else None)
    via = ORIGINS[origin] if origin else None
    pick = s.get("scalp_pick")
    if isinstance(pick, dict):
        kind = str(pick.get("kind") or "").upper()
        if kind == "AUTOARM":
            code, label = _AUTOARM.get(str(pick.get("source") or "BOTH").upper(), _AUTOARM["BOTH"])
        else:
            code, label = _PICKS.get(kind, ("SCALP_PICKS", "Scalp picks"))
        return {"code": code, "label": label, "via": via if origin in _STARTERS else None}
    armed = bool(s.get("as_armed"))
    if origin and origin != "replay":
        return {"code": origin.upper(), "label": ORIGINS[origin], "via": "as armed live" if armed else None}
    if armed:
        return {"code": "AS_ARMED", "label": "Replay as armed live", "via": None}
    return {"code": "REPLAY", "label": "Replay bar" if origin else "Replay", "via": None}
