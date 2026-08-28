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

---

## Step 3.6 — Hypothesis 1: Opening Range Breakout — **REJECTED**

Reused the diagnostics harness; the EMA strategy was not modified. New:
`services/orb_hypothesis.py` (replays the live ORB entry rule over history)
and `services/hypothesis_lab.py` (matched controls, significance, OOS split).

### Matched controls

An unmatched random baseline is easy to beat for the wrong reason — signals
fire on volatile bars, so comparing them to bars drawn from the whole session
compares volatility, not skill. Two matched controls instead:

* **same bar, random side** — identical instant and volatility; isolates
  whether the rule knows *which way* price goes.
* **same day, random bar, same side** — identical session and direction;
  isolates whether it knows *when* to act.

### Result — first breakout of the day, 18 symbols, 132 signals

| Horizon | Signal | Same-bar control | Same-day control |
|---|---|---|---|
| 6 bars | **0.66** | 0.91 | 1.10 |
| 12 bars | **0.81** | 0.97 | 1.07 |
| 24 bars | **0.94** | 1.05 | 1.09 |

ORB loses to both controls at every horizon. Taking all breakouts rather than
the first (4,817 signals) does not rescue it: 0.95 / 1.00 / 0.96 against a
same-day control of 1.14 / 1.24 / 1.31, with p<0.001 in the wrong direction.

Long breakouts are the weaker half throughout (0.59 / 0.62 / 0.68).

### Two confounds checked before concluding

**Market drift.** Signals ran 92 short to 40 long, which would flatter shorts
for reasons unrelated to ORB. The window fell on 6 of 8 days (−0.23% average,
90 of 144 symbol-days down), so the skew is drift, not selection. Shorts did
score better than longs — and ORB still lost to its controls anyway.

**Independence.** 132 signals came from 8 days, and signals inside one day move
together, so the effective sample is closer to 8 than 132. Per-day MFE/MAE
ranged 0.44 to 1.55 (stdev 0.35) — the day-to-day swing is larger than any
effect measured. Re-running the permutation at the day level (shuffling whole
days, which both preserves within-day correlation and pairs signal against
control on the same day) gives **p = 0.040 for ORB being worse than a coin
flip**, versus p = 0.228 treating signals as independent.

### Verdict

The decision gate says reject, so ORB does not proceed to backtest. It is not
merely absent of edge — on this sample it is measurably worse than random
entry at the same instant.

**Confidence is limited by the window, not the method.** 8 trading days is thin
for a rule that fires once per symbol per day. This rejects ORB *as configured
on this sample*; it is not proof the pattern never works.

### The binding constraint is now data

`candle_store.MAX_BARS = 1500` and `BACKFILL_DAYS["5m"] = 10` cap 5-minute
history at ~20 trading days, and only 8 are currently stored. Both are
self-imposed — `GrowwClient.get_candles(days=...)` takes any window. Hypotheses
2 (Pullback Continuation) and 3 (Consolidation Breakout) are day-structure
rules that will hit exactly the same ceiling, so **extending history should
come before testing them**. Raising `MAX_BARS` also raises live memory for
every symbol and interval, so a separate research dataset on disk is the
cleaner option and does not touch the live path.

### Harness status

`hypothesis_lab.evaluate(data, generator)` now grades any hypothesis exposing
`generator(symbol, candles) -> [(bar_index, side)]`. 15 tests cover ORB range
construction, the per-day reset, the entry cutoff, the cost floor and the
permutation test. Suite: 46 passing.

---

## Step 4 — Historical Research Data Store

### What actually limited history

Three limits, only one of them the API's:

| Limit | Value | Whose | Effect |
|---|---|---|---|
| `candle_store` is **in-memory** | — | app | The real cause. Running under `--reload`, every code edit erased all history |
| `candle_store.MAX_BARS` | 1500 | app | ~20 trading days at 5m, per symbol/interval/source |
| `BACKFILL_DAYS["5m"]` | 10 | app | Fetch window for the chart's backfill |
| Groww max request window | **15 days** at 5m | **API** | 20-day windows are refused: "Invalid interval value" |
| Groww history depth | **starts 2026-06-01** | **API** | ~3 months rolling. Windows placed earlier return zero rows |

The store being in-memory was the binding constraint, and raising the other two
would not have fixed it. Measured, not assumed: `probe_max_history` walks
widening windows and then slides a known-legal window backwards, because "how
wide may one request be" and "how far back does data exist" are different
questions that produce the same error.

`GrowwClient.get_candles(days=...)` measures back from *now*, so old windows
were unreachable at any width. Added `get_candles_window(start, end)`;
`get_candles` now delegates to the same parser and behaves identically.

### Architecture

```
                         Groww API
                             |
              +--------------+--------------+
              |                             |
        LIVE PIPELINE                RESEARCH INGESTION
        tick loop, backfill          research/ingestion.py
              |                             |
        candle_store (RAM)           research.db (SQLite, disk)
        1500 bars, ephemeral         unbounded, durable
              |                             |
        SCANNER / CHART              HYPOTHESIS LAB
```

Isolation is enforced, not just intended: `research.db` is a separate file from
`trading.db`, the research package imports no live trading module (a test
asserts this), and tests pin `MAX_BARS == 1500` and `BACKFILL_DAYS["5m"] == 10`
so a future change cannot quietly "fix" research by growing the live process.

### Storage: SQLite, not Parquet

- `pyarrow` is not installed; `sqlite3` is stdlib and already this project's DB.
- Incremental append is the core requirement; Parquet files are immutable, so
  adding a day means rewriting a partition and hand-rolling dedupe. A primary
  key on `(symbol, interval, source, ts)` gives exact duplicate prevention.
- ~900k rows at full scale is trivial for SQLite.
- WAL mode is transactional; a partial Parquet rewrite on Windows is not.
- `read()` returns the live `OHLCV` type, so `entry_diagnostics`,
  `hypothesis_lab` and the backtester consume it with no adapter.

`research_coverage` records per-day fetch status, which is what makes a
backfill resumable and distinguishes *not fetched* from *fetched and genuinely
empty*. Without that distinction a holiday is indistinguishable from a gap and
gets re-requested forever.

### Dataset `research_5m_v1`

```
DATASET QUALITY REPORT
  Symbols:              39          Duplicate candles:    0
  Period:  2026-06-01 -> 2026-08-27 Missing intervals:    0
  Interval:             5m          Invalid OHLC rows:    0
  Trading days:         63          Invalid volume rows:  0
  Total candles:        184,261     Timestamp issues:     0
                                    Holidays detected:    1  (2026-06-26)
  STATUS: READY
```

63 trading days against the 8 previously available. TATAMOTORS returned no
data and is excluded rather than substituted. The holiday was inferred from the
cross-section — a weekday on which every symbol is absent — because the app has
no NSE holiday calendar and a hardcoded one would go stale silently.

### Re-test: both hypotheses still rejected, now decisively

Three-way chronological split by day: in-sample 2026-06-01..07-22 (37 days),
validation 07-23..08-07 (12), hold-out 08-10..08-27 (14). Significance by
day-level block permutation, which treats the trading day as the unit of
independence.

**EMA 9/21 crossover — 8,166 signals**

| Horizon | Signal | Same-bar control | Edge | p |
|---|---|---|---|---|
| 6 bars | 0.83 | 0.99 | −0.161 | 0.000 |
| 12 bars | 0.89 | 1.00 | −0.111 | 0.000 |
| 24 bars | 0.93 | 1.00 | −0.070 | 0.000 |

**ORB first breakout — 2,296 signals**

| Horizon | Signal | Same-bar control | Edge | p |
|---|---|---|---|---|
| 6 bars | 0.74 | 0.98 | −0.241 | 0.000 |
| 12 bars | 0.83 | 1.02 | −0.198 | 0.000 |
| 24 bars | 0.88 | 1.00 | −0.117 | 0.000 |

Hold-out edges: EMA −0.07 / −0.01 / +0.02; ORB −0.30 / −0.18 / −0.13. ORB stays
clearly negative out of sample; EMA converges towards the control without ever
beating it.

Two things the larger sample corrected:

* **The long/short asymmetry was drift, not structure.** The 8-day sample ran
  92 short to 40 long and shorts scored better. Balanced here (4,074 long,
  4,092 short) the two are identical — EMA 0.82 vs 0.84, ORB 0.76 vs 0.72.
* **The direction of the result is consistent and significant**, where before
  it was borderline. Both rules score *below* a same-bar coin flip at every
  horizon on 63 days.

That both are reliably worse than random is a fact about this dataset, not a
recipe: the inverse of a bad entry rule is not automatically a good one, since
MFE/MAE does not invert and the 0.183% cost floor applies either way. It is a
hypothesis to test properly, not a conclusion to trade.

### Is the dataset sufficient?

For these two rules, yes — the verdict is stable across all three splits at
p=0.000. For future work, partly: the unit of independence is the trading day,
so 63 days is enough to detect a large effect and thin for a small one. Groww's
~3-month rolling window means the dataset can only grow forward, about one day
per day, via `update()`. Testing anything needing a year of history, or several
market regimes, requires a different data source — not proposed here.

---

## Step 5 — Hypothesis Registry + H003 pre-registration

### Harness audit — the interface a new hypothesis must implement

| Component | Interface | Reuse for H003 |
|---|---|---|
| Generator | `generate(symbol, candles, params) -> [(bar_index, side)]` | implement exactly this |
| Scoring | `entry_diagnostics.observe(symbol, candles, i, side, horizon, ctx)` | unchanged |
| Context | `entry_diagnostics.context_series(candles, fast, slow)` | unchanged |
| Controls | `hypothesis_lab.build_controls(...) -> (same_bar, same_day)` | **needs one addition, see below** |
| Aggregation | `hypothesis_lab.Stats.of(observations)` | **needs one metric added** |
| Significance | `hypothesis_lab.block_permutation_p(sig_by_day, ctrl_by_day)` | unchanged |
| Split | `hypothesis_lab.split_days(days, (0.6, 0.2, 0.2))` | unchanged |
| Data | `research.store.read_many(symbols, "5m", "live")` -> `list[OHLCV]` | unchanged |

**One defect found.** `hypothesis_lab.evaluate()` still uses the per-signal
`permutation_p` and a two-way `split=0.7`. The EMA/ORB re-test did not go
through it — it used `block_permutation_p` and the three-way split directly.
Anyone calling `evaluate()` would get the weaker test we already showed
understates dependence. It should be aligned before H003 runs. Left in place
for now rather than changed mid-task.

### H003 v1 — Pullback Continuation

**Market behaviour claimed.** When a stock makes a clean directional move, some
participants take profit and some fade it, producing a counter-move. If the
original move was driven by real demand rather than noise, that demand should
still be present once the counter-move exhausts. The claim is that the moment
demand reasserts itself — after the pullback, not during it — is a better
located entry than an arbitrary moment in the same session.

The claim is specific and falsifiable: it says the *pullback structure* carries
information over and above simply being in a stock that is already trending.

**Why this is not EMA crossover with filters.** A crossover compares two
smoothed averages of price. Nothing in H003 uses a moving average or a crossing.
The impulse is measured as raw displacement normalised by ATR, and its quality
by the efficiency ratio — path-based measures with no smoothing. The trigger is
a break of a specific prior bar's high, a price-structure event rather than a
state comparison. A crossover fires when two averages change relative order;
H003 fires when price exceeds a level a specific earlier bar established. In a
sustained trend the crossover fires once, while H003 can fire repeatedly.

#### Long rule

```
Let L = 12 bars, K = 1.0, E = 0.50, P_max = 6 bars
All quantities computed from bars <= t only.

IMPULSE
  C1  H0 = max(high[t-L .. t]),  L0 = min(low[t-L .. t])
  C2  (close[idx(H0)] - close[t-L]) / ATR(14)[t] >= K       impulse >= 1 ATR
  C3  efficiency = |close[t] - close[t-L]|
                   / sum(|close[i] - close[i-1]|, i in t-L+1..t)  >= E
  C4  idx(H0) != t                              the high is already behind us

PULLBACK
  R = H0 - L0                                               impulse range
  C5  p_bars = t - idx(H0),   1 <= p_bars <= P_max
  C6  retrace[t] = (H0 - low[t]) / R,   depth_min <= retrace[t] <= depth_max
  C7  max(retrace[i]) for i in (idx(H0), t] <= depth_max
        the pullback never exceeded the band; deeper is a reversal

TRIGGER
  C8  close[t] > high[t-1]

  Signal: BUY at bar t.
```

#### Short rule (mirrored)

```
IMPULSE
  C1  L0 = min(low[t-L .. t]),  H0 = max(high[t-L .. t])
  C2  (close[t-L] - close[idx(L0)]) / ATR(14)[t] >= K
  C3  efficiency >= E                                       (same formula)
  C4  idx(L0) != t

PULLBACK
  R = H0 - L0
  C5  p_bars = t - idx(L0),   1 <= p_bars <= P_max
  C6  retrace[t] = (high[t] - L0) / R,   depth_min <= retrace[t] <= depth_max
  C7  max(retrace[i]) for i in (idx(L0), t] <= depth_max

TRIGGER
  C8  close[t] < low[t-1]

  Signal: SELL at bar t.
```

**Why each condition exists**

| | Purpose | Behaviour represented |
|---|---|---|
| C2 | size the move against the instrument's own volatility | a move large enough to reflect intent, comparable across a Rs 100 and a Rs 3000 stock |
| C3 | require the move be directional, not a round trip | the efficiency ratio separates a trend from chop that happened to end higher; ADX approximates this, efficiency measures it directly from price |
| C4 | the impulse must be complete | without it the "pullback" could still be forming |
| C5 | bound the pause | beyond ~30 minutes the move has stopped being a pullback and become a new range |
| C6 | define depth explicitly | shallow and deep retracements are different behaviours, tested separately |
| C7 | reject broken impulses | a retrace that went too deep and recovered is a reversal, not a continuation |
| C8 | require resumption, observable at the close of t | the entry event itself |

**No look-ahead.** Every term reads bars with index <= t: `H0`/`L0` from the
window ending at t, ATR and efficiency from closes up to t, `high[t-1]` from the
previous bar, `close[t]` from the signal bar's own close — the convention H001
and H002 used. The forward window begins after t. To be pinned by the same
truncation test already applied to the EMA engine
(`test_no_lookahead_truncating_future_bars_changes_nothing`): evaluating bar t
with all future bars removed must give an identical answer.

**Fixed parameters, chosen before testing and not searched**

| Param | Value | Reason chosen a priori |
|---|---|---|
| L | 12 bars | one hour at 5m — long enough to contain a move, short enough to stay intraday |
| K | 1.0 ATR | one average day-range unit; the natural scale-free "this move is real" threshold |
| E | 0.50 | half of all movement was net directional — the midpoint of the ratio's range, not a tuned value |
| P_max | 6 bars | 30 minutes; beyond this the pause is a range |
| ATR period | 14 | already the project default everywhere |

### Pre-registered variants — two, not three

| Variant | depth_min | depth_max | Behaviour |
|---|---|---|---|
| **H003-A** | 0.20 | 0.40 | shallow. Trend so strong buyers barely let go; continuation is the base case |
| **H003-B** | 0.40 | 0.65 | medium. Textbook two-way pullback: real profit-taking, then demand returns |

Depth is the single parameter varied, because it is the only one where two
genuinely distinct market behaviours exist rather than two settings of the same
behaviour. A shallow retrace and a medium retrace say different things about who
is in control.

**Why no third variant.** A deep band (0.65–0.85) is not a pullback hypothesis —
at that depth the impulse is nearly erased and the setup is closer to a
reversal, a separate claim deserving its own hypothesis ID rather than a slot
here. Adding it would also raise the comparison count from 6 to 9 and make an
accidental pass more likely. Every other parameter is fixed at one value, so the
total pre-registered comparison count is 2 variants × 3 horizons = **6**.

### Evaluation plan

**Horizons** — 6, 12, 24 bars, unchanged from H001/H002 so results stay directly
comparable.

**Metrics**

| Metric | Role |
|---|---|
| `net_move_pct` = mean of `(close[t+h] - close[t]) * dir / close[t] * 100` | **primary.** Directly answers "did price move further in the predicted direction than against it" |
| MFE, MAE, MFE/MAE | secondary, for comparability with H001/H002 |
| `favourable_pct` (MFE > MAE) | secondary |
| `clears_cost_pct` (MFE > cost floor) | economic context, not a first gate |

`net_move_pct` is promoted to primary because MFE/MAE proved to be a property of
the entry *and the measurement window* — it moved from 0.83 to 1.08 on one
unchanged signal set purely by widening the horizon. Net displacement has no
such failure mode. This adds one field to `Stats`; no existing metric is removed.

**Controls**

| Control | Definition | Question |
|---|---|---|
| **A** same bar, random side | unchanged | does it know *which direction*? |
| **B** same day, random bar, same side | unchanged | does it know *when*? |
| **C** same day, same side, random bar **drawn only from bars passing C1–C4** | new | does the *pullback structure* add anything beyond being in a trending stock? |

Control C is the addition H003 needs and H001/H002 did not. H003 is a compound
claim — impulse selection plus pullback structure — and A and B together cannot
separate them. Without C, a pass could mean nothing more than "trending stocks
drift", which is a known effect and not this hypothesis. C holds the impulse
condition constant and varies only the pullback and the trigger.

**Statistics** — day-level block permutation, unit of dependence = the trading
day. Justified twice over: signals within a day share serial correlation, and
across 39 symbols they share a market-wide move, so a day-block captures both at
once. Demonstrated empirically on the 8-day ORB sample, where the correct unit
moved p from 0.228 to 0.040. Fixed in advance; it will not be changed after
seeing results.

**Split** — reuse 60/20/20 chronological by day: development 2026-06-01..07-22
(37 days), validation 07-23..08-07 (12), hold-out 08-10..08-27 (14). Split by
day, never by row. The hold-out is not read until gates 1–3 have been decided.
Stated limitation: 14 hold-out days is thin, so hold-out evidence can confirm a
reversal but cannot by itself confirm a small positive edge.

### Gates, fixed before implementation

| Gate | Criterion | Failure |
|---|---|---|
| **0 — power** | >= 300 signals and >= 25 distinct signal-days in development | `UNDERPOWERED`, not REJECTED — an untested claim, not a refuted one |
| **1 — direction** | `net_move_pct` edge over Control A > 0 at >= 2 of 3 horizons, with block-permutation p < 0.05 at >= 1 | REJECTED |
| **2 — structure** | edge over Control C > 0 at the horizons that passed Gate 1 | REJECTED — the information is in impulse selection, not the pullback |
| **3 — validation** | sign of the edge preserved in the validation period at those horizons | REJECTED |
| **4 — hold-out** | sign preserved and edge >= 0 in the hold-out | REJECTED. A reversal is a rejection, not noise to explain away |
| **5 — economic** | mean MFE at a qualifying horizon > the 0.183% cost floor | `PROMISING` but flagged untradeable; not a rejection of the information claim |
| **6 — reporting** | both variants reported whatever the outcome; "SIGNIFICANT" claimed only at p < 0.05/6 = 0.008 | — |

Passing all gates sets status `ACCEPTED`, meaning only that it cleared the gates
defined here — not that it is safe to trade.

If H003 fails it is recorded REJECTED with its numbers, and the next independent
hypothesis begins. No filters will be added to make it pass; a changed rule is
H003 v2, registered separately, with v1's verdict intact.

---

## Step 5b — H003 v1 pre-registration, revised after design review

Revised while still `PROPOSED` and before any data was touched, so this is
pre-registration being done properly rather than a rule being edited to fit a
result. Once the status moves to `DEFINED`, any change becomes v2.

### Fix 1 — the ordered-swing contamination was real

The review is correct, and the failure mode is worse than imprecision. With

```
H0 = max(high[t-L..t]),  L0 = min(low[t-L..t])
```

the two extremes are found independently, so `L0` may occur *after* `H0` — in
which case `L0` is the pullback's own low. The denominator
`R = H0 - L0` then measures the pullback, and

```
retrace = (H0 - low[t]) / (H0 - L0)
```

has the pullback on both sides of the division. It lands near 1.0 when the
current bar is the pullback low, and drifts through the 0.20-0.65 band as the
pullback bounces — so a directionless zig-zag can satisfy the depth condition
by construction. That is not a weak filter; it is a broken measurement.

#### Ordered impulse identification (causal, no look-ahead)

Anchor on the high first, then search for the low only in the bars *preceding*
it. The ordering is then structural rather than something to check afterwards.

```
For a LONG candidate at bar t, using only bars <= t:

  1. h_idx = argmax(high[i])  for i in [t-L, t-1]
             ties -> earliest index (deterministic)
             t excluded: the impulse high must already be behind us

  2. l_idx = argmin(low[i])   for i in [t-L, h_idx]
             search bounded ABOVE by h_idx, so l_idx <= h_idx by construction

  3. require l_idx < h_idx                       at least one bar of advance

  4. impulse_low  = low[l_idx]
     impulse_high = high[h_idx]
     R            = impulse_high - impulse_low
     impulse_bars = h_idx - l_idx

  5. pullback window = bars (h_idx, t]
     p_bars = t - h_idx
```

SHORT mirrors exactly: `l_idx = argmin(low[i])` over `[t-L, t-1]`, then
`h_idx = argmax(high[i])` over `[t-L, l_idx]`, requiring `h_idx < l_idx`.

Every index and value comes from bars at or before t. The identified swing pair
may *change* as t advances — a new higher high replaces the old one — and that
is correct behaviour, not instability: each bar's evaluation is independent and
uses only its own past. The truncation test
(`test_no_lookahead_truncating_future_bars_changes_nothing`) will pin it.

### Fix 2 — parameter rationale, including one that was wrong

**K = 1.0 ATR was badly wrong and is withdrawn.**

ATR(14) on 5-minute bars is the average *5-minute* true range. Requiring an
impulse of 1 ATR therefore asked for a move the size of one ordinary bar —
roughly 0.1% on these symbols, against a median 12-bar MFE of 0.33%. It would
have admitted almost everything and the "impulse" stage would have been
decorative. Caught by doing the arithmetic the review asked for.

A principled replacement: under a driftless random walk, net displacement over
N bars has standard deviation `sigma * sqrt(N)`. With N=12 that is `3.46*sigma`,
and ATR exceeds per-bar sigma by roughly 1.2x, so 12-bar displacement has
SD ~= 2.9 ATR. Setting **K = 2.5 ATR** puts the threshold at about 0.85 SD,
selecting roughly the top fifth of 12-bar moves by directional displacement —
selective enough to mean something, loose enough to clear Gate 0.

| Param | Value | Market behaviour | Basis |
|---|---|---|---|
| **L** | 12 bars (1h) | the window a complete impulse-plus-pullback must fit inside | **Partly derived.** Floor is structural: impulse needs >= 2 bars and the pullback >= 1, so L must exceed their sum with margin. Ceiling is that a structure taking over an hour leaves little session to resolve in. 12 sits above the floor; 10 or 15 would also be defensible. **Exploratory within a derived range.** |
| **K** | 2.5 ATR | "this move is larger than a random walk usually produces over this window" | **Reasoned, not fitted.** ~0.85 SD of 12-bar displacement under a driftless random walk. Approximate: the ATR-to-sigma factor is taken as ~1.2 rather than measured. |
| **depth band** | 0.20-0.40 / 0.40-0.65 | shallow vs medium retracement | **Exploratory, and the variant axis.** Conventional retracement zones. Held apart deliberately because they describe different behaviours. |
| **ATR period** | 14 | — | **Inherited convention** — the project default everywhere. Not independently justified here. |
| ~~E = 0.50~~ | **removed** | — | see below |
| ~~P_max = 6~~ | **replaced** | — | see below |

**E (efficiency ratio) is removed from v1.** It had a real justification — a
driftless random walk's expected efficiency over N bars is exactly `1/sqrt(N)`,
so 0.29 at N=12, and 0.50 is about 1.7x that. But it constrains the *same*
thing K does: both ask "is this impulse real". Keeping both means a failure
cannot be attributed and a pass cannot be decomposed, which is exactly the
review's objection. With K now set at a defensible level, E is redundant.
Recorded as a candidate for v2 if v1 shows the impulse stage matters but is
noisy.

**P_max = 6 is replaced by a relationship, removing a free parameter.**
A fixed 30-minute cap was the weakest number in the original set — genuinely
arbitrary. Instead:

```
p_bars <= impulse_bars
```

The pause must not outlast the move that produced it. This is the actual
structural claim ("a pullback is an interruption of a move, not a new range"),
it needs no chosen constant, and L=12 bounds both terms automatically since
they share one window.

### Fix 3 — simplified to four stages, six conditions, three parameters

Down from eight conditions and five parameters.

#### LONG rule

```
Parameters: L = 12, K = 2.5, (depth_min, depth_max) per variant

STAGE A — ordered directional impulse
  A1  h_idx = argmax(high[i]), i in [t-L, t-1]
      l_idx = argmin(low[i]),  i in [t-L, h_idx]
      require l_idx < h_idx
  A2  (impulse_high - impulse_low) / ATR(14)[t] >= K

      R = impulse_high - impulse_low
      impulse_bars = h_idx - l_idx

STAGE B — defined pullback
  B1  p_bars = t - h_idx,   1 <= p_bars <= impulse_bars
  B2  retrace[t] = (impulse_high - low[t]) / R
      depth_min <= retrace[t] <= depth_max

STAGE C — pullback still structurally valid
  C1  min(low[i]) for i in (h_idx, t]  >  impulse_low

STAGE D — continuation trigger
  D1  close[t] > high[t-1]

  -> BUY at bar t
```

#### SHORT rule (exact mirror)

```
STAGE A
  A1  l_idx = argmin(low[i]),  i in [t-L, t-1]
      h_idx = argmax(high[i]), i in [t-L, l_idx]
      require h_idx < l_idx
  A2  (impulse_high - impulse_low) / ATR(14)[t] >= K

      R = impulse_high - impulse_low
      impulse_bars = l_idx - h_idx

STAGE B
  B1  p_bars = t - l_idx,   1 <= p_bars <= impulse_bars
  B2  retrace[t] = (high[t] - impulse_low) / R
      depth_min <= retrace[t] <= depth_max

STAGE C
  C1  max(high[i]) for i in (l_idx, t]  <  impulse_high

STAGE D
  D1  close[t] < low[t-1]

  -> SELL at bar t
```

**On Stage C being redundant.** With `depth_max <= 0.65`, B2 already implies the
pullback stayed above the impulse low. It is stated anyway, as the review asked:
it documents the structural intent, and it stops a future widening of the depth
band from silently admitting broken impulses. Redundant-but-explicit is the
right trade here.

**What was removed:** the efficiency condition (redundant with K), the fixed
`P_max` (replaced by `p_bars <= impulse_bars`), and the separate "max retrace
over the pullback" check (subsumed by Stage C). No moving average, RSI, MACD,
ADX, Supertrend or volume condition appears anywhere.

### Fix 4 — Control C, stated precisely

| | Held constant | Randomised |
|---|---|---|
| **Control A** | bar, symbol, day | direction |
| **Control B** | day, symbol, direction | bar (any bar in the session) |
| **Control C** | day, symbol, direction, **Stage A passing** | bar (any bar that same day where Stage A passes), and therefore Stages B, C and D |

Control C draws from bars where the ordered-impulse test passes but the
pullback depth, the pullback duration and the continuation trigger are **not**
required. It answers precisely: does the pullback-and-resumption structure add
information beyond having selected a stock that has just made a directional
move?

Two implementation notes fixed now rather than improvised later:

* If a signal's day has fewer than 2 other Stage-A bars, that signal
  contributes no Control C observation and is **skipped**, not substituted from
  another day — substituting would break the day-level pairing the block
  permutation depends on.
* The Stage-A pool is computed per (symbol, day) once, before sampling, so the
  control population does not depend on which signals fired.

### Fix 5 — Gate 5 reworded, and demoted

Accepted without reservation. MFE is a best-case excursion measured with
perfect hindsight about where the favourable extreme fell; no real exit
captures it. Treating `MFE > cost floor` as evidence of tradeability would
reintroduce exactly the flattery the backtester was built to avoid.

Gate 5 is therefore no longer a gate. It becomes a **label**:

> **Movement sufficiency (label, not a gate).** If mean MFE at a qualifying
> horizon exceeds the 0.183% round-trip cost floor, record
> `movement potentially sufficient for further economic testing`. This is a
> statement about available range, not about profitability. Economic viability
> is a separate question requiring an explicit entry, exit, stop, slippage and
> charge model — the existing `backtester.py`, run only if the directional
> gates pass first.

### Revised gates

| Gate | Criterion | Failure |
|---|---|---|
| **0 — power** | >= 300 signals and >= 25 distinct signal-days in development | `UNDERPOWERED` — untested, not refuted |
| **1 — direction** | `net_move_pct` edge over Control A > 0 at >= 2 of 3 horizons, block-permutation p < 0.05 at >= 1 | REJECTED |
| **2 — structure** | edge over Control C > 0 at the horizons that passed Gate 1 | REJECTED — information is in impulse selection, not the pullback |
| **3 — validation** | sign of the edge preserved in validation at those horizons | REJECTED |
| **4 — hold-out** | sign preserved and edge >= 0 in the hold-out | REJECTED. A reversal is a rejection |
| **5 — reporting** | both variants reported whatever the outcome; "SIGNIFICANT" only at p < 0.05/6 = 0.008 | — |
| **label** | mean MFE > 0.183% -> "movement potentially sufficient for further economic testing" | not a gate |

Unchanged: horizons 6/12/24; `net_move_pct` primary with MFE/MAE secondary;
day-level block permutation with the trading day as the unit of dependence;
60/20/20 chronological split by day (dev 37 days, validation 12, hold-out 14),
hold-out unread until gates 1-3 are decided.

### One open question for review

K = 2.5 rests on an approximation (ATR ~ 1.2x per-bar sigma). It can be set
exactly instead, by measuring the distribution of the impulse statistic across
the development period and choosing K at a stated percentile — **using only the
predictor's own distribution, never forward returns**. That is calibration on
inputs, not selection on outcomes, so it cannot bias the test; it would replace
an approximation with a measurement. Not done unilaterally, since it changes a
pre-registered value.

---

## Step 5c — Input-only calibration of K

Run on the **development period only** (37 days, 2026-06-01..2026-07-22).
Validation and hold-out were not read. The calibration script imports
`find_impulse`, `atr`, `store` and `split_days` and nothing else — no
`measure_forward`, no `observe`, no `Stats`, no backtester, no paper engine. No
signals were generated and no threshold was applied during measurement.

### 1. Global impulse_score distribution

`impulse_score = (impulse_high - impulse_low) / ATR(14)`, over every causal
ordered impulse opportunity.

| n | mean | median | p25 | p50 | p60 | p70 | **p75** | p80 | p90 |
|---|---|---|---|---|---|---|---|---|---|
| 134,365 | 2.84 | 2.67 | 1.96 | 2.67 | 2.97 | 3.31 | **3.52** | 3.75 | 4.46 |

Population accounting:

| | count | share |
|---|---|---|
| bars in development period | 108,225 | |
| skipped, lookback crosses a session boundary | 17,316 | 16.0% |
| eligible bars with no ordered pair in either direction | 656 | 0.6% |
| impulse opportunities measured (UP + DOWN per bar) | 134,365 | |

The 16% exclusion is the first 12 bars of each session, where a full same-day
lookback does not exist. That is the intended behaviour, not data loss.

### 2. K = 2.5 was still too permissive — and the reasoning behind it was wrong

**Measured, K = 2.5 retains 56.2% of impulse opportunities.** It is close to the
median (2.67), not the upper quartile.

The error was in the statistic, not the arithmetic. The random-walk argument
computed the standard deviation of *net displacement* over 12 bars
(`sigma * sqrt(N)`), but `impulse_score` measures the *range between ordered
swing extremes*, which is systematically larger — a random walk's expected range
is about `1.6 * sigma * sqrt(N)`, roughly 1.6x the displacement SD. Comparing a
range threshold against a displacement distribution understated it by about that
factor, which is close to the observed gap between 2.5 and 3.5.

This is the second time a reasoned value for K came out too permissive, in the
same direction. The measured distribution replaces the reasoning rather than
supplementing it.

### 3. Long vs short

| Population | n | mean | median | p75 |
|---|---|---|---|---|
| UP (long candidates) | 66,100 | 2.84 | 2.65 | 3.51 |
| DOWN (short candidates) | 68,265 | 2.84 | 2.69 | 3.52 |

p75 differs by **0.007**, or 0.2%. There is no basis for separate long and short
thresholds, and the symmetry is a mild check that the mirrored algorithm is
genuinely mirrored.

### 4. Cross-symbol

Per-symbol p75 across the 39 symbols:

| min | median | max | stdev | spread as share of global p75 |
|---|---|---|---|---|
| 3.33 | 3.53 | 3.70 | 0.102 | **10.5%** |

Lowest: HCLTECH 3.33, WIPRO 3.34, INFY 3.35, SUNPHARMA 3.36, ICICIBANK 3.38.
Highest: AXISBANK 3.66, INDUSINDBK 3.66, COALINDIA 3.68, MARUTI 3.70,
NESTLEIND 3.70.

A single global threshold is **not** grossly inappropriate. The whole
cross-symbol range spans about a tenth of the threshold's own value, and the
ordering is unsurprising — large-cap IT names produce the smoothest paths. ATR
normalisation is evidently doing its job, since these are stocks whose absolute
prices differ by more than an order of magnitude. Per-symbol thresholds are not
proposed, and would add 39 free parameters to buy a 10% adjustment.

### 5. Candidate K

Under the pre-agreed conceptual definition — *an impulse is a move in the upper
quartile of causal impulse opportunities* — the value is the measured p75:

```
exact p75 = 3.5186
```

| K | retained | count (dev) | per dev day |
|---|---|---|---|
| 2.50 | 56.2% | 75,506 | 2,041 |
| 3.00 | 39.0% | 52,399 | 1,416 |
| 3.25 | 31.6% | 42,405 | 1,146 |
| **3.50** | **25.4%** | **34,159** | **923** |
| 3.52 | 25.0% | 33,543 | 907 |
| 3.75 | 20.1% | 26,992 | 730 |
| 4.00 | 15.8% | 21,276 | 575 |

**Proposed: K = 3.5**, the measured p75 rounded to two significant figures. It
retains 25.4% against the definition's 25.0%; the 0.4 percentage-point
difference is far smaller than the sampling noise in a percentile estimated from
heavily overlapping windows, and a round number is less likely to be mistaken
for a fitted one. **Not committed** pending review.

Two cautions on these numbers:

* Consecutive bars share 12-bar windows, so the 134,365 observations are far
  from independent. The percentile is a reliable description of this
  development period; no confidence interval should be attached to it.
* Every one of the 37 development days contains at least one Stage-A pass at
  K=3.5, so Gate 0's 25-signal-day requirement has headroom at the impulse
  stage. The eventual *signal* count is unknown — Stages B, C and D will cut
  34,159 Stage-A passes down substantially — and cannot be known without
  generating signals, which was not done.

### 6. Non-repainting test result

`tests/test_impulse_structure.py` — 16 tests, all passing. The decisive ones:

| Test | Guarantee |
|---|---|
| `test_truncating_the_future_does_not_change_the_structure` | for every bar and both directions, evaluating bar t with all later bars deleted gives an identical `Impulse` |
| `test_appending_future_bars_does_not_change_an_earlier_structure` | the same guarantee approached from the other side |
| `test_structure_is_allowed_to_differ_between_different_bars` | stops the two above passing trivially on a function that always returns None |
| `test_low_is_always_before_high_for_an_up_impulse` | a late crash low is not adopted as the impulse low |
| `test_window_may_not_cross_into_the_previous_session` | no overnight gap inside an impulse |

The existing `test_no_lookahead_truncating_future_bars_changes_nothing` covers
the EMA engine only, so this is a separate suite rather than an extension.

### 7. Clarified pullback and trigger timing

The trigger bar is no longer counted as a pullback bar, and the pullback depth
no longer reads the trigger bar's low.

```
LONG
  impulse:        low_idx  ->  high_idx          (low_idx < high_idx)
  pullback bars:  high_idx + 1  ..  t - 1
  trigger bar:    t

  pullback_bar_count = t - high_idx - 1          >= 1, so t >= high_idx + 2
  pullback_low       = min(low[i]) for i in [high_idx + 1, t - 1]
  retrace            = (impulse_high - pullback_low) / R
  structural check   = pullback_low > impulse_low
  trigger            = close[t] > high[t-1]

SHORT (mirror)
  impulse:        high_idx ->  low_idx           (high_idx < low_idx)
  pullback bars:  low_idx + 1  ..  t - 1
  trigger bar:    t

  pullback_bar_count = t - low_idx - 1           >= 1
  pullback_high      = max(high[i]) for i in [low_idx + 1, t - 1]
  retrace            = (pullback_high - impulse_low) / R
  structural check   = pullback_high < impulse_high
  trigger            = close[t] < low[t-1]
```

This changes the earlier draft in one substantive way: retrace is measured from
the completed pullback bars, not from the trigger bar. Previously
`retrace[t] = (impulse_high - low[t]) / R` used bar t's own low, which meant the
trigger bar was simultaneously the last pullback bar and the resumption bar. The
duration bound is unchanged in intent — `pullback_bar_count <= impulse_bars` —
but now counts an unambiguous set of bars.

`pullback_bar_count` and the timing are implemented and tested in
`app/research/impulse_structure.py`; the depth and trigger stages are specified
here but not implemented.

---

## Step 6 — H003 v1 evaluated: **REJECTED**, both variants

Rule frozen before implementation (Step 5b/5c). Registry status
`DEFINED` -> `REJECTED`. Rule definition unchanged.

### Signal anatomy (diagnostic only — not used as a filter)

| | H003-A shallow [0.20, 0.40) | H003-B medium [0.40, 0.65] |
|---|---|---|
| total signals | 2,571 | 1,536 |
| signal-days | 63 of 63 | 63 of 63 |
| long / short | 1,231 / 1,340 | 796 / 740 |
| symbols firing | 39 of 39 | 39 of 39 |
| signals per day | min 19, median 38, max 72 | min 7, median 24, max 45 |
| signals per symbol | min 47 (ICICIBANK), median 66, max 89 (TATASTEEL) | min 25 (ITC), median 38, max 61 (MARUTI) |
| impulse_bars | min 1, median 8, max 10 | min 1, median 7, max 10 |
| retracement depth | min 0.200, median 0.292, max 0.400 | min 0.400, median 0.483, max 0.650 |
| impulse_score | min 3.50, median 4.26, max 9.16 | min 3.50, median 3.99, max 8.18 |

Pullback duration:

| bars | H003-A | H003-B |
|---|---|---|
| 1 | 822 (32.0%) | 142 (9.2%) |
| 2 | 746 (29.0%) | 307 (20.0%) |
| 3 | 542 (21.1%) | 402 (26.2%) |
| 4+ | 461 (17.9%) | 685 (44.6%) |

The variants separate on duration as well as depth, which was not designed in:
shallow retracements resolve in 1-2 bars 61% of the time, medium ones take 4+
bars 45% of the time. Both distributions are broad rather than piled against a
boundary, so neither band is being defined by its own edge. Long/short balance
is close to even for both, unlike the 8-day ORB sample where drift produced a
3:1 skew.

### Development results — primary metric `net_move_pct`

Development period only (37 days, 2026-06-01..07-22), day-level block
permutation on a difference of means.

| Variant | h | n | edge vs **Control A** | p | edge vs **Control C** | p |
|---|---|---|---|---|---|---|
| **A** | 6 | 1,447 | **+0.0192** | **0.0037** | **−0.0325** | **0.0010** |
| A | 12 | 1,305 | +0.0107 | 0.4375 | −0.0301 | 0.1150 |
| A | 24 | 1,010 | +0.0357 | 0.1737 | −0.0243 | 0.4392 |
| **B** | 6 | 888 | −0.0121 | 0.2975 | −0.0220 | 0.1118 |
| B | 12 | 791 | −0.0118 | 0.4657 | −0.0344 | 0.1403 |
| B | 24 | 612 | −0.0142 | 0.5357 | −0.0100 | 0.7695 |

Full-sample group means (all 63 days), for context:

| Variant | h | SIGNAL | Control A | Control B | Control C | MFE% | MAE% | ratio |
|---|---|---|---|---|---|---|---|---|
| A | 6 | 0.0106 | 0.0062 | 0.0588 | 0.0456 | 0.243 | 0.269 | 0.90 |
| A | 12 | 0.0074 | −0.0071 | 0.1328 | 0.0441 | 0.332 | 0.349 | 0.95 |
| A | 24 | 0.0090 | 0.0114 | 0.2462 | 0.0267 | 0.452 | 0.467 | 0.97 |
| B | 6 | 0.0057 | −0.0020 | 0.0384 | 0.0349 | 0.239 | 0.270 | 0.88 |
| B | 12 | 0.0014 | 0.0035 | 0.0932 | 0.0355 | 0.325 | 0.347 | 0.93 |
| B | 24 | −0.0159 | 0.0012 | 0.2080 | −0.0098 | 0.429 | 0.468 | 0.92 |

### Validation and hold-out

Reported for completeness; gates 3 and 4 were never reached.

| Variant | h | dev edge vs A | validation | HOLD-OUT |
|---|---|---|---|---|
| A | 6 | +0.0132 | −0.0257 | +0.0040 |
| A | 12 | +0.0214 | +0.0073 | −0.0002 |
| A | 24 | +0.0104 | −0.0305 | −0.0142 |
| B | 6 | +0.0031 | +0.0235 | +0.0085 |
| B | 12 | −0.0208 | +0.0392 | +0.0286 |
| B | 24 | −0.0227 | +0.0325 | −0.0460 |

### Gate-by-gate verdict

| Gate | H003-A | H003-B |
|---|---|---|
| **0 — power** (>=300 signals, >=25 signal-days in dev) | **PASS** 1,447 / 1,305 / 1,010 signals, 37 days | **PASS** 888 / 791 / 612 signals, 37 days |
| **1 — direction vs Control A** (edge > 0 at >=2 of 3 horizons, p < 0.05 at >=1) | **PASS** 3 of 3 positive; p = 0.0037 at 6 bars | **FAIL** 0 of 3 positive |
| **2 — structure vs Control C** (edge > 0 at the horizons that passed Gate 1) | **FAIL** negative at all 3; p = 0.0010 at 6 bars, i.e. significantly *worse* | not reached |
| 3 — validation | not reached | not reached |
| 4 — hold-out | not reached | not reached |
| label — movement | mean MFE 0.24-0.45% exceeds the 0.183% floor -> *movement potentially sufficient for further economic testing*. Not evidence of tradeability | same |

**H003 v1 -> REJECTED.**

### What the result actually says

Variant A is the first hypothesis in this project to pass the direction test.
Against a same-bar random-side control it is positive at all three horizons and
significant at 6 bars (p = 0.0037, below the 0.008 multiple-comparison
threshold). H001 and H002 were both *negative* against that control.

Control C is what kills it. Against a random stage-A bar — same day, same
symbol, same direction, impulse condition already satisfied — variant A is
negative at every horizon and significantly negative at 6 bars (p = 0.0010).
The directional information is in the impulse selection. The pullback and
continuation structure, which is what H003 actually proposed, subtracts from it.

That is precisely the decomposition Control C was added to make, and it is the
reason the hypothesis can be rejected cleanly rather than being recorded as a
weak positive. Without Control C, variant A's Gate 1 pass would have looked like
a discovery.

Variant B fails earlier and more simply: no directional edge at all, negative
against Control A at every horizon.

### Caveats, stated with the result rather than after it

* **Magnitude.** The largest development edge is +0.036 percentage points, about
  3.6 basis points, against a round-trip cost floor of 18.3 bp. Even taken at
  face value the effect is an order of magnitude too small to trade.
* **Estimation noise.** Re-drawing the control sample moved edge estimates by
  roughly 2 bp — the same order as the edges themselves. Individual cells in
  these tables should not be read as precise.
* **Control C position bias.** Signals fire on trigger bars, while the stage-A
  pool spans the session, and a signal is dropped when its horizon would cross
  the close. That asymmetry could favour Control C. The rejection does not rest
  on it: variant B fails Gate 1 on Control A, which is same-bar and carries no
  positional bias, and variant A's Control A edge is 2-4 bp either way.
* **Control B is not comparable across horizons.** Its mean rises steeply with
  horizon (0.06 -> 0.13 -> 0.25 for variant A) because random bars sit earlier
  in the session on average and capture more of the day's drift. It is reported
  because it was pre-registered, but Controls A and C are the ones the gates use.
* Scope: 39 large-cap NSE symbols, 5-minute bars, 63 trading days, one market
  regime. This rejects H003 v1 on this dataset and configuration.

No filters were added, no parameters re-tuned, and the rule was not modified
after results were visible. Any revision must be registered as H003 v2 with v1
preserved.

---

## Step 7 — Control C infrastructure review

**H003 v1 remains REJECTED.** Its pre-registered rule, gates and recorded
results are unchanged. Everything below Task 2 is labelled sensitivity analysis
and has no bearing on that verdict.

### Task 2 — Control C time-of-day audit (development period)

Distribution of signals and their original Control C samples across 30-minute
session buckets, with mean `net_move_pct` at the 6-bar horizon.

**H003-A**

| bucket | signals | sig % | ctrl C | ctl % | sig move% | ctl move% |
|---|---|---|---|---|---|---|
| 10:15-10:45 | 201 | 12.5% | 854 | 13.4% | 0.0028 | 0.0558 |
| 10:45-11:15 | 171 | 10.7% | 597 | 9.3% | −0.0292 | 0.0062 |
| 11:15-11:45 | 141 | 8.8% | 511 | 8.0% | 0.0046 | 0.0168 |
| 11:45-12:15 | 153 | 9.5% | 553 | 8.6% | 0.0297 | 0.0168 |
| 12:15-12:45 | 119 | 7.4% | 584 | 9.1% | 0.0136 | 0.0465 |
| 12:45-13:15 | 149 | 9.3% | 567 | 8.9% | 0.0124 | 0.0734 |
| 13:15-13:45 | 146 | 9.1% | 615 | 9.6% | 0.0184 | 0.0551 |
| 13:45-14:15 | 161 | 10.0% | 600 | 9.4% | 0.0552 | 0.0667 |
| 14:15-14:45 | 152 | 9.5% | 552 | 8.6% | 0.0562 | 0.0840 |
| 14:45-15:15 | 123 | 7.7% | 599 | 9.4% | −0.0180 | 0.0322 |
| 15:15-15:45 | 89 | 5.5% | 364 | 5.7% | n/a | n/a |

**H003-B**

| bucket | signals | sig % | ctrl C | ctl % | sig move% | ctl move% |
|---|---|---|---|---|---|---|
| 10:15-10:45 | 137 | 13.7% | 517 | 13.0% | 0.0414 | 0.0872 |
| 10:45-11:15 | 82 | 8.2% | 364 | 9.2% | 0.0216 | 0.0012 |
| 11:15-11:45 | 98 | 9.8% | 331 | 8.3% | −0.0127 | 0.0349 |
| 11:45-12:15 | 66 | 6.6% | 339 | 8.5% | −0.0676 | −0.0080 |
| 12:15-12:45 | 68 | 6.8% | 359 | 9.0% | −0.0354 | 0.0308 |
| 12:45-13:15 | 118 | 11.8% | 395 | 9.9% | −0.0006 | 0.0130 |
| 13:15-13:45 | 86 | 8.6% | 392 | 9.9% | −0.0342 | −0.0107 |
| 13:45-14:15 | 97 | 9.7% | 307 | 7.7% | 0.0286 | 0.0906 |
| 14:15-14:45 | 90 | 9.0% | 352 | 8.9% | 0.0521 | 0.0661 |
| 14:45-15:15 | 84 | 8.4% | 385 | 9.7% | −0.0539 | 0.0391 |
| 15:15-15:45 | 73 | 7.3% | 231 | 5.8% | n/a | n/a |

**Do signals sit systematically earlier or later than their controls?**

| | mean bucket offset (control − signal) | control earlier | same bucket | control later |
|---|---|---|---|---|
| H003-A | **+0.067** | 35.1% | 25.3% | 39.7% |
| H003-B | **−0.083** | 37.5% | 26.9% | 35.6% |

**Measurable, but small and not systematic.** The mean offsets are under a
tenth of a bucket — under three minutes — and they point in *opposite*
directions for the two variants. Per-bucket shares differ by 1-2 percentage
points in places (signals are over-represented at 10:45-11:15 and
under-represented at 14:45-15:15 for both variants), so the distributions are
not identical, but there is no consistent "signals fire later" bias of the kind
that would inflate a control's forward return.

The more informative column is the last pair. Control C's mean move exceeds the
signal's mean move in 8 of 10 measurable buckets for variant A and 8 of 10 for
variant B. The signal underperforms its control **inside** each time bucket,
which is not something time-of-day can explain.

### Task 3 — general time-matched control mechanism

`app/research/matched_controls.py`. Deliberately generic; nothing in it refers
to H003.

**Held constant:** symbol, trading day, direction, the prerequisite-stage
population supplied by the caller, and session bucket.
**Randomised:** which eligible bar within that cell.

**Bucketing.** Deterministic: `bucket = floor((minutes since 09:15) / 30)`,
giving 13 buckets over the 09:15-15:30 session, the last a half-width tail.

**Why 30 minutes, argued before any result.** Two constraints pull opposite
ways: a bucket must be wide enough that a (symbol, day, bucket) cell usually
holds several eligible candidates, and narrow enough that drift within a bucket
is small next to drift between buckets. At a 5-minute interval, 30 minutes is
6 bars — the smallest round subdivision of the session that reliably leaves
more than a handful of candidates per cell. The width was not selected by
looking at H003 forward performance, and the realised match rates (below)
confirm the cell-occupancy argument independently of any outcome.

**When no eligible control exists.** In order, and never further:

1. Sample from the signal's own bucket. An occupied own-bucket is used
   *alone*, never pooled with neighbours — pooling would dilute a match already
   achieved.
2. If empty, widen to the immediately adjacent buckets (±1) and pool those.
3. If still empty, **skip the signal**: it contributes neither a control nor
   itself to the matched comparison.

It is never filled from another day or another symbol. Those are the variables
the matching exists to hold constant, and borrowing across days would break the
day-level pairing the block permutation depends on. Exact, widened and skipped
counts are reported with every result rather than absorbed silently.

Because a skipped signal is excluded from both sides, the matched comparison is
properly paired: every signal in it obtained a matched control.

18 tests cover bucket boundaries, day and direction constraints, widening,
skip-rather-than-substitute, and exact-preferred-over-widened.

### Task 4 — POST-HOC SENSITIVITY ANALYSIS

**Not a re-run of the pre-registered test. H003 v1's verdict, parameters and
recorded gate results are unchanged.**

Development period, `net_move_pct`, day-level block permutation.

**H003-A**

| h | n | original Control C | p | **time-matched Control C** | p | exact | widened | skipped |
|---|---|---|---|---|---|---|---|---|
| 6 | 1,433 | −0.0301 | 0.0060 | **−0.0696** | **0.0000** | 98.3% | 1.5% | 0.2% |
| 12 | 1,284 | −0.0505 | 0.0170 | **−0.0696** | **0.0035** | 98.2% | 1.6% | 0.2% |
| 24 | 978 | −0.0259 | 0.4025 | **−0.0705** | 0.0367 | 98.0% | 1.7% | 0.3% |

**H003-B**

| h | n | original Control C | p | **time-matched Control C** | p | exact | widened | skipped |
|---|---|---|---|---|---|---|---|---|
| 6 | 877 | −0.0264 | 0.0480 | **−0.0663** | **0.0000** | 95.7% | 3.0% | 1.2% |
| 12 | 766 | −0.0349 | 0.1422 | **−0.0593** | 0.0165 | 95.2% | 3.4% | 1.4% |
| 24 | 591 | −0.0091 | 0.7708 | **−0.0620** | 0.0770 | 94.4% | 4.1% | 1.5% |

Match quality is high — 94-98% of signals found a control in their own bucket,
and fewer than 2% were skipped — so the matched comparison retains essentially
the whole sample.

**The negative structural result is not only robust, it sharpens.** Every
matched edge is more negative than its unmatched counterpart, and every p-value
falls. Time-of-day noise was *understating* how badly the signal did relative to
its stage-A population, not manufacturing the deficit.

The matched edges are also strikingly stable across horizons — −0.070, −0.070,
−0.071 for variant A, against −0.030, −0.051, −0.026 unmatched. Stability of
that kind is what removing a nuisance source of variance looks like, and it is a
second, independent sign that the matching is doing what it claims.

Note: the "original Control C" figures here differ slightly from the registered
result (−0.0325 / −0.0301 / −0.0243 for variant A) because controls are
re-sampled and this run pairs signals to controls before comparing. The
registered numbers stand as recorded; the discrepancy is the ~2 bp of
control-resampling noise already flagged with the original result.

### Recommended control framework for H004

1. **Control A (same bar, random side) stays the Gate 1 instrument.** It is
   immune to every positional confound by construction — same instant, same
   volatility — so it answers "does this know direction" cleanly and needs no
   matching.
2. **Control C becomes time-matched by default** for any staged hypothesis.
   The audit shows unmatched sampling adds variance and can bias the estimate;
   here it biased *towards* the hypothesis, but the sign of such a bias is not
   predictable in advance and should not be left to chance.
3. **Retire Control B from gating.** Its mean rose 0.06 -> 0.13 -> 0.25 with
   horizon purely because unrestricted random bars sit earlier in the session
   and capture more drift. Time-matching would fix that, at which point it
   becomes Control C without the stage restriction — of little additional value.
   Keep it as a reported diagnostic, not a gate.
4. **Report match quality with every result** — exact, widened and skipped
   percentages. A control with a 30% skip rate is a different comparison from
   one with 2%, and the reader cannot tell without the numbers.
5. **State the prerequisite-stage population in the pre-registration.** For a
   staged hypothesis it defines Control C, and therefore defines what "does the
   later structure add anything" actually means.

---

## Step 8 — Episode diagnostics, a dataset defect, and the H004 proposal

### Approved framework, now the default

Control A (same bar, random side) for direction; **time-matched** Control C
(symbol, day, direction, prerequisite stage, 30-minute session bucket) for
structure; Control B diagnostic only; day-level block permutation; 60/20/20
chronological split.

### 8.1 Signal-episode diagnostics

`app/research/episodes.py`. Diagnostic only — it filters nothing and revises no
verdict.

**Episode rule.** Two signals share an episode when they have the same symbol,
trading day and direction and are separated by fewer than
`min_separation_bars`. The separation is set to the hypothesis's own input
window, because signals closer together than that were computed from
overlapping data and cannot be independent observations of distinct events.
That argument is about inputs alone; no forward return is consulted, and the
separation must never be chosen by looking at performance.

| | H001 EMA | H002 ORB | H003-A | H003-B |
|---|---|---|---|---|
| separation used | 21 (slow EMA) | 75 (one session) | 12 (lookback L) | 12 (lookback L) |
| total signals | 8,180 | 2,296 | 2,571 | 1,536 |
| signal-days | 63 | 63 | 63 | 63 |
| signals per signal-day | 129.8 | 36.4 | 40.8 | 24.4 |
| **distinct episodes** | **6,563** | **2,296** | **1,870** | **1,212** |
| signals per episode | 1.25 | 1.00 | 1.37 | 1.27 |
| median episode duration | 0 bars | 0 bars | 0 bars | 0 bars |
| max episode duration | 58 bars | 0 bars | 24 bars | 19 bars |
| singleton episodes | 80.8% | 100% | 72.1% | 77.3% |
| largest episode | 6 signals | 1 signal | 8 signals | 4 signals |

"Signals per signal-day" counts across all 39 symbols, so H001's 129.8 is about
3.3 per symbol-day.

**Reading.** Clustering is mild. The worst case, H003-A, collapses 2,571 signals
into 1,870 episodes — a 27% reduction, not the five- or ten-fold inflation that
would mean a handful of market events masquerading as thousands of
observations. Median episode duration is zero bars everywhere: the typical
episode is a single signal. H002 is 1.00 by construction, since it takes one
breakout per session and the separation is a whole session.

This does not alter H003's verdict and was not used to. It says the samples
behind all three verdicts are less concentrated than raw counts might suggest,
which if anything supports the conclusions already recorded.

### 8.2 Dataset defect found: `volume` is cumulative, not per-bar

Found while inspecting H004 candidate variables — a candidate built on volume
produced a minimum of 1.011 across 33,525 observations, with nothing below 1.0.
That is not a plausible distribution for a ratio of two volumes.

Evidence:

| check | result |
|---|---|
| symbols whose volume never falls within a session | **39 of 39** |
| volume drops at a session boundary (a reset) | 2,355 (~60 per symbol; 62 boundaries exist) |
| volume drops within a session | 791 of ~183,000 bars (0.4%) |
| RELIANCE, first session | first bar 988,614 -> last bar 10,698,449 over 75 bars |
| first five successive differences | 278,159 / 209,891 / 220,437 / 187,768 / 99,982 |

The field is **cumulative volume since the session open**. The differences are
plausible 5-minute volumes; the raw values are not.

**What this does and does not affect.**

* **No registered verdict changes.** H001 was tested with `volume_filter` off,
  H002's headline result used `rvol_threshold=0`, and H003 has no volume
  condition at any stage. None of the three gates read this field.
* **`entry_diagnostics.observe()` computes `rvol` from it**, so the RVOL
  segmentation reported in the early entry-diagnostics work was bucketing
  cumulative volume, not relative volume. That table's own conclusion was
  "noise, p=0.271", so nothing rests on it — but the row labels were wrong and
  should not be cited.
* **`OpeningRange.rvol` and the live scanner's volume filter** read per-bar
  volume from the live `candle_store`, which builds bars from tick deltas and
  is a different path. This finding is about the research store only; the live
  path is not implicated and was not changed.
* **Any future volume-based hypothesis is blocked** until the research store
  differences volume within each session.

The fix is mechanical — difference within session, guarding the reset — but it
rewrites a column across 184,261 rows and is not being done unilaterally.

### 8.3 H004 research proposal (candidates only — not implemented)

**Question.** After an unusually large causal directional impulse, what
observable structure distinguishes continuation from exhaustion?

**Design.** H004 v1 is a **variable-screening study, not a trading rule.** The
population is the stage-A impulse-end bars already defined by H003's Stage A
(ordered impulse, R/ATR(14) >= 3.5, lookback 12, no session crossing) —
34,159 observations in the development period. Variables are measured **at the
impulse end bar**, and the forward window starts there. No pullback and no
trigger are required, which is what separates this from H003: H003 asked
whether a *specific structure after* the impulse adds information, and the
answer was no. H004 asks what, if anything, measured *at the impulse itself*
separates the two outcomes.

Any variable that survives screening becomes the basis of a pre-registered
trading rule in a later hypothesis, tested on the untouched hold-out. Screening
therefore uses development and validation only; **the hold-out is not read.**

#### Candidate variables

Measured at `end_idx` from bars <= `end_idx`. Distributions below are
development-period, predictor-side only — no forward return was computed or
imported while selecting these.

| variable | n | p25 | median | p75 | stdev |
|---|---|---|---|---|---|
| V1 terminal close location | 34,159 | 0.271 | 0.558 | 0.786 | 0.297 |
| V2 volume trajectory | 33,525 | 1.116 | 1.192 | 1.362 | 0.442 |
| V3 impulse velocity | 34,159 | 0.464 | 0.596 | 0.841 | 0.623 |
| V4 impulse-leg efficiency | 33,524 | 0.604 | 0.757 | 0.913 | 0.202 |

Pairwise correlations are all |r| <= 0.054, so these are four distinct
measurements rather than four names for one thing.

---

**V1 — terminal close location**

*Definition.* At the impulse end bar: `(close - low) / (high - low)` for an up
impulse, `(high - close) / (high - low)` for a down impulse.

*Behaviour.* Where the final bar of the move closed inside its own range. Near
1, the move ended with the aggressor still in control. Near 0, the bar was
rejected — price reached the extreme and was pushed back within the same five
minutes, the classic shape of exhaustion.

*Causal.* Yes; one bar, fully closed.

*Confounders.* Unstable on narrow-range bars, where a small absolute move spans
the ratio's whole scale; a minimum range in ATR units may be needed as a
reported stratum, not a filter. Also mechanically related to how the impulse end
is located: `end_idx` is the bar containing the extreme high, so its close
sitting below that high is partly structural. The right comparison is therefore
*within* the stage-A population, which is what Control C provides.

*Novelty.* **Genuinely new.** No registered hypothesis has used bar-internal
geometry. The app contains a candlestick-pattern module, but it has never been
part of a registered hypothesis, and this is a continuous variable rather than a
named pattern.

---

**V2 — volume trajectory ⚠️ BLOCKED**

*Definition.* Volume summed over the last third of the impulse leg divided by
the first third.

*Behaviour.* Rising participation into the extreme versus fading participation.
Note the interpretation is genuinely ambiguous: rising volume is read as
continuation in one tradition and as a climax top in another. Measuring it is
how that ambiguity gets settled rather than assumed.

*Causal.* Yes in principle — **but not computable on the current dataset.** See
8.2: the stored volume is cumulative, which is why this variable's minimum came
out above 1.0. It cannot be screened until the store is corrected.

*Confounders.* Intraday volume follows a U-shape across the session, so an
impulse spanning the open will show falling volume mechanically. Time-matched
Control C absorbs part of this; the rest needs a session-normalised volume.

*Novelty.* **Genuinely new** — no registered hypothesis has used volume at all.

---

**V3 — impulse velocity**

*Definition.* `R / (impulse_bars * ATR(14)[end_idx])`.

*Behaviour.* How fast the move covered its distance. Stage A already requires
the move to be large; this separates a large move that took ten bars from one
that took three. A near-vertical advance is the shape most associated with
climax and mean reversion; a steady one with participation that may persist.

*Causal.* Yes.

*Confounders.* ATR(14) is backward-looking, so a volatility regime shift
inflates velocity mechanically rather than through market behaviour.
`impulse_bars` is bounded above by the lookback of 12, which compresses the
denominator's range. It also shares R and ATR with the stage-A threshold, so
its correlation with `impulse_score` must be reported before it is interpreted —
that was not computed here and is a required first step.

*Novelty.* **Partly new.** It reuses stage-A's ingredients but forms a different
quantity by normalising for duration, which stage-A does not do.

---

**V4 — impulse-leg path efficiency**

*Definition.* `|close[end] - close[start]| / sum(|close[i] - close[i-1]|)` over
the impulse leg only.

*Behaviour.* Directness. A leg that went almost straight up reflects one side
consistently in control; the same net move achieved through heavy two-way trade
reflects a contest, which is more likely to continue resolving in both
directions.

*Causal.* Yes.

*Confounders.* **Mechanically tied to leg length**: a driftless random walk over
N bars has expected efficiency `1/sqrt(N)`, so short legs score higher for
arithmetic reasons alone. It must be compared within `impulse_bars` strata, or
normalised as `efficiency * sqrt(impulse_bars)` — decided before screening,
never after.

*Novelty.* **A variation, and flagged as such.** This is close to the efficiency
condition written into H003's pre-registration and then removed before testing
as redundant with a correctly-set K. It has never been evaluated, so it is
untested rather than rejected, but it is not a new idea. It is retained
precisely because it was dropped on reasoning rather than evidence, and
reasoning about thresholds has already been wrong twice in this project.

---

#### Proposed screening plan (for review, not yet approved)

* **Population:** stage-A impulse-end bars, development + validation. Hold-out
  untouched.
* **Horizons:** 6, 12, 24 bars from `end_idx`.
* **Primary metric:** `net_move_pct` in the impulse direction.
* **Contrast:** top versus bottom tercile of each variable, within the stage-A
  population, compared against time-matched Control C.
* **Comparisons:** 3 usable variables x 3 horizons = 9 (12 if V2 is unblocked),
  so alpha = 0.05/9 = 0.0056.
* **Statistics:** day-level block permutation; episode diagnostics reported
  alongside, since the stage-A population overlaps heavily.
* **Terciles are cut on the development-period predictor distribution only** —
  the same input-only discipline used for K.

**Open items before H004 can be approved:** the volume defect (8.2) must be
resolved or V2 dropped; V3's correlation with `impulse_score` must be measured;
and V4's length normalisation must be fixed in advance.

---

## Step 9 — Volume architecture and feature audits

**H001, H002, H003 remain REJECTED, unmodified.** No forward return was
computed anywhere in this step.

### 9.1 Corrected research-volume architecture

| column | meaning |
|---|---|
| `volume` | the broker's value, untouched (cumulative since session open) |
| `raw_cumulative_volume` | explicit copy of the same, so the raw semantics are named rather than implied |
| `bar_volume` | derived per-bar volume; **NULL** where not derivable |
| `volume_quality` | `OK` / `FIRST_BAR` / `UNKNOWN` |

`read()` gains `volume="bar"` (default) or `volume="raw_cumulative"`. The raw
path reproduces H001-H003 exactly; none of their conditions read volume, so
their verdicts are unaffected either way.

**A defect caught while doing this.** The first implementation put derivation in
a one-off migration, which meant `upsert()` wrote rows with a NULL `bar_volume`
— so any *future* backfill would have read back as **zero volume** under the new
default. Seven tests failed and exposed it. Derivation now lives in the store
and `upsert()` recomputes every session it touches, so the invariant is
maintained on write rather than depending on a migration being re-run.

### 9.2 Raw vs derived semantics

```
bar_volume[t] = cumulative[t] - cumulative[t-1]     within a session
bar_volume[first bar of session] = cumulative[first]
```

**First bar** is flagged `FIRST_BAR`, not `OK`. The counter starts from zero
before the open, so its cumulative is its own volume — but the median first-bar
value is 3.15x a typical later bar, which is equally consistent with a genuine
opening burst and with pre-open auction volume folded in. This data cannot
settle that, so the flag records the ambiguity instead of hiding it.

Migration result over the full dataset:

| | rows | share |
|---|---|---|
| `OK` | 180,326 | 97.9% |
| `FIRST_BAR` | 2,457 | 1.3% |
| `UNKNOWN` | 1,478 | 0.80% |

### 9.3 The 791 within-session drops

| property | finding |
|---|---|
| count | 791 (0.429% of 184,261 bars) |
| symbols affected | **39 of 39** (19-22 each — uniform, not symbol-specific) |
| time of day | **all 791 in the 15:00 hour**; none earlier |
| magnitude | median 1,852,349 shares; p25 704,696; max 58,939,390 |
| relative | median **97.6%** of the previous cumulative; p90 100% |
| drops to <10% of previous | 779 (98.5%) |
| decrease under 1% (revision-like) | 1 (0.1%) |
| next bar resumes increasing | 687 of 731 (94.0%) |
| next bar exceeds the pre-drop value | 498 (68.1%) |

**Classification: counter resets at the session tail, not vendor revisions.**
A revision would be a small correction spread across the day; these are
near-total drops confined to the last half hour. Two sub-shapes appear —
`874,272 -> 16,145 -> 891,228`, where the next bar returns to the main counter
(and 874,272 + 16,145 ≈ 891,228, so the anomalous value looks like a bare
per-bar figure), and `833,647 -> 0 -> 16,560`, where a fresh small counter
begins. The two are indistinguishable from a single bar, which is why neither
is guessed at.

**Treatment rule (deterministic, data-semantic, no forward information):**

1. A bar whose cumulative is below its predecessor's -> `UNKNOWN`, volume NULL.
2. The bar *immediately after* one -> also `UNKNOWN`: its difference would be
   taken against a reset baseline and would report most of the day's volume as
   a single bar.
3. Nothing is clipped to zero. A fabricated zero is indistinguishable
   downstream from a genuinely quiet bar.

1,478 bars = 791 resets + 687 successors (60 resets are a session's last bar).
Volume-sensitive analysis must exclude these; `unknown_volume_timestamps()`
names them, and `read(volume="bar")` renders them 0 purely because `OHLCV.volume`
is typed `int` — that rendering is documented as not a claim of zero volume.

**Validation of the fix.** Recomputing the V2 candidate on the corrected column:

| | n | min | p25 | median | p75 | max | below 1.0 |
|---|---|---|---|---|---|---|---|
| before (raw cumulative) | 33,525 | 1.011 | 1.116 | 1.192 | 1.362 | 12.1 | **0 (0.0%)** |
| after (derived per-bar) | 33,525 | 0.013 | 0.770 | 1.311 | 2.369 | 69.1 | **12,276 (36.6%)** |

A ratio of two volumes that never once fell below 1.0 across 33,525 samples was
the symptom; it now behaves like a ratio.

### 9.4 Live-vs-research comparison — **NOT POSSIBLE TODAY**

Reporting this as a stop condition rather than working around it.

| | |
|---|---|
| research store | 2026-06-01 .. 2026-08-27, 63 days |
| live store | 2026-08-28 only, 5 bars |
| overlapping bars | **0** |

Three separate reasons it cannot be run right now:

1. **No date overlap** — the live in-memory store was emptied by the most
   recent `--reload` and holds only today.
2. **The live feed is currently SIMULATED.** Its RELIANCE bars print 2779 ->
   3028 -> 2510 against a real price near 1330. Synthetic prices cannot validate
   real volume.
3. **Market is closed**, so no new tick-derived bars are forming.

**A correction to what I reported earlier.** I previously wrote that the live
path "is not implicated". That was only half right, and the half that is wrong
matters:

* `candle_store.on_tick` computes `delta = max(0, cum_volume - prev_cum)` —
  genuine per-bar volume. Correct.
* `backfill.ensure_backfilled` seeds the same store from
  `client.get_candles(...)` — **the same cumulative values the research store
  had.** So the live series mixes both semantics, with backfilled history in
  cumulative units and today's tick bars in per-bar units, in one column.

That is a real defect in the live charting path. It does not affect any
registered verdict, and I have not changed the live path. It is reported for a
decision.

**Exact procedure to run the comparison when possible:** during a live NSE
session with the feed on LIVE, let `candle_store` build tick bars for a set of
symbols; after the close, ingest that same day into the research store; then
compare bar-for-bar on `(symbol, ts)`. Only tick-derived live bars are a valid
comparator — backfilled live bars come from the same API call as research and
would agree by construction.

### 9.5 V1 audit — terminal close location: **PASSES**

| check | result |
|---|---|
| bars with zero range (denominator 0) | **0 of 34,159** |
| end-bar range, p1 / p5 / median (ATR units) | 0.453 / 0.608 / 1.215 |
| end-bar range < 0.20 ATR | 1 bar (0.003%) |
| end-bar range < 0.25 ATR | 15 bars (0.04%) |
| V1 stdev in the narrow subset vs elsewhere | 0.301 vs 0.297 |
| V1 exactly 0.0 / exactly 1.0 | 1,381 (4.0%) / 1,317 (3.9%) |

The denominator is not a practical problem: the impulse-end bar is by
construction the bar containing the move's extreme, so it is rarely narrow, and
the 1st percentile is still 0.45 ATR. Variance in the narrow tail is
indistinguishable from the rest.

**Deterministic handling rule:** if `high == low`, V1 is undefined — the
observation is *excluded and counted*, never imputed. No threshold filter is
applied, because the data shows none is needed and a threshold would be a
parameter chosen without justification. Values of exactly 0.0 and 1.0 are
legitimate (a bar closing on its low or high), not degenerate.

### 9.6 V3 audit — **REDUNDANT, DROPPED**

```
V3 = impulse_range / (impulse_bars × ATR)
   = (impulse_range / ATR) / impulse_bars
   = impulse_score / impulse_bars          [exact identity]
```

Reconstruction error against the computed values: 4.4e-16 — floating point only.
V3 is not a new measurement; it is Stage-A's own statistic divided by duration.

| | value |
|---|---|
| corr(V3, impulse_score) | +0.095 |
| corr(V3, impulse_bars) | **−0.722** |
| stdev of V3 overall | 0.623 |
| mean stdev *within* a fixed duration | **0.213** |

| impulse_bars | n | V3 median | impulse_score median |
|---|---|---|---|
| 1 | 634 | 4.025 | 4.025 |
| 3 | 1,862 | 1.333 | 3.999 |
| 5 | 2,936 | 0.817 | 4.087 |
| 8 | 4,035 | 0.532 | 4.255 |
| 11 | 5,046 | 0.409 | 4.498 |

`impulse_score` is essentially flat across durations (4.0 -> 4.5, because
Stage A thresholds it at 3.5), while V3 falls as 1/bars. Holding duration
fixed removes 66% of V3's variance. **V3 is impulse duration wearing a
different name.**

**Dropped.** If duration is the interesting quantity, the honest candidate is
`impulse_bars` itself — already available, directly interpretable, and not
dressed up as something new. Whether to add it is a decision for review, not
something to slip in as a replacement.

### 9.7 V4 audit — **NORMALISATION NOT DEFENSIBLE, BLOCKED**

| impulse_bars | n | V4 median | 1/√N | V4 × √N |
|---|---|---|---|---|
| 1 | 634 | **1.000** | 1.000 | 1.000 |
| 2 | 1,160 | **1.000** | 0.707 | 1.414 |
| 3 | 1,862 | **1.000** | 0.577 | 1.732 |
| 4 | 2,628 | 0.955 | 0.500 | 1.909 |
| 6 | 3,396 | 0.816 | 0.408 | 2.000 |
| 8 | 4,035 | 0.729 | 0.354 | 2.061 |
| 11 | 5,046 | 0.646 | 0.302 | 2.144 |

corr(V4, impulse_bars) = −0.418.

Two blocking problems:

1. **Structurally degenerate for short impulses.** At 1-3 bars the median is
   *exactly* 1.000 — 3,656 observations (10.7%) pinned at the ceiling
   regardless of market behaviour. A monotone 2-3 bar leg has efficiency 1 by
   construction; that is arithmetic, not information.
2. **`V4 × √N` does not work.** The proposed normalisation was motivated by a
   random walk's expected efficiency of `1/√N`, but the observed medians sit far
   *above* that curve — as they must, since Stage A selects large directional
   moves. Multiplying by √N therefore over-corrects: the normalised column
   climbs 1.000 -> 2.144 rather than flattening. It removes no length
   dependence and introduces a new one.

**Blocked.** The defensible alternative needs no constant at all: compare V4
**within `impulse_bars` strata**, restricted to legs of 4+ bars where the
measure is not pinned. That is a design change and is proposed, not adopted.

### 9.8 Eligible H004 candidates after the audits

| | status | why |
|---|---|---|
| **V1** terminal close location | **ELIGIBLE** | Distinct from Stage A: Stage A measures the *size* of the move over 12 bars; V1 measures where the *final bar* closed inside its own range. No prior hypothesis used bar-internal geometry — H001 uses two averages, H002 a session level, H003 swing structure and a prior-bar break. Denominator verified stable. |
| **V2** volume trajectory | **ELIGIBLE, conditional** | Now computable. Genuinely new: no registered hypothesis has used volume at all. Conditional on the live cross-validation in 9.4, because the derivation is currently validated only internally. |
| ~~V3~~ impulse velocity | **DROPPED** | An exact transform of Stage A's own statistic, dominated by duration |
| ~~V4~~ leg efficiency | **BLOCKED** | Degenerate below 4 bars; the pre-proposed normalisation is not defensible |

### 9.9 Stop conditions — three triggered

| condition | status |
|---|---|
| research volume semantics unresolved | **resolved** — cumulative with session-tail resets, documented and derived |
| research/live volume disagree materially | **CANNOT BE TESTED TODAY** (9.4) |
| V3 effectively redundant with impulse_score | **TRIGGERED** — exact identity |
| V4 normalisation not defensible | **TRIGGERED** |
| V1 denominator unstable | not triggered — V1 is clean |

Stopping before any forward screening, as instructed.

### 9.10 Hold-out

**Untouched.** Every measurement in this step used the development period only
(37 days, 2026-06-01..2026-07-22), except the volume-defect classification,
which is a data-integrity audit over the whole store and reads no outcome of any
kind. The validation and hold-out periods have never been read by any screening
or forward computation.
