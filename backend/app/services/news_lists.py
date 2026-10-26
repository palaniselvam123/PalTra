"""News watch lists and stock prices for the News page and the news alerts (information only, no orders).

* **Watch lists** (`NewsWatchList` rows): any number of named lists of stocks, each any NSE stocks from the
  instrument list (not only F&O). The news watcher (`news_impact.watch_list`) reads every list's stocks on each
  pass next to the bots' armed / held ones, so they get the full per-stock read and alerts. The Scalp page adds to
  them (on the scanner server through the desk link, `routes_link`).
* **Prices** (`prices`): the last traded price for the alert text and the page, from the SMA bots' own quotes
  first (`news_prices` in the terminal), then the desk's live feed (`state.latest_quotes`), then one batched Groww
  LTP call on the desk's session, skipped while the bots are rate-limited (`groww_pressure`). A stock without a
  price is simply left out.
"""
from __future__ import annotations

import logging
import re
import time

from sqlalchemy import select

from app.models.database import NewsWatchList, async_session

log = logging.getLogger(__name__)

MAX_LISTS = 50
MAX_PER_LIST = 200
NAME_MAX = 40
PRICE_TTL_SEC = 30
_SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,25}$")
_prices: dict[str, tuple[float, float]] = {}  # symbol -> (ltp, monotonic time)


class ListError(ValueError):
    """A watch-list change that cannot be made (shown to the person as is)."""


def split(symbols: str | None) -> list[str]:
    return [s for s in (symbols or "").split(",") if s]


def out(row: NewsWatchList) -> dict:
    return {"id": row.id, "name": row.name, "symbols": split(row.symbols)}


def clean(symbols: list[str]) -> list[str]:
    """Upper-case, de-duplicated NSE symbols; unknown ones are refused when the instrument list is loaded."""
    names = list(dict.fromkeys((s or "").strip().upper() for s in symbols if (s or "").strip()))
    bad = [s for s in names if not _SYMBOL.match(s)]
    if bad:
        raise ListError(f"Not a stock symbol: {', '.join(bad[:5])}")
    known = _known()
    if known:
        unknown = [s for s in names if s not in known]
        if unknown:
            raise ListError(f"Not on NSE's list: {', '.join(unknown[:5])}")
    return names


def _known() -> set[str]:
    try:
        from app.services.instruments import instrument_master

        # Only when already loaded: loading downloads Groww's list, which must not happen on a request.
        return set(getattr(instrument_master, "_by_symbol", None) or {})
    except Exception:  # noqa: BLE001 - no instrument list: accept any well-formed symbol
        return set()


async def all_lists() -> list[dict]:
    async with async_session() as s:
        rows = (await s.execute(select(NewsWatchList).order_by(NewsWatchList.id))).scalars().all()
    return [out(r) for r in rows]


async def lists_or_none() -> list[dict]:
    """For the news watcher: the lists, or none when they cannot be read (the bots' stocks are still watched)."""
    try:
        return await all_lists()
    except Exception:  # noqa: BLE001
        log.exception("news watch lists could not be read")
        return []


async def create(name: str, symbols: list[str]) -> dict:
    name = (name or "").strip()[:NAME_MAX]
    if not name:
        raise ListError("Give the list a name.")
    names = clean(symbols)[:MAX_PER_LIST]
    async with async_session() as s:
        count = len((await s.execute(select(NewsWatchList.id))).all())
        if count >= MAX_LISTS:
            raise ListError(f"At most {MAX_LISTS} watch lists.")
        row = NewsWatchList(name=name, symbols=",".join(names))
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return out(row)


async def update(list_id: int, *, name: str | None = None, symbols: list[str] | None = None,
                 add: list[str] | None = None, remove: list[str] | None = None) -> dict:
    async with async_session() as s:
        row = await s.get(NewsWatchList, list_id)
        if row is None:
            raise LookupError("No such watch list.")
        if name is not None:
            text = name.strip()[:NAME_MAX]
            if not text:
                raise ListError("Give the list a name.")
            row.name = text
        names = clean(symbols) if symbols is not None else split(row.symbols)
        if add:
            names += [n for n in clean(add) if n not in names]
        if remove:
            gone = {(r or "").strip().upper() for r in remove}
            names = [n for n in names if n not in gone]
        if len(names) > MAX_PER_LIST:
            raise ListError(f"At most {MAX_PER_LIST} stocks in one list.")
        row.symbols = ",".join(names)
        await s.commit()
        await s.refresh(row)
    return out(row)


async def delete(list_id: int) -> None:
    async with async_session() as s:
        row = await s.get(NewsWatchList, list_id)
        if row is None:
            raise LookupError("No such watch list.")
        await s.delete(row)
        await s.commit()


# ---- prices --------------------------------------------------------------------


def _bot_prices(symbols: list[str]) -> dict[str, float]:
    try:
        from app.sma_host import load_terminal

        mod = load_terminal()
        fn = getattr(mod, "news_prices", None) if mod is not None else None
        return dict(fn(symbols)) if fn is not None else {}
    except Exception:  # noqa: BLE001
        return {}


async def prices(symbols: list[str], groww: bool = True) -> dict[str, float]:
    """symbol -> last price for as many of `symbols` as can be priced (see the module note)."""
    from app import state

    names = list(dict.fromkeys(s.upper() for s in symbols if s))
    found: dict[str, float] = {}
    now = time.monotonic()
    for name in names:
        hit = _prices.get(name)
        if hit and now - hit[1] < PRICE_TTL_SEC:
            found[name] = hit[0]
    for name, ltp in _bot_prices([n for n in names if n not in found]).items():
        if ltp and ltp > 0:
            found[name] = float(ltp)
    for name in names:
        if name not in found:
            q = state.latest_quotes.get(name) or {}
            if q.get("ltp"):
                found[name] = float(q["ltp"])
    missing = [n for n in names if n not in found]
    if missing and groww:
        found.update(await _groww_ltps(missing))
    for name, ltp in found.items():
        _prices[name] = (ltp, now)
    return found


async def _groww_ltps(symbols: list[str]) -> dict[str, float]:
    try:
        import groww_pressure

        if groww_pressure.bots_under_pressure():
            return {}
    except Exception:  # noqa: BLE001 - not on this server
        pass
    try:
        from app.services.groww_funds import groww_client

        client = groww_client()
        if client is None or not getattr(client, "_access_token", None):
            return {}
        return await client.get_ltp_batch(symbols[:100])
    except Exception as exc:  # noqa: BLE001 - the alert goes without a price
        log.info("news prices: Groww LTP failed: %s", exc)
        return {}


def price_text(ltp: float | None) -> str:
    return f"₹{ltp:,.2f}" if ltp else ""
