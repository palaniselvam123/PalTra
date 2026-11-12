"""After-close orders scan (read-only; the Scalp page's "After the close" tab, `components/Scalp/CloseOrders.tsx`).

The owner's ALICON day: after the market shut, the buy orders waiting in Groww's book were far more than the sell
orders. That can mean real demand (people want the stock and could not get it), or the price jump at the end was short
sellers buying back what they sold earlier. This scan reads, per stock:

* **Orders waiting** (Groww's quote): total buy quantity and total sell quantity in the book when scanned, and how many
  orders sit at the best five prices when Groww sends the count (`orders` on a depth level).
* **The last hour** (Groww's 1-minute candles of the day): the move before 14:30, the move from 14:30 to the close,
  and the end-of-day volume rush (the volume per minute after 14:45 against the day's average per minute).
* **Delivery %** (NSE's end-of-day file, about 18:00): the share of the day's volume taken home. Short sellers buying
  back are intraday trades, so a low delivery % points to them; a high one to real buyers.
* **F&O open interest** (NSE's OI spurts, live, F&O stocks only): price up with OI down = short sellers closing;
  price up with OI up = new buyers.
* **Short selling** (NSE's daily short-selling file, published the next day): shares sold short that day.

`read` adds up plain points for "real demand" and for "short sellers buying back" and names the verdict
(`DEMAND` / `SHORT_COVER` / `MIXED` / `QUIET`), each point with its reason in plain words. An estimate: nobody publishes
who bought. No order path; nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import httpx

from app.core.market_clock import IST, ist_now

log = logging.getLogger("close_orders")

OPEN = dt.time(9, 15)
CLOSE = dt.time(15, 30)
BEFORE_END = dt.time(14, 30)     # "the day so far" ends here; the last hour starts here
RUSH_FROM = dt.time(14, 45)      # the end-of-day volume rush is read from here to the close
SESSION_MIN = 375
CALL_GAP_SEC = 0.25
MAX_STOCKS = 250

# Points. A side needs this many to be named, and this many more than the other side.
NAME_AT = 3
LEAD_BY = 2
BUY_RATIO_STRONG = 3.0     # buy orders waiting at least 3× the sell orders
BUY_RATIO_SOME = 2.0
BUY_VS_VOLUME = 5.0        # buy orders waiting at least 5 % of the day's volume
LATE_RISE = 0.7            # % rise from 14:30 to the close
WEAK_BEFORE = -0.3         # % move from the previous close to 14:30 at or under this = it was falling
RUSH = 2.0                 # volume per minute after 14:45 at least 2× the day's average per minute
DELIVERY_HIGH = 1.2        # delivery % at least 1.2× its usual
DELIVERY_LOW = 0.8
SHORT_SHARE = 2.0          # shares sold short at least 2 % of the day's volume
OI_MOVE = 2.0              # open interest change of at least 2 %

NSE_HOME = "https://www.nseindia.com/"
NSE_OI = "https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings"
NSE_OI_PAGE = "https://www.nseindia.com/market-data/oi-spurts"
NSE_SHORTS = "https://nsearchives.nseindia.com/archives/equities/shortSelling/shortselling_{d:%d%m%Y}.csv"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_short_cache: dict[dt.date, tuple[float, dict | None]] = {}


def _num(v) -> float | None:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _pick(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def book(quote: dict) -> dict:
    """The orders waiting in Groww's quote: total buy / sell quantity, and the order count at the best five prices
    when Groww sends one (`orders` / `num_orders` on a depth level; None otherwise)."""
    buy = _num(_pick(quote, "total_buy_quantity", "total_bid_quantity"))
    sell = _num(_pick(quote, "total_sell_quantity", "total_ask_quantity"))
    depth = _pick(quote, "depth", "market_depth")
    counts: dict[str, int | None] = {"buy": None, "sell": None}
    if isinstance(depth, dict):
        for side in ("buy", "sell"):
            levels = depth.get(side) or []
            got = [_num(_pick(lv, "orders", "num_orders", "order_count", "number_of_orders")) for lv in levels if isinstance(lv, dict)]
            got = [g for g in got if g is not None]
            counts[side] = int(sum(got)) if got else None
    return {"buy_qty": buy, "sell_qty": sell, "buy_orders_top5": counts["buy"], "sell_orders_top5": counts["sell"]}


def minute_volumes(rows: list) -> list[float]:
    """Per-minute volume from 1-minute candles. Groww's candle volume is the session's running total (it never
    falls); a series that falls somewhere is already per minute."""
    vols = [_num(r[5]) or 0.0 for r in rows]
    if len(vols) > 1 and all(b >= a for a, b in zip(vols, vols[1:])):
        return [vols[0]] + [b - a for a, b in zip(vols, vols[1:])]
    return vols


def tape(rows: list, day: dt.date, prev_close: float | None) -> dict:
    """The day's shape from its 1-minute candles [(ts, o, h, l, c, v), ...]: move to 14:30, move after it,
    end-of-day volume rush, where it closed in the day's range."""
    rows = sorted((r for r in rows if r and _num(r[4])), key=lambda r: r[0])
    if not rows:
        return {}
    at = lambda t: int(dt.datetime.combine(day, t, tzinfo=IST).timestamp())  # noqa: E731
    first, last = rows[0], rows[-1]
    base = prev_close or _num(first[1])
    upto = [r for r in rows if r[0] < at(BEFORE_END)]
    mid = _num(upto[-1][4]) if upto else None
    close = _num(last[4])
    hi = max(_num(r[2]) or 0 for r in rows)
    lo = min(_num(r[3]) or 1e18 for r in rows)
    vols = minute_volumes(rows)
    late = sum(v for r, v in zip(rows, vols) if r[0] >= at(RUSH_FROM))
    late_min = sum(1 for r in rows if r[0] >= at(RUSH_FROM))
    total = sum(vols)
    out = {
        "day_volume": total,
        "move_to_1430_pct": round((mid / base - 1) * 100, 2) if mid and base else None,
        "last_hour_pct": round((close / mid - 1) * 100, 2) if mid and close else None,
        "day_move_pct": round((close / base - 1) * 100, 2) if close and base else None,
        "close_in_range": round((close - lo) / (hi - lo), 2) if hi > lo and close else None,
        "end_rush": round((late / late_min) / (total / len(rows)), 2) if late_min and total else None,
        "end_volume_share_pct": round(late / total * 100, 1) if total else None,
    }
    return out


def read(r: dict) -> dict:
    """Points for real demand and for short sellers buying back, each with a reason in plain words, and the verdict."""
    demand: list[tuple[int, str]] = []
    cover: list[tuple[int, str]] = []
    buy, sell, vol = r.get("buy_qty"), r.get("sell_qty"), r.get("day_volume") or r.get("volume")
    ratio = (buy / sell) if buy and sell else (None if not buy else float("inf"))
    r["buy_sell_ratio"] = None if ratio is None else (999.0 if ratio == float("inf") else round(ratio, 2))
    r["buy_vs_volume_pct"] = round(buy / vol * 100, 2) if buy and vol else None
    if ratio is not None:
        if ratio >= BUY_RATIO_STRONG:
            demand.append((2, f"Buy orders waiting are {_x(ratio)} the sell orders: people still want it and could not get it."))
        elif ratio >= BUY_RATIO_SOME:
            demand.append((1, f"Buy orders waiting are {_x(ratio)} the sell orders."))
    if r["buy_vs_volume_pct"] is not None and r["buy_vs_volume_pct"] >= BUY_VS_VOLUME:
        demand.append((1, f"Buy orders waiting equal {r['buy_vs_volume_pct']:g}% of the whole day's volume."))
    cir = r.get("close_in_range")
    if cir is not None and cir >= 0.9:
        demand.append((1, "It closed at the top of the day's range."))
    d, usual = r.get("delivery_pct"), r.get("delivery_usual_pct")
    if d is not None and usual:
        if d >= usual * DELIVERY_HIGH:
            demand.append((2, f"Delivery {d:g}% against a usual {usual:g}%: more buyers took the shares home."))
        elif d <= usual * DELIVERY_LOW:
            cover.append((2, f"Delivery {d:g}% against a usual {usual:g}%: most of the volume was intraday, as when short sellers buy back."))
    before, late = r.get("move_to_1430_pct"), r.get("last_hour_pct")
    if late is not None and late >= LATE_RISE and before is not None and before <= WEAK_BEFORE:
        cover.append((2, f"It fell {abs(before):g}% until 14:30 and then jumped {late:g}% in the last hour: the usual shape of short sellers buying back before the 15:20 square-off."))
    if r.get("end_rush") is not None and r["end_rush"] >= RUSH and late is not None and late > 0:
        cover.append((1, f"Volume after 14:45 ran {_x(r['end_rush'])} the day's usual pace while the price rose."))
    oi = r.get("oi_change_pct")
    if oi is not None and r.get("day_move_pct") is not None and r["day_move_pct"] > 0:
        if oi <= -OI_MOVE:
            cover.append((3, f"F&O open interest fell {abs(oi):g}% while the price rose: short positions were being closed."))
        elif oi >= OI_MOVE:
            demand.append((2, f"F&O open interest rose {oi:g}% with the price: new buyers were opening positions."))
    sh = r.get("short_pct")
    if sh is not None and sh >= SHORT_SHARE:
        cover.append((1, f"{sh:g}% of the day's volume was sold short (NSE's short-selling file): those shares must be bought back."))
    if late is not None and late >= LATE_RISE and ratio is not None and ratio < 1.5:
        cover.append((1, "The late jump left few buy orders waiting: the buying was done, not still queued."))
    dp, cp = sum(p for p, _ in demand), sum(p for p, _ in cover)
    if dp >= NAME_AT and dp >= cp + LEAD_BY:
        verdict = "DEMAND"
    elif cp >= NAME_AT and cp >= dp + LEAD_BY:
        verdict = "SHORT_COVER"
    elif dp >= 2 and cp >= 2:
        verdict = "MIXED"
    else:
        verdict = "QUIET"
    r["unusual_buy"] = bool((ratio is not None and ratio >= BUY_RATIO_STRONG) or (r["buy_vs_volume_pct"] or 0) >= BUY_VS_VOLUME)
    missing = []
    if d is None:
        missing.append("delivery % (NSE publishes it about 18:00)")
    if r.get("fno") and oi is None:
        missing.append("F&O open interest")
    r.update(
        demand_points=dp, cover_points=cp, verdict=verdict,
        demand_why=[w for _, w in demand], cover_why=[w for _, w in cover], missing=missing,
    )
    return r


def _x(v: float) -> str:
    return "many times" if v == float("inf") or v >= 100 else f"{v:.1f}×"


# ---- NSE extras -------------------------------------------------------------------------------------------------


async def _nse(client: httpx.AsyncClient, url: str, referer: str, as_json: bool = True):
    res = await client.get(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": referer})
    res.raise_for_status()
    return res.json() if as_json else res.text


async def oi_changes() -> dict[str, float]:
    """symbol -> today's F&O open interest change in %, from NSE's OI spurts (live, one request)."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        await client.get(NSE_HOME, headers={"User-Agent": UA, "Accept": "text/html"})
        data = await _nse(client, NSE_OI, NSE_OI_PAGE)
    out = {}
    for row in (data.get("data") if isinstance(data, dict) else data) or []:
        latest, prev = _num(row.get("latestOI")), _num(row.get("prevOI"))
        if row.get("symbol") and latest is not None and prev:
            out[str(row["symbol"]).upper()] = round((latest / prev - 1) * 100, 2)
    return out


def parse_shorts(text: str) -> tuple[dt.date | None, dict[str, float]]:
    """NSE's short-selling CSV: its trade date and symbol -> shares sold short."""
    import csv
    import io

    day = None
    out: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        sym = (row.get("Symbol Name") or "").strip().upper()
        qty = _num((row.get("Quantity") or "").replace(",", ""))
        if day is None:
            try:
                day = dt.datetime.strptime((row.get("Trade Date") or "").strip(), "%d-%b-%Y").date()
            except ValueError:
                pass
        if sym and qty is not None:
            out[sym] = out.get(sym, 0.0) + qty
    return day, out


async def shorts_for(day: dt.date) -> dict | None:
    """Shares sold short on `day`, from NSE's short-selling file (named for the day it is published, usually the next
    trading day). None until it is out. Cached in memory (an hour for a miss)."""
    hit = _short_cache.get(day)
    if hit and (hit[1] is not None or time.monotonic() - hit[0] < 3600):
        return hit[1]
    found = None
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for ahead in range(1, 5):
            d = day + dt.timedelta(days=ahead)
            if d.weekday() >= 5:
                continue
            try:
                text = await _nse(client, NSE_SHORTS.format(d=d), NSE_HOME, as_json=False)
            except Exception:  # noqa: BLE001 - not published yet
                continue
            trade_day, rows = parse_shorts(text)
            if trade_day == day:
                found = rows
                break
    _short_cache[day] = (time.monotonic(), found)
    return found


# ---- the scan ---------------------------------------------------------------------------------------------------


class CloseOrderScanner:
    """One pass at a time over the picked stocks; the last pass is kept and saved to the scan history."""

    def __init__(self) -> None:
        self.running = False
        self.rows: list[dict] = []
        self.as_of: dt.datetime | None = None
        self.day: dt.date | None = None
        self.done = 0
        self.total = 0
        self.error: str | None = None
        self.notes: list[str] = []
        self._stop = False
        self.task: asyncio.Task | None = None
        self.on_done = None

    def stop(self) -> bool:
        if not self.running:
            return False
        self._stop = True
        return True

    def view(self) -> dict:
        return {
            "running": self.running, "done": self.done, "total": self.total, "error": self.error,
            "as_of": self.as_of.isoformat(timespec="seconds") if self.as_of else None,
            "day": self.day.isoformat() if self.day else None, "notes": self.notes, "rows": self.rows,
        }

    async def scan(self, client, symbols: list[str], fno: set[str] | None = None) -> None:
        import groww_pressure

        from app.services import delivery

        if self.running:
            return
        self.running, self._stop, self.error, self.notes = True, False, None, []
        names = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))[:MAX_STOCKS]
        self.done, self.total = 0, len(names)
        now = ist_now()
        day = now.date()
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
        try:
            extras = await asyncio.gather(
                asyncio.to_thread(delivery.report, names, 5), oi_changes(), shorts_for(day), return_exceptions=True
            )
            deliv, oi, shorts = extras
            if isinstance(deliv, Exception) or not isinstance(deliv, dict) or deliv.get("day") != day.isoformat():
                self.notes.append("Delivery % for this day is not out yet (NSE publishes it about 18:00). Scan again after that for a surer answer.")
                deliv = {}
            if isinstance(oi, Exception):
                self.notes.append("NSE's F&O open interest could not be read now.")
                oi = {}
            if isinstance(shorts, Exception) or shorts is None:
                self.notes.append("NSE's short-selling file for this day is not out yet (it comes the next trading day).")
                shorts = None
            if now.time() < CLOSE and now.date() == day:
                self.notes.append("The market is still open: these are the orders waiting right now, not after the close.")
            rows: list[dict] = []
            start = dt.datetime.combine(day, OPEN, tzinfo=IST)
            end = min(dt.datetime.combine(day, CLOSE, tzinfo=IST), now)
            for sym in names:
                if self._stop:
                    self.error = "Stopped by you. The stocks read so far are shown."
                    break
                await groww_pressure.wait_for_bots()
                try:
                    quote = await client.get_quote_payload(sym)
                    await asyncio.sleep(CALL_GAP_SEC)
                    candles = await client.get_candles_window(sym, 1, start, end)
                    await asyncio.sleep(CALL_GAP_SEC)
                except Exception as exc:  # noqa: BLE001 - one stock's failure is skipped
                    log.info("close orders %s: %s", sym, exc)
                    self.done += 1
                    continue
                rows.append(self.row(sym, quote, candles, day, (deliv.get("rows") or {}).get(sym), oi, shorts, fno))
                self.done += 1
                self.rows = sorted(rows, key=sort_key)
            self.rows = sorted(rows, key=sort_key)
            self.as_of, self.day = ist_now(), day
            if self.on_done is not None and self.rows:
                try:
                    self.on_done(self.view())
                except Exception:  # noqa: BLE001 - history never breaks a scan
                    log.exception("could not keep the after-close scan")
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"[:200]
        finally:
            self.running = False

    @staticmethod
    def row(sym: str, quote: dict, candles: list, day: dt.date, deliv: dict | None, oi: dict, shorts: dict | None,
            fno: set[str] | None) -> dict:
        ohlc = quote.get("ohlc") if isinstance(quote.get("ohlc"), dict) else {}
        prev_close = _num(ohlc.get("close"))
        ltp = _num(_pick(quote, "last_price", "ltp"))
        volume = _num(_pick(quote, "volume", "day_volume", "total_traded_volume"))
        r = {"symbol": sym, "ltp": ltp, "prev_close": prev_close, "volume": volume, **book(quote), **tape(candles, day, prev_close)}
        if volume:
            r["day_volume"] = volume  # Groww's own day total wins over the candles' sum
        if deliv:
            r["delivery_pct"] = _num(_pick(deliv, "pct", "delivery_pct"))
            r["delivery_usual_pct"] = _num(_pick(deliv, "avg_pct", "usual_pct", "avg"))
        r["fno"] = bool(fno and sym in fno)
        r["oi_change_pct"] = oi.get(sym) if oi else None
        if shorts is not None:
            qty = shorts.get(sym, 0.0)
            r["short_qty"] = qty
            r["short_pct"] = round(qty / r["day_volume"] * 100, 2) if r.get("day_volume") else None
        return read(r)


def sort_key(r: dict):
    order = {"DEMAND": 0, "SHORT_COVER": 1, "MIXED": 2, "QUIET": 3}
    return (order.get(r.get("verdict"), 4), not r.get("unusual_buy"), -(r.get("buy_sell_ratio") or 0))


close_scanner = CloseOrderScanner()
