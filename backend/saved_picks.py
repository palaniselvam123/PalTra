"""Saved-scan backtest: trade the top stocks of a saved Most active scan, one click for many days.

The owner's routine done by hand (Most active → History → the day's first scan at 09:15 → sort by volume vs usual →
top 24 → backtest on Bot 1 at qty 100) as one rule. For each day of the range:

* the first saved Most active scan taken at or after `at` (in the session) that has at least `top_n` stocks with every
  ranking column known is used; earlier scans that were too thin are named in the pick's `why` ("09:15 not
  available: …"), so the run says when it could not start at the asked time;
* the stocks pass the filters (money traded, price range, volume vs usual, who leads), then are ranked by one or more
  columns (`rank_by`); with several, each stock's place in each column is averaged (a stock missing a column is last
  in it), ties by money traded;
* the top `top_n` are that day's picks, traded from the minute after the latest order-book read among them
  (`read_at`, else the scan's start): a scan reads stocks one after another, so nobody could have seen the list before.

The picks are worked out once before the run (`choose_for_day`, on this server's saved scans or the desk's over the
link) and saved with the run, so Resume plays the same. Practice money; no order, nothing armed.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from groww_client import IST

KIND = "SAVED"
MAX_TOP_N = 50
MAX_UNIVERSE = 400

#: Ranking columns: key → (label, value from a saved row, higher is better). None = not known for that stock.
def _share(r):
    import pressure_exit

    return pressure_exit.usable_share(r.get("buy_share"))


def _f(r, key):
    try:
        v = r.get(key)
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _abs(v):
    return None if v is None else abs(v)


def _flow(r):
    v = _f(r, "flow_buy")
    return v if v is not None and 0.0 <= v <= 100.0 else None


COLUMNS: dict[str, tuple[str, callable]] = {
    "rvol": ("Vol vs usual", lambda r: _f(r, "rvol")),
    "value": ("Money traded", lambda r: _f(r, "value_cr")),
    "buyers": ("Buyers % (waiting)", lambda r: None if _share(r) is None else _share(r) * 100.0),
    "sellers": ("Sellers % (waiting)", lambda r: None if _share(r) is None else (1.0 - _share(r)) * 100.0),
    "imbalance": ("Buyer/seller imbalance", lambda r: None if _share(r) is None else abs(_share(r) - 0.5) * 200.0),
    "exec_buyers": ("Executed buy %", _flow),
    "exec_sellers": ("Executed sell %", lambda r: None if _flow(r) is None else 100.0 - _flow(r)),
    "move": ("Day move (either way)", lambda r: _abs(_f(r, "change_pct"))),
    "up": ("Day move up", lambda r: _f(r, "change_pct")),
    "down": ("Day move down", lambda r: None if _f(r, "change_pct") is None else -_f(r, "change_pct")),
    "speed": ("Speed (either way)", lambda r: _abs(_f(r, "speed_pct_min"))),
    "vwap": ("Distance from average price", lambda r: _abs(_f(r, "vwap_dist_pct"))),
    "range": ("Day range", lambda r: _f(r, "range_pct")),
}
SIDES = ("ANY", "BUYERS", "SELLERS")


@dataclass
class SavedScanRule:
    pick_time: dt.time = dt.time(9, 15)       # the asked time: the first usable scan at or after it
    top_n: int = 24
    rank_by: list[str] = field(default_factory=lambda: ["rvol"])
    qty: int = 0                              # shares per picked stock, 0 = the bot's own quantity
    side: str = "ANY"                         # ANY / BUYERS (buyers lead the waiting orders) / SELLERS
    min_value_cr: float = 0.0
    min_rvol: float = 0.0
    price_min: float = 0.0
    price_max: float = 0.0
    until: dt.time | None = None
    #: day (ISO) → the picks worked out before the run (choose_for_day), saved with it for Resume.
    chosen: dict[str, list[dict]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    kind: str = KIND

    def as_dict(self) -> dict:
        return {
            "kind": KIND,
            "pick_time": self.pick_time.strftime("%H:%M"),
            "top_n": self.top_n,
            "rank_by": list(self.rank_by),
            "qty": self.qty,
            "side": self.side,
            "min_value_cr": self.min_value_cr,
            "min_rvol": self.min_rvol,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "until": self.until.strftime("%H:%M") if self.until else None,
            "chosen": {k: list(v) for k, v in self.chosen.items()},
            "notes": dict(self.notes),
        }

    def query(self) -> dict:
        """The settings choose_for_day reads (sent to the desk over the link)."""
        return {k: v for k, v in self.as_dict().items() if k not in ("chosen", "notes", "kind", "until")}


def clean_rank(keys) -> list[str]:
    out = [str(k) for k in (keys or []) if str(k) in COLUMNS]
    return list(dict.fromkeys(out)) or ["rvol"]


def rule_from_dict(data: dict) -> SavedScanRule:
    def hhmm(v, default):
        try:
            return dt.time(int(str(v)[:2]), int(str(v)[3:5]))
        except (TypeError, ValueError):
            return default

    chosen = data.get("chosen") if isinstance(data.get("chosen"), dict) else {}
    notes = data.get("notes") if isinstance(data.get("notes"), dict) else {}
    return SavedScanRule(
        pick_time=hhmm(data.get("pick_time"), dt.time(9, 15)),
        top_n=max(1, min(MAX_TOP_N, int(data.get("top_n") or 24))),
        rank_by=clean_rank(data.get("rank_by")),
        qty=max(0, int(data.get("qty") or 0)),
        side=str(data.get("side") or "ANY") if str(data.get("side") or "ANY") in SIDES else "ANY",
        min_value_cr=float(data.get("min_value_cr") or 0.0),
        min_rvol=float(data.get("min_rvol") or 0.0),
        price_min=float(data.get("price_min") or 0.0),
        price_max=float(data.get("price_max") or 0.0),
        until=hhmm(data.get("until"), None) if data.get("until") else None,
        chosen={str(k): [p for p in v if isinstance(p, dict)] for k, v in chosen.items() if isinstance(v, list)},
        notes={str(k): str(v) for k, v in notes.items()},
    )


def _when(value) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=IST)).astimezone(IST)


def _passes(r: dict, rule: SavedScanRule) -> bool:
    import auto_arm

    if not str(r.get("symbol") or "").strip():
        return False
    if (_f(r, "value_cr") or 0.0) < rule.min_value_cr:
        return False
    if not auto_arm.price_ok(r.get("ltp"), rule.price_min, rule.price_max):
        return False
    if rule.min_rvol > 0 and (_f(r, "rvol") or 0.0) < rule.min_rvol:
        return False
    share = _share(r)
    if rule.side == "BUYERS" and (share is None or share <= 0.5):
        return False
    if rule.side == "SELLERS" and (share is None or share >= 0.5):
        return False
    return True


def rank_rows(rows: list[dict], rule: SavedScanRule) -> list[tuple[dict, float, dict]]:
    """(row, average place, {column: value}) best first, for the rows that pass the filters."""
    keys = clean_rank(rule.rank_by)
    rows = [r for r in rows if _passes(r, rule)]
    n = len(rows)
    places: list[list[float]] = [[] for _ in rows]
    values: list[dict] = [{} for _ in rows]
    for key in keys:
        get = COLUMNS[key][1]
        vals = [get(r) for r in rows]
        order = sorted((i for i, v in enumerate(vals) if v is not None), key=lambda i: -vals[i])
        place = {i: p + 1 for p, i in enumerate(order)}
        for i, v in enumerate(vals):
            places[i].append(place.get(i, n + 1))
            values[i][key] = None if v is None else round(v, 2)
    scored = [(rows[i], sum(places[i]) / len(keys), values[i]) for i in range(n)]
    scored.sort(key=lambda t: (t[1], -(_f(t[0], "value_cr") or 0.0), str(t[0].get("symbol"))))
    return scored


def _known(rows: list[dict], rule: SavedScanRule) -> int:
    """Stocks of a scan that pass the filters with every ranking column known."""
    keys = clean_rank(rule.rank_by)
    return sum(1 for r in rows if _passes(r, rule) and all(COLUMNS[k][1](r) is not None for k in keys))


def choose_for_day(scans: list[dict], rule: SavedScanRule) -> tuple[list[dict], str]:
    """The day's picks from its saved Most active scans (each {as_of, rows}) and a plain note.

    Each pick: {symbol, at "HH:MM", side BUY/SELL/None, rank, scan "HH:MM:SS", why}."""
    from pressure_study import in_session

    asked = rule.pick_time.strftime("%H:%M")
    ordered = sorted((s for s in scans if in_session(s)), key=lambda s: str(s.get("as_of") or ""))
    later = [s for s in ordered if (_when(s.get("as_of")) or dt.datetime.min.replace(tzinfo=IST)).time() >= rule.pick_time]
    if not ordered:
        return [], f"no saved Most active scans in the session"
    if not later:
        return [], f"no saved Most active scan at or after {asked}"
    labels = ", ".join(COLUMNS[k][0] for k in clean_rank(rule.rank_by))
    skipped: list[str] = []
    for scan in later:
        rows = [r for r in scan.get("rows") or [] if isinstance(r, dict)]
        start = _when(scan.get("as_of"))
        when = start.strftime("%H:%M:%S") if start else "?"
        known = _known(rows, rule)
        if known < rule.top_n:
            skipped.append(f"{when} ({known} of {len(rows)} stocks usable)")
            continue
        top = rank_rows(rows, rule)[: rule.top_n]
        reads = [_when(r.get("read_at")) for r, _p, _v in top]
        last = max([t for t in reads if t is not None] or [start])
        at = (last.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)).strftime("%H:%M")
        if at < "09:16":
            at = "09:16"
        late = ""
        if skipped:
            late = f"{asked} not available ({'; '.join(skipped[:3])}{' …' if len(skipped) > 3 else ''}), so run from this scan. "
        elif start and start.strftime("%H:%M") != asked:
            late = f"First scan at or after {asked} is {start:%H:%M}. "
        picks = []
        for place, (r, avg, vals) in enumerate(top, 1):
            share = _share(r)
            side = None if share is None else ("BUY" if share > 0.5 else "SELL" if share < 0.5 else None)
            shown = ", ".join(
                f"{COLUMNS[k][0]} {'-' if v is None else v}" for k, v in vals.items()
            )
            picks.append({
                "symbol": str(r["symbol"]).upper(), "at": at, "side": side, "rank": place, "scan": when,
                "source": "Most active",
                "why": f"{late}Scan {when}, #{place} by {labels} (avg place {avg:g}): {shown}. Traded from {at}.",
            })
        note = f"scan {when}, traded from {at}" + (f"; {asked} not available" if skipped else "")
        return picks, note
    return [], f"no saved scan at or after {asked} had {rule.top_n} usable stocks ({'; '.join(skipped[:3])})"


def picks_for_day(frames: dict, day: dt.date, rule: SavedScanRule) -> list[dict]:
    """The picks worked out before the run, for the stocks whose candles were loaded."""
    return [p for p in rule.chosen.get(day.isoformat(), []) if p.get("symbol") in frames]
