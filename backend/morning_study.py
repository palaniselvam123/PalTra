"""Morning trend test (read-only research; the Scalp page's "Morning test" tab, no order path, nothing in the strategy
reads it).

The owner's idea: a stock that is bullish from the morning keeps doing well (for intraday, delivery and MTF), while a
stock that is bearish from the morning is mostly bought back up before the close. This measures it on past days.

For each picked stock and each past trading day:

* **The morning** (09:15 to the picked time `at`: 10:00 / 10:30 / 11:00 / 12:00 IST) is labelled ``BULLISH``,
  ``BEARISH`` or ``SIDEWAYS``: bullish when the price at `at` is at least `min_move` % (0.5) above the day's open, SMA
  fast is above SMA slow on the last closed candle of the bot's size (`minutes`, SMAs warmed up on the days before), and
  the morning's path was not a zig-zag (efficiency |net| / sum |moves| of the 1-minute closes ≥ `MIN_EFFICIENCY` 0.3);
  bearish the mirror; anything else sideways.
* **The rest of the day** from the price at `at` (`ref`): the move to the close (`rest_pct`), the lowest and highest
  price after `at` and when, the day's low / high and when, the bounce from the day's low to the close (`bounce_pct`) and
  the give-back from the day's high (`giveback_pct`).
* **The next day**: its opening gap from this close (`next_gap_pct`) and its close against this close (`next_day_pct`,
  only once that session has closed), for holding as delivery / MTF.
* **Delivery %** that day from NSE's evening bhavcopy (`app.services.delivery.load_day`; skipped when it cannot be read).

`summarize` gives per label: how many days, % closed higher than at `at`, the average and median move to the close, the
average bounce from the day's low and % of days with a bounce of 1 % or more, when the day's low / high came (half-hour
buckets), the next day's gap and close, the average delivery %, and plain-words `answer` lines. `by_stock` gives each
stock's own count and hit rate per label. `MorningStudy` downloads each stock once (after the bots' rate limit clears)
and keeps the last report in `morning_study.json` next to the SMA DB (`MORNING_STUDY_FILE`), like the Sideways test.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import indicators
from candles import resample
from regime_study import trading_days

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
CUT_TIMES = ("10:00", "10:30", "11:00", "12:00")
SIZES = (1, 3, 5, 10, 15)
MIN_DAYS, MAX_DAYS = 3, 30
MAX_STOCKS = 200
MIN_EFFICIENCY = 0.3
DEFAULT_MIN_MOVE = 0.5
BOUNCE_BIG = 1.0  # % from the day's low to the close that counts as "bought back up"
MIN_SESSION_ROWS = 300  # a day with fewer 1-minute candles is skipped (half day, missing data)
SESSION_OPEN = dt.time(9, 15)
SESSION_LAST = dt.time(15, 29)
BULLISH, BEARISH, SIDEWAYS = "BULLISH", "BEARISH", "SIDEWAYS"
LABEL_ORDER = (BULLISH, BEARISH, SIDEWAYS)
WORDS = {BULLISH: "Bullish mornings", BEARISH: "Bearish mornings", SIDEWAYS: "Sideways mornings"}


def _hhmm(raw: str) -> dt.time:
    h, m = str(raw).split(":")
    return dt.time(int(h), int(m))


def _ist(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts.astype("int64"), unit="s", utc=True).dt.tz_convert(IST)


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or not b:
        return None
    return (a / b - 1) * 100


def _efficiency(close: np.ndarray) -> float | None:
    if len(close) < 3:
        return None
    path = float(np.abs(np.diff(close)).sum())
    return abs(float(close[-1] - close[0])) / path if path > 0 else None


def label(move_pct: float | None, side: str | None, efficiency: float | None, min_move: float) -> str:
    if move_pct is None or side is None or efficiency is None or efficiency < MIN_EFFICIENCY:
        return SIDEWAYS
    if move_pct >= min_move and side == "UP":
        return BULLISH
    if move_pct <= -min_move and side == "DOWN":
        return BEARISH
    return SIDEWAYS


def _sma_side(tape: pd.DataFrame, cut_ts: int, minutes: int, sma_fast: int, sma_slow: int) -> str | None:
    """SMA fast vs slow on the last candle of `minutes` closed by `cut_ts` (the SMAs run across the days before)."""
    bars = resample(tape[tape["ts"].astype("int64") < cut_ts], minutes)
    if bars is None or bars.empty:
        return None
    bars = bars[bars["ts"].astype("int64") + minutes * 60 <= cut_ts]
    if len(bars) < sma_slow + 1:
        return None
    enriched = indicators.enrich(bars.drop(columns=["volume"], errors="ignore").reset_index(drop=True), sma_fast, sma_slow)
    last = enriched.iloc[-1]
    fast, slow = last.get("sma_9"), last.get("sma_21")
    if pd.isna(fast) or pd.isna(slow) or fast == slow:
        return None
    return "UP" if fast > slow else "DOWN"


def analyze_stock(
    symbol: str,
    tape: pd.DataFrame,
    days: list[dt.date],
    at: str,
    minutes: int,
    sma_fast: int,
    sma_slow: int,
    min_move: float = DEFAULT_MIN_MOVE,
    delivery: dict[dt.date, dict] | None = None,
) -> list[dict]:
    """One sample per day of `days` with a full session in the 1-minute `tape`."""
    if tape is None or getattr(tape, "empty", True):
        return []
    tape = tape.sort_values("ts").reset_index(drop=True)
    when = _ist(tape["ts"])
    dates = when.dt.date
    clock = when.dt.time
    cut = _hhmm(at)
    sessions = sorted(set(dates))
    out: list[dict] = []
    for day in days:
        rows = tape[(dates == day).to_numpy()]
        if len(rows) < MIN_SESSION_ROWS:
            continue
        times = clock[(dates == day).to_numpy()]
        cut_ts = int(dt.datetime.combine(day, cut, tzinfo=IST).timestamp())
        morning = rows[rows["ts"].astype("int64") < cut_ts]
        rest = rows[rows["ts"].astype("int64") >= cut_ts]
        if len(morning) < 10 or len(rest) < 10:
            continue
        day_open = float(rows["open"].iloc[0])
        ref = float(morning["close"].iloc[-1])
        close = float(rows["close"].iloc[-1])
        move = _pct(ref, day_open)
        eff = _efficiency(morning["close"].to_numpy(dtype=float))
        side = _sma_side(tape, cut_ts, minutes, sma_fast, sma_slow)
        lab = label(move, side, eff, min_move)
        low_i = rest["low"].astype(float).idxmin()
        high_i = rest["high"].astype(float).idxmax()
        day_low_i = rows["low"].astype(float).idxmin()
        day_high_i = rows["high"].astype(float).idxmax()
        stamp = lambda i: times.loc[i].strftime("%H:%M")  # noqa: E731
        day_low, day_high = float(rows["low"].loc[day_low_i]), float(rows["high"].loc[day_high_i])
        nxt = [d for d in sessions if d > day]
        next_gap = next_day = None
        if nxt:
            nrows = tape[(dates == nxt[0]).to_numpy()]
            if len(nrows):
                next_gap = _pct(float(nrows["open"].iloc[0]), close)
                ended = clock[(dates == nxt[0]).to_numpy()].iloc[-1] >= SESSION_LAST
                next_day = _pct(float(nrows["close"].iloc[-1]), close) if ended else None
        deliv = ((delivery or {}).get(day) or {}).get(symbol.upper()) or {}
        out.append(
            {
                "symbol": symbol.upper(),
                "day": day.isoformat(),
                "label": lab,
                "side": side,
                "morning_move_pct": move,
                "efficiency": eff,
                "ref": ref,
                "rest_pct": _pct(close, ref),
                "day_pct": _pct(close, day_open),
                "low_after_pct": _pct(float(rest["low"].loc[low_i]), ref),
                "low_after_at": stamp(low_i),
                "high_after_pct": _pct(float(rest["high"].loc[high_i]), ref),
                "high_after_at": stamp(high_i),
                "day_low_at": stamp(day_low_i),
                "day_high_at": stamp(day_high_i),
                "bounce_pct": _pct(close, day_low),
                "giveback_pct": _pct(close, day_high),
                "next_gap_pct": next_gap,
                "next_day_pct": next_day,
                "deliv_pct": deliv.get("pct"),
            }
        )
    return out


def _avg(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 3) if values else None


def _median(values: list[float]) -> float | None:
    return round(float(np.median(values)), 3) if values else None


def _share(values: list[bool]) -> float | None:
    return round(100.0 * sum(values) / len(values), 1) if values else None


def _half_hours(stamps: list[str]) -> list[dict]:
    """Counts per half hour (09:15 is put in 09:00) for when the day's low / high came."""
    counts: dict[str, int] = {}
    for s in stamps:
        h, m = s.split(":")
        key = f"{h}:{'00' if int(m) < 30 else '30'}"
        counts[key] = counts.get(key, 0) + 1
    total = len(stamps) or 1
    return [{"at": k, "count": v, "pct": round(100.0 * v / total, 1)} for k, v in sorted(counts.items())]


def _group(rows: list[dict]) -> dict:
    vals = lambda k: [r[k] for r in rows if r.get(k) is not None]  # noqa: E731
    rest = vals("rest_pct")
    deliv = vals("deliv_pct")
    nxt = vals("next_gap_pct")
    nday = vals("next_day_pct")
    out = {
        "days": len(rows),
        "stocks": len({r["symbol"] for r in rows}),
        "closed_higher_pct": _share([v > 0 for v in rest]),
        "avg_rest_pct": _avg(rest),
        "median_rest_pct": _median(rest),
        "avg_low_after_pct": _avg(vals("low_after_pct")),
        "avg_high_after_pct": _avg(vals("high_after_pct")),
        "avg_bounce_pct": _avg(vals("bounce_pct")),
        "bounce_big_pct": _share([v >= BOUNCE_BIG for v in vals("bounce_pct")]),
        "avg_giveback_pct": _avg(vals("giveback_pct")),
        "low_times": _half_hours(vals("day_low_at")),
        "high_times": _half_hours(vals("day_high_at")),
        "avg_next_gap_pct": _avg(nxt),
        "next_gap_up_pct": _share([v > 0 for v in nxt]),
        "avg_next_day_pct": _avg(nday),
        "next_day_up_pct": _share([v > 0 for v in nday]),
        "next_days": len(nday),
        "avg_deliv_pct": _avg(deliv),
        "deliv_days": len(deliv),
    }
    if len(deliv) >= 6:
        cut = float(np.median(deliv))
        hi = [r["rest_pct"] for r in rows if r.get("deliv_pct") is not None and r["deliv_pct"] >= cut and r.get("rest_pct") is not None]
        lo = [r["rest_pct"] for r in rows if r.get("deliv_pct") is not None and r["deliv_pct"] < cut and r.get("rest_pct") is not None]
        out["deliv_split"] = {
            "at_pct": round(cut, 1),
            "high": {"days": len(hi), "closed_higher_pct": _share([v > 0 for v in hi]), "avg_rest_pct": _avg(hi)},
            "low": {"days": len(lo), "closed_higher_pct": _share([v > 0 for v in lo]), "avg_rest_pct": _avg(lo)},
        }
    return out


def _peak(times: list[dict]) -> str | None:
    if not times:
        return None
    best = max(times, key=lambda t: t["count"])
    return f"around {best['at']} ({best['pct']:.0f}% of those days)"


def _of(share: float | None, days: int) -> str:
    """"7 of 10 days (70%)" from a share and a day count."""
    if share is None or not days:
        return "—"
    return f"{round(share * days / 100)} of {days} days ({share:.0f}%)"


def _move(v: float | None) -> str:
    if v is None:
        return "—"
    return f"rose {v:.2f}%" if v > 0 else f"fell {abs(v):.2f}%" if v < 0 else "did not move"


def answer(groups: dict[str, dict], at: str) -> list[str]:
    """Plain words for the owner's two questions, one short sentence at a time."""
    lines: list[str] = []
    bear, bull = groups.get(BEARISH) or {}, groups.get(BULLISH) or {}
    if bear.get("days"):
        lines.append(
            f"When a stock fell in the morning (09:15 to {at}, {bear['days']} days): it ended the day higher than at {at} on "
            f"{_of(bear['closed_higher_pct'], bear['days'])}. On average it {_move(bear['avg_rest_pct'])} from {at} to the close."
        )
        lines.append(
            f"Its lowest price of the day came most often {_peak(bear['low_times'])}, and from that low it climbed "
            f"{bear['avg_bounce_pct']:.2f}% by the close on average."
        )
        share = bear["closed_higher_pct"]
        if share is not None:
            lines.append(
                "So yes: a falling morning was usually bought back up before the close."
                if share >= 60
                else "So no: after a falling morning the stock usually stayed down."
                if share <= 40
                else "So it is a coin toss: after a falling morning it was bought back up on about half the days."
            )
    if bull.get("days"):
        lines.append(
            f"When a stock rose in the morning ({bull['days']} days): it ended higher than at {at} on "
            f"{_of(bull['closed_higher_pct'], bull['days'])}. On average it {_move(bull['avg_rest_pct'])} from {at} to the close."
        )
        if bull.get("next_gap_up_pct") is not None:
            nxt = f"If held overnight, the next morning it opened higher on {bull['next_gap_up_pct']:.0f}% of days"
            if bull.get("next_day_up_pct") is not None:
                nxt += f" and closed higher the next day on {bull['next_day_up_pct']:.0f}% of days"
            lines.append(nxt + ".")
    for lab, g in ((BULLISH, bull), (BEARISH, bear)):
        split = g.get("deliv_split")
        if split and split["high"]["days"] and split["low"]["days"]:
            word = "rising" if lab == BULLISH else "falling"
            lines.append(
                f"On {word} mornings with high delivery ({split['at_pct']:.0f}% or more), the stock ended higher on "
                f"{split['high']['closed_higher_pct']:.0f}% of days, against {split['low']['closed_higher_pct']:.0f}% with low delivery."
            )
    small = [WORDS[lab].lower() for lab, g in ((BULLISH, bull), (BEARISH, bear)) if 0 < g.get("days", 0) < 5]
    if small:
        lines.append(f"Careful: only a few {' and '.join(small)} — too few days to trust. Try more stocks or more days.")
    if not lines:
        lines.append("Not enough days with a clear rising or falling morning. Try more days, more stocks or a smaller move.")
    return lines


def summarize(samples: list[dict], at: str) -> dict:
    groups = {lab: _group([s for s in samples if s["label"] == lab]) for lab in LABEL_ORDER}
    return {
        "samples": len(samples),
        "stocks": len({s["symbol"] for s in samples}),
        "groups": groups,
        "answer": answer(groups, at),
        "by_stock": by_stock(samples),
    }


def by_stock(samples: list[dict]) -> list[dict]:
    names = sorted({s["symbol"] for s in samples})
    out = []
    for name in names:
        mine = [s for s in samples if s["symbol"] == name]
        row: dict = {"symbol": name, "days": len(mine)}
        for lab in LABEL_ORDER:
            picked = [s["rest_pct"] for s in mine if s["label"] == lab and s.get("rest_pct") is not None]
            row[lab.lower()] = {"days": len(picked), "closed_higher_pct": _share([v > 0 for v in picked]), "avg_rest_pct": _avg(picked)}
        deliv = [s["deliv_pct"] for s in mine if s.get("deliv_pct") is not None]
        row["avg_deliv_pct"] = _avg(deliv)
        out.append(row)
    out.sort(key=lambda r: (-(r["bearish"]["days"] + r["bullish"]["days"]), r["symbol"]))
    return out


def report_path() -> Path:
    explicit = os.environ.get("MORNING_STUDY_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("morning_study.json")
    return Path("morning_study.json")


def _delivery_days(days: list[dt.date]) -> tuple[dict[dt.date, dict], str | None]:
    """NSE's delivery file for each day (read-only); an error leaves the rest of the test alone."""
    try:
        from app.services import delivery
    except Exception as exc:  # noqa: BLE001 - the terminal without the desk package
        return {}, f"delivery % not available here ({exc})"
    out: dict[dt.date, dict] = {}
    missed = 0
    for day in days:
        try:
            rows = delivery.load_day(day)
        except Exception:  # noqa: BLE001
            rows = None
        if rows:
            out[day] = rows
        else:
            missed += 1
    return out, (f"delivery % missing for {missed} day(s)" if missed else None)


class MorningStudy:
    """One test at a time: download each stock, read its days, summarize. Memory plus the last report on disk."""

    def __init__(self) -> None:
        self.status = "IDLE"
        self.done = 0
        self.total = 0
        self.errors: list[dict] = []
        self.params: dict = {}
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.report: dict | None = None
        self._task: asyncio.Task | None = None
        self._stop = False
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(report_path().read_text())
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and raw.get("report"):
            self.report = raw.get("report")
            self.params = raw.get("params") or {}
            self.finished_at = raw.get("finished_at")
            self.status = "DONE"

    def _save(self) -> None:
        try:
            report_path().write_text(json.dumps({"report": self.report, "params": self.params, "finished_at": self.finished_at}))
        except OSError as exc:
            log.warning("could not save the morning test: %s", exc)

    @property
    def running(self) -> bool:
        return self.status == "RUNNING"

    def view(self) -> dict:
        return {
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "errors": self.errors[-20:],
            "params": self.params,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "report": self.report,
            "cut_times": list(CUT_TIMES),
            "sizes": list(SIZES),
        }

    def start(
        self, symbols: list[str], fetch, days: int, at: str, minutes: int, sma_fast: int, sma_slow: int,
        min_move: float = DEFAULT_MIN_MOVE, with_delivery: bool = True, wait=None,
    ) -> dict:
        if self.running:
            raise RuntimeError("A morning test is already running.")
        if at not in CUT_TIMES:
            raise ValueError(f"The morning must end at one of {', '.join(CUT_TIMES)}.")
        if minutes not in SIZES:
            raise ValueError(f"Candle size must be one of {', '.join(map(str, SIZES))} minutes.")
        names = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))[:MAX_STOCKS]
        if not names:
            raise ValueError("Pick at least one stock.")
        days = max(MIN_DAYS, min(MAX_DAYS, int(days)))
        self.status = "RUNNING"
        self.done, self.total = 0, len(names)
        self.errors = []
        self._stop = False
        self.params = {
            "symbols": len(names), "days": days, "at": at, "minutes": minutes, "sma_fast": sma_fast, "sma_slow": sma_slow,
            "min_move": min_move, "delivery": with_delivery,
        }
        self.started_at = dt.datetime.now(IST).isoformat(timespec="seconds")
        self.finished_at = None
        self._task = asyncio.create_task(self._run(names, fetch, wait))
        return self.view()

    def stop(self) -> dict:
        self._stop = True
        return self.view()

    async def _run(self, names, fetch, wait) -> None:
        p = self.params
        day_list = trading_days(dt.datetime.now(IST).date(), p["days"])
        start = dt.datetime.combine(day_list[0] - dt.timedelta(days=5), SESSION_OPEN, tzinfo=IST)
        # One more session after the last day, for the next day's open / close (up to now).
        end = min(dt.datetime.now(IST), dt.datetime.combine(day_list[-1] + dt.timedelta(days=4), dt.time(15, 30), tzinfo=IST))
        samples: list[dict] = []
        try:
            deliv: dict[dt.date, dict] = {}
            if p.get("delivery"):
                deliv, note = await asyncio.to_thread(_delivery_days, day_list)
                if note:
                    self.errors.append({"symbol": "", "error": note})
            for name in names:
                if self._stop:
                    break
                try:
                    if wait is not None:
                        await wait()
                    tape = await fetch(name, start, end)
                    got = await asyncio.to_thread(
                        analyze_stock, name, tape, day_list, p["at"], p["minutes"], p["sma_fast"], p["sma_slow"], p["min_move"], deliv
                    )
                    samples.extend(got)
                except Exception as exc:  # noqa: BLE001 - one stock's failure never stops the test
                    self.errors.append({"symbol": name, "error": str(exc)[:200]})
                self.done += 1
                await asyncio.sleep(0)
            self.report = await asyncio.to_thread(summarize, samples, p["at"])
            self.report["stopped"] = self._stop
            self.status = "DONE"
        except Exception as exc:  # noqa: BLE001
            log.exception("morning test failed")
            self.errors.append({"symbol": "", "error": str(exc)[:200]})
            self.status = "FAILED"
        finally:
            self.finished_at = dt.datetime.now(IST).isoformat(timespec="seconds")
            if self.status == "DONE":
                self._save()


_STUDY: MorningStudy | None = None


def study() -> MorningStudy:
    global _STUDY
    if _STUDY is None:
        _STUDY = MorningStudy()
    return _STUDY
