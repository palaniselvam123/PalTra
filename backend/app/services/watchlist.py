"""User-managed additions to the live watchlist, on top of the bot's fixed
20-name strategy universe.

Every add is checked against the instrument master first. Silently accepting
an unrecognised or wrong-exchange ticker was flagged as a real trap earlier —
`get_ltp_batch` drops anything Groww doesn't return, with no error, so a typo
would just never tick and look identical to "the stock isn't coming" with no
way to tell the two apart. Validating here means a rejected add fails loudly,
at the moment it matters, instead of silently at query time.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.database import WatchlistSymbol, async_session
from app.services.instruments import instrument_master
from app.services.market_data import market_data


class WatchlistRejected(Exception):
    def __init__(self, reason: str, status_code: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


async def add(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    if not symbol:
        raise WatchlistRejected("Symbol is required.")

    inst = instrument_master.get(symbol)
    if inst is None:
        raise WatchlistRejected(
            f"'{symbol}' is not an NSE cash-market symbol Groww recognises. Check the exact "
            "trading symbol — search /api/instruments/search to find it.",
            404,
        )
    # Not intraday-eligible does NOT reject the add: Groww's own app lets you
    # watch and search delivery-only names too, you just can't place an MIS
    # order on them. The actual block belongs on the order path, not here —
    # see the intraday_allowed check in manual_desk.place().

    added = market_data.add_symbol(symbol)
    async with async_session() as session:
        existing = (
            await session.execute(select(WatchlistSymbol).where(WatchlistSymbol.symbol == symbol))
        ).scalar_one_or_none()
        if existing is None:
            session.add(WatchlistSymbol(symbol=symbol))
            await session.commit()

    return {
        "symbol": symbol,
        "name": inst.name,
        "was_new": added,
        "intraday_allowed": inst.intraday_allowed,
    }


async def remove(symbol: str) -> bool:
    symbol = symbol.strip().upper()
    if not market_data.remove_symbol(symbol):
        return False
    async with async_session() as session:
        row = (
            await session.execute(select(WatchlistSymbol).where(WatchlistSymbol.symbol == symbol))
        ).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            await session.commit()
    return True


async def list_added() -> list[dict]:
    async with async_session() as session:
        rows = (await session.execute(select(WatchlistSymbol).order_by(WatchlistSymbol.added_at))).scalars().all()
    out = []
    for r in rows:
        inst = instrument_master.get(r.symbol)
        out.append(
            {
                "symbol": r.symbol,
                "name": inst.name if inst else "",
                "added_at": r.added_at.isoformat() if r.added_at else None,
            }
        )
    return out


async def restore() -> list[str]:
    """Re-adds every persisted symbol to live streaming at startup. Without
    this an added symbol would vanish from the feed on every restart, even
    though the database still remembered it — the watchlist would look
    populated in one place and empty in another.
    """
    async with async_session() as session:
        rows = (await session.execute(select(WatchlistSymbol))).scalars().all()
    restored = []
    for row in rows:
        if market_data.add_symbol(row.symbol):
            restored.append(row.symbol)
    return restored
