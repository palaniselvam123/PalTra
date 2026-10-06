"""Turn replayed trades plus readings into the stock-selection evidence.

Honesty rules built in:

* Days are split in time order: the first part (development) is where every
  threshold and weight is chosen; the later part (hold-out) is only ever used
  to report how that choice did. A threshold is called "supported" only when
  it also improves net P&L and profit factor on the hold-out with enough
  trades left; otherwise the report says "Insufficient evidence".
* Stock-day filters (decided before the open, or at 09:45) remove whole
  stock-days, which is exact: each stock was replayed on its own.
  Trade-level filters (SMA gap / gap change at entry) are applied to the
  baseline trade list, which is approximate: skipping one trade can change
  the next under stop-and-reverse. They are labelled as such.
* Readings are cut on development quantiles, never on hold-out values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

MIN_TRADES_STOCK = 20  # fewer trades than this and a stock is not ranked
MIN_HOLDOUT_TRADES = 30
QUANTILES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
GAP_GRID = (0.05, 0.10, 0.15, 0.20, 0.30, 0.50)

# Experiments A-E: each stage adds one family of readings to the previous.
# Both directions are tried for every reading; development data picks one.
STAGES = {
    "pre": [
        ("A", "Liquidity", ["pre_turnover_cr_med"]),
        ("B", "+ Volatility", ["pre_atr_pct", "pre_range_pct"]),
        ("C", "+ Relative volume", ["pre_rvol"]),
        ("D", "+ Directional movement", ["pre_efficiency", "pre_crosses"]),
        ("E", "+ SMA 9/21 separation", ["pre_separation"]),
    ],
    "o30": [
        ("A", "Liquidity", ["o30_turnover_cr"]),
        ("B", "+ Volatility", ["o30_atr_pct", "o30_range_pct"]),
        ("C", "+ Relative volume", ["o30_rvol"]),
        ("D", "+ Directional movement", ["o30_efficiency", "o30_crosses"]),
        ("E", "+ SMA 9/21 separation", ["o30_separation", "o30_abs_gap"]),
    ],
}


def metrics(trades: pd.DataFrame, base: pd.DataFrame | None = None) -> dict:
    """Every figure the report asks for, on one set of trades."""
    n = len(trades)
    out: dict = {"trades": n}
    if n == 0:
        out.update({k: 0 for k in ("long", "short", "wins", "losses", "gross", "charges", "net")})
    else:
        net = trades["net"]
        wins, losses = trades[net > 0], trades[net <= 0]
        ordered = trades.sort_values("exit_time")
        curve = ordered["net"].cumsum()
        peak = curve.cummax().clip(lower=0)
        win_sum, loss_sum = float(wins["net"].sum()), float(losses["net"].sum())
        out.update(
            {
                "long": int((trades["direction"] == "LONG").sum()),
                "short": int((trades["direction"] == "SHORT").sum()),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(100 * len(wins) / n, 1),
                "gross": round(float(trades["gross"].sum()), 2),
                "charges": round(float(trades["charges"].sum()), 2),
                "net": round(float(net.sum()), 2),
                "avg_trade": round(float(net.mean()), 2),
                "avg_win": round(float(wins["net"].mean()), 2) if len(wins) else None,
                "avg_loss": round(float(losses["net"].mean()), 2) if len(losses) else None,
                "profit_factor": round(win_sum / -loss_sum, 2) if loss_sum < 0 else None,
                "max_drawdown": round(float((curve - peak).min()), 2),
                "largest_loss": round(float(net.min()), 2),
                "avg_minutes": round(float(trades["minutes_held"].mean()), 1) if "minutes_held" in trades else None,
            }
        )
    if base is not None:
        kept = set(trades["tid"]) if n else set()
        gone = base[~base["tid"].isin(kept)]
        out["avoided"] = len(gone)
        out["winners_filtered_out"] = int((gone["net"] > 0).sum())
        out["losers_filtered_out"] = int((gone["net"] <= 0).sum())
        out["net_change"] = round(out["net"] - float(base["net"].sum()), 2)
    return out


def split_days(days: list[str], dev_share: float) -> tuple[list[str], list[str]]:
    days = sorted(days)
    cut = max(1, int(round(len(days) * dev_share)))
    return days[:cut], days[cut:]


@dataclass
class Cut:
    feature: str
    op: str  # ">=" or "<="
    value: float

    def mask(self, frame: pd.DataFrame) -> pd.Series:
        col = frame[self.feature]
        return (col >= self.value) if self.op == ">=" else (col <= self.value)

    def text(self) -> str:
        return f"{self.feature} {self.op} {self.value:.4g}"


def _apply(trades: pd.DataFrame, days: pd.DataFrame, cuts: list[Cut]) -> pd.DataFrame:
    if not cuts:
        return trades
    keep = pd.Series(True, index=days.index)
    for c in cuts:
        keep &= c.mask(days).fillna(False)
    ok = set(zip(days.loc[keep, "symbol"], days.loc[keep, "date"]))
    return trades[[(s, d) in ok for s, d in zip(trades["symbol"], trades["date"])]]


def _candidates(days_dev: pd.DataFrame, feature: str, grid: tuple[float, ...] = ()) -> list[Cut]:
    values = days_dev[feature].dropna()
    if values.empty:
        return []
    points = sorted({float(values.quantile(q)) for q in QUANTILES} | set(grid))
    return [Cut(feature, op, v) for v in points for op in (">=", "<=")]


def _min_keep(n_base: int) -> int:
    return max(30, int(0.15 * n_base))


def greedy_experiments(trades: pd.DataFrame, days: pd.DataFrame, dev: list[str], hold: list[str], mode: str) -> list[dict]:
    """Experiments A-E: at each stage add the cut that most improves development net P&L."""
    t_dev, t_hold = trades[trades["date"].isin(dev)], trades[trades["date"].isin(hold)]
    d_dev, d_hold = days[days["date"].isin(dev)], days[days["date"].isin(hold)]
    rows = [{"id": "BASE", "name": "Baseline: SMA 9/21 cross, immediate entry", "cuts": [],
             "dev": metrics(t_dev, t_dev), "holdout": metrics(t_hold, t_hold)}]
    cuts: list[Cut] = []
    floor = _min_keep(len(t_dev))
    for sid, name, feats in STAGES[mode]:
        best, best_net = None, metrics(_apply(t_dev, d_dev, cuts))["net"]
        for f in feats:
            if f not in d_dev:
                continue
            grid = GAP_GRID if "separation" in f or "gap" in f else ()
            for c in _candidates(d_dev, f, grid):
                kept = _apply(t_dev, d_dev, cuts + [c])
                if len(kept) < floor:
                    continue
                m = metrics(kept)
                if m["net"] > best_net:
                    best, best_net = c, m["net"]
        if best is not None:
            cuts = cuts + [best]
        rows.append(
            {
                "id": sid,
                "name": name,
                "changed": best is not None,
                "added": best.text() if best else "nothing improved development net P&L",
                "cuts": [c.text() for c in cuts],
                "dev": metrics(_apply(t_dev, d_dev, cuts), t_dev),
                "holdout": metrics(_apply(t_hold, d_hold, cuts), t_hold),
            }
        )
    return rows


def _pf(m: dict) -> float:
    """Profit factor, with no losing trade counted as unbeatable."""
    pf = m.get("profit_factor")
    if pf is not None:
        return float(pf)
    return math.inf if m.get("wins") else 0.0


def supported(row: dict, base_hold: dict) -> bool:
    h = row["holdout"]
    return h["trades"] >= MIN_HOLDOUT_TRADES and h["net"] > base_hold["net"] and _pf(h) >= _pf(base_hold)


def quintiles(trades: pd.DataFrame, table: pd.DataFrame, feature: str, dev: list[str], hold: list[str]) -> list[dict]:
    """Outcome by reading bucket; bucket edges from development data only."""
    vals = table.loc[table["date"].isin(dev), feature].dropna()
    if len(vals) < 25:
        return []
    edges = sorted(set(vals.quantile([0.2, 0.4, 0.6, 0.8]).tolist()))
    bins = [-math.inf, *edges, math.inf]
    out = []
    for lo, hi in zip(bins, bins[1:]):
        inside = table[(table[feature] > lo) & (table[feature] <= hi)]
        if "tid" in table:  # trade-level table
            sel = trades[trades["tid"].isin(inside["tid"])]
        else:
            ok = set(zip(inside["symbol"], inside["date"]))
            sel = trades[[(s, d) in ok for s, d in zip(trades["symbol"], trades["date"])]]
        out.append(
            {
                "bucket": f"({lo:.4g}, {hi:.4g}]",
                "dev": metrics(sel[sel["date"].isin(dev)]),
                "holdout": metrics(sel[sel["date"].isin(hold)]),
            }
        )
    return out


def spearman(x: pd.Series, y: pd.Series) -> float | None:
    ok = x.notna() & y.notna()
    if ok.sum() < 20:
        return None
    r = x[ok].rank().corr(y[ok].rank())
    return None if pd.isna(r) else round(float(r), 3)


def stock_day_pnl(trades: pd.DataFrame, days: pd.DataFrame) -> pd.DataFrame:
    pnl = trades.groupby(["symbol", "date"])["net"].sum().rename("day_net").reset_index()
    return days.merge(pnl, on=["symbol", "date"], how="left").fillna({"day_net": 0.0})


def correlations(trades: pd.DataFrame, days: pd.DataFrame, dev: list[str], hold: list[str], features: list[str]) -> list[dict]:
    table = stock_day_pnl(trades, days)
    rows = []
    for f in features:
        if f not in table:
            continue
        d, h = table[table["date"].isin(dev)], table[table["date"].isin(hold)]
        rows.append({"feature": f, "rho_dev": spearman(d[f], d["day_net"]), "rho_holdout": spearman(h[f], h["day_net"])})
    return rows


def score_test(trades: pd.DataFrame, days: pd.DataFrame, dev: list[str], hold: list[str], features: list[str]) -> dict:
    """A 0-100 score from day-wise percentile ranks, weights = development Spearman.

    Readings whose development correlation is weaker than 0.05 get no weight.
    Then: each hold-out day, trade only the top N stocks by score.
    """
    table = stock_day_pnl(trades, days)
    d = table[table["date"].isin(dev)]
    weights = {}
    for f in features:
        if f not in table:
            continue
        r = spearman(d[f], d["day_net"])
        if r is not None and abs(r) >= 0.05:
            weights[f] = r
    if not weights:
        return {"weights": {}, "note": "No reading correlated with stock-day net P&L on development days (|rho| < 0.05)."}
    total = sum(abs(w) for w in weights.values())
    parts = []
    for f, w in weights.items():
        ranked = table.groupby("date")[f].rank(pct=True)
        parts.append((ranked if w > 0 else 1 - ranked).fillna(0.5) * abs(w) / total)
    table["score"] = sum(parts) * 100
    result = {"weights": {f: round(abs(w) / total * 100, 1) for f, w in weights.items()},
              "direction": {f: ("higher is better" if w > 0 else "lower is better") for f, w in weights.items()},
              "top_n": []}
    for n in (5, 10, 15, 20):
        row = {"n": n}
        for part, label in ((dev, "dev"), (hold, "holdout")):
            sub = table[table["date"].isin(part)]
            top = sub.sort_values("score", ascending=False).groupby("date").head(n)
            ok = set(zip(top["symbol"], top["date"]))
            picked = pd.Series([(s, dd) in ok for s, dd in zip(trades["symbol"], trades["date"])], index=trades.index)
            sel = trades[picked & trades["date"].isin(part)]
            m = metrics(sel)
            # What N random stocks a day would have made: the day's mean stock-day net x N.
            per_day = sub.groupby("date")["day_net"].mean() * np.minimum(n, sub.groupby("date").size())
            m["random_n_expected_net"] = round(float(per_day.sum()), 2)
            row[label] = m
        result["top_n"].append(row)
    return result


def gap_tables(trades: pd.DataFrame, dev: list[str], hold: list[str]) -> dict:
    """SMA gap at entry and its change: frequency and outcome (trade-level, approximate)."""
    out: dict = {"thresholds": [], "expansion": []}
    for col in ("gap_entry", "gap_d1", "gap_d3"):
        for g in GAP_GRID:
            sel = trades[trades[col] >= g]
            out["thresholds"].append(
                {
                    "reading": col,
                    "rule": f"{col} >= {g}",
                    "share_of_trades_pct": round(100 * len(sel) / max(1, len(trades)), 1),
                    "dev": metrics(sel[sel["date"].isin(dev)]),
                    "holdout": metrics(sel[sel["date"].isin(hold)]),
                }
            )
    for col in ("gap_d1", "gap_d3"):
        for label, sel in (("expanding (> 0)", trades[trades[col] > 0]), ("shrinking or flat (<= 0)", trades[trades[col] <= 0])):
            out["expansion"].append(
                {"reading": col, "group": label, "dev": metrics(sel[sel["date"].isin(dev)]), "holdout": metrics(sel[sel["date"].isin(hold)])}
            )
    out["gap_entry_distribution_pct"] = {
        f"p{int(q * 100)}": round(float(trades["gap_entry"].quantile(q)), 4) for q in (0.1, 0.25, 0.5, 0.75, 0.9)
    } if trades["gap_entry"].notna().any() else {}
    return out


def per_stock(trades: pd.DataFrame, dev: list[str], hold: list[str]) -> list[dict]:
    rows = []
    for sym, g in trades.groupby("symbol"):
        m = metrics(g)
        m["symbol"] = sym
        m["dev_net"] = round(float(g.loc[g["date"].isin(dev), "net"].sum()), 2)
        m["holdout_net"] = round(float(g.loc[g["date"].isin(hold), "net"].sum()), 2)
        m["holdout_trades"] = int(g["date"].isin(hold).sum())
        rows.append(m)
    rows.sort(key=lambda r: r["net"], reverse=True)
    return rows
