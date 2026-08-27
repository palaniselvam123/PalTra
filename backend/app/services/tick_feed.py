"""Tick source for the app.

In Paper mode (the default, and the only mode that works without broker
credentials) this generates a synthetic feed so the dashboard, strategy
engine and paper fills have something to react to. It is NOT market data —
prices here have no relationship to real quotes.

The synthesis deliberately models three things a plain random walk misses,
because without them the ORB strategy can never trigger realistically:

* **Volume dispersion** — real universes have order-of-magnitude differences
  in turnover between names, which is the entire basis of an RVOL filter.
* **Volume surges** — a breakout that matters comes with a volume spike.
* **Trend regimes** — a pure random walk mean-reverts and almost never sustains
  a directional break; real intraday moves come in runs.

Swap `SimulatedTickFeed` for a broker-backed feed once a live BrokerClient is
wired up (see brokers/base.py).
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

from app.strategies.scanner import NIFTY50_UNIVERSE


@dataclass
class Tick:
    symbol: str
    ltp: float
    bid: float
    ask: float
    volume: int


@dataclass
class _SymbolState:
    price: float
    cum_volume: int
    volume_rate: float      # baseline shares per tick — wide dispersion across names
    drift: float            # current regime drift per tick
    regime_ticks_left: int
    surge_ticks_left: int
    surge_multiplier: float


class SimulatedTickFeed:
    def __init__(self, symbols: list[str] | None = None, interval_sec: float = 1.0):
        self.symbols = symbols or NIFTY50_UNIVERSE[:10]
        self.interval_sec = interval_sec
        # Base prices are derived deterministically from the symbol name rather
        # than re-rolled each run. An unseeded `random.uniform(500, 3500)` gave
        # WIPRO ₹180 in one session and ₹1,700 in the next, so anything that
        # compared a price across a restart — a restored position, a stored
        # entry price — produced nonsense.
        self._state: dict[str, _SymbolState] = {s: self._new_state(s) for s in self.symbols}

    @staticmethod
    def _new_state(symbol: str) -> _SymbolState:
        return _SymbolState(
            price=round(random.Random(f"price:{symbol}").uniform(500, 3500), 2),
            cum_volume=random.Random(f"vol:{symbol}").randint(50_000, 200_000),
            # Log-uniform so a few names are genuinely heavier than the rest.
            volume_rate=10 ** random.uniform(2.0, 4.0),
            drift=0.0,
            regime_ticks_left=0,
            surge_ticks_left=0,
            surge_multiplier=1.0,
        )

    def ensure_symbol(self, symbol: str) -> None:
        """Adds a symbol discovered after construction — a user searching and
        adding a stock to the watchlist mid-session. `stream()` iterates
        `self.symbols` on every pass, so appending here is enough for it to
        start ticking on the next cycle; without a state entry too it would
        KeyError the first time `_step` looked it up.
        """
        if symbol not in self._state:
            self._state[symbol] = self._new_state(symbol)
        if symbol not in self.symbols:
            self.symbols.append(symbol)

    def remove_symbol(self, symbol: str) -> None:
        self._state.pop(symbol, None)
        if symbol in self.symbols:
            self.symbols.remove(symbol)

    def _roll_regime(self, st: _SymbolState) -> None:
        """Pick a new drift regime: mostly flat/noisy, sometimes a real run."""
        roll = random.random()
        if roll < 0.55:
            st.drift = 0.0                                  # chop
        elif roll < 0.80:
            st.drift = random.uniform(-0.0004, 0.0004)       # mild lean
        else:
            st.drift = random.choice([-1, 1]) * random.uniform(0.0008, 0.0020)  # trending run
        st.regime_ticks_left = random.randint(15, 60)

        # A directional regime usually arrives with participation.
        if abs(st.drift) > 0.0008 and random.random() < 0.7:
            st.surge_ticks_left = random.randint(10, 30)
            st.surge_multiplier = random.uniform(2.5, 6.0)

    def _step(self, symbol: str) -> Tick:
        st = self._state[symbol]

        if st.regime_ticks_left <= 0:
            self._roll_regime(st)
        st.regime_ticks_left -= 1

        noise = random.gauss(0, 0.0012)
        st.price = max(round(st.price * (1 + st.drift + noise), 2), 1.0)

        volume_this_tick = st.volume_rate * random.uniform(0.6, 1.4)
        if st.surge_ticks_left > 0:
            volume_this_tick *= st.surge_multiplier
            st.surge_ticks_left -= 1
            if st.surge_ticks_left == 0:
                st.surge_multiplier = 1.0
        st.cum_volume += int(volume_this_tick)

        spread = max(round(st.price * random.uniform(0.0002, 0.0006), 2), 0.05)
        return Tick(
            symbol=symbol,
            ltp=st.price,
            bid=round(st.price - spread / 2, 2),
            ask=round(st.price + spread / 2, 2),
            volume=st.cum_volume,
        )

    async def stream(self):
        while True:
            for symbol in self.symbols:
                yield self._step(symbol)
            await asyncio.sleep(self.interval_sec)
