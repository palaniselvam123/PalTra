"""Quarterly results: the calendar, each company's filed numbers, and whether they are good or bad (read-only, no order).

The owner saw MyAlgoMate show Q2 results before the news channels: companies file their results with NSE first, and
NSE publishes the numbers from the company's own results data file. PalTra reads the same NSE pages:

* **Calendar** (`calendar`): NSE's board-meeting calendar (`api/corporate-board-meetings`, falling back to
  `api/event-calendar`), the meetings whose purpose is financial results, per day, with an F&O flag.
* **Filed** (`filed`): NSE's latest financial-results filings (`api/corporates-financial-results`), each with the time
  it was filed.
* **Numbers** (`stock`): NSE's results comparison for one company (`api/results-comparision`, NSE's spelling): revenue
  from operations, total income, profit before tax, net profit and basic EPS for its last quarters.
* **Verdict** (`analyze`): the newest quarter against the same quarter a year before (YoY) and the quarter before (QoQ):
  growth of sales, profit before tax and net profit, the change in net margin, a loss turned into a profit (or back),
  scored -1..1 (`score`), POSITIVE / NEGATIVE / MIXED with an impact HIGH / MEDIUM / LOW and one plain line why.

NSE's field names are matched loosely (`_pick`) because NSE renames them now and then; `probe` shows exactly what NSE
answered (the keys and one row of each source), so a renamed field shows up at once. NSE's results comparison gives
money in ₹ lakh (`LAKH_PER_CRORE`): the figures are shown in ₹ crore. The verdict only uses ratios, so it is right
whatever the unit. Information only: nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import re
import time
from urllib.parse import quote_plus

import httpx

from app.services.stock_news import NSE_HOME, TIMEOUT, UA

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NSE = "https://www.nseindia.com"
RESULTS_PAGE = f"{NSE}/companies-listing/corporate-filings-financial-results"
BOARD_PAGE = f"{NSE}/companies-listing/corporate-filings-board-meetings"
COMPARE_API = f"{NSE}/api/results-comparision?symbol={{symbol}}"
FILED_API = f"{NSE}/api/corporates-financial-results?index=equities&period=Quarterly"
BOARD_API = f"{NSE}/api/corporate-board-meetings?index=equities&from_date={{start}}&to_date={{end}}"
EVENT_API = f"{NSE}/api/event-calendar?index=equities&from_date={{start}}&to_date={{end}}"

LAKH_PER_CRORE = 100.0
STOCK_CACHE_SEC = 30 * 60
LIST_CACHE_SEC = 10 * 60
MAX_ANALYZE = 40
GAP_SEC = 0.6                     # between two NSE requests in a batch
FULL_GROWTH = 25.0                # % growth that scores a full point
FULL_MARGIN = 3.0                 # percentage points of net margin that score a full point
WEIGHTS = {"profit_yoy": 0.35, "revenue_yoy": 0.25, "pbt_yoy": 0.15, "profit_qoq": 0.15, "margin_yoy": 0.10}
_RESULTS_PURPOSE = re.compile(r"financial\s+results?|\bresults?\b", re.I)

_cache: dict[str, tuple[float, object]] = {}


# ---- loose field matching -----------------------------------------------------------------------------------------

def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _pick(row: dict, *wants: str, avoid: tuple[str, ...] = ()) -> object:
    """The value of the first key containing one of `wants` (in that order) and none of `avoid`, keys compared without
    case or punctuation (`re_net_sale`, `reNetSale`, `NetSale` all match "netsale")."""
    keys = {_norm(k): k for k in row}
    for want in wants:
        w = _norm(want)
        for nk, k in keys.items():
            if w in nk and not any(_norm(a) in nk for a in avoid):
                return row[k]
    return None


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "NA", "N/A", "null", "None"):
        return None
    neg = text.startswith("(") and text.endswith(")")
    try:
        out = float(text.strip("()"))
    except ValueError:
        return None
    return -out if neg else out


_DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%b-%y")


def _date(value: object) -> dt.date | None:
    if not value:
        return None
    text = str(value).strip()[:11].strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text.title() if "%b" in fmt or "%B" in fmt else text, fmt).date()
        except ValueError:
            continue
    return None


def _when(value: object) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
        try:
            return dt.datetime.strptime(text.title() if "%b" in fmt else text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    day = _date(text)
    return dt.datetime.combine(day, dt.time(0, 0), tzinfo=IST) if day else None


def _rows(payload: object) -> list[dict]:
    """The list of records in an NSE answer: the answer itself, or the first list of dicts inside it."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("resCmpData", "data", "rows"):
            got = payload.get(key)
            if isinstance(got, list):
                return [r for r in got if isinstance(r, dict)]
        for got in payload.values():
            if isinstance(got, list) and got and isinstance(got[0], dict):
                return [r for r in got if isinstance(r, dict)]
    return []


# ---- the numbers of one company --------------------------------------------------------------------------------------

def parse_quarter(row: dict) -> dict | None:
    """One quarter from NSE's results comparison: dates, money in ₹ crore (NSE gives ₹ lakh), EPS in ₹."""
    end = _date(_pick(row, "to_dt", "todate", "period_end", "periodend", "toDt"))
    if end is None:
        return None
    start = _date(_pick(row, "from_dt", "fromdate", "period_start", "fromDt"))

    def money(*wants: str, avoid: tuple[str, ...] = ()) -> float | None:
        raw = _num(_pick(row, *wants, avoid=avoid))
        return round(raw / LAKH_PER_CRORE, 2) if raw is not None else None

    revenue = money("net_sale", "revenue_from_operations", "revenuefromoperations", "income_from_operations",
                    "inc_from_op", "netsales", "sales", avoid=("other", "tot", "total"))
    total = money("tot_inc", "total_inc", "totalincome", "tot_income")
    pbt = money("bef_tax", "before_tax", "beforetax", "pbt", avoid=("except", "extra"))
    net = money("net_profit", "netprofit", "pro_loss_aft_tax", "aft_tax", "after_tax", "profit_loss_for_period",
                "profitlossforperiod", "pat", avoid=("bef", "minority", "share_of", "other_comp", "oci"))
    eps = _num(_pick(row, "basic_eps_for_cont", "basic_eps", "basiceps", "eps_basic", "eps", avoid=("dil", "discont")))
    kind = str(_pick(row, "res_type", "audited", "restype") or "").strip()
    audited = None
    if kind:
        audited = kind.upper().startswith("A") and not kind.upper().startswith("UN")
    return {
        "start": start.isoformat() if start else None, "end": end.isoformat(), "label": quarter_label(end),
        "revenue": revenue, "total_income": total, "pbt": pbt, "net_profit": net, "eps": eps,
        "audited": audited, "consolidated": _consolidated(row),
    }


def _consolidated(row: dict) -> bool | None:
    raw = _pick(row, "consolidated", "re_con", "cons")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in ("c", "consolidated", "y", "yes", "true", "1"):
        return True
    if text in ("s", "standalone", "non-consolidated", "n", "no", "false", "0"):
        return False
    return None


def quarter_label(end: dt.date) -> str:
    """Indian financial year: the quarter ending Jun is Q1 of the year ending the next March (Q1 FY27 = Apr-Jun 2026)."""
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(end.month)
    fy = end.year + 1 if end.month >= 4 else end.year
    return f"Q{q} FY{fy % 100:02d}" if q else end.strftime("%b %Y")


def parse_comparison(payload: object) -> list[dict]:
    """Every quarter NSE gave, newest first, one per period end (the first one met wins)."""
    seen: dict[str, dict] = {}
    for row in _rows(payload):
        q = parse_quarter(row)
        if q and q["end"] not in seen:
            seen[q["end"]] = q
    return sorted(seen.values(), key=lambda q: q["end"], reverse=True)


# ---- the verdict --------------------------------------------------------------------------------------------------

def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    if old < 0:
        return None  # a % change from a loss means nothing; the turnaround is judged on its own
    return round((new - old) / abs(old) * 100, 1)


def _find(quarters: list[dict], end: dt.date, days_back: int, tolerance: int = 20) -> dict | None:
    target = end - dt.timedelta(days=days_back)
    best, gap = None, None
    for q in quarters:
        d = abs((dt.date.fromisoformat(q["end"]) - target).days)
        if d <= tolerance and (gap is None or d < gap):
            best, gap = q, d
    return best


def _margin(q: dict | None) -> float | None:
    if not q or q.get("net_profit") is None or not q.get("revenue"):
        return None
    return round(q["net_profit"] / q["revenue"] * 100, 2)


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _profit_points(new: float | None, old: float | None) -> tuple[float | None, str | None]:
    """Points for net profit against an earlier quarter; a loss turned into a profit (or back) is a full point."""
    if new is None or old is None:
        return None, None
    if old <= 0 < new:
        return 1.0, "turned a loss into a profit"
    if old > 0 >= new:
        return -1.0, "swung from a profit to a loss"
    if old < 0 and new < 0:
        return (0.5, "loss narrowed") if new > old else (-0.5, "loss widened")
    g = _pct(new, old)
    return (_clip(g / FULL_GROWTH), None) if g is not None else (None, None)


def analyze(quarters: list[dict]) -> dict:
    """The newest quarter against the same quarter last year and the one before: growth, score, verdict, why."""
    if not quarters:
        return {"verdict": "NO_DATA", "sentiment": "NO_DATA", "impact": None, "score": None, "why": "No quarters from NSE."}
    q0 = quarters[0]
    end = dt.date.fromisoformat(q0["end"])
    yoy = _find(quarters[1:], end, 365)
    qoq = _find(quarters[1:], end, 91)
    growth = {
        "revenue_yoy": _pct(q0["revenue"], yoy and yoy["revenue"]),
        "revenue_qoq": _pct(q0["revenue"], qoq and qoq["revenue"]),
        "pbt_yoy": _pct(q0["pbt"], yoy and yoy["pbt"]),
        "profit_yoy": _pct(q0["net_profit"], yoy and yoy["net_profit"]),
        "profit_qoq": _pct(q0["net_profit"], qoq and qoq["net_profit"]),
        "eps_yoy": _pct(q0["eps"], yoy and yoy["eps"]),
        "margin": _margin(q0), "margin_yoy_pts": None, "margin_qoq_pts": None,
    }
    m0, my, mq = _margin(q0), _margin(yoy), _margin(qoq)
    if m0 is not None and my is not None:
        growth["margin_yoy_pts"] = round(m0 - my, 2)
    if m0 is not None and mq is not None:
        growth["margin_qoq_pts"] = round(m0 - mq, 2)

    points: dict[str, float] = {}
    notes: list[str] = []
    p, note = _profit_points(q0["net_profit"], yoy and yoy["net_profit"])
    if p is not None:
        points["profit_yoy"] = p
    if note:
        notes.append(f"net profit {note} vs last year")
    if growth["revenue_yoy"] is not None:
        points["revenue_yoy"] = _clip(growth["revenue_yoy"] / FULL_GROWTH)
    pb, _ = _profit_points(q0["pbt"], yoy and yoy["pbt"])
    if pb is not None:
        points["pbt_yoy"] = pb
    pq, note_q = _profit_points(q0["net_profit"], qoq and qoq["net_profit"])
    if pq is not None:
        points["profit_qoq"] = pq
    if note_q:
        notes.append(f"net profit {note_q} vs last quarter")
    if growth["margin_yoy_pts"] is not None:
        points["margin_yoy"] = _clip(growth["margin_yoy_pts"] / FULL_MARGIN)
    if not points:
        return {"verdict": "NO_DATA", "sentiment": "NO_DATA", "impact": None, "score": None, "quarter": q0["label"],
                "growth": growth, "why": "Not enough earlier quarters from NSE to compare."}
    weight = sum(WEIGHTS[k] for k in points)
    score = round(sum(WEIGHTS[k] * v for k, v in points.items()) / weight, 3)
    rev, prof = points.get("revenue_yoy"), points.get("profit_yoy")
    split = rev is not None and prof is not None and rev * prof < 0 and min(abs(rev), abs(prof)) >= 0.2
    if split and abs(score) < 0.35:
        sentiment = "MIXED"
    elif score >= 0.15:
        sentiment = "POSITIVE"
    elif score <= -0.15:
        sentiment = "NEGATIVE"
    else:
        sentiment = "MIXED" if split else "NEUTRAL"
    size = abs(score)
    impact = "HIGH" if size >= 0.5 else "MEDIUM" if size >= 0.25 else "LOW"
    return {
        "verdict": sentiment if sentiment in ("MIXED", "NEUTRAL") else f"{sentiment}_{impact}",
        "sentiment": sentiment, "impact": impact, "score": score, "quarter": q0["label"],
        "compared_yoy": yoy["label"] if yoy else None, "compared_qoq": qoq["label"] if qoq else None,
        "growth": growth, "points": {k: round(v, 2) for k, v in points.items()},
        "why": why_line(growth, notes, yoy is not None, qoq is not None),
    }


def _signed(x: float | None, unit: str = "%") -> str | None:
    return None if x is None else f"{x:+.1f}{unit}"


def why_line(g: dict, notes: list[str], has_yoy: bool, has_qoq: bool) -> str:
    parts = []
    if has_yoy:
        bits = [f"sales {_signed(g['revenue_yoy'])}" if g["revenue_yoy"] is not None else None,
                f"net profit {_signed(g['profit_yoy'])}" if g["profit_yoy"] is not None else None,
                f"margin {_signed(g['margin_yoy_pts'], ' pts')}" if g["margin_yoy_pts"] is not None else None]
        bits = [b for b in bits if b]
        if bits:
            parts.append("vs last year: " + ", ".join(bits))
    if has_qoq:
        bits = [f"sales {_signed(g['revenue_qoq'])}" if g["revenue_qoq"] is not None else None,
                f"net profit {_signed(g['profit_qoq'])}" if g["profit_qoq"] is not None else None]
        bits = [b for b in bits if b]
        if bits:
            parts.append("vs last quarter: " + ", ".join(bits))
    parts += notes
    return "; ".join(parts) + "." if parts else "No comparison possible."


# ---- calendar and filings ------------------------------------------------------------------------------------------

def parse_calendar(payload: object, fno: set[str]) -> list[dict]:
    """Results meetings: symbol, company, date, purpose, F&O; other meetings (dividends, fund raising) are left out."""
    out: dict[tuple[str, str], dict] = {}
    for row in _rows(payload):
        symbol = str(_pick(row, "bm_symbol", "symbol") or "").strip().upper()
        day = _date(_pick(row, "bm_date", "meetingdate", "date"))
        purpose = " ".join(str(_pick(row, k) or "") for k in ("bm_purpose", "purpose")).strip()
        desc = str(_pick(row, "bm_desc", "desc", "description") or "").strip()
        if not symbol or day is None or not _RESULTS_PURPOSE.search(f"{purpose} {desc}"):
            continue
        key = (symbol, day.isoformat())
        out.setdefault(key, {
            "symbol": symbol, "company": str(_pick(row, "sm_name", "company", "companyname", "name") or "").strip(),
            "date": day.isoformat(), "purpose": purpose or desc[:120], "fno": symbol in fno,
        })
    return sorted(out.values(), key=lambda r: (r["date"], not r["fno"], r["symbol"]))


def parse_filed(payload: object, fno: set[str]) -> list[dict]:
    """NSE's latest results filings: symbol, company, period end, filed at, consolidated, audited, link."""
    out = []
    for row in _rows(payload):
        symbol = str(_pick(row, "symbol") or "").strip().upper()
        if not symbol:
            continue
        filed = _when(_pick(row, "filingdate", "broadcastdate", "filing_date", "exchdisstime", "date"))
        end = _date(_pick(row, "todate", "to_date", "period_end", "periodend"))
        out.append({
            "symbol": symbol, "company": str(_pick(row, "companyname", "company", "sm_name") or "").strip(),
            "period_end": end.isoformat() if end else None, "quarter": quarter_label(end) if end else None,
            "filed_at": filed.isoformat(timespec="minutes") if filed else None,
            "consolidated": _consolidated(row), "audited": str(_pick(row, "audited") or "").strip() or None,
            "xbrl": str(_pick(row, "xbrl") or "").strip() or None, "fno": symbol in fno,
        })
    out.sort(key=lambda r: r["filed_at"] or "", reverse=True)
    return out


# ---- NSE access ---------------------------------------------------------------------------------------------------

def _headers(referer: str) -> dict:
    return {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer}


async def _client() -> httpx.AsyncClient:
    """An NSE session. NSE answers its API only with the cookies its home page sets; when the home page fails the API
    call that follows fails too and reports why, so the error is not raised here."""
    client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
    try:
        await client.get(NSE_HOME, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
    except Exception:  # noqa: BLE001 - the API call reports it
        pass
    return client


async def _get_json(client: httpx.AsyncClient, url: str, referer: str) -> object:
    res = await client.get(url, headers=_headers(referer))
    res.raise_for_status()
    return res.json()


def _cached(key: str, ttl: float) -> object | None:
    hit = _cache.get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < ttl else None


def _keep(key: str, value: object) -> object:
    if len(_cache) > 400:
        _cache.clear()
    _cache[key] = (time.monotonic(), value)
    return value


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"NSE answered {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "NSE timed out"
    return str(exc)[:160] or type(exc).__name__


def _fno() -> set[str]:
    try:
        from app.services.instruments import instrument_master

        return set(instrument_master.fno_stocks())
    except Exception:  # noqa: BLE001 - no list: nothing is flagged F&O
        return set()


def _name(symbol: str) -> str | None:
    try:
        from app.services.instruments import instrument_master

        inst = instrument_master.get(symbol)
        return inst.name if inst else None
    except Exception:  # noqa: BLE001
        return None


async def stock(symbol: str, *, force: bool = False, client: httpx.AsyncClient | None = None) -> dict:
    """One company's quarters from NSE and the verdict on the newest one (cached STOCK_CACHE_SEC)."""
    symbol = symbol.upper()
    key = f"stock:{symbol}"
    if not force and (hit := _cached(key, STOCK_CACHE_SEC)) is not None:
        return hit  # type: ignore[return-value]
    own = client is None
    try:
        client = client or await _client()
        payload = await _get_json(client, COMPARE_API.format(symbol=quote_plus(symbol)), RESULTS_PAGE)
        quarters = parse_comparison(payload)
        out = {"symbol": symbol, "company": _name(symbol), "quarters": quarters, "analysis": analyze(quarters),
               "unit": "₹ crore", "read_at": dt.datetime.now(IST).isoformat(timespec="seconds"), "error": None}
        if not quarters:
            out["error"] = "NSE gave no quarters for this stock (or renamed its fields: see Check NSE)."
        return _keep(key, out)  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001 - shown on the page
        return {"symbol": symbol, "company": _name(symbol), "quarters": [], "analysis": analyze([]), "unit": "₹ crore",
                "read_at": dt.datetime.now(IST).isoformat(timespec="seconds"), "error": _why(exc)}
    finally:
        if own and client is not None:
            await client.aclose()


async def many(symbols: list[str], *, force: bool = False) -> list[dict]:
    """Several companies one after another on one NSE session, `GAP_SEC` apart (NSE throttles fast callers)."""
    out = []
    client = await _client()
    try:
        for i, s in enumerate(dict.fromkeys(x.upper() for x in symbols)):
            if i >= MAX_ANALYZE:
                break
            fresh = force or _cached(f"stock:{s}", STOCK_CACHE_SEC) is None
            out.append(await stock(s, force=force, client=client))
            if fresh:
                await asyncio.sleep(GAP_SEC)
    finally:
        await client.aclose()
    return out


async def calendar(days: int = 7, *, today: dt.date | None = None) -> dict:
    """Results meetings from today for `days` days, per day (cached LIST_CACHE_SEC)."""
    today = today or dt.datetime.now(IST).date()
    days = max(1, min(30, int(days)))
    key = f"cal:{today}:{days}"
    if (hit := _cached(key, LIST_CACHE_SEC)) is not None:
        return hit  # type: ignore[return-value]
    end = today + dt.timedelta(days=days - 1)
    fno = await asyncio.to_thread(_fno)
    rows, error, source = [], None, None
    client = await _client()
    try:
        for url, referer, name in ((BOARD_API, BOARD_PAGE, "board meetings"), (EVENT_API, BOARD_PAGE, "event calendar")):
            try:
                payload = await _get_json(client, url.format(start=f"{today:%d-%m-%Y}", end=f"{end:%d-%m-%Y}"), referer)
            except Exception as exc:  # noqa: BLE001 - try the other source
                error = _why(exc)
                continue
            rows = [r for r in parse_calendar(payload, fno) if today.isoformat() <= r["date"] <= end.isoformat()]
            source, error = name, None
            if rows:
                break
    finally:
        await client.aclose()
    by_day = []
    for i in range(days):
        d = (today + dt.timedelta(days=i)).isoformat()
        mine = [r for r in rows if r["date"] == d]
        by_day.append({"date": d, "count": len(mine), "fno": sum(r["fno"] for r in mine), "rows": mine})
    out = {"from": today.isoformat(), "to": end.isoformat(), "days": by_day, "total": len(rows),
           "fno_total": sum(r["fno"] for r in rows), "source": source, "error": error}
    return _keep(key, out) if not error else out  # type: ignore[return-value]


async def filed(limit: int = 200) -> dict:
    """NSE's latest results filings, newest first (cached LIST_CACHE_SEC)."""
    if (hit := _cached("filed", LIST_CACHE_SEC)) is not None:
        rows = hit  # type: ignore[assignment]
    else:
        fno = await asyncio.to_thread(_fno)
        client = await _client()
        try:
            rows = parse_filed(await _get_json(client, FILED_API, RESULTS_PAGE), fno)
        except Exception as exc:  # noqa: BLE001
            return {"rows": [], "error": _why(exc)}
        finally:
            await client.aclose()
        _keep("filed", rows)
    return {"rows": rows[: max(1, min(1000, int(limit)))], "error": None}


async def probe(symbol: str = "ALKEM") -> dict:
    """What NSE answered for each source: status, how many rows, the keys and the first row (to spot renamed fields)."""
    today = dt.datetime.now(IST).date()
    end = today + dt.timedelta(days=6)
    urls = {
        "results_comparison": (COMPARE_API.format(symbol=quote_plus(symbol.upper())), RESULTS_PAGE),
        "filed": (FILED_API, RESULTS_PAGE),
        "board_meetings": (BOARD_API.format(start=f"{today:%d-%m-%Y}", end=f"{end:%d-%m-%Y}"), BOARD_PAGE),
        "event_calendar": (EVENT_API.format(start=f"{today:%d-%m-%Y}", end=f"{end:%d-%m-%Y}"), BOARD_PAGE),
    }
    out: dict[str, dict] = {}
    client = await _client()
    try:
        for name, (url, referer) in urls.items():
            try:
                res = await client.get(url, headers=_headers(referer))
                info: dict = {"status": res.status_code, "url": url}
                try:
                    payload = res.json()
                except ValueError:
                    info["body"] = res.text[:300]
                    out[name] = info
                    continue
                rows = _rows(payload)
                info.update(top_keys=sorted(payload.keys()) if isinstance(payload, dict) else None, rows=len(rows),
                            keys=sorted(rows[0].keys()) if rows else [],
                            first={k: (str(v)[:120] if v is not None else None) for k, v in (rows[0].items() if rows else [])})
                if name == "results_comparison":
                    info["parsed"] = parse_comparison(payload)[:2]
                out[name] = info
            except Exception as exc:  # noqa: BLE001
                out[name] = {"error": _why(exc), "url": url}
            await asyncio.sleep(GAP_SEC)
    finally:
        await client.aclose()
    return out
