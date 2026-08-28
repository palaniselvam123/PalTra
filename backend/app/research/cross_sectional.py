"""H004 v1 — cross-sectional sector-neutral relative strength.

**Question.** At a fixed 5-minute timestamp, does a stock's volatility-adjusted
return relative to the median return of its own sector contain information about
its subsequent sector-relative return?

This is a new hypothesis, not a variant of H001-H003. Those conditioned on a
completed directional move in one instrument and asked whether it continued;
this compares instruments to each other at a single instant and never conditions
on a move having happened at all.

**What is deliberately absent.** No EMA, RSI, MACD, ADX, VWAP, Supertrend or
volume appears anywhere in this module. ATR enters only as a scale so that a
Rs 200 stock and a Rs 8,000 stock are comparable; it is not a filter.

**Leave-one-out is required, not cosmetic.** Benchmarking a stock against a
sector median that includes itself makes the median stock's relative return
exactly zero by construction — measured at 6.81% of observations on this
dataset. Excluding the stock from its own benchmark reduces that to 0.02% while
correlating +0.988 with the naive version.

**Minimum sector size is structural, not tuned.** With leave-one-out a sector of
size n benchmarks each member against n-1 peers. At n=2 the peer group is one
stock and the two members become mirror images; at n=3 the peer median is again
a single stock. Five members gives a four-peer median, the smallest group where
the benchmark is a group statistic rather than an individual. No performance
figure informed this.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass

LOOKBACK_BARS = 12
ATR_PERIOD = 14
HORIZONS = (6, 12, 24)
MIN_SECTOR_SIZE = 5

# Frozen sector reference map. Assigned by hand from the issuer's line of
# business — reference information containing no return data of any kind.
SECTORS: dict[str, str] = {
    "IT": "TCS INFY WIPRO HCLTECH TECHM MPHASIS PERSISTENT OFSS",
    "BANK": ("HDFCBANK ICICIBANK SBIN AXISBANK KOTAKBANK INDUSINDBK BANKBARODA CANBK PNB "
             "AUBANK IDFCFIRSTB YESBANK"),
    "FIN": ("BAJFINANCE BAJAJFINSV SHRIRAMFIN CHOLAFIN MUTHOOTFIN PFC RECLTD IRFC SBICARD "
            "JIOFIN LICI HDFCLIFE SBILIFE ICICIGI ICICIPRULI BAJAJHLDNG"),
    "AUTO": ("MARUTI M&M EICHERMOT HEROMOTOCO BAJAJ-AUTO TVSMOTOR ASHOKLEY MOTHERSON "
             "BHARATFORG BOSCHLTD TIINDIA"),
    "PHARMA": ("SUNPHARMA CIPLA DRREDDY DIVISLAB TORNTPHARM ALKEM AUROPHARMA LUPIN ZYDUSLIFE "
               "MAXHEALTH APOLLOHOSP"),
    "FMCG": ("HINDUNILVR ITC NESTLEIND BRITANNIA DABUR GODREJCP MARICO COLPAL TATACONSUM VBL "
             "UNITDSPR JUBLFOOD"),
    "METAL": "TATASTEEL JSWSTEEL HINDALCO VEDL JINDALSTEL SAIL NMDC",
    "ENERGY": "RELIANCE ONGC BPCL IOC GAIL PETRONET COALINDIA",
    "POWER": "NTPC POWERGRID TATAPOWER ADANIPOWER ADANIGREEN ADANIENSOL TORNTPOWER SUZLON",
    "CEMENT": "ULTRACEMCO GRASIM SHREECEM AMBUJACEM LT",
    "CONSUMER": "TITAN TRENT DMART PAGEIND INDHOTEL NAUKRI INDIGO",
    "CAPGOODS": "SIEMENS ABB HAL BEL CGPOWER CUMMINSIND POLYCAB HAVELLS GMRAIRPORT",
    "CHEM": "ASIANPAINT BERGEPAINT PIDILITIND SRF UPL",
    # Below MIN_SECTOR_SIZE — retained in the map for completeness and excluded
    # by rule rather than deleted, so the exclusion stays visible.
    "TELECOM": "BHARTIARTL INDUSTOWER TATACOMM",
    "REALTY": "DLF OBEROIRLTY",
    "CONGLOM": "ADANIENT ADANIPORTS",
}

SECTOR_OF: dict[str, str] = {s: sec for sec, names in SECTORS.items() for s in names.split()}


def sector_of(symbol: str) -> str | None:
    return SECTOR_OF.get(symbol)


def sector_members(sector: str) -> list[str]:
    return SECTORS[sector].split()


def eligible_sectors(present: set[str] | None = None) -> list[str]:
    """Sectors meeting the minimum size, optionally among symbols present."""
    out = []
    for sec, names in SECTORS.items():
        members = [s for s in names.split() if present is None or s in present]
        if len(members) >= MIN_SECTOR_SIZE:
            out.append(sec)
    return sorted(out)


def research_universe(present: set[str] | None = None) -> list[str]:
    """Symbols in sectors that meet the minimum size."""
    keep = set(eligible_sectors(present))
    return sorted(s for s, sec in SECTOR_OF.items()
                  if sec in keep and (present is None or s in present))


# ---- score ---------------------------------------------------------------


def sector_median_loo(returns: dict[str, float], symbol: str) -> float | None:
    """Median return of `symbol`'s sector peers, excluding the symbol itself.

    None when the symbol has no sector, or its sector has too few members
    present at this timestamp.
    """
    sec = SECTOR_OF.get(symbol)
    if sec is None:
        return None
    members = [s for s in returns if SECTOR_OF.get(s) == sec]
    if len(members) < MIN_SECTOR_SIZE:
        return None
    peers = [returns[s] for s in members if s != symbol]
    if not peers:
        return None
    return statistics.median(peers)


def sector_relative_return(returns: dict[str, float], symbol: str) -> float | None:
    med = sector_median_loo(returns, symbol)
    if med is None or symbol not in returns:
        return None
    return returns[symbol] - med


def h004_score(returns: dict[str, float], symbol: str, atr_over_close: float) -> float | None:
    """The pre-registered score.

    `returns` are the LOOKBACK_BARS returns of every symbol observed at this
    timestamp; `atr_over_close` is ATR(14)/close for `symbol` at the same bar.
    Every input is known at time t.
    """
    rel = sector_relative_return(returns, symbol)
    if rel is None or not atr_over_close or atr_over_close <= 0:
        return None
    return rel / atr_over_close


# ---- outcome -------------------------------------------------------------


def future_sector_relative_return(
    future_returns: dict[str, float], symbol: str
) -> float | None:
    """Outcome matching the score: forward return minus the leave-one-out
    median forward return of the same sector's eligible peers.

    Identical construction to the score's benchmark, so a positive result
    cannot be an artefact of measuring the two against different baselines.
    """
    return sector_relative_return(future_returns, symbol)


# ---- bucketing -----------------------------------------------------------


@dataclass(frozen=True)
class TercileBounds:
    """Frozen tercile cut points for one sector."""

    sector: str
    low: float
    high: float

    def bucket(self, score: float) -> str:
        if score < self.low:
            return "low"
        return "mid" if score < self.high else "high"


def within_sector_terciles(scores_by_sector: dict[str, list[float]]) -> dict[str, TercileBounds]:
    """Tercile boundaries computed INDEPENDENTLY inside each sector.

    Global terciles would tilt the extremes toward small sectors: a median over
    four peers is a noisier benchmark than one over fifteen, and the measured
    score dispersion is about 15% wider in five-member sectors than in
    twelve-member ones. Cutting within sector removes that by construction and
    needs no correction factor.
    """
    out: dict[str, TercileBounds] = {}
    for sec, vals in scores_by_sector.items():
        if len(vals) < 3:
            continue
        ordered = sorted(vals)
        n = len(ordered)
        lo = ordered[max(0, min(n - 1, int(n / 3)))]
        hi = ordered[max(0, min(n - 1, int(2 * n / 3)))]
        out[sec] = TercileBounds(sec, lo, hi)
    return out


def assign_bucket(bounds: dict[str, TercileBounds], symbol: str, score: float) -> str | None:
    sec = SECTOR_OF.get(symbol)
    if sec is None or sec not in bounds:
        return None
    return bounds[sec].bucket(score)


# ---- null ----------------------------------------------------------------


def permute_within_sector(
    scores: dict[str, float], rng: random.Random
) -> dict[str, float]:
    """Re-pair scores to stocks INSIDE each sector, never across sectors.

    Permuting across the whole cross-section would break sector membership too,
    so a purely sector-driven result could still beat that null. Confining the
    shuffle to a sector makes the sector's own move identical in every
    permutation, and therefore incapable of contributing to the null
    distribution.

    Everything else is preserved exactly: the timestamp, which stocks are
    present, sector membership, the market's move, each sector's move, the
    volatility environment, and the marginal distribution of scores within each
    sector. Only the score-to-stock assignment is randomised.
    """
    out: dict[str, float] = {}
    by_sector: dict[str, list[str]] = {}
    for s in scores:
        by_sector.setdefault(SECTOR_OF.get(s, "?"), []).append(s)
    for _sec, members in by_sector.items():
        members = sorted(members)
        values = [scores[s] for s in members]
        rng.shuffle(values)
        out.update(dict(zip(members, values)))
    return out
