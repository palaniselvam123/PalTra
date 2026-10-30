"""News entries: one bot enters at once on a strong, fresh news headline.

The desk's news watcher (`app/services/news_impact.py`) scores each headline (rules or
OpenAI) and hands every fresh one here through `consider` (via `main.news_entry_consider`).
When the settings on bot 1's row allow it, the picked bot (`news_entry_bot`, 1-4, 0 = off)
orders straight away through `StrategyEngine.news_order`: no SMA cross, candle pattern or
entry filter is read, and no pullback / candle wait. Good news buys, bad news shorts.

* `news_entry_impact` LOW / MEDIUM / HIGH: the weakest headline that may trade (a critical one
  counts as HIGH). A headline mainly about another company (`about: False`) or NEUTRAL never does.
* `news_entry_max_age_min` (15): an older headline is not traded.
* `news_entry_scope` FNO (any F&O stock: the stock is put on the bot's Trade list with the source
  chip "News", so its normal exits watch it) or ARMED (only stocks already armed on that bot).
* `news_entry_max_day` (5): news entries per bot per IST day; the bot's own trade cap also applies.

PAPER and LIVE. A LIVE bot follows the usual rules in `_open` (one LIVE bot per stock, the
Groww session, the stop), and a stock another LIVE bot owns is not armed. Turning news
entries on for a LIVE bot, or picking a LIVE bot, needs `confirm_live` (main.py).

After the entry the bot's own exits manage the trade: stop / trailing stop / target, the SMA
cross, candle colour or pattern, the other exits and the square-off. Each headline is
traded at most once (memory, `_taken`); replays and research never see news.
"""
from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger(__name__)

IMPACTS = ("LOW", "MEDIUM", "HIGH")
SCOPES = ("FNO", "ARMED")
SOURCE = "News"
MAX_AGE_MIN = 240
MAX_DAY = 50

_taken: set[str] = set()  # headline keys already acted on
_today: dict[tuple[int, dt.date], int] = {}  # (bot, IST day) -> news entries
recent: list[dict] = []  # newest first: what each considered headline did


def settings(cfg) -> dict:
    """The news-entry settings kept on bot 1's row."""
    bot = int(getattr(cfg, "news_entry_bot", 0) or 0)
    impact = (getattr(cfg, "news_entry_impact", None) or "HIGH").upper()
    scope = (getattr(cfg, "news_entry_scope", None) or "FNO").upper()
    return {
        "bot": bot if 1 <= bot <= 5 else 0,
        "impact": impact if impact in IMPACTS else "HIGH",
        "scope": scope if scope in SCOPES else "FNO",
        "max_age_min": int(getattr(cfg, "news_entry_max_age_min", None) or 15),
        "max_day": int(getattr(cfg, "news_entry_max_day", None) or 5),
    }


def signal_of(item: dict) -> str | None:
    s = (item.get("sentiment") or "").upper()
    return "BULLISH" if s == "POSITIVE" else "BEARISH" if s == "NEGATIVE" else None


def _age_min(at: str | None, now: dt.datetime) -> float | None:
    if not at:
        return None
    try:
        when = dt.datetime.fromisoformat(at)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=now.tzinfo)
    return (now - when).total_seconds() / 60


def why_not(item: dict, conf: dict, now: dt.datetime) -> str | None:
    """Why this headline does not trade, or None when it may."""
    if item.get("about") is False:
        return "mainly about another company"
    if signal_of(item) is None:
        return "no direction (neutral)"
    level = "HIGH" if item.get("critical") else (item.get("impact") or "LOW").upper()
    if IMPACTS.index(level if level in IMPACTS else "LOW") < IMPACTS.index(conf["impact"]):
        return f"{level.lower()} impact, below {conf['impact'].lower()}"
    age = _age_min(item.get("at"), now)
    if age is None:
        return "no time on the headline"
    if age > conf["max_age_min"]:
        return f"{age:.0f} min old (over {conf['max_age_min']})"
    return None


def arm(bot: int, symbol: str) -> str | None:
    """Put `symbol` on the bot's Trade list (source "News"). Returns why not, or None."""
    from database import session_factory
    from models import BotConfig
    import bots
    from strategy_engine import MAX_TRADE_SYMBOLS, apply_trade_sources, trade_names

    name = symbol.upper()
    with session_factory()() as db:
        row = db.get(BotConfig, bots.config_id_for(bot))
        if row is None:
            return f"Bot {bot} is not set up"
        names = trade_names(row)
        if name in names:
            return None
        if len(names) >= MAX_TRADE_SYMBOLS:
            return f"Trade list is full ({MAX_TRADE_SYMBOLS})"
        if (row.trading_mode or "PAPER").upper() == "LIVE":
            clash = bots.live_conflict(bot, [name])
            if clash:
                return clash
        names.append(name)
        row.trade_symbols = ",".join(names)
        row.trade_sources = apply_trade_sources(row, {name: SOURCE}, names)
        db.commit()
    return None


def _note(entry: dict) -> None:
    recent.insert(0, entry)
    del recent[50:]


async def consider(symbol: str, item: dict, cfg, engine, now: dt.datetime) -> str | None:
    """Act on one fresh headline. Returns what happened, or None when it was not for trading."""
    from groww_client import market_is_open
    from strategy_engine import ForceRefused, trade_names

    conf = settings(cfg)
    key = item.get("key") or f"{symbol}|{item.get('title')}"
    if not conf["bot"] or engine is None or key in _taken:
        return None
    reason = why_not(item, conf, now)
    if reason is not None:
        return None
    _taken.add(key)
    name = symbol.upper()
    signal = signal_of(item)
    day = now.date()
    done = _today.get((conf["bot"], day), 0)
    if engine.status != "RUNNING":
        result = f"not traded: Bot {conf['bot']} is {engine.status}, not RUNNING"
    elif not market_is_open(now):
        result = "not traded: market is closed"
    elif done >= conf["max_day"]:
        result = f"not traded: {conf['max_day']} news entries already today"
    else:
        result = None
        if name not in trade_names(engine.load_config()):
            if conf["scope"] != "FNO":
                result = "not traded: not armed on the news bot (scope: armed stocks)"
            else:
                why = arm(conf["bot"], name)
                result = f"not traded: {why}" if why else None
        if result is None:
            try:
                result = await engine.news_order(name, signal, note=(item.get("title") or "")[:160])
            except ForceRefused as exc:
                result = f"not traded: {exc}"
            except Exception as exc:  # noqa: BLE001 - the next headline still goes
                log.exception("news entry failed for %s", name)
                result = f"not traded: {exc}"
            if result.startswith(("opened", "reversed")):
                _today[(conf["bot"], day)] = done + 1
    _note({
        "at": now.isoformat(timespec="seconds"), "bot": conf["bot"], "symbol": name,
        "side": "BUY" if signal == "BULLISH" else "SHORT", "impact": item.get("impact"),
        "title": (item.get("title") or "")[:200], "url": item.get("url"), "result": result,
    })
    log.info("news entry %s %s: %s", name, signal, result)
    return result
