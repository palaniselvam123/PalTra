"""Safety net after a target (target_net.py; owner's request: once the day high / low or pivot target is touched, let the
trade run on with a safety net, like the all-bots profit safety net on the Bots page).

`target_net` (off by default; per bot and per stock, PAPER and LIVE, replays too). With it on, the pivot target
(pivots.py) and the day high / low target (day_target.py) no longer close the trade when reached. From the moment the
best price since the entry (`OpenPosition.high` / `low`) reaches the target, the trade keeps `target_net_keep` (70) % of
its best profit per share: floor = entry + keep % × (best − entry) for a long (mirror for a short). Once the best
profit is at least `target_net_at2` (2.0, 0 = no second step) × the target's distance from the entry, it keeps
`target_net_keep2` (75) % instead. The floor only rises. A price at or through the floor closes the trade
(`TARGET_NET`, via `_exit_now`: a LIVE stop is cancelled first). With the stop on, the stop is also moved up to the floor
(tighter only, `_move_stop`: LIVE modifies the Groww stop), so the exchange holds it too. Stateless: the floor comes from
the best price, so a restart finds the same one.

**Start on every trade** (`target_net_start`: `TARGET` default = only at the pivot / day target, as above; `PCT` = once
the trade is `target_net_start_pct` (0.25) % of the entry price in profit; `RS` = once it is `target_net_start_rs`
(₹500) in profit for its quantity; `ATR` = once it is `target_net_start_atr` (1.0) × the ATR at entry in profit). With a
profit start the net works on every trade, with or without the pivot / day targets (`StrategyEngine._watch_profit_net`);
the start distance plays the target's part (the second step is a multiple of it). The pivot / day targets keep their own
net too; whichever floor the price reaches first closes. Pure helpers.
"""
from __future__ import annotations

EXIT_REASON = "TARGET_NET"
DEFAULT_KEEP = 70.0
DEFAULT_KEEP2 = 75.0
DEFAULT_AT2 = 2.0
MAX_AT2 = 20.0


def on(cfg) -> bool:
    return bool(getattr(cfg, "target_net", False))


def _num(cfg, name: str, default: float, lo: float, hi: float) -> float:
    try:
        raw = getattr(cfg, name, None)
        value = float(default if raw is None else raw)
    except (TypeError, ValueError):
        return default
    if value != value:
        return default
    return max(lo, min(hi, value))


def keep(cfg) -> float:
    return _num(cfg, "target_net_keep", DEFAULT_KEEP, 1.0, 99.0)


def keep2(cfg) -> float:
    return _num(cfg, "target_net_keep2", DEFAULT_KEEP2, 1.0, 99.0)


def at2(cfg) -> float:
    return _num(cfg, "target_net_at2", DEFAULT_AT2, 0.0, MAX_AT2)


STARTS = ("TARGET", "PCT", "RS", "ATR")


def start(cfg) -> str:
    value = str(getattr(cfg, "target_net_start", None) or "TARGET").upper()
    return value if value in STARTS else "TARGET"


def start_pct(cfg) -> float:
    return _num(cfg, "target_net_start_pct", 0.25, 0.01, 20.0)


def start_rs(cfg) -> float:
    return _num(cfg, "target_net_start_rs", 500.0, 1.0, 10_000_000.0)


def start_atr(cfg) -> float:
    return _num(cfg, "target_net_start_atr", 1.0, 0.05, 20.0)


def start_level(direction: str, entry: float, qty: int, atr: float | None, cfg) -> tuple[str, float] | None:
    """(label, price) at which the net starts on any trade, or None (start TARGET, or not workable)."""
    kind = start(cfg)
    if kind == "TARGET" or not entry > 0:
        return None
    if kind == "PCT":
        dist, label = entry * start_pct(cfg) / 100.0, f"{start_pct(cfg):g}% profit"
    elif kind == "RS":
        if not qty or qty <= 0:
            return None
        dist, label = start_rs(cfg) / float(qty), f"₹{start_rs(cfg):g} profit"
    else:
        if atr is None or not atr > 0:
            return None
        dist, label = float(atr) * start_atr(cfg), f"{start_atr(cfg):g}× ATR profit"
    sign = 1.0 if direction == "LONG" else -1.0
    return label, entry + sign * dist


def floor(direction: str, entry: float, target: float, best: float | None, cfg) -> tuple[float, float] | None:
    """(floor price, keep % used) once `best` has reached `target`, else None (the net is not armed yet)."""
    if best is None or not entry > 0:
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    reach = (target - entry) * sign
    gained = (best - entry) * sign
    if reach <= 0 or gained < reach:
        return None
    share = keep(cfg)
    step = at2(cfg)
    if step > 0 and gained >= step * reach:
        share = keep2(cfg)
    return entry + sign * gained * share / 100.0, share


def hit(direction: str, price: float, level: float) -> bool:
    return price <= level if direction == "LONG" else price >= level
