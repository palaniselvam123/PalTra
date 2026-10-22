"""Options page: Groww's option chain, its numbers (PCR, max pain, OI walls) and a practice book.

Data comes from Groww's trading API when the desk has a Groww session:
`get_expiries` (expiry dates) and `get_option_chain` (every strike's call and put:
last price, open interest, volume and Greeks with IV). Groww's option chain needs a
paid API plan; a refusal is shown as is. Without a session the page runs on a
simulated chain (Black-Scholes prices around a slowly moving spot) so it still
works with no credentials, and says so (`source: "simulated"`).

The **practice book** (`OptionPaperTrade`) buys or sells option lots at the chain's
last price, marks them to the same feed and closes them at its last price. It never
calls Groww's order API: there is no live options order path anywhere. Real Groww
prices only fill while NSE is open (a closed market's last price is stale); the
simulated chain fills at any time. An expired position is closed at its intrinsic
value from the last spot seen.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import random
import time
from dataclasses import dataclass

from sqlalchemy import select

from app.core.market_clock import IST, is_market_open
from app.models.database import OptionPaperTrade

CHAIN_TTL_SEC = 5.0
EXPIRY_TTL_SEC = 3600.0
RATE = 0.065  # risk-free rate for the simulated prices and Greeks

# The index underlyings Groww's option chain takes, with their exchange.
INDICES: dict[str, str] = {
    "NIFTY": "NSE",
    "BANKNIFTY": "NSE",
    "FINNIFTY": "NSE",
    "MIDCPNIFTY": "NSE",
    "SENSEX": "BSE",
}
# Simulated feed only: a starting spot, strike step and lot size for each index.
SIM_INDEX = {
    "NIFTY": (25000.0, 50.0, 75),
    "BANKNIFTY": (56000.0, 100.0, 35),
    "FINNIFTY": (26500.0, 50.0, 65),
    "MIDCPNIFTY": (13000.0, 25.0, 140),
    "SENSEX": (82000.0, 100.0, 20),
}
SIM_STRIKES_EACH_SIDE = 30

# Groww intraday options charges (estimates, shown next to the P&L).
BROKERAGE_PER_ORDER = 20.0
STT_SELL = 0.001  # on the sell premium
EXCHANGE_TXN = 0.0003503
SEBI = 10 / 1e7
STAMP_BUY = 0.00003
GST = 0.18


class OptionsError(RuntimeError):
    """A refusal with a reason the page shows as is."""


def exchange_for(underlying: str) -> str:
    return INDICES.get(underlying.upper(), "NSE")


# ---- Groww session ---------------------------------------------------------


def _groww_sdk():
    """The desk's logged-in Groww SDK, or None (no session: the simulated chain is used)."""
    try:
        from app.services.groww_funds import groww_client

        client = groww_client()
    except Exception:  # noqa: BLE001 - no session
        return None
    sdk = getattr(client, "_sdk", None) if client is not None else None
    return sdk if sdk is not None and getattr(client, "_access_token", None) else None


def _why(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    low = text.lower()
    if "forbidden" in low or "403" in low:
        return f"Groww refused the option data ({text[:120]}). The option chain needs Groww's paid API plan."
    return f"Groww: {text[:200]}"


# ---- Black-Scholes (simulated feed and missing Greeks) ---------------------


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(spot: float, strike: float, years: float, iv: float, kind: str, rate: float = RATE) -> dict:
    """Price and Greeks of a European option. theta is per day, vega and rho per 1 %."""
    years = max(years, 1e-6)
    iv = max(iv, 1e-4)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / (iv * math.sqrt(years))
    d2 = d1 - iv * math.sqrt(years)
    disc = math.exp(-rate * years)
    gamma = _npdf(d1) / (spot * iv * math.sqrt(years))
    vega = spot * _npdf(d1) * math.sqrt(years) / 100
    if kind == "CE":
        price = spot * _ncdf(d1) - strike * disc * _ncdf(d2)
        delta = _ncdf(d1)
        theta = (-spot * _npdf(d1) * iv / (2 * math.sqrt(years)) - rate * strike * disc * _ncdf(d2)) / 365
        rho = strike * years * disc * _ncdf(d2) / 100
    else:
        price = strike * disc * _ncdf(-d2) - spot * _ncdf(-d1)
        delta = _ncdf(d1) - 1
        theta = (-spot * _npdf(d1) * iv / (2 * math.sqrt(years)) + rate * strike * disc * _ncdf(-d2)) / 365
        rho = -strike * years * disc * _ncdf(-d2) / 100
    return {"price": max(price, 0.0), "delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


def years_to_expiry(expiry: str, now: dt.datetime | None = None) -> float:
    now = now or dt.datetime.now(IST)
    end = dt.datetime.combine(dt.date.fromisoformat(expiry), dt.time(15, 30), tzinfo=IST)
    return max((end - now).total_seconds(), 60.0) / (365 * 86400)


def intrinsic(spot: float, strike: float, kind: str) -> float:
    return max(spot - strike, 0.0) if kind == "CE" else max(strike - spot, 0.0)


# ---- simulated chain -------------------------------------------------------


def _sim_base(underlying: str) -> tuple[float, float, int]:
    u = underlying.upper()
    if u in SIM_INDEX:
        return SIM_INDEX[u]
    spot = 0.0
    try:
        from app import state

        spot = float((state.latest_quotes.get(u) or {}).get("ltp") or 0)
    except Exception:  # noqa: BLE001
        spot = 0.0
    if spot <= 0:
        spot = 500.0 + random.Random(u).random() * 2500
    for limit, step in ((100, 1.0), (250, 2.5), (500, 5.0), (1000, 10.0), (2500, 20.0), (5000, 50.0)):
        if spot < limit:
            return spot, step, 500
    return spot, 100.0, 250


def sim_spot(underlying: str, now: float | None = None) -> float:
    base = _sim_base(underlying)[0]
    t = time.time() if now is None else now
    return base * (1 + 0.004 * math.sin(2 * math.pi * t / 1800) + 0.0015 * math.sin(2 * math.pi * t / 317))


def sim_expiries(underlying: str, today: dt.date | None = None) -> dict[str, int]:
    """The next four Tuesdays (weekly NSE expiry since Sept 2025; Thursdays for SENSEX)."""
    today = today or dt.datetime.now(IST).date()
    weekday = 3 if underlying.upper() == "SENSEX" else 1
    first = today + dt.timedelta(days=(weekday - today.weekday()) % 7)
    lot = _sim_base(underlying)[2]
    return {(first + dt.timedelta(weeks=i)).isoformat(): lot for i in range(4)}


def sim_chain(underlying: str, expiry: str, now: dt.datetime | None = None) -> dict:
    """A Groww-shaped option chain from Black-Scholes around the simulated spot."""
    u = underlying.upper()
    now = now or dt.datetime.now(IST)
    spot = sim_spot(u, now.timestamp())
    _base, step, _lot = _sim_base(u)
    iv_base = 0.13 if u in INDICES else 0.28
    years = years_to_expiry(expiry, now)
    atm = round(spot / step) * step
    strikes: dict[str, dict] = {}
    for i in range(-SIM_STRIKES_EACH_SIDE, SIM_STRIKES_EACH_SIDE + 1):
        k = atm + i * step
        if k <= 0:
            continue
        row = {}
        for kind in ("CE", "PE"):
            iv = iv_base + 0.6 * abs(math.log(k / spot))
            g = black_scholes(spot, k, years, iv, kind)
            rnd = random.Random(f"{u}|{expiry}|{k}|{kind}")
            otm = (k > spot) if kind == "CE" else (k < spot)
            hump = math.exp(-(((k - spot) / (9 * step)) ** 2))
            oi = int((800_000 if otm else 250_000) * hump * (0.5 + rnd.random()) / 25) * 25
            row[kind] = {
                "trading_symbol": f"{u}{expiry.replace('-', '')[2:]}{k:g}{kind}",
                "ltp": round(g["price"] * 20) / 20,
                "open_interest": oi,
                "volume": int(oi * (1.5 + 3 * rnd.random())),
                "greeks": {
                    "delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"],
                    "vega": g["vega"], "rho": g["rho"], "iv": iv * 100,
                },
            }
        strikes[f"{k:g}"] = row
    return {"underlying_ltp": spot, "strikes": strikes}


# ---- reading Groww's chain -------------------------------------------------


def _num(value, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _leg(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    greeks = raw.get("greeks") if isinstance(raw.get("greeks"), dict) else {}
    iv = _num(greeks.get("iv") if greeks else None, _num(raw.get("iv")))
    # Groww's IV is a percentage; a value under 3 is read as a fraction and turned into one.
    if iv is not None and 0 < iv < 3:
        iv *= 100
    return {
        "symbol": str(raw.get("trading_symbol") or raw.get("tradingSymbol") or ""),
        "ltp": _num(raw.get("ltp")),
        "oi": _num(raw.get("open_interest", raw.get("oi"))),
        "volume": _num(raw.get("volume")),
        "iv": iv,
        "delta": _num(greeks.get("delta")),
        "gamma": _num(greeks.get("gamma")),
        "theta": _num(greeks.get("theta")),
        "vega": _num(greeks.get("vega")),
    }


def normalize_chain(raw: dict) -> tuple[float | None, list[dict]]:
    """Groww's `{underlying_ltp, strikes: {"25000": {"CE": {...}, "PE": {...}}}}` as sorted rows."""
    if not isinstance(raw, dict):
        return None, []
    spot = _num(raw.get("underlying_ltp"))
    strikes = raw.get("strikes") or {}
    rows = []
    items = strikes.items() if isinstance(strikes, dict) else []
    for key, legs in items:
        strike = _num(key)
        if strike is None or not isinstance(legs, dict):
            continue
        ce = _leg(legs.get("CE") or legs.get("ce"))
        pe = _leg(legs.get("PE") or legs.get("pe"))
        if ce is None and pe is None:
            continue
        rows.append({"strike": strike, "ce": ce, "pe": pe})
    rows.sort(key=lambda r: r["strike"])
    return spot, rows


def max_pain(rows: list[dict]) -> float | None:
    """The expiry price at which option buyers would be paid the least in total."""
    best, best_pay = None, None
    for cand in rows:
        k = cand["strike"]
        pay = 0.0
        for r in rows:
            ce_oi = (r["ce"] or {}).get("oi") or 0
            pe_oi = (r["pe"] or {}).get("oi") or 0
            pay += ce_oi * max(k - r["strike"], 0) + pe_oi * max(r["strike"] - k, 0)
        if best_pay is None or pay < best_pay:
            best, best_pay = k, pay
    return best


def summarize(spot: float | None, rows: list[dict]) -> dict:
    ce_oi = sum((r["ce"] or {}).get("oi") or 0 for r in rows)
    pe_oi = sum((r["pe"] or {}).get("oi") or 0 for r in rows)
    atm = min(rows, key=lambda r: abs(r["strike"] - spot))["strike"] if rows and spot else None

    def top(side: str):
        best = max(rows, key=lambda r: (r[side] or {}).get("oi") or 0, default=None)
        return best["strike"] if best and (best[side] or {}).get("oi") else None

    atm_row = next((r for r in rows if r["strike"] == atm), None)
    ivs = [x for x in ((atm_row or {}).get("ce") or {}, (atm_row or {}).get("pe") or {}) if x.get("iv")]
    straddle = None
    if atm_row and (atm_row["ce"] or {}).get("ltp") is not None and (atm_row["pe"] or {}).get("ltp") is not None:
        straddle = atm_row["ce"]["ltp"] + atm_row["pe"]["ltp"]
    return {
        "atm": atm,
        "pcr": round(pe_oi / ce_oi, 3) if ce_oi else None,
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "max_pain": max_pain(rows) if rows else None,
        "resistance": top("ce"),
        "support": top("pe"),
        "atm_iv": round(sum(x["iv"] for x in ivs) / len(ivs), 2) if ivs else None,
        "straddle": round(straddle, 2) if straddle is not None else None,
    }


# ---- caches ----------------------------------------------------------------

_chains: dict[tuple[str, str, str], tuple[float, dict]] = {}
_expiries: dict[tuple[str, str], tuple[float, dict[str, int]]] = {}
_last_spot: dict[tuple[str, str], float] = {}  # (feed, underlying) -> spot
_last_ltp: dict[tuple[str, str], float] = {}  # (feed, trading symbol) -> last price


def reset_for_tests() -> None:
    for cache in (_chains, _expiries, _last_spot, _last_ltp):
        cache.clear()


async def expiries(underlying: str) -> dict:
    """Upcoming expiries with lot sizes: the instrument list, else Groww's API, else simulated."""
    u = underlying.strip().upper()
    sdk = _groww_sdk()
    feed = "groww" if sdk is not None else "simulated"
    hit = _expiries.get((feed, u))
    if hit and time.monotonic() - hit[0] < EXPIRY_TTL_SEC:
        return {"underlying": u, "source": feed, "expiries": _expiry_rows(hit[1]), "error": None}
    today = dt.datetime.now(IST).date().isoformat()
    if sdk is None:
        found = sim_expiries(u)
        _expiries[(feed, u)] = (time.monotonic(), found)
        return {"underlying": u, "source": feed, "expiries": _expiry_rows(found), "error": None}
    found: dict[str, int] = {}
    error = None
    try:
        from app.services.instruments import instrument_master

        found = {d: lot for d, lot in (await asyncio.to_thread(instrument_master.option_expiries, u)).items() if d >= today}
    except Exception as exc:  # noqa: BLE001 - Groww's API below
        error = f"instrument list: {exc}"
    if not found:
        try:
            now = dt.datetime.now(IST)
            years = [now.year] + ([now.year + 1] if now.month >= 11 else [])
            for year in years:
                raw = await asyncio.to_thread(sdk.get_expiries, exchange_for(u), u, year, None, 15)
                for d in (raw or {}).get("expiries", []) if isinstance(raw, dict) else []:
                    d = str(d)[:10]
                    if d >= today:
                        found.setdefault(d, 0)
            error = None
        except Exception as exc:  # noqa: BLE001
            error = _why(exc)
    if found:
        _expiries[(feed, u)] = (time.monotonic(), dict(sorted(found.items())))
    return {"underlying": u, "source": feed, "expiries": _expiry_rows(found), "error": None if found else error}


def _expiry_rows(found: dict[str, int]) -> list[dict]:
    return [{"date": d, "lot_size": lot or None} for d, lot in sorted(found.items())]


async def chain(underlying: str, expiry: str, *, force: bool = False) -> dict:
    """The option chain for one expiry, normalised, with its summary numbers."""
    u = underlying.strip().upper()
    try:
        dt.date.fromisoformat(expiry)
    except ValueError as exc:
        raise OptionsError(f"Expiry must be YYYY-MM-DD, got {expiry!r}") from exc
    sdk = _groww_sdk()
    feed = "groww" if sdk is not None else "simulated"
    key = (feed, u, expiry)
    hit = _chains.get(key)
    if hit and not force and time.monotonic() - hit[0] < CHAIN_TTL_SEC:
        return hit[1]
    note = None
    if sdk is None:
        raw = sim_chain(u, expiry)
    else:
        from groww_pressure import bots_under_pressure

        if bots_under_pressure() and hit:
            stale = dict(hit[1])
            stale["note"] = "The bots hit Groww's rate limit just now, so this chain was not refreshed."
            return stale
        try:
            raw = await asyncio.to_thread(sdk.get_option_chain, exchange_for(u), u, expiry, 15)
        except Exception as exc:  # noqa: BLE001
            return {"underlying": u, "expiry": expiry, "source": feed, "spot": None, "rows": [],
                    "summary": summarize(None, []), "error": _why(exc), "note": None,
                    "fetched_at": dt.datetime.now(IST).isoformat(timespec="seconds")}
    spot, rows = normalize_chain(raw)
    if spot:
        _last_spot[(feed, u)] = spot
    for r in rows:
        for leg in (r["ce"], r["pe"]):
            if leg and leg["symbol"] and leg["ltp"] is not None:
                _last_ltp[(feed, leg["symbol"])] = leg["ltp"]
    out = {
        "underlying": u,
        "expiry": expiry,
        "source": feed,
        "spot": spot,
        "rows": rows,
        "summary": summarize(spot, rows),
        "error": None if rows else "Groww sent no strikes for this expiry.",
        "note": note,
        "days_to_expiry": round(years_to_expiry(expiry) * 365, 2),
        "fetched_at": dt.datetime.now(IST).isoformat(timespec="seconds"),
    }
    _chains[key] = (time.monotonic(), out)
    return out


# ---- practice book ---------------------------------------------------------


def charges(buy_value: float, sell_value: float, orders: int) -> float:
    """Groww's intraday option charges for one round trip (an estimate)."""
    brokerage = BROKERAGE_PER_ORDER * orders
    exchange = (buy_value + sell_value) * EXCHANGE_TXN
    sebi = (buy_value + sell_value) * SEBI
    gst = (brokerage + exchange + sebi) * GST
    return round(brokerage + exchange + sebi + gst + sell_value * STT_SELL + buy_value * STAMP_BUY, 2)


@dataclass
class Ticket:
    underlying: str
    expiry: str
    strike: float
    option_type: str  # CE | PE
    side: str  # BUY | SELL
    lots: int


def _leg_of(book_chain: dict, strike: float, kind: str) -> dict | None:
    row = next((r for r in book_chain.get("rows", []) if abs(r["strike"] - strike) < 1e-6), None)
    return (row or {}).get("ce" if kind == "CE" else "pe")


def _expired(expiry: str, now: dt.datetime) -> bool:
    end = dt.datetime.combine(dt.date.fromisoformat(expiry), dt.time(15, 30), tzinfo=IST)
    return now >= end


def _fill_allowed(feed: str, now: dt.datetime) -> None:
    if feed == "groww" and not is_market_open(now):
        raise OptionsError("NSE is closed: Groww's last price is stale, so practice orders on real prices wait for 09:15.")


async def place(session, ticket: Ticket, now: dt.datetime | None = None) -> OptionPaperTrade:
    """A practice fill at the chain's last price. Never sends anything to Groww."""
    now = now or dt.datetime.now(IST)
    kind, side = ticket.option_type.upper(), ticket.side.upper()
    if kind not in ("CE", "PE") or side not in ("BUY", "SELL"):
        raise OptionsError("Pick CE or PE, and BUY or SELL.")
    if not 1 <= ticket.lots <= 100:
        raise OptionsError("Lots must be 1 to 100.")
    if _expired(ticket.expiry, now):
        raise OptionsError("That expiry is over.")
    got = await chain(ticket.underlying, ticket.expiry)
    if got.get("error"):
        raise OptionsError(got["error"])
    _fill_allowed(got["source"], now)
    leg = _leg_of(got, ticket.strike, kind)
    if not leg or not leg.get("ltp") or leg["ltp"] <= 0:
        raise OptionsError(f"No last price for {ticket.underlying} {ticket.strike:g} {kind} on {ticket.expiry}.")
    lot = await lot_size(ticket.underlying, ticket.expiry)
    row = OptionPaperTrade(
        underlying=got["underlying"],
        expiry=ticket.expiry,
        strike=ticket.strike,
        option_type=kind,
        trading_symbol=leg["symbol"] or f"{got['underlying']} {ticket.strike:g} {kind}",
        side=side,
        lots=ticket.lots,
        lot_size=lot,
        quantity=ticket.lots * lot,
        entry_price=leg["ltp"],
        entry_spot=got.get("spot"),
        feed=got["source"],
        status="OPEN",
        opened_at=now.astimezone(dt.timezone.utc).replace(tzinfo=None),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def lot_size(underlying: str, expiry: str) -> int:
    found = await expiries(underlying)
    for e in found["expiries"]:
        if e["date"] == expiry and e["lot_size"]:
            return int(e["lot_size"])
    if found["source"] == "simulated":
        return _sim_base(underlying)[2]
    raise OptionsError(f"Groww's list has no lot size for {underlying.upper()} {expiry}; try again once the instrument list loads.")


async def _mark(rows: list[OptionPaperTrade]) -> dict[int, float | None]:
    """Last price of each open row from its own feed (one chain call per underlying and expiry)."""
    sdk_feed = "groww" if _groww_sdk() is not None else "simulated"
    prices: dict[int, float | None] = {}
    groups: dict[tuple[str, str], list[OptionPaperTrade]] = {}
    for r in rows:
        groups.setdefault((r.underlying, r.expiry), []).append(r)
    for (u, expiry), members in groups.items():
        got = None
        if any(m.feed == sdk_feed for m in members):
            try:
                got = await chain(u, expiry)
            except OptionsError:
                got = None
        for m in members:
            leg = _leg_of(got, m.strike, m.option_type) if got and m.feed == sdk_feed else None
            price = leg.get("ltp") if leg else None
            if price is None:
                price = _last_ltp.get((m.feed, m.trading_symbol))
            prices[m.id] = price
    return prices


def _pnl(row: OptionPaperTrade, price: float) -> float:
    sign = 1 if row.side == "BUY" else -1
    return round(sign * (price - row.entry_price) * row.quantity, 2)


def _close_row(row: OptionPaperTrade, price: float, reason: str, now: dt.datetime) -> None:
    buy = row.entry_price if row.side == "BUY" else price
    sell = price if row.side == "BUY" else row.entry_price
    row.exit_price = price
    row.status = "CLOSED"
    row.exit_reason = reason
    row.pnl = _pnl(row, price)
    row.charges = charges(buy * row.quantity, sell * row.quantity, 2 if price > 0 else 1)
    row.closed_at = now.astimezone(dt.timezone.utc).replace(tzinfo=None)


async def settle_expired(session, now: dt.datetime | None = None) -> int:
    """Close open rows past their expiry at intrinsic value from the last spot seen."""
    now = now or dt.datetime.now(IST)
    rows = (await session.execute(select(OptionPaperTrade).where(OptionPaperTrade.status == "OPEN"))).scalars().all()
    done = 0
    for r in rows:
        if not _expired(r.expiry, now):
            continue
        spot = _last_spot.get((r.feed, r.underlying))
        if spot is None and r.feed == "simulated":
            spot = sim_spot(r.underlying)
        if spot is None:
            continue  # no spot seen yet for that feed: left open, the page says so
        _close_row(r, round(intrinsic(spot, r.strike, r.option_type), 2), "EXPIRED", now)
        done += 1
    if done:
        await session.commit()
    return done


async def close(session, trade_id: int, now: dt.datetime | None = None) -> OptionPaperTrade:
    now = now or dt.datetime.now(IST)
    row = await session.get(OptionPaperTrade, trade_id)
    if row is None or row.status != "OPEN":
        raise OptionsError("That practice position is not open.")
    _fill_allowed(row.feed, now)
    price = (await _mark([row])).get(row.id)
    if price is None:
        raise OptionsError(
            "No current price for it on its own feed"
            + (" (it was opened on Groww prices; log in to Groww again)." if row.feed == "groww" else ".")
        )
    _close_row(row, price, "MANUAL_EXIT", now)
    await session.commit()
    await session.refresh(row)
    return row


def _row_out(r: OptionPaperTrade, price: float | None = None) -> dict:
    opened = r.opened_at.replace(tzinfo=dt.timezone.utc).astimezone(IST).isoformat(timespec="seconds") if r.opened_at else None
    closed = r.closed_at.replace(tzinfo=dt.timezone.utc).astimezone(IST).isoformat(timespec="seconds") if r.closed_at else None
    out = {
        "id": r.id, "underlying": r.underlying, "expiry": r.expiry, "strike": r.strike,
        "option_type": r.option_type, "symbol": r.trading_symbol, "side": r.side, "lots": r.lots,
        "lot_size": r.lot_size, "quantity": r.quantity, "entry_price": r.entry_price, "entry_spot": r.entry_spot,
        "feed": r.feed, "status": r.status, "exit_price": r.exit_price, "exit_reason": r.exit_reason,
        "pnl": r.pnl, "charges": r.charges, "opened_at": opened, "closed_at": closed,
    }
    if r.status == "OPEN":
        out["ltp"] = price
        out["pnl"] = _pnl(r, price) if price is not None else None
    return out


async def book(session, closed_limit: int = 200) -> dict:
    await settle_expired(session)
    open_rows = (await session.execute(
        select(OptionPaperTrade).where(OptionPaperTrade.status == "OPEN").order_by(OptionPaperTrade.id.desc())
    )).scalars().all()
    closed_rows = (await session.execute(
        select(OptionPaperTrade).where(OptionPaperTrade.status != "OPEN").order_by(OptionPaperTrade.id.desc()).limit(closed_limit)
    )).scalars().all()
    prices = await _mark(list(open_rows))
    positions = [_row_out(r, prices.get(r.id)) for r in open_rows]
    closed = [_row_out(r) for r in closed_rows]
    today = dt.datetime.now(IST).date().isoformat()
    closed_today = [c for c in closed if (c["closed_at"] or "")[:10] == today]
    return {
        "positions": positions,
        "closed": closed,
        "totals": {
            "open_pnl": round(sum(p["pnl"] or 0 for p in positions), 2),
            "open_unpriced": sum(1 for p in positions if p["pnl"] is None),
            "closed_today_pnl": round(sum(c["pnl"] or 0 for c in closed_today), 2),
            "closed_today_charges": round(sum(c["charges"] or 0 for c in closed_today), 2),
            "closed_today": len(closed_today),
        },
    }
