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
