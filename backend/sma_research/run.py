"""Replay every stock, take the readings, and write the evidence report.

Runs anywhere the candles are (no Groww access needed): each worker process
replays whole stocks into its own throw-away SQLite file.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

logger = logging.getLogger("sma_research")

# Sessions kept back before the first replayed day so SMA 21, ATR 14, the
# previous day's readings and the 20-session volume average have data.
WARMUP_SESSIONS = 6


def _worker_init(db_dir: str) -> None:
    os.environ["SMA_DATABASE_URL"] = f"sqlite:///{db_dir}/worker-{os.getpid()}.db"
    os.environ.setdefault("SMA_TICK_SIZES", "off")
    import database

    database.reset_engine()
    database.init_db()


def _replay_one(path: str, settings: dict, out_dir: str) -> dict:
    """One stock: replay its days, read its stock-days and its trades' entries."""
    from sma_research.features import prepare, stock_days, trade_features
    from sma_research.replayer import replay_symbol, trading_days

    symbol = Path(path).name.split(".")[0]
    cache = Path(out_dir) / f"{symbol}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    frame = pd.read_pickle(path)
    days = trading_days(frame)[WARMUP_SESSIONS:]
    result: dict = {"symbol": symbol, "days": [d.isoformat() for d in days], "trades": [], "stock_days": []}
    if days:
        trades = asyncio.run(replay_symbol(frame, symbol, days, settings))
        prepared = prepare(frame, int(settings.get("sma_fast") or 9), int(settings.get("sma_slow") or 21),
                           int(settings.get("atr_period") or 14))
        for t in trades:
            t.update(trade_features(prepared, t))
            t["entry_time"] = t["entry_time"].isoformat() if t["entry_time"] else None
            t["exit_time"] = t["exit_time"].isoformat() if t["exit_time"] else None
        result["trades"] = trades
        result["stock_days"] = stock_days(prepared, symbol, days)
    cache.write_text(json.dumps(result, default=str))
    return result


def replay_all(data_dir: Path, out: Path, settings: dict, jobs: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    files = sorted(str(p) for p in (data_dir / "candles").glob("*.pkl.gz"))
    if not files:
        raise SystemExit(f"No candles in {data_dir / 'candles'}. Run the download step first.")
    (out / "replays").mkdir(parents=True, exist_ok=True)
    (out / "db").mkdir(exist_ok=True)
    results = []
    with ProcessPoolExecutor(max_workers=jobs, initializer=_worker_init, initargs=(str(out / "db"),)) as pool:
        futures = {pool.submit(_replay_one, f, settings, str(out / "replays")): f for f in files}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("replay %s failed: %s", futures[fut], exc)
                continue
            results.append(res)
            logger.info("%d/%d %s: %d trades", i, len(files), res["symbol"], len(res["trades"]))
    trades = pd.DataFrame([t for r in results for t in r["trades"]])
    days = pd.DataFrame([d for r in results for d in r["stock_days"]])
    if trades.empty:
        raise SystemExit("The replays produced no trades.")
    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])
    trades = trades.sort_values(["entry_time", "symbol"]).reset_index(drop=True)
    trades["tid"] = range(len(trades))
    return trades, days


def analyze(trades: pd.DataFrame, days: pd.DataFrame, dev_share: float) -> dict:
    from sma_research import analysis as A

    all_days = sorted(set(days["date"]) | set(trades["date"]))
    dev, hold = A.split_days(all_days, dev_share)
    res: dict = {"days": {"development": [dev[0], dev[-1], len(dev)], "holdout": [hold[0], hold[-1], len(hold)] if hold else None}}
    res["baseline_all"] = A.metrics(trades)
    res["experiments"] = {}
    for mode in ("pre", "o30"):
        tr = trades
        if mode == "o30":
            # A 09:45 choice cannot remove trades taken before 09:45.
            tr = trades[trades["entry_time"].dt.hour * 60 + trades["entry_time"].dt.minute >= 9 * 60 + 45]
        rows = A.greedy_experiments(tr, days, dev, hold, mode)
        for r in rows[1:]:
            # A stage that added nothing has no evidence of its own.
            r["supported"] = A.supported(r, rows[0]["holdout"]) if r["changed"] else None
        res["experiments"][mode] = rows
    features = [c for c in days.columns if c.startswith(("pre_", "o30_"))]
    res["correlations"] = A.correlations(trades, days, dev, hold, features)
    res["quintiles"] = {f: A.quintiles(trades, days, f, dev, hold) for f in features}
    trade_feats = [c for c in ("gap_entry", "gap_d1", "gap_d3", "atr_pct_entry", "vol_ratio_entry") if c in trades]
    res["trade_quintiles"] = {f: A.quintiles(trades, trades, f, dev, hold) for f in trade_feats}
    res["gap"] = A.gap_tables(trades, dev, hold)
    pre_feats = [f for f in features if f.startswith("pre_")]
    res["score"] = A.score_test(trades, days, dev, hold, pre_feats)
    res["per_stock"] = A.per_stock(trades, dev, hold)
    res["cross_bar_check"] = {
        "trades_with_cross_on_signal_bar_pct": round(100 * float(trades["is_cross_bar"].mean()), 1)
        if "is_cross_bar" in trades else None
    }
    return res


def run(data_dir: Path, out: Path, jobs: int, dev_share: float) -> Path:
    from sma_research.replayer import baseline_settings
    from sma_research.report import write_report

    saved = json.loads((data_dir / "settings.json").read_text())
    settings = baseline_settings(saved)
    meta = json.loads((data_dir / "universe.json").read_text()) if (data_dir / "universe.json").exists() else {}
    out.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now()
    trades, days = replay_all(data_dir, out, settings, jobs)
    trades.drop(columns=["tid"]).to_csv(out / "trades.csv", index=False)
    days.to_csv(out / "stock_days.csv", index=False)
    res = analyze(trades, days, dev_share)
    res["settings_used"] = {k: settings.get(k) for k in (
        "sma_fast", "sma_slow", "atr_period", "atr_multiplier", "use_stop", "stop_type", "gap_sl_mult", "gap_tp_mult",
        "gap_min_pct", "tsl_sl_points", "tsl_trail_points", "tsl_target_points", "qty", "entry_cutoff_time",
        "square_off_time")}
    res["universe"] = {k: meta.get(k) for k in ("start", "end", "requested_days", "top", "symbols")}
    res["runtime_min"] = round((dt.datetime.now() - started).total_seconds() / 60, 1)
    (out / "results.json").write_text(json.dumps(res, default=str, indent=1))
    return write_report(res, out / "report.md")
