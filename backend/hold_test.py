"""Hold for N days: how many trading days a stock took to reach a profit % (arithmetic on real candles; read-only, no
order, nothing armed).

The owner's question on the buyers / sellers day list: "if I keep the stock for the next N days, how many days will it
take to reach that profit %?" Each row is a stock, a day, a side and the price it was taken at (the saved scan's last
price at the time buyers / sellers first reached the share). From the minute after that time, the stock's real Groww
1-minute candles (`strategy_lab.load_tape`, cached per day) are followed through that day and the next `days` trading
sessions:

* **Reached** (`on`, `hit_at`): the first minute whose high (a buy) / low (a sell) touches entry × (1 ± `target_pct` / 100). `day_no` 0 =
  the same day, 1 = the next trading session, and so on; with the date and the time.
* **Worst before**: the furthest the price went against the trade before the target (or over the whole hold when the
  target was never reached): the drawdown that hold needed.
* **Best** / **at the end**: the furthest it went its way, and the close of the last session held, signed the trade's way.

Sessions are the days that have candles, so weekends and holidays are skipped. A row whose later sessions have not
happened yet reports how many it had (`sessions`). Before charges; delivery / MTF costs are not counted.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

import numpy as np
import pandas as pd

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MAX_ROWS = 60
MAX_DAYS = 20


def calendar_span(days: int) -> int:
    """Calendar days to download after the day to cover `days` trading sessions (weekends and a few holidays)."""
    return int(math.ceil(days * 7 / 5)) + 4


def _parse_at(day: dt.date, at: str) -> dt.datetime:
    hh, mm = (at or "09:15").split(":")[:2]
    return dt.datetime.combine(day, dt.time(int(hh), int(mm)), tzinfo=IST)


def follow(tape: pd.DataFrame | None, day: dt.date, at: str, side: str, price: float, target_pct: float, days: int) -> dict:
    """One row's hold from `at` on `day` for the same day plus `days` sessions. `side` BUY / LONG buys, else sells."""
    sign = 1 if side in ("BUY", "LONG") else -1
    out: dict = {"reached": False, "day_no": None, "on": None, "hit_at": None, "worst_before_pct": None, "best_pct": None,
                 "end_pct": None, "sessions": 0, "target_price": None}
    if tape is None or tape.empty or not price or price <= 0:
        out["error"] = "no candles"
        return out
    target = price * (1 + sign * target_pct / 100.0)
    out["target_price"] = round(target, 2)
    start = int(_parse_at(day, at).timestamp()) + 60  # the minute after the scan saw it
    frame = tape[tape["ts"] >= start].sort_values("ts")
    if frame.empty:
        out["error"] = "no candle after that time"
        return out
    dates = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    sessions = [d for d in dict.fromkeys(dates) if d >= day]
    keep = sessions[: days + 1] if sessions and sessions[0] == day else sessions[:days]
    if not keep:
        out["error"] = "no session after that day yet"
        return out
    mask = dates.isin(keep).to_numpy()
    frame, dates = frame[mask], dates[mask]
    out["sessions"] = len([d for d in keep if d != day])
    high = frame["high"].to_numpy(dtype="float64")
    low = frame["low"].to_numpy(dtype="float64")
    ts = frame["ts"].to_numpy(dtype="int64")
    fav = (high - price) / price * 100 if sign == 1 else (price - low) / price * 100
    adv = (low - price) / price * 100 if sign == 1 else (price - high) / price * 100
    hit = np.nonzero(fav >= target_pct)[0]
    upto = int(hit[0]) + 1 if hit.size else len(ts)
    out["worst_before_pct"] = round(float(np.min(adv[:upto])), 2) if upto else None
    out["best_pct"] = round(float(np.max(fav)), 2)
    last_close = float(frame["close"].iloc[-1])
    out["end_pct"] = round(sign * (last_close - price) / price * 100, 2)
    if hit.size:
        i = int(hit[0])
        when = dt.datetime.fromtimestamp(int(ts[i]), IST)
        out.update(reached=True, on=when.date().isoformat(), hit_at=when.strftime("%H:%M"),
                   day_no=keep.index(when.date()) + (0 if keep[0] == day else 1))
    return out


def summarize(rows: list[dict], days: int, target_pct: float) -> dict:
    judged = [r for r in rows if not r.get("error")]
    hit = [r for r in judged if r.get("reached")]
    by_day = [{"day_no": n, "count": sum(1 for r in hit if r["day_no"] == n)} for n in range(days + 1)]
    run = 0
    for b in by_day:
        run += b["count"]
        b["by_then"] = run
        b["by_then_pct"] = round(run / len(judged) * 100, 1) if judged else None
    nums = [r["day_no"] for r in hit]
    answer = []
    if judged:
        answer.append(f"{len(hit)} of {len(judged)} stocks reached +{target_pct:g}% within {days} trading day"
                      f"{'' if days == 1 else 's'} of being picked ({len(hit) / len(judged) * 100:.0f}%).")
        same = sum(1 for r in hit if r["day_no"] == 0)
        if hit:
            answer.append(f"{same} of them did it the same day; on average it took {statistics.mean(nums):.1f} trading days "
                          f"(median {statistics.median(nums):g}).")
            worst = [r["worst_before_pct"] for r in hit if r.get("worst_before_pct") is not None]
            if worst:
                answer.append(f"Before reaching it they went against you by {statistics.mean(worst):.2f}% on average "
                              f"(worst {min(worst):.2f}%): the stop a hold would have needed.")
        short = sum(1 for r in judged if r["sessions"] < days)
        if short:
            answer.append(f"{short} stock{'' if short == 1 else 's'} had fewer than {days} later sessions so far (recent days).")
    return {"judged": len(judged), "reached": len(hit), "reached_pct": round(len(hit) / len(judged) * 100, 1) if judged else None,
            "avg_days": round(statistics.mean(nums), 2) if nums else None,
            "median_days": statistics.median(nums) if nums else None, "by_day": by_day, "answer": answer}


async def run(rows: list[dict], load, *, days: int, target_pct: float) -> dict:
    """`rows`: [{symbol, day, at, side, price}]. `load(symbol, first, last)` gives 1-minute candles (awaitable)."""
    today = dt.datetime.now(IST).date()
    out, errors = [], []
    for r in rows[:MAX_ROWS]:
        try:
            day = dt.date.fromisoformat(r["day"])
            last = min(today, day + dt.timedelta(days=calendar_span(days)))
            got = follow(await load(r["symbol"], day, last), day, r.get("at") or "09:15", r["side"], float(r["price"]), target_pct, days)
        except Exception as exc:  # noqa: BLE001 - one stock never stops the test
            errors.append({"symbol": r.get("symbol"), "error": str(exc)[:200]})
            continue
        out.append({**{k: r.get(k) for k in ("symbol", "day", "at", "side", "price")}, **got})
    return {"days": days, "target_pct": target_pct, "rows": out, "errors": errors, **summarize(out, days, target_pct)}
