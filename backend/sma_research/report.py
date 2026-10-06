"""Markdown report in the order the owner asked for (sections 3-8 and 10 are data)."""
from __future__ import annotations

from pathlib import Path

COLS = [
    ("trades", "Trades"), ("long", "Long"), ("short", "Short"), ("wins", "Win"), ("losses", "Loss"),
    ("win_rate", "Win %"), ("gross", "Gross ₹"), ("charges", "Charges ₹"), ("net", "Net ₹"),
    ("avg_win", "Avg win"), ("avg_loss", "Avg loss"), ("profit_factor", "PF"), ("max_drawdown", "Max DD"),
    ("largest_loss", "Worst"), ("avg_minutes", "Avg min"), ("avoided", "Avoided"),
    ("winners_filtered_out", "Winners cut"), ("losers_filtered_out", "Losers cut"),
]


def _v(x) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:,.2f}"
    return f"{x:,}" if isinstance(x, int) else str(x)


def _table(rows: list[tuple[str, dict]], cols=COLS) -> str:
    head = "| | " + " | ".join(c[1] for c in cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"
    return head + "".join("| " + label + " | " + " | ".join(_v(m.get(k)) for k, _ in cols) + " |\n" for label, m in rows)


SHORT = [("trades", "Trades"), ("win_rate", "Win %"), ("net", "Net ₹"), ("profit_factor", "PF"), ("avg_trade", "Avg ₹")]


def write_report(res: dict, path: Path) -> Path:
    L: list[str] = []
    w = L.append
    dev, hold = res["days"]["development"], res["days"]["holdout"]
    w("# SMA 9/21 stock selection — evidence report\n")
    w(f"Data: {len(res['universe'].get('symbols') or [])} stocks, replayed sessions "
      f"{dev[0]} → {(hold or dev)[1]}. Development (thresholds chosen here): {dev[0]} → {dev[1]} ({dev[2]} days). "
      + (f"Hold-out (only reported): {hold[0]} → {hold[1]} ({hold[2]} days)." if hold else "No hold-out days.") + "\n")
    w(f"Strategy replayed unchanged: SMA {res['settings_used'].get('sma_fast')}/{res['settings_used'].get('sma_slow')} "
      "cross on closed 1-minute candles, immediate entry, stop-and-reverse, the saved stop settings "
      f"({res['settings_used'].get('stop_type')}, use_stop={res['settings_used'].get('use_stop')}). Entry filters and the "
      "Bollinger exit OFF; no daily loss limit (each stock replayed on its own). Runtime "
      f"{res.get('runtime_min')} min.\n")
    w("Approximate parts are labelled. A threshold is **supported** only if, chosen on development days, it also "
      "raised hold-out net P&L and kept hold-out profit factor ≥ baseline with ≥ 30 hold-out trades.\n")

    w("\n## 3. What makes a stock good for SMA 9/21? (stock-day readings vs that stock-day's net P&L)\n")
    w("Spearman rank correlation; same sign in both periods = consistent.\n\n| Reading | ρ development | ρ hold-out | Consistent |\n|---|---|---|---|\n")
    for c in sorted(res["correlations"], key=lambda r: -abs(r["rho_dev"] or 0)):
        same = c["rho_dev"] is not None and c["rho_holdout"] is not None and c["rho_dev"] * c["rho_holdout"] > 0
        w(f"| {c['feature']} | {_v(c['rho_dev'])} | {_v(c['rho_holdout'])} | {'yes' if same else 'no'} |\n")
    w("\nOutcome by reading bucket (edges from development days):\n")
    for f, rows in res["quintiles"].items():
        if not rows or not f.startswith("pre_"):
            continue
        w(f"\n**{f}**\n\n" + _table([(f"{r['bucket']} dev", r["dev"]) for r in rows] + [(f"{r['bucket']} hold-out", r["holdout"]) for r in rows], SHORT))

    w("\n## 4. Best stock-selection readings (ranked)\n")
    ranked = [c for c in res["correlations"] if c["rho_dev"] is not None and c["rho_holdout"] is not None and c["rho_dev"] * c["rho_holdout"] > 0]
    ranked.sort(key=lambda c: -min(abs(c["rho_dev"]), abs(c["rho_holdout"])))
    if ranked:
        for i, c in enumerate(ranked, 1):
            w(f"{i}. {c['feature']} — {'higher' if c['rho_dev'] > 0 else 'lower'} is better (ρ {c['rho_dev']} / {c['rho_holdout']})\n")
    else:
        w("Insufficient evidence: no reading related to stock-day P&L with the same sign in both periods.\n")

    w("\n## 5. Backtest results — experiments A-E\n")
    for mode, title in (("pre", "Chosen before the open (previous sessions only)"), ("o30", "Chosen at 09:45 (trades from 09:45 only)")):
        rows = res["experiments"][mode]
        w(f"\n### {title}\n\n")
        for r in rows[1:]:
            verdict = ("no change (no reading improved development)" if r.get("supported") is None
                       else "**supported**" if r["supported"] else "not supported on hold-out")
            w(f"- {r['id']} {r['name']}: added `{r.get('added')}` → cuts {r['cuts'] or 'none'} — {verdict}\n")
        w("\nDevelopment:\n\n" + _table([(f"{r['id']}", r["dev"]) for r in rows]))
        w("\nHold-out:\n\n" + _table([(f"{r['id']}", r["holdout"]) for r in rows]))

    w("\n## 6. Best thresholds\n")
    any_supported = False
    for mode in ("pre", "o30"):
        for r in res["experiments"][mode][1:]:
            if r.get("supported"):
                any_supported = True
                h = r["holdout"]
                w(f"- ({mode}) {r['id']}: {', '.join(r['cuts'])} — hold-out net ₹{_v(h['net'])}, PF {_v(h.get('profit_factor'))}, "
                  f"{h['trades']} trades\n")
    if not any_supported:
        w("Insufficient evidence: no stock-day threshold improved the hold-out.\n")
    w("\n**SMA 9/21 gap at entry (trade-level, approximate under stop-and-reverse).** gap_entry = signed gap on the "
      "cross candle; gap_d1 / gap_d3 = how much the gap widened in the trade's direction over 1 / 3 candles.\n")
    dist = res["gap"].get("gap_entry_distribution_pct")
    if dist:
        w(f"\nGap % on the cross candle, distribution: {dist}\n")
    w("\n" + _table([(f"{r['rule']} ({r['share_of_trades_pct']}% of trades) dev", r["dev"]) for r in res["gap"]["thresholds"]]
                    + [(f"{r['rule']} hold-out", r["holdout"]) for r in res["gap"]["thresholds"]], SHORT))
    w("\nExpanding vs shrinking separation at entry:\n\n" + _table(
        [(f"{r['reading']} {r['group']} dev", r["dev"]) for r in res["gap"]["expansion"]]
        + [(f"{r['reading']} {r['group']} hold-out", r["holdout"]) for r in res["gap"]["expansion"]], SHORT))

    sc = res["score"]
    w("\n### Suitability score (weights from development correlations)\n")
    if sc.get("weights"):
        w(f"Weights /100: {sc['weights']}; direction: {sc['direction']}\n\n")
        w("| Top N a day | Dev net ₹ | Dev random-N ₹ | Hold-out net ₹ | Hold-out random-N ₹ | Hold-out PF | Hold-out trades |\n|---|---|---|---|---|---|---|\n")
        for r in sc["top_n"]:
            w(f"| {r['n']} | {_v(r['dev']['net'])} | {_v(r['dev']['random_n_expected_net'])} | {_v(r['holdout']['net'])} | "
              f"{_v(r['holdout']['random_n_expected_net'])} | {_v(r['holdout'].get('profit_factor'))} | {_v(r['holdout']['trades'])} |\n")
    else:
        w(sc.get("note", "Insufficient evidence.") + "\n")

    stocks = [s for s in res["per_stock"] if s["trades"] >= 20]
    cols = [("trades", "Trades"), ("win_rate", "Win %"), ("net", "Net ₹"), ("profit_factor", "PF"), ("avg_trade", "Avg ₹"),
            ("max_drawdown", "Max DD"), ("dev_net", "Dev net"), ("holdout_net", "Hold-out net")]
    w(f"\n## 7. Best stocks for this strategy (≥ 20 trades; {len(stocks)} of {len(res['per_stock'])} stocks)\n\n")
    best = [s for s in stocks if s["dev_net"] > 0 and s["holdout_net"] > 0][:15]
    w(_table([(s["symbol"], s) for s in best], cols) if best else "Insufficient evidence: no stock was profitable in both periods.\n")
    w("\n## 8. Stocks to avoid (lost money in both periods)\n\n")
    worst = [s for s in reversed(stocks) if s["dev_net"] < 0 and s["holdout_net"] < 0][:15]
    w(_table([(s["symbol"], s) for s in worst], cols) if worst else "Insufficient evidence.\n")
    w("\nAll stocks:\n\n" + _table([(s["symbol"], s) for s in res["per_stock"]], cols))

    w("\n## 10. Expected impact (hold-out, best supported experiment)\n")
    picks = [(m, r) for m in ("pre", "o30") for r in res["experiments"][m][1:] if r.get("supported")]
    if picks:
        mode, r = max(picks, key=lambda p: p[1]["holdout"]["net"])
        base = res["experiments"][mode][0]["holdout"]
        h = r["holdout"]
        w(f"{mode} {r['id']} ({', '.join(r['cuts'])}): trades {base['trades']} → {h['trades']} (avoided {h['avoided']}: "
          f"{h['losers_filtered_out']} losers, {h['winners_filtered_out']} winners); net ₹{_v(base['net'])} → ₹{_v(h['net'])}; "
          f"max drawdown ₹{_v(base.get('max_drawdown'))} → ₹{_v(h.get('max_drawdown'))}.\n")
    else:
        w("Insufficient evidence.\n")
    chk = res.get("cross_bar_check", {})
    w(f"\nCheck: {chk.get('trades_with_cross_on_signal_bar_pct')}% of trades have the SMA cross on the candle before entry "
      "(expected ~100%; reversals after the cut-off and square-off can differ).\n")
    path.write_text("".join(L))
    return path
