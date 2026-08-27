# Intraday Opportunity Ranking Engine — Audit & Plan

Living plan. Records what already exists, what is genuinely new, and one
recommended change to the build order.

---

## Phase 1 — Audit result

### Already built (do not rebuild)

| Spec item | Where it lives |
|---|---|
| Single indicator source of truth | `services/indicators.py` |
| ATR, VWAP, ADX, RSI, MACD, Bollinger, Supertrend, EMA, SMA | `indicators.py` — all 6 Phase-17 priorities except range expansion and S/R |
| Candlestick patterns (11, geometry + trend context) | `services/patterns.py` |
| EMA crossover + filter logic | `services/scanner_engine.py` |
| Relative volume | `scanner_engine.py` (volume_ratio), `strategy_runner.py` (RVOL) |
| Backtester | `services/backtester.py` |
| Risk sizing (1% rule + leverage cap) | `core/risk_manager.py`, mirrored in `backtester._size` |
| Cost model (slippage + charges) | `services/paper_engine.py` |
| Scanner API + UI | `api/routes_scanner_engine.py`, `app/scanner/page.tsx` |
| Charts with indicator overlays | `api/routes_chart.py`, `components/Chart/AdvancedChart.tsx` |
| Explainability layer | `services/explain.py` — plain-English reasons per signal |

### Phase 15 (validation) — mostly satisfied already

- **No look-ahead** — `evaluate_at` reads only up to index `i`; entry is on the
  NEXT bar's open. Documented in the backtester docstring.
- **Stop/target collision** — resolves to the STOP, the conservative side.
  Documented. *Gap: not yet configurable.*
- **Position state** — a new entry requires `open_pos is None`, so BUY/BUY/BUY
  cannot occur. *Gap: no explicit FLAT/LONG/SHORT enum.*
- **Costs centralised** — `paper_engine.estimate_charges` is the only
  implementation, shared by live and backtest. **Gap: no unit tests.**

### Phase 16 (performance) — done

`precompute()` + `evaluate_at()` removed the O(n²) recompute and the per-bar
history copy. Verified bit-identical to the original across all five filter
combinations. **Gap: that equivalence check is a throwaway script, not a
committed regression test.**

### Genuinely new

Liquidity filter (volume side), market regime engine, movement potential,
range expansion, direction engine, entry quality / available price room,
support & resistance, risk-reward engine, opportunity ranker, top-opportunities
dashboard, MFE/MAE forward metrics, time-of-day analysis.

---

## One recommended change to the build order

The spec's Step 7 backtests the ranking **after** Steps 3–6 build it. That
means every component is written before anything can tell you whether it
carries information.

**Move Phase 14 (MFE/MAE forward outcomes) to immediately after Step 2.**

MFE/MAE is the yardstick the whole system is graded against. With it in place
first, every later component can be validated as it lands:

- Movement Potential (Step 3) → does HIGH actually produce larger MFE than LOW?
- Relative Volume (Step 4) → does high RVOL improve MFE/MAE?
- Entry Quality (Step 5) → does "enough room" reduce MAE?
- Ranker (Step 6) → do top-ranked names out-move rank 50–100?

Built in the spec's order, all four questions stay unanswerable until the very
end, and a component that adds nothing would not be detected until then.

This matters here specifically: the backtest already run on this app returned
**profit factor 0.30–0.40 across every filter variant**, all losing. Adding
scoring on top of a strategy with no demonstrated edge, without measuring as
you go, risks producing a well-ranked list of bad trades.

---

## Revised implementation order

| Step | Work | Status |
|---|---|---|
| 1 | Audit | **done** |
| 2 | Stabilise backtester: cost-model unit tests, committed regression test, configurable collision rule, explicit position state | next |
| 3 | **MFE/MAE + forward returns (5/15/30/60m)** — the measurement substrate | next |
| 4 | ATR-based Movement Potential (required move vs available volatility) → validate against MFE | |
| 5 | Range expansion + time-of-day relative volume → validate | |
| 6 | Support/resistance + available price room + Entry Quality → validate against MAE | |
| 7 | Market Regime engine (formalise existing ADX/EMA/VWAP into labels) | |
| 8 | Direction engine (separate from signal) | |
| 9 | Risk/Reward engine | |
| 10 | Opportunity Ranker + configurable weights | |
| 11 | Backtest the ranker (Tests 1–6 from the spec) | |
| 12 | API + Top Opportunities dashboard | |

---

## Design constraints carried forward

- **One indicator implementation.** Everything new extends `indicators.py`.
  No pandas-ta; a second EMA means an alert can disagree with its own chart.
- **The score is a ranking, not a probability.** Emit
  `score_type: "ranking_score"`, `not_probability: true`. No "82% chance"
  until calibration data exists.
- **Show disagreement.** When direction is bullish and movement potential is
  low, say so and downgrade — never hide the negative component.
- **Required move includes costs.** Minimum gross move + charges + slippage +
  margin, compared against ATR. This is the check that stops the app
  recommending a stock that cannot pay for its own round trip.
- **Thresholds configurable and backtestable**, never hard-coded constants.

---

## Step 3.5 — Entry Diagnostics (findings)

Built `services/entry_diagnostics.py` to answer one question: *under exactly
what conditions does the current entry signal have MFE > MAE?*

Population: 289 signals, 12 NSE symbols, 5-minute bars, EMA 9/21 crossover.

### The 0.64 figure did not survive

MFE/MAE is not a property of the entry — it is a property of the entry **and
the measurement window**. Same signals, same data, varying only the horizon:

| Horizon | 3 bars | 6 bars | 12 bars | 24 bars | 48 bars |
|---|---|---|---|---|---|
| MFE/MAE | 0.83 | 0.88 | 0.99 | 1.03 | 1.08 |

The ratio rises monotonically with the window because MAE is bounded early
while MFE keeps accumulating. Quoting a single number without its horizon
says nothing. **The earlier "0.64 proves entries are badly located" claim was
an artefact of one horizon on one symbol set, and is withdrawn.**

### The control that actually matters

Against random entries on the same bars and symbols (n≈705 per horizon):

| Horizon | Signal | Random | Edge |
|---|---|---|---|
| 6 bars | 0.88 | 1.05 | **−0.17** |
| 12 bars | 0.99 | 1.15 | **−0.16** |
| 24 bars | 1.03 | 1.02 | +0.01 |

A coin flip scores the same or better. This is the finding: not that entries
are mislocated, but that the crossover carries **no directional information at
all**. Every segmentation below is therefore a search for a subset where a
zero-information signal happens to look good.

### Segmentation (12-bar horizon)

| Entry condition | Trades | Avg MFE% | Avg MAE% | MFE/MAE |
|---|---|---|---|---|
| ALL | 289 | 0.298 | 0.300 | 0.99 |
| BUY | 144 | 0.310 | 0.345 | 0.90 |
| SELL | 144 | 0.287 | 0.254 | 1.13 |
| ADX <15 | 76 | 0.311 | 0.329 | 0.94 |
| ADX 15–20 | 76 | 0.356 | 0.267 | 1.34 |
| ADX 20–25 | 48 | 0.264 | 0.308 | 0.86 |
| ADX >25 | 88 | 0.257 | 0.297 | 0.87 |
| Aligned with VWAP | 254 | 0.296 | 0.289 | 1.03 |
| Against VWAP | 34 | 0.317 | 0.379 | 0.84 |
| RVOL <0.8x | 66 | 0.309 | 0.352 | 0.88 |
| RVOL 0.8–1.2x | 77 | 0.326 | 0.354 | 0.92 |
| RVOL 1.2–2x | 143 | 0.279 | 0.246 | 1.14 |
| Prior move <0.1% (early) | 76 | 0.287 | 0.334 | 0.86 |
| Prior move 0.1–0.3% | 130 | 0.256 | 0.270 | 0.95 |
| Prior move >0.3% (late) | 82 | 0.377 | 0.313 | 1.21 |
| First crossover of day | 91 | 0.278 | 0.308 | 0.90 |
| Second | 76 | 0.268 | 0.299 | 0.89 |
| Third or later | 121 | 0.334 | 0.293 | 1.14 |
| Room to session extreme <0.1% | 37 | 0.275 | 0.286 | 0.96 |
| Room 0.1–0.4% | 126 | 0.283 | 0.271 | 1.05 |
| Room >0.4% | 125 | 0.321 | 0.332 | 0.97 |
| Open (to 09:45) | 29 | 0.345 | 0.442 | 0.78 |
| Morning | 67 | 0.261 | 0.252 | 1.03 |
| Midday | 88 | 0.212 | 0.239 | 0.89 |
| Afternoon | 61 | 0.430 | 0.308 | 1.39 |
| Close (from 14:45) | 43 | 0.317 | 0.387 | 0.82 |

### Permutation tests

Two buckets beat the rest at p<0.05: afternoon (p=0.038) and ADX 15–20
(p=0.044). **Neither is claimed as real.** Six hypotheses were tested, so the
multiple-comparison threshold is 0.05/6 = 0.008 and both fail it. ADX is also
non-monotonic (0.94 → 1.34 → 0.86 → 0.87): if trend strength drove the result,
ADX >25 would be the best bucket, not the worst-but-one. That pattern is what
noise looks like, not what an effect looks like.

### The five bad-entry hypotheses

| # | Hypothesis | Verdict |
|---|---|---|
| A | Late entry — signal fires after the move | **Contradicted.** Late entries scored 1.21 vs 0.86 early — the opposite direction |
| B | Sideways whipsaw | **Not supported.** ADX <15 (0.94) is no worse than ADX >25 (0.87) |
| C | Entry into resistance | **Not supported.** Room buckets are flat: 0.96 / 1.05 / 0.97 |
| D | No volume confirmation | **Weak.** RVOL >1.2x helps directionally but p=0.271 |
| E | Wrong market direction | **Untestable as built** — no index feed. VWAP alignment as a proxy: p=0.556 |

No subset of entries is identifiably bad, because none is identifiably good.

### The cost floor

At the median entry price (Rs 1,136, 88 shares for a 1L position):

- charges 0.083% + slippage 0.100% = **0.183% round trip**
- average MFE = 0.298%

Costs consume **61% of the average best-case excursion** — the move captured
only by an exit placed at the perfect bar. Even the best segment in the table
(afternoon, MFE 0.430%) leaves 0.247% before an exit rule takes its share.
A signal needs a large real edge to clear this floor, and this one has none.

### Conclusion

The answer to "under exactly what conditions does this entry have MFE > MAE"
is: **none that survive a significance test.** Movement Potential, Entry
Quality and the Ranker would all be scoring a signal with no measured
information content. Building them next would produce a well-ordered list of
coin flips.

Next work should replace the entry hypothesis rather than decorate it, and
`entry_diagnostics.collect()` is now the harness that grades whatever replaces
it — any candidate must beat the random baseline before it earns a place.
