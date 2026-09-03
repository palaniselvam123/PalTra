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

---

## Step 10 — Canonical volume contract, implemented and cross-validated

H001, H002, H003 remain REJECTED and unmodified. No forward return was computed.

### 10.1 Final volume contract

`app/services/volume_contract.py` — one rule, imported by every ingestion path.

> **`OHLCV.volume` is the volume traded during that candle. Always.**

Nothing may place a cumulative counter in that field. Supporting fields:

| field | meaning |
|---|---|
| `bar_volume` | canonical per-bar volume (what `OHLCV.volume` carries) |
| `raw_cumulative_volume` | the broker's original counter, never destroyed |
| `volume_quality` | `OK` / `FIRST_BAR` / `UNKNOWN` |
| `provenance` | `TICK` (observed here) / `BACKFILL` (broker history) |

The module lives in `services/` rather than `research/` so the live path never
imports from the research package — that isolation is deliberate and tested.
`research/store.py` imports the same `derive_session`; a test asserts the two
are literally the same function object, because two copies would drift and
drift is how the paths disagreed in the first place.

### 10.2 Backfill derivation

```
Groww historical rows (cumulative since session open)
        -> volume_contract.derive_rows()        split by IST session
        -> OHLCV.volume = bar_volume
           quality + raw cumulative passed alongside into candle_store.seed()
```

Previously `backfill.py` seeded `volume=r[5]` — the broker's cumulative value —
straight into the same field the tick path fills with per-bar volume. A test now
asserts that expression cannot reappear.

### 10.3 Tick derivation

Unchanged in behaviour, now labelled: `candle_store.on_tick` differences the
cumulative day counter per tick (`delta = cum - prev_cum`) and accumulates it
into the open bar. Each such bar is recorded `provenance=TICK`,
`quality=OK`, with the raw counter retained.

### 10.4 Reset handling

Kept exactly as concluded, and code inspection did not contradict it:

* reset bar -> `bar_volume = NULL`, `quality = UNKNOWN`
* immediately following bar -> `bar_volume = NULL`, `quality = UNKNOWN`
* negative differences are never clipped to zero; volume is never invented

Across the research store: 180,326 `OK` (97.9%), 2,457 `FIRST_BAR` (1.3%),
1,478 `UNKNOWN` (0.80%). `unknown_volume_timestamps()` names the UNKNOWN bars;
they render as 0 only because `OHLCV.volume` is typed `int`, and that rendering
is documented as not a claim of zero volume.

### 10.5 Live-vs-research cross-validation — **RUN, DURING A LIVE SESSION**

Performed 2026-08-28 between 11:40 and 12:11 IST with the market OPEN and the
feed on LIVE. **Only `TICK`-provenance live bars were used** — a backfilled live
bar is the same API response the research store ingests, so comparing those
would compare one source against itself.

**Per-bar comparison, 70 matched bars across 10 symbols:**

| measure | value |
|---|---|
| matched bars | 70 |
| exact match | 1 (1.4%) |
| mean absolute difference | 8,682 shares |
| median relative difference | **5.97%** |
| p95 relative difference | 33.38% |

**Aggregate comparison over the same bars:**

| symbol | bars | live total | research total | diff | diff % | corr |
|---|---|---|---|---|---|---|
| RELIANCE | 7 | 413,450 | 410,384 | 3,066 | 0.75% | 0.993 |
| TCS | 7 | 291,987 | 286,434 | 5,553 | 1.94% | 0.991 |
| HDFCBANK | 7 | 862,610 | 858,666 | 3,944 | 0.46% | 0.956 |
| INFY | 7 | 301,722 | 294,826 | 6,896 | 2.34% | 0.932 |
| ITC | 7 | 706,579 | 701,020 | 5,559 | 0.79% | 0.987 |
| SBIN | 7 | 349,175 | 342,066 | 7,109 | 2.08% | 0.907 |
| AXISBANK | 7 | 477,433 | 476,002 | 1,431 | 0.30% | **−0.125** |
| MARUTI | 7 | 11,890 | 11,837 | 53 | 0.45% | 0.994 |
| TITAN | 7 | 30,263 | 30,168 | 95 | 0.31% | 0.998 |
| WIPRO | 7 | 1,081,551 | 1,074,510 | 7,041 | 0.66% | 0.999 |
| **ALL** | **70** | **4,526,660** | **4,485,913** | **40,747** | **0.91%** | pooled 0.894 |

**Explanation of the expected differences.** The paths agree on *quantity* and
differ on *attribution at bar edges*:

* The live path assigns a delta to the bucket of the **poll time** (2-second
  polling), while broker history assigns volume by **trade time**. A burst
  straddling 11:49:59 lands in different bars.
* AXISBANK shows this in its purest form: 11:45 live 35,970 vs research
  209,798, and 11:50 live 225,506 vs research 52,210 — nearly swapped, summing
  to 261,768 against 262,008. Its −0.125 per-bar correlation is one boundary
  event, not a semantic disagreement.
* The decisive test: **widening the window collapses the error.** Median
  relative error falls from **6.10% for a single bar to 2.81% for two adjacent
  bars**, and p95 from **49.05% to 11.36%**. Edge attribution cancels when
  summed; a semantic mismatch would not.
* Totals agree to 0.91%, with live consistently slightly *higher* — see the
  residual defect below.

**Conclusion: the two paths now produce the same semantic quantity.** The
residual is timing granularity, not meaning.

**One residual defect found and not fixed.** `on_tick` does
`bar.volume += delta` on whatever bar occupies the bucket. If backfill seeded
that bucket first and ticks then arrive for it, the backfilled figure and the
tick deltas are summed — inflating the boundary bar. That likely contributes to
the consistent ~0.9% live excess. The fix is for `on_tick` to reset a bar's
volume the first time it writes to a `BACKFILL` bucket. It is reported rather
than applied, because it changes live charting behaviour.

### 10.6 Test results

**230 passing, 0 failing.** New suite `tests/test_volume_contract.py` (23 tests)
covers the required points:

| | requirement | covered by |
|---|---|---|
| A | backfill exposes canonical per-bar volume | `TestBackfillPath` — including that the seeded column is *not* monotonically rising, the symptom that exposed the defect |
| B | tick exposes canonical per-bar volume | `TestTickPath` |
| C | raw cumulative preserved | `test_rows_preserve_the_raw_cumulative`, `test_raw_cumulative_is_recoverable` |
| D | reset handling deterministic | `TestDerivationRule` |
| E | both paths satisfy one contract | `test_same_cumulative_series_yields_the_same_bar_volumes` — identical underlying data through both paths must yield identical canonical volume |
| F | RVOL consumes bar volume | `test_entry_diagnostics_rvol_uses_the_candle_volume_field` — RVOL sees a 5x burst as ~5.0 on canonical volume and under half that on cumulative |
| G | no silent fallback | `TestNoSilentFallback` — asserts `derive_rows` is called, `volume=r[5]` cannot reappear, and both stores share one function object |

Two of my own tests failed first and were wrong, not the code: an off-by-one in
the both-paths comparison, and an arbitrary RVOL threshold. Corrected.

### 10.7 Superseded historical diagnostics

Marked **INVALID / SUPERSEDED — do not cite its numerical result**:

| item | why |
|---|---|
| The **RVOL segmentation table** in the early entry-diagnostics work (buckets `<0.8x`, `0.8-1.2x`, `1.2-2x`, `>2x`, reported as "noise, p=0.271") | computed `rvol` from cumulative volume, so the buckets do not mean what they are labelled |
| Any **`Observation.rvol`** value produced before this step | same cause |
| The H002 note that an **RVOL >= 1.5 ORB variant** "left only 23 signals" | that gate read `OpeningRange.rvol`, itself built on the same field |

No registered verdict depended on any of these: H001 ran with `volume_filter`
off, H002's headline used `rvol_threshold=0`, and H003 has no volume condition.
**H001/H002/H003 verdicts stand unchanged.**

### 10.8 V2 status — input-only, unblocked

| | n | min | p25 | median | p75 | max | below 1.0 |
|---|---|---|---|---|---|---|---|
| before (raw cumulative) | 33,525 | 1.011 | 1.116 | 1.192 | 1.362 | 12.1 | **0 (0.0%)** |
| after (canonical bar volume) | 33,525 | 0.013 | 0.770 | 1.311 | 2.369 | 69.1 | **12,276 (36.6%)** |

A ratio of two volumes that never once fell below 1.0 across 33,525 samples was
the symptom; it now behaves like a ratio. Impulse legs touching an
`UNKNOWN`-volume bar: 0 in the development period. **V2 is eligible for
input-only screening.** No forward return has been inspected.

### 10.9 Candidate dispositions

| | status |
|---|---|
| **V1** terminal close location | **ELIGIBLE.** No zero-range denominators in 34,159 observations; p1 end-bar range 0.453 ATR; variance in the narrow tail 0.301 vs 0.297 elsewhere. Rule: if `high == low`, exclude and count — never impute, no threshold filter |
| **V2** volume trajectory | **ELIGIBLE.** Contract implemented and cross-validated; distribution now sane |
| **V3** impulse velocity | **DROPPED.** `V3 = impulse_score / impulse_bars` exactly (error 4.4e-16); corr with `impulse_bars` −0.722; holding duration fixed removes 66% of its variance |
| **V4** leg efficiency | **BLOCKED, and left blocked.** Pinned at exactly 1.000 for 1-3 bar impulses (10.7% of observations); `×√N` over-corrects, climbing 1.000 -> 2.144 instead of flattening. No replacement normalisation invented |

### 10.10 `impulse_bars` — registered as a separate candidate

Not a substitute for V3. Registered on its own terms:

> **V5 — impulse duration.** *Conditional on a strong directional impulse, does
> impulse duration contain information about subsequent continuation versus
> exhaustion?*
>
> **Definition:** `impulse_bars = |end_idx − start_idx|` from the ordered causal
> swing pair, measured at the impulse end bar.
> **Causal:** yes — both indices come from bars <= t.
> **Distinct from Stage A:** Stage A thresholds impulse *magnitude*
> (`R/ATR >= 3.5`) and says nothing about how long it took; the observed
> `impulse_score` median is essentially flat across durations (4.03 at 1 bar,
> 4.50 at 11), so duration is close to orthogonal to what Stage A selects.
> **Distinct from H001-H003:** none used duration. H003 used
> `impulse_bars` only as a *bound* on pullback length, never as a variable.
> **Confounders:** bounded above by the lookback of 12, which truncates the
> upper tail; and correlated with V4 (−0.418), so the two cannot be interpreted
> independently.
> **Status: PROPOSED. Not tested. No forward outcome inspected.**

### 10.11 Hold-out

**Untouched.** No screening has been run. Every distribution in this step is
predictor-side and, where a period is involved, development only. The validation
and hold-out periods have never been read by any forward computation.

---

## Step 11 — Live candle volume lifecycle fixed and re-validated

H001 = REJECTED, H002 = REJECTED, H003 v1 = REJECTED — unchanged. Pre-fix
volume diagnostics remain INVALID / SUPERSEDED. No forward outcome inspected.

### 11.1 Final candle volume lifecycle

**Invariant: each trade's volume is counted exactly once.**

The defect was subtle because both halves were individually right. Backfill
wrote a bar covering the *whole* bucket; ticks arriving afterwards then added
the volume they observed, which is a *subset* of that same interval. Two
correct numbers, summed over overlapping ranges — nothing crashed, the bar was
merely too big.

The old form could not be made safe by checking anything, because the ranges
are not disjoint and cannot be made disjoint after the fact. So the accumulation
was removed entirely:

```
OLD:  bar.volume += delta                      accumulate, order-dependent
NEW:  bar.volume  = cumulative - anchor        assign, idempotent
```

`anchor` is the session counter as it stood when the bucket **opened**, stored
per bucket. A bar's volume is therefore always a difference across its own span,
recomputed from scratch on every tick. Replaying a tick cannot change it, and
there is no accumulated state to corrupt.

| lifecycle stage | anchor | provenance |
|---|---|---|
| bucket opens under live observation | last observed cumulative | `TICK` |
| bucket populated by backfill, no ticks yet | none needed | `BACKFILL` |
| first tick into a backfilled bucket | `raw_cumulative - bar.volume` (recovers the bucket-start counter) | `RECONCILED` |
| counter resets mid-bucket | frozen; volume left at its last good value | quality `UNKNOWN` |

### 11.2 Backfill -> tick reconciliation

The mechanism recovers the bucket-start counter rather than choosing between the
two sources:

```
backfilled bar covers [bucket_start, fetch_time)  ->  volume V
broker's counter at fetch_time                    ->  raw_cumulative C

    anchor = C - V          the counter as it stood at bucket_start

thereafter:  bar.volume = cumulative_now - anchor
```

Worked example, verified in the tests: backfill reports 120,000 traded with the
counter at 500,000, so the bucket opened at 380,000. A later tick at 530,000
gives 150,000 — the backfilled 120,000 plus exactly the 30,000 that is genuinely
new. Not 120,000 + 530,000, and not 120,000 + 30,000 + 120,000.

**Why this rather than rebuilding from ticks.** Rebuilding would discard the
volume that traded before this process was watching — for a bucket already
half-elapsed when backfill ran, that is most of the bar. The anchor keeps the
broker's figure for the unobserved portion and takes ticks for the rest, which
is the only reconstruction that uses each source where it is authoritative.

`RECONCILED` is a genuine third state, not a label of convenience: such a bar is
part broker history and part local observation, and anyone comparing ingestion
paths must be able to exclude it, since it belongs wholly to neither.

### 11.3 Regression tests — 252 passing, 0 failing

New suite `tests/test_volume_lifecycle.py` (26 tests):

| | requirement | key test |
|---|---|---|
| A | backfilled candle has correct canonical volume | `test_backfill_volume_is_per_bar` |
| B | tick ingestion does not double-count | `test_tick_adds_only_genuinely_new_volume`; `test_volume_never_exceeds_the_counter_movement`; 50 repeated ticks do not inflate the bar |
| C | backfill -> tick transition stays correct | `test_backfilled_volume_is_preserved_not_discarded`; provenance becomes `RECONCILED`; the next bucket opens as a pure `TICK` bar |
| D | reprocessing is idempotent | replaying a tick five times is a no-op; replaying a whole sequence three times equals once; re-seeding does not disturb a reconciled bar |
| E | volume never falls on a source change | non-decreasing across a tick sequence; a mid-bucket counter reset flags `UNKNOWN` rather than shrinking the bar |
| F | raw cumulative preserved | raw and canonical verified as different quantities on the same bar |
| G | consumers read canonical volume only | a repo-wide scan asserts no module outside the storage layer references `raw_cumulative` |

### 11.4 Updated live/research comparison

Run 2026-08-28, 12:53-13:25 IST, market OPEN, feed LIVE. `TICK`-provenance bars
only; `RECONCILED` bars excluded because their early volume comes from the same
broker response the research store ingested.

| measure | **before fix** | **after fix** |
|---|---|---|
| matched bars | 70 | 70 |
| bars requiring reconciliation | n/a | **10** |
| exact match | 1 (1.4%) | 0 (0.0%) |
| mean absolute difference | 8,682 | 9,215 |
| median relative error | 5.97% | **4.11%** |
| p95 relative error | 33.38% | 159.17% |
| **aggregate total error** | **+0.91%** | **+0.18%** |

| symbol | bars | live total | research | diff % | corr |
|---|---|---|---|---|---|
| RELIANCE | 7 | 460,013 | 459,623 | +0.08% | 0.742 |
| TCS | 7 | 303,367 | 300,987 | +0.79% | 0.989 |
| HDFCBANK | 7 | 814,294 | 841,389 | **−3.22%** | 0.958 |
| INFY | 7 | 817,896 | 801,190 | +2.09% | 0.897 |
| ITC | 7 | 2,115,234 | 2,109,369 | +0.28% | 0.999 |
| SBIN | 7 | 235,219 | 235,369 | **−0.06%** | 0.999 |
| AXISBANK | 7 | 272,595 | 264,807 | +2.94% | 0.998 |
| MARUTI | 7 | 27,238 | 27,279 | **−0.15%** | 0.999 |
| TITAN | 7 | 34,497 | 34,745 | **−0.71%** | 0.999 |
| WIPRO | 7 | 865,376 | 860,051 | +0.62% | 0.924 |

**Adjacent-bar aggregation:**

| window | median rel. error | p95 | n |
|---|---|---|---|
| single bar | 4.11% | 159.17% | 70 |
| **2-bar window** | **1.51%** | **18.82%** | 60 |

**The strongest evidence is the change in sign pattern, not the headline
number.** Before the fix, live exceeded research for **10 of 10 symbols** — a
systematic inflation, which is exactly what double-counting a tail overlap
produces (under a fair coin, 10/10 has probability ~0.001). After the fix the
signs are mixed, **6 positive and 4 negative**, which is what symmetric
attribution noise looks like. The aggregate error falling from +0.91% to +0.18%
is consistent, but the disappearance of the one-sided bias is the part that
identifies the cause.

**Remaining differences are bar-edge attribution.** The live path assigns volume
to the bucket of the 2-second poll; the broker assigns by trade time. The
largest discrepancies are again near-swaps between neighbouring bars —
RELIANCE 13:00 live 122,508 vs research 193,273, and 13:05 live 141,329 vs
70,211, summing to 263,837 against 263,484, a 0.13% difference over the pair.
Widening to two bars cuts the median error to 1.51% and p95 from 159% to 19%.

Two honest caveats. The p95 of 159% is not a semantic failure: relative error is
unstable on small bars, where a few thousand shares of attribution is a large
percentage of a thin bar — the aggregate and adjacent-bar figures are the
meaningful ones. And the before/after runs cover different half-hours, so they
are not a controlled experiment; the sign-pattern change is what carries the
argument, not the difference of two point estimates.

### 11.5 Final dispositions

| | status |
|---|---|
| **V1** terminal close location | **ELIGIBLE.** 0 zero-range denominators in 34,159 observations; p1 end-bar range 0.453 ATR; narrow-tail variance 0.301 vs 0.297. Rule: `high == low` -> exclude and count, never impute |
| **V2** volume trajectory | **ELIGIBLE.** Contract implemented, lifecycle fixed, cross-validated on live tick data. Distribution behaves like a ratio: 36.6% below 1.0, previously 0% |
| **V4** leg efficiency | **BLOCKED, unchanged.** Pinned at exactly 1.000 for 1-3 bar impulses (10.7% of observations); no normalisation invented to rescue it |
| **V5** impulse duration | **PROPOSED, unchanged.** *"Conditional on a strong causal directional impulse, does impulse duration contain information about subsequent continuation versus exhaustion?"* Not a replacement for V3, not tested |
| V3 impulse velocity | DROPPED (exact identity `impulse_score / impulse_bars`) |

V1, V2 and V5 are **not** combined. No forward screening has been run.

### 11.6 Hold-out

**Untouched.** Nothing in this step read a forward outcome at all — it is a data
correctness fix and a same-day live comparison. The validation and hold-out
periods have never been read by any screening or forward computation.

---

## Step 12 — H004 feature screening: V1, V2, V5

A screening study, not a strategy. H001/H002/H003 remain REJECTED. **The
hold-out was never loaded.**

### 12.1 Design as executed

| | |
|---|---|
| population | stage-A impulse-end bars: ordered impulse, `R/ATR(14) >= 3.5`, lookback 12, no session crossing |
| observation point | the impulse-end bar `e`; features from bars <= e, outcome from `close[e]` |
| enumeration | **1:1 per impulse end** — a bar is an impulse end if `find_impulse` at `e+1` returns an impulse ending at `e` |
| outcome | `net_move_pct` in the impulse direction at 6 / 12 / 24 bars |
| terciles | cut on the **development** predictor distribution, frozen before validation |
| primary test | high-tercile minus low-tercile `net_move_pct`, day-level block permutation |
| comparisons | 3 features x 3 horizons = 9, alpha = **0.0056** |
| controls | A (same bar, random direction) and time-matched C — reported as **secondary** |

**A correction to the earlier population count.** Previous audits reported
34,159 stage-A observations. That enumerated by *evaluation bar*, so one impulse
appeared once for every subsequent bar it remained visible from. Enumerating
impulse ends 1:1 gives **15,182** distinct observations. The earlier figure was
not wrong for what it measured — the Control C sampling pool — but it is not a
count of distinct impulses, and this study needed the latter.

| | |
|---|---|
| development | 38 days, 2026-06-01 .. 2026-07-23, **11,919** observations |
| validation | 12 days, 2026-07-24 .. 2026-08-10, **3,263** observations |
| hold-out | 14 days — **not loaded** |

Exclusions: V1 lost 2 observations to a zero-range end bar; V2 lost 268 to
`volume_quality != OK` and 52 to an impulse leg under 3 bars. Usable in
development: V1 11,919, V2 11,724, V5 11,919.

### 12.2 Results — primary test

Tercile boundaries, frozen on development:

| feature | low < | high >= |
|---|---|---|
| V1 terminal close location | 0.5128 | 0.8077 |
| V2 volume trajectory | 0.9403 | 1.8118 |
| V5 impulse duration | 9 bars | 11 bars |

| feature | h | n (hi/lo) | mean hi | mean lo | **spread** | **p** | Cohen's d | validation |
|---|---|---|---|---|---|---|---|---|
| V1 | 6 | 3513 / 3518 | +0.0207 | +0.0128 | +0.0079 | 0.7792 | +0.023 | FLIPPED |
| V1 | 12 | 3152 / 3146 | +0.0412 | +0.0161 | +0.0250 | 0.3495 | +0.054 | preserved |
| V1 | 24 | 2384 / 2424 | −0.0031 | +0.0120 | −0.0151 | 0.4308 | −0.024 | FLIPPED |
| V2 | 6 | 2938 / 3818 | +0.0187 | +0.0059 | +0.0127 | 0.5152 | +0.038 | preserved |
| V2 | 12 | 2585 / 3506 | +0.0385 | +0.0163 | +0.0222 | 0.4073 | +0.048 | FLIPPED |
| V2 | 24 | 1821 / 2781 | −0.0021 | +0.0039 | −0.0060 | 0.9040 | −0.010 | FLIPPED |
| V5 | 6 | 4754 / 2876 | +0.0086 | +0.0129 | −0.0043 | 0.6920 | −0.013 | FLIPPED |
| V5 | 12 | 4245 / 2594 | +0.0250 | +0.0283 | −0.0033 | 0.8320 | −0.007 | FLIPPED |
| V5 | 24 | 3249 / 1961 | +0.0058 | +0.0021 | +0.0037 | 0.8952 | +0.006 | preserved |

**Zero of nine reach alpha = 0.0056. None reach even p < 0.05.** The smallest
p-value across all nine is 0.3495. Every effect size is negligible: the largest
|Cohen's d| is 0.054, against 0.2 as the conventional floor for "small".

Signs are not consistent across horizons within any feature — V1 gives +, +, −;
V2 gives +, +, −; V5 gives −, −, +. A real relationship would not reverse
between 12 and 24 bars while staying the same size.

### 12.3 Secondary controls, and an artifact found in Control C

Control C came out negative in all nine cells (−0.034 to −0.057). That
uniformity across three unrelated features was itself suspicious, so it was
tested directly: **the entire stage-A population, compared against its own
time-matched Control C sample, scores −0.0214 (p = 0.3422).**

Signal and control are drawn from the *same* population there, so the true
expected edge is exactly zero. The deficit is therefore a property of the
control construction, not of any feature.

The mechanism is a selection asymmetry this design introduced: a signal is kept
only if its own forward window fits inside the session, and a control draw is
kept only if *its* window fits. Within a 30-minute bucket, an earlier draw is
more likely to fit, so surviving controls skew earlier in the session and
capture more of the day's remaining drift. Measured: controls sit 0.4 bars
earlier on average — small, and in the predicted direction.

**None of the Control C numbers above should be read as evidence about V1, V2 or
V5.** The primary high-minus-low test is internal to each feature and is
unaffected by this. For future screening, a control draw must inherit the same
forward-window feasibility as the signal it is matched to.

Match quality was otherwise good: 73-82% exact bucket matches, 5.8-10.5%
skipped.

### 12.4 Why the validation numbers must not rescue V5

V5's validation spreads are all strongly positive (+0.048, +0.046, +0.068) while
its development spreads are essentially zero. It would be easy to present that
as a finding. It is not one.

Terciles were frozen on development and the screening decision belongs to
development. Validation is confirmatory: it can refute a development result, but
it cannot promote a feature that showed nothing in development. Treating a null
development result plus a positive validation result as evidence is selecting on
the validation set, which is exactly the failure mode the three-way split
exists to prevent. The same applies to V1 at 24 bars and V2 at 24 bars.

### 12.5 Feature verdicts

| feature | verdict | reasoning |
|---|---|---|
| **V1** terminal close location | **weak / inconclusive** | No horizon approaches significance (best p = 0.35); \|d\| <= 0.054; sign reverses between 12 and 24 bars; validation sign flips at 2 of 3 horizons |
| **V2** volume trajectory | **weak / inconclusive** | Same pattern: best p = 0.41, \|d\| <= 0.048, sign reverses at 24 bars, validation flips at 2 of 3 |
| **V5** impulse duration | **rejected as constructed** | Effect sizes indistinguishable from zero (\|d\| <= 0.013); and the variable is crippled by its own definition — see below |

**V5's definitional problem.** The lookback of 12 caps duration, so the
distribution piles against the ceiling: development terciles fall at 9 and 11
bars, and the "high" bucket (11 bars) holds 5,196 of 11,919 observations. The
three buckets are therefore "<=8 bars", "9-10 bars" and "exactly 11 bars" — a
narrow and truncated range, not a spread of durations. This is a null result for
V5 *as defined within a 12-bar lookback*; a wider lookback would be a different
variable and would need its own registration and its own K calibration.

### 12.6 Promotion assessment

| criterion | V1 | V2 | V5 |
|---|---|---|---|
| 1. causal and stable definition | pass | pass | pass |
| 2. meaningful relationship in development | **fail** | **fail** | **fail** |
| 3. does not collapse against control | not assessable (12.3) | not assessable | not assessable |
| 4. directionally consistent validation | **fail** (2/3 flip) | **fail** (2/3 flip) | **fail** (2/3 flip) |
| 5. not a transform of Stage-A selection | pass | pass | partial — truncated by the same lookback |
| 6. enough observations | pass | pass | pass |

**No feature is recommended for promotion.**

### 12.7 Is this underpowered?

No, for effect sizes worth acting on, though the honest answer has two parts.

Per observation the study is well powered: with ~3,500 per tercile, alpha
0.0056 and 80% power, the detectable effect is roughly d = 0.086. Observed
effects are all at or below d = 0.054, so an effect of even conventionally
"small" size (d = 0.2) would have been unmistakable.

But the day-level blocking is the binding constraint, and the effective sample
is closer to **38 development days** than to 11,919 observations. Against that
unit, only a fairly large and consistent daily effect would clear alpha. So this
result rules out a substantial, stable relationship; it does not rule out a
faint one that a much longer dataset might resolve. Given that Groww supplies
about three rolling months, that longer dataset is not currently obtainable.

### 12.8 Status after screening

| | |
|---|---|
| H001 EMA 9/21 | REJECTED |
| H002 ORB | REJECTED |
| H003 v1 Pullback Continuation | REJECTED |
| V1 terminal close location | screened — weak / inconclusive, not promoted |
| V2 volume trajectory | screened — weak / inconclusive, not promoted |
| V3 impulse velocity | DROPPED |
| V4 impulse efficiency | BLOCKED |
| V5 impulse duration | screened — rejected as constructed |
| hold-out | **UNREAD** |

No H004 v1 created. No features combined. No parameters optimised.

### 12.9 Recommendation

Nothing here earns promotion. Three observations worth carrying forward:

* **The Control C feasibility asymmetry must be fixed before the next
  screening**, or every future structural comparison inherits the same silent
  bias. This is a concrete, mechanical fix.
* **The measured ceiling is now visible.** Five variables have been screened
  around a single Stage-A definition — two rejected outright, two inconclusive,
  one dropped as an identity, one blocked as degenerate. That pattern suggests
  the limiting factor is no longer the choice of variable but the framing:
  every one of them conditions on the same impulse definition, and none has
  moved the needle.
* **The dataset is the binding constraint on resolving faint effects**, at 38
  development days with a roughly three-month rolling ceiling. Deciding whether
  a faint effect matters is a data-acquisition question, not a modelling one.

---

## Step 13 — Control framework repair and research framing review

All verdicts frozen: H001 REJECTED, H002 REJECTED, H003 v1 REJECTED, V1/V2
weak-inconclusive, V3 dropped, V4 blocked, V5 rejected as constructed. **The
hold-out was not loaded.**

### A. The Control C defect, and the fix

**Defect.** A control was sampled and *then* discarded if its forward window ran
past the close:

```
sample control bar  ->  compute forward return  ->  drop if it ran out of session
```

That is correct for a signal (the observation simply does not exist) and wrong
for a control, because it changes the population after selection. Within a
30-minute bucket an earlier bar is likelier to have room, so surviving controls
skew earlier in the session and capture more of the day's remaining drift.
Measured during the H004 screening: the whole stage-A population scored −0.0214
against a control drawn from **itself**, where the true edge is zero by
construction.

**Fix.** Eligibility is applied *before* sampling, to build the population:

```
build population -> keep only bars whose [t+1, t+h] fits the session -> sample
```

`TimeMatchedSampler` now takes `horizon` as a **required** argument. It cannot be
omitted, because a control population is only well defined against the window it
will be measured over — making it optional would let the defect return silently.
Every result now reports eligible population, sampled controls, exact/widened/
skipped percentages and horizon feasibility.

### B. Control A audit

Control A shares the signal's **own bar** — same index, same horizon — so it is
structurally immune: it is defined exactly when the signal is, and is the exact
negation of the signal's forward return. No change to its statistical meaning
("does the hypothesis know which direction?").

Pinned by test rather than left as an assumption: if Control A were ever rebuilt
from a neighbouring bar it would acquire precisely the asymmetry Control C had.

### C. Generic observation-eligibility layer

`app/services/observation_window.py` — one definition used by signals and
controls alike.

```
is_forward_window_valid(i, h)  ==  i + h <= session_end(i)
```

* **Session-aware** — sessions are derived by grouping bars on the IST date.
* **Holiday-aware without a calendar** — a holiday is a date with no bars, and
  nothing can span it because the window must stay inside one session. A
  hardcoded holiday list would go stale; this cannot.
* **No overnight leakage** — enforced by the same rule.
* **No future-data selection** — the rule reads only bar indices and timestamps.
* **Deterministic and reusable** — `SessionIndex` is built once per series;
  `forward_return_pct` delegates eligibility to it so no consumer can invent a
  different definition.

Missing bars are handled honestly rather than redefined: eligibility counts
*bars*, matching the convention every existing hypothesis already used, and
`window_is_contiguous` reports gaps separately instead of silently widening the
window.

### D. Test results — **274 passing, 0 failing**

New `tests/test_observation_window.py` (28 tests): last bar of session,
second-last, interior, exact boundary inclusivity, session boundary, holiday gap,
missing bars, horizons 6/12/24, feasibility reporting, and Control A's shared
eligibility.

The decisive one is `test_sampling_does_not_shift_controls_earlier_than_signals`,
which asserts the mean bar position of sampled controls does not drift from that
of their signals — the measurable symptom of the old defect, rather than a
restatement of the new code.

Making `horizon` required broke eight older control tests. That is the fix
working: they were updated to pass one explicitly.

### E. H003 v1 POST-HOC CONTROL SENSITIVITY

**Not a new experiment. H003 v1 = REJECTED, unchanged, no parameter touched.**
Development period only.

| variant | h | old Control C | p | **corrected Control C** | p | horizon feasible |
|---|---|---|---|---|---|---|
| A shallow | 6 | −0.0672 | 0.0000 | **−0.0690** | 0.0000 | 89.2% |
| A shallow | 12 | −0.0689 | 0.0018 | **−0.0726** | 0.0013 | 80.3% |
| A shallow | 24 | −0.0710 | 0.0328 | **−0.0690** | 0.0270 | 62.4% |
| B medium | 6 | −0.0690 | 0.0000 | **−0.0709** | 0.0000 | 89.2% |
| B medium | 12 | −0.0641 | 0.0080 | **−0.0688** | 0.0053 | 80.3% |
| B medium | 24 | −0.0638 | 0.0668 | **−0.0620** | 0.0890 | 62.4% |

Match quality 94-98% exact, 0.4-1.7% skipped.

**H003's structural conclusion is robust.** Every corrected edge lands within
0.005 of its uncorrected value, same sign, comparable p — even though the fix
removes 38% of the control population at the 24-bar horizon.

Why the same defect mattered for H004 and not H003: in H004 the signal
population *was* the stage-A population, so the true edge is zero and a 0.02
bias is the entire measurement. In H003 the signal is a strict subset and the
measured edge (−0.07) is several times the bias. A bias matters in proportion to
the effect it sits next to.

### F. What H001-H003 and the screening collectively teach us

| | conditioning event | result |
|---|---|---|
| H001 | EMA 9/21 crossover has occurred | worse than random, p≈0 |
| H002 | price has broken the opening range | worse than random, p≈0 |
| H003 | a ≥3.5 ATR impulse completed, then retraced | passes direction, fails structure |
| V1 | ...the terminal bar of that impulse | \|d\| ≤ 0.054, null |
| V2 | ...the volume path of that impulse | \|d\| ≤ 0.048, null |
| V5 | ...the duration of that impulse | \|d\| ≤ 0.013, null |

Six investigations, one finding: **describing a completed directional move more
precisely does not make it more informative.**

### G. The common framing assumption

Yes — we have been conditioning on the same market event six times.

Every hypothesis and every feature takes as given that **a large directional
move has already happened**, then asks whether it continues. H001's crossover
can only fire after a move has separated two averages; H002's breakout requires
price to have already left the range; H003 requires an explicit ≥3.5 ATR
impulse; V1, V2 and V5 are literally three descriptions of that same impulse.
The variation between them is in the adjective, not the noun.

Three reasons this framing is actively unfavourable, not merely repetitive:

1. **Selecting on a large realized move selects on its noise.** A window
   qualifies as a large impulse partly through genuine order flow and partly
   through transient noise, and the noise component is by construction not
   persistent. Conditioning on large realized moves therefore selects a
   population with a built-in reversion tendency, biasing *against* continuation
   before any rule is applied. Every continuation-flavoured result being
   negative is consistent with exactly this.
2. **The event is cheap and universally visible.** Stage A at K=3.5 retains a
   quarter of all opportunities — 15,182 distinct impulses over 50 days on 39
   liquid large-caps, about 8 per symbol-day. If a completed large move carried
   usable information, it would be the most heavily competed signal on the
   exchange.
3. **Our own control already demonstrated it.** Control C holds the impulse
   constant and randomises everything else. When every descriptor scores ~0
   against it, the arithmetic conclusion is that the impulse is doing all of the
   (non-)work and the descriptors add nothing.

The earlier entry diagnostics said the same thing in a different currency: for
the EMA signal, the move *before* the trigger was +0.228% with 87% favourable,
and *after* it was −0.005% with 47% favourable. We have been repeatedly arriving
after the event.

### H. Three genuinely different research directions

Each breaks the shared framing by conditioning on something other than a
completed large move. None is adopted; none has parameters.

---

**Direction 1 — Compression before expansion** *(conditions on the ABSENCE of movement)*

1. **Claim.** Periods where realized range is unusually small relative to that
   instrument's own recent norm resolve into expansion, and something observable
   during the compression indicates which way.
2. **Available before entry.** Realized range over a trailing window against its
   own distribution; where the close sits inside the compressed range; canonical
   volume during compression; time of day.
3. **Different how.** It is the exact inverse selection. Instead of selecting
   windows with large realized moves — and therefore large noise — it selects
   windows with small ones. That inverts the regression-to-the-mean bias rather
   than fighting it.
4. **Supported by this dataset.** Yes, entirely: bar ranges, ATR, close
   position, canonical per-bar volume.
5. **Missing.** Order-book depth, which is the natural mechanism (compression as
   liquidity provision). Not obtainable here.
6. **Baseline.** Control A unchanged. Control C becomes "random bar, same
   symbol/day/direction/time bucket, *not* compressed" — a cleaner prerequisite
   population than we have had, because the prerequisite is a state rather than
   an event.
7. **Testable in 63 days?** Unknown until the episode frequency is measured
   input-only. Compression episodes are rarer than impulses, and this is the
   first thing to check rather than assume.

---

**Direction 2 — Cross-sectional relative strength** *(conditions on RELATIVE, not absolute, position)*

1. **Claim.** At a given instant, a stock's move relative to the simultaneous
   move of its peers carries information that its own move alone does not — a
   stock rising against a falling cross-section is a different object from one
   rising with it.
2. **Available before entry.** Each symbol's return over a trailing window; the
   cross-sectional median across the 39 symbols at the same timestamp; the
   symbol's deviation from it; dispersion across the cross-section.
3. **Different how.** Fundamentally. Every prior hypothesis examined one symbol
   in isolation on a time-series axis. This conditions on position within a
   cross-section at a fixed instant.
4. **Supported by this dataset.** Yes — and notably **without an index feed**: an
   equal-weighted proxy is derivable from the 39 symbols already stored.
5. **Missing.** True NIFTY levels with correct weights; sector classification
   (which would separate "moving against the market" from "moving with its
   sector against the market").
6. **Baseline.** Control A unchanged, plus a genuinely stronger control this
   design makes possible: **shuffle the cross-sectional label across symbols at a
   fixed timestamp.** That holds the instant, the market-wide move and the
   prevailing volatility *exactly* constant, which no time-series control can do.
7. **Testable in 63 days?** Better than anything tried so far, for a reason that
   matters — see the recommendation.

---

**Direction 3 — Participation change without price displacement** *(conditions on VOLUME, not on a move)*

1. **Claim.** An abrupt change in participation — volume far from its own
   time-of-day norm — marks a change in who is trading, and that precedes
   directional resolution. The conditioning event explicitly *excludes* windows
   that have already made a large price move, which is the opposite of a climax
   setup.
2. **Available before entry.** Canonical per-bar volume; a time-of-day-normalised
   volume baseline; price displacement over the same window, used to *exclude*
   large moves rather than require them.
3. **Different how.** The conditioning variable is participation rather than
   displacement, and the large-move condition is inverted from requirement to
   exclusion.
4. **Supported by this dataset.** Only since the volume work: per-bar volume is
   now canonical, cross-validated against live ticks, and quality-flagged. This
   direction was not honestly testable a week ago.
5. **Missing.** Trade-level data with aggressor side, which is what would turn
   "participation changed" into "who was pushing". Order-book depth likewise.
6. **Baseline.** Control A; Control C over all bars in the same session and time
   bucket. Volume's strong intraday U-shape makes the time-matched control
   essential rather than optional here.
7. **Testable in 63 days?** Yes — the conditioning event is frequent, so
   observation count will not be the binding constraint.

### I. Data gaps

| gap | classification | note |
|---|---|---|
| Canonical per-bar volume | **AVAILABLE NOW** | fixed and cross-validated in Steps 10-11 |
| Equal-weighted index proxy | **CAN DERIVE** | cross-sectional median of the 39 stored symbols; needs no new source |
| Sector labels | **CAN DERIVE** | 39 manual labels; enables sector-relative vs market-relative separation |
| 1-minute bars | **CAN DERIVE — worth verifying** | the API supports the interval; ~5x observations per day directly attacks the power ceiling. Depth and per-request window at 1m are unmeasured and would need the same probe treatment as 5m |
| True NIFTY index level | REQUIRES NEW DATA SOURCE | the derived proxy is a substitute, not an equivalent |
| History beyond ~3 months | REQUIRES NEW DATA SOURCE | measured hard limit: 5-minute history starts 2026-06-01 |
| Trade-level data / aggressor side | REQUIRES NEW DATA SOURCE | would materially strengthen Direction 3 |
| Order-book depth | REQUIRES NEW DATA SOURCE | the mechanism behind Direction 1 |
| Bid-ask spread per symbol | REQUIRES NEW DATA SOURCE | the 0.183% cost floor currently assumes uniform slippage across all 39 symbols, which is certainly wrong in detail |
| Pre-open auction volume, separated | REQUIRES NEW DATA SOURCE | why every session's first bar is flagged `FIRST_BAR` rather than `OK` |
| **Corporate actions / splits** | **NOT VERIFIED — a real gap** | an unadjusted split inside the window would appear as a huge overnight gap. Large gaps are **CAN DERIVE** as candidates, but confirming them REQUIRES NEW DATA SOURCE. This has not been checked and should be before any cross-sectional work |
| Independent trading days | REQUIRES NEW DATA SOURCE, or time | grows one per day via `update()` |

### J. Recommended next direction — **Direction 2, cross-sectional relative strength**

Not because it sounds more profitable, but for three specific reasons:

1. **It is the most orthogonal to everything already tested.** Directions 1 and
   3 still condition on a time-series property of a single instrument.
   Direction 2 changes the axis rather than the adjective — and the audit above
   says the axis is what has been wrong.
2. **It needs no new data.** The equal-weighted proxy comes from the 39 symbols
   already stored and validated.
3. **It attacks the statistical ceiling that has capped every result so far.**
   Everything to date has been limited to roughly 38 effective observations,
   because signals within a day share a market-wide move and the day is the unit
   of independence. A cross-sectional comparison at a fixed timestamp
   *differences that move out by construction*: the market factor is common to
   both sides and cancels. The residual is idiosyncratic, so the effective
   sample is far larger for the same calendar span. That is a structural
   improvement in power, not merely a different question.

The control it enables — shuffling cross-sectional labels within a timestamp —
is also the strongest we have been able to construct, holding time, market move
and volatility exactly rather than approximately constant.

**Before any of it:** check for unadjusted corporate actions. A split inside the
window would masquerade as an extreme relative-strength observation and would
corrupt a cross-sectional study specifically.

### K. Hold-out

**Untouched.** Nothing in this step read a forward outcome except the H003
post-hoc, which used the development period only. The validation and hold-out
periods remain unread by any screening.

---

## Step 14 — Corporate-action audit, 1-minute capability, cross-sectional design

All verdicts frozen. **Hold-out not loaded.** No forward outcome was read in
this step.

### A. Corporate-action findings

**Price-adjustment semantics: UNKNOWN / NOT VERIFIED.**

| check | finding |
|---|---|
| project code | no adjustment, split, bonus or corporate-action handling anywhere in `groww_client.py`, `backfill.py` or the research package |
| SDK signature | `get_historical_candle_data(trading_symbol, exchange, segment, start_time, end_time, interval_in_minutes, timeout)` — **no adjustment parameter exists** |
| SDK docstring | no mention of adjustment, splits, bonuses or corporate actions |

The API neither offers nor documents an adjustment policy, so the semantics
cannot be established from the interface. Nothing has been adjusted, and nothing
should be until the policy is known.

**Empirical scan of the research window (2026-06-01 -> 2026-08-28):**

| measure | value |
|---|---|
| overnight gaps examined | 2,428 across 39 symbols |
| median / p95 / p99 | 0.337% / 1.518% / 2.468% |
| maximum | **6.755%** |
| gaps > 10% | **0** |
| exact split/bonus ratio matches (2:1, 3:1, 5:1, 10:1, 1:2, 2:3, within 3%) | **0** |
| intraday bar-to-bar jumps > 5% | **0** |

**No unadjusted corporate action is detectable inside the research window.**

Stated precisely, because the distinction matters: this does *not* prove the
feed is adjusted. It is equally consistent with no corporate actions having
occurred for these 39 large-caps in a three-month span, which is entirely
ordinary. The dataset appears usable as it stands; the semantics remain
unverified, so a longer or different universe could contain one.

The largest gaps are also informative for what comes next: 2026-06-19 shows
TECHM −6.76%, INFY −5.80%, TCS −4.45%, HCLTECH −4.26% — four IT names on one
day. That is a sector event, not a corporate action, and it is exactly the
structure a cross-sectional design has to reckon with.

**Recommendation (not implemented):** add the gap and ratio-match scan to the
dataset validator as a standing check. It detects the failure mode regardless of
whether the adjustment policy is ever established, which is the property that
matters.

### B. 1-minute API capability

Probed with the same discipline used for 5-minute.

| property | 1-minute | 5-minute (for comparison) |
|---|---|---|
| max request window | **7 days** (8 refused) | 15 days |
| history depth | **starts 2026-06-01** | starts 2026-06-01 |
| accessible range | **2026-06-01 -> present, ~63 trading days** | identical |
| old windows reachable | yes, at 70 and 80 days back | yes |
| bars per full session | **367 observed** vs 375 nominal (97.9%) | 75 of 75 |
| rate limiting | none observed | none observed |

**Missing-bar behaviour — a systematic finding.** RELIANCE and TCS, on both
2026-08-26 and 2026-08-27, each returned exactly 367 bars, first 09:15, last
15:28, with the identical two gaps: **15:15 -> 15:20** and **15:24 -> 15:28**.

Four identical patterns across two symbols and two days is not absent trading in
India's two most liquid names; it is a feed artifact. It also lands in the same
place as the volume-counter resets found earlier, every one of which fell in the
15:00 hour. Something in the last fifteen minutes of the Groww feed is
structurally unreliable, and 1-minute data exposes it more sharply than
5-minute does.

Practical cost of a full 1-minute backfill, if ever wanted: 39 symbols x ~88
calendar days at 7-day windows is roughly 500 requests, about 10 minutes at the
current 1.2s spacing, producing ~900,000 rows. Feasible — `CHUNK_DAYS["1m"]`
would need raising from 3 to 6, still under the measured ceiling of 7. **Not
done.** Only two symbols and two days were ingested as a probe; the 5-minute
dataset is untouched (184,771 rows, 39 symbols, unchanged).

### C. Recommended cross-sectional research design

**Question.** At a fixed timestamp, does relative strength across stocks contain
information about subsequent *relative* performance?

**The two properties that make this different from everything tested so far:**

1. Stocks are compared at the **same timestamp**, so the market-wide move at
   that instant is common to every observation and cancels by construction.
2. The outcome is **relative**, not absolute: a stock's forward return minus the
   cross-sectional median forward return over the same window. Measuring an
   absolute forward return would simply re-import the market move the design
   exists to remove.

**Observation sampling.** Timestamps drawn every N bars, non-overlapping, so no
two observations share a lookback window. At N = 12 on 5-minute bars that is
about 6 timestamps per session.

### D. Candidate scores — three, one recommended

All are computed from bars <= t only, and all use the same lookback N.

**X1 — return relative to the cross-sectional median**

```
r_i(t)     = close_i(t) / close_i(t-N) - 1
score_i(t) = r_i(t) - median_j r_j(t)
```

Simplest possible expression of the claim. One parameter (N).

**X2 — volatility-adjusted relative return** *(recommended)*

```
score_i(t) = [ r_i(t) - median_j r_j(t) ] / (ATR_i(14, t) / close_i(t))
```

Same numerator, divided by the stock's own relative volatility.

**X3 — cross-sectional percentile rank**

```
score_i(t) = percentile rank of r_i(t) among all j at time t
```

Robust to outliers; discards magnitude entirely.

**Recommendation: X2.**

X1 is simpler, but it has a known identity problem of exactly the kind that
sank V3. On a day the market moves, high-volatility names move further for the
same information, so ranking raw relative returns ranks *beta* — and the study
would be measuring a well-known and uninteresting property while calling it
relative strength. X2 removes that before the test rather than discovering it
afterwards.

The volatility normaliser is ATR(14), which is this project's established
default and was chosen long before this question existed, so it is inherited
rather than fitted. Parameter count stays at one.

X3 is retained as a described alternative but not recommended: discarding
magnitude throws away information for a robustness benefit that a 39-name
universe does not obviously need.

**A predictor-side diagnostic, not a second test:** the correlation between X1
and X2, and between X1 and a simple volatility proxy, is computable from inputs
alone and would show directly how much of X1 is beta. That reads no forward
outcome and is not hypothesis shopping.

**Lookback N = 12 bars (one hour at 5-minute).** Inherited from the impulse
lookback already fixed in this project rather than newly chosen. If it needs
justifying independently it should be calibrated the way K was — from the
predictor distribution alone, never from outcomes.

### E. Control and null design

**Permutation: shuffle stock labels within each timestamp.**

```
for each timestamp t:
    take the 39 scores and the 39 forward relative returns
    randomly re-pair them
```

**Null hypothesis tested.** *At a given timestamp, the assignment of
cross-sectional scores to stocks carries no information about which stocks
subsequently outperform the cross-section.* Formally: scores are exchangeable
across stocks within a timestamp.

**Preserved exactly** — not approximately, which is what makes this stronger
than any control used so far:

| preserved | how |
|---|---|
| timestamp | permutation is within a single instant |
| universe | the same 39 names appear on both sides |
| market condition | the market-wide move is common to all and identical under permutation |
| volatility environment | unchanged; the whole cross-section is the same set of numbers |
| the marginal distribution of scores | unchanged — only the pairing is broken |
| the marginal distribution of forward returns | unchanged |

**Destroyed:** only the link between a stock's own score and its own forward
return. That is precisely the thing under test.

No modelling assumption about the market factor is required, because it cancels
identically rather than being estimated and subtracted.

**Dependence handling.** Consecutive timestamps within a day remain correlated —
a stock relatively strong at 10:00 tends to still be so at 10:05 — so
significance is still assessed with day-level blocking, and timestamps are
sampled non-overlapping. Control A is retained unchanged as the direction test.

### F. Data and power assessment

**A correction to what I said last step.** I described the cross-sectional
design as giving a "far larger effective sample". That was loose and I want to
state it accurately: **it does not increase the number of independent blocks.**
Days remain the unit of independence, so development still has 38 of them.

The gain is different, and real: it **reduces the variance inside each block**.
Every result so far has been dominated by the day's market-wide move — the ORB
analysis measured a day-to-day standard deviation of 0.35 on a mean of 0.85,
roughly 40% relative. A within-timestamp contrast removes that factor exactly,
so what remains is idiosyncratic. Power comes from a smaller denominator, not
from more observations.

The size of that reduction is **measurable from inputs alone** — compare the
variance of raw returns against the variance of cross-sectional residuals — and
should be measured before committing, exactly as K was calibrated before H003.

| question | assessment |
|---|---|
| Is 5-minute sufficient? | **Yes, for the first test.** At N = 12 non-overlapping it gives ~6 timestamps per session, 38 dev days, 39 names — roughly 8,900 stock-observations across 228 timestamps |
| Would 1-minute materially help? | **Not for this.** It yields ~5x the raw timestamps, but a stock's relative rank at 10:00 and 10:01 is nearly the same number, so the extra observations carry little independent information. It would matter only for a genuinely short-lived effect measured at sub-5-minute horizons — and it brings the 15:15-15:29 gap problem with it. **Do not backfill 1-minute yet.** |
| Is the 39-stock universe sufficient? | **Workable but thin.** Deciles hold four names each, and the cross-sectional median is estimated from 39 points |
| Would more symbols be valuable? | **Yes — this is the highest-value data step.** ~100 liquid NSE names would sharpen rank resolution and stabilise the cross-sectional median, and unlike everything else on the wish-list it needs no new provider: same API, same 2026-06-01 depth, roughly 1,300 requests and ~470k additional rows |

The clear conclusion on data: **widen the universe, not the frequency.** A
cross-sectional design gains from more names per timestamp and gains very little
from more timestamps per day.

One caution carried forward from the gap scan: the 2026-06-19 IT cluster shows
that sector co-movement is a live confound. Sector labels are derivable by hand
for 39 (or 100) names and would separate "moving against the market" from
"moving with its sector against the market". That is a real limitation of the
design as stated, not a refinement to bolt on later.

### G. Recommended next step

In order, none of it started:

1. **Widen the universe to ~100 liquid NSE symbols at 5-minute**, same date
   range. Cheap, no new provider, and it is the input the cross-sectional design
   is most sensitive to.
2. **Run the gap and ratio-match scan over the widened universe** before using
   it. A wider universe has a materially higher chance of containing a real
   corporate action, and this is the one screen that catches it without knowing
   the adjustment policy.
3. **Measure the variance reduction from cross-sectional differencing**, input
   only, to establish that the power argument in F actually holds on this data
   before a hypothesis is registered on it.
4. **Then pre-register the hypothesis** with X2, N = 12, the within-timestamp
   permutation null, relative forward returns at 6/12/24 bars, day-level
   blocking, and a fresh chronological split over the widened universe.

**Not started, and not to be started before review:** no hypothesis registered,
no generator written, no forward outcome inspected, no hold-out read.

---

## Step 15 — Expanded universe, quality audit, cross-sectional power test

All verdicts frozen. **No forward return was computed. Hold-out not loaded.**
H004 remains NOT DEFINED.

### A. Expanded universe

127 candidates proposed from NIFTY-100-style large and mid caps, each checked
against the Groww instrument master before fetching. **125 verified, 2 rejected
and not added:**

| rejected | reason |
|---|---|
| LTIM | not in instrument master (renamed on NSE) |
| ZOMATO | not in instrument master (renamed on NSE) |

All 125 are NSE CASH, `series = EQ` (mainboard equity), `intraday_allowed = True`,
each with a resolved ISIN. Verification caught two stale symbols that would
otherwise have produced silent gaps.

**Ingest: 86 new symbols, 86 succeeded, 0 failures, 0 rejected rows, 411,745 new
candles.**

| | before | after |
|---|---|---|
| symbols | 39 | **125** |
| candles | 184,771 | **596,516** |
| range | 2026-06-01 .. 2026-08-28 | unchanged |

**Coverage.** 96 symbols carry 64 days, 29 carry 63. The difference is entirely
2026-08-28, which is the **current, still-trading session** — not a data defect.
Most common bar counts are 4,787 / 4,788 / 4,725, i.e. 63-64 sessions of 75.

**Liquidity, measured rather than assumed.** Median per-bar turnover ranges from
Rs 12.4 lakh (BERGEPAINT) to Rs 21.6 crore (HDFCBANK). **Zero symbols fall below
Rs 10 lakh per bar.** The universe is genuinely liquid throughout; the thinnest
names are BERGEPAINT, TORNTPOWER, BAJAJHLDNG, SHREECEM.

### B. Data-quality results

```
DATASET QUALITY REPORT
  Symbols:            125          Duplicate candles:   0
  Period:  2026-06-01 -> 2026-08-28 Invalid OHLC rows:   0
  Trading days:       64           Invalid volume rows: 0
  Total candles:      596,516      Timestamp issues:    0
                                   Missing intervals:   1,240
                                   Holidays detected:   1  (2026-06-26)
  STATUS: WARNING
```

The WARNING is driven entirely by the partial current session: every one of the
first 96 flagged shortfalls is dated 2026-08-28. Excluding it leaves **63
complete sessions**, which is what the analysis below uses. Per-symbol missing
bars on complete days peak at 25 of ~4,800 (0.52%).

**Volume semantics, across all 125:**

| quality | rows | share |
|---|---|---|
| OK | 583,922 | 97.89% |
| FIRST_BAR | 7,971 | 1.34% |
| UNKNOWN | 4,623 | 0.78% |

**Zero sessions have a per-bar volume series that never falls** — the signature
of a cumulative column, and its absence confirms the canonical contract holds
across the expanded universe, not just the original 39.

**End-of-session anomaly confirmed at scale.** All 4,623 UNKNOWN bars fall in the
**15:00 hour**, with none earlier. The same signature now appears across 125
symbols and 63 days, alongside the 1-minute gaps at 15:15-15:20 and 15:24-15:28
found earlier. Something in the final fifteen minutes of the Groww feed is
systematically unreliable. Flagged, not corrected.

### C. Cross-sectional variance decomposition — **this partly refutes my own rationale**

12-bar returns, 63 complete sessions, 441 non-overlapping timestamps (~7 per
session), 54,986 stock-timestamp observations.

| transformation | variance | std | reduction |
|---|---|---|---|
| A raw return | 3.848e-05 | 0.6203% | — |
| B minus cross-sectional median | 3.208e-05 | 0.5664% | **16.6%** |
| C minus sector median | 2.360e-05 | 0.4858% | **38.7%** |

**The market common-mode is 16.6% of total variance — not the dominant term.**
I argued in Steps 13 and 14 that cross-sectional differencing would remove the
variance that has swamped every result. Measured, it removes about a sixth.
That is the input-only power test doing its job, and it corrects me.

**Sector explains a further 22.0% — more than the market factor does.** That was
not anticipated and it is the single most consequential finding in this step.

Internal consistency check: a 16.6% common-mode share implies an average
pairwise correlation near 0.166, and the measured mean pairwise correlation is
0.1754. The decomposition is self-consistent.

**But the naive reading of that 16.6% understates the gain**, and the reason
matters. A day-level statistic pooling K signals has variance

```
Var = sigma_market^2 + sigma_idio^2 / K
```

The idiosyncratic term shrinks as K grows; **the market term does not**. With
the measured split and K = 40 signals per day, the market component accounts for
about 89% of what remains, and adding more signals cannot reduce it. Removing it
converts a variance *floor* into a term that shrinks with sample size — roughly
a 9x variance reduction, or 3x on the standard error, at K = 40.

So the correct statement is neither "it removes the dominant variance" (Step 13,
too strong) nor "it only reduces within-block variance" (Step 14, too weak): it
removes the component that does not average away. That is a real and large gain,
for a reason different from the one I originally gave.

### D. Sector effect

| | all pairs | within sector | cross sector | difference |
|---|---|---|---|---|
| raw returns | 0.1754 | **0.3339** | 0.1635 | +0.1704 |
| after removing cross-sectional mean | — | **0.1771** | −0.0208 | +0.1979 |

(16 hand-assigned sectors, 125 symbols, all labelled; 542 within-sector pairs,
7,208 cross-sector.)

Within-sector correlation is roughly double cross-sector, and — decisively —
**the gap does not close when the market is removed; it widens slightly**
(+0.170 -> +0.198). Market-neutralisation therefore leaves the sector factor
almost entirely intact.

The practical consequence: a cross-sectional score that neutralises only the
market will substantially rank **sectors** rather than stocks. On 2026-06-19,
TECHM −6.76%, MPHASIS −6.83%, INFY −5.80%, TCS −4.45% and HCLTECH −4.26% would
all have ranked at one extreme together, and the score would have been measuring
"IT had a bad morning".

Per instruction, sector neutralisation has **not** been added to X2. It is
reported as a design decision requiring review.

### E. Corporate-action findings — expanded universe

| check | result |
|---|---|
| overnight gaps examined | 7,846 |
| median / p95 / p99 | 0.330% / 1.446% / 2.442% |
| maximum | **10.000%** (GODREJCP, 2026-08-12) |
| gaps > 10% | **0** |
| ratio-signature matches (>15% near an exact split/bonus ratio) | **0** |
| intraday bar-to-bar jumps > 5% | **0** |

**Adjustment status remains UNKNOWN / NOT VERIFIED.** The SDK exposes no
adjustment parameter and documents no policy; absence of extreme events is not
evidence of adjustment support, and is not being read as such.

**Symbols requiring manual/reference-data verification: 3** — GODREJCP, LICI,
MUTHOOTFIN. GODREJCP is the one to check first: 1025.00 -> 922.50 is *exactly*
−10.00%, which is a suspiciously round figure suggesting a circuit limit or a
data artifact rather than ordinary trading. None of the three matches a split or
bonus ratio.

### F. X2 input-only audit

54,861 observations; **125 missing (0.23%)**, all from an absent or zero ATR.

| property | value |
|---|---|
| distribution | p1 −4.881, p25 −1.242, **median +0.000**, p75 +1.304, p99 +5.404 |
| range / stdev | −11.94 to +13.63, stdev 2.073 |
| denominator ATR/close | p1 0.098%, median 0.202%, p99 0.495%, **min 0.057%** |
| denominator below 0.01% of price | **0** |

**Denominator stability: sound.** The smallest ATR/close observed is 0.057% of
price, three orders of magnitude above zero. There is no division-blow-up risk
and no clipping is needed.

**Is X2 a disguised volatility ranking? No.**

| | median ATR/close |
|---|---|
| bottom decile of X2 | 0.2176% |
| top decile of X2 | 0.2336% |
| all observations | 0.2019% |

corr(X2, volatility proxy) = **+0.039**. The deciles are nearly identical in
volatility, so X2 ranks direction, not risk.

**A correction to my own audit.** The first run reported corr(X2, raw relative
return) = +0.033, which was an artifact: I sliced two arrays of different lengths
against each other, so 125 missing-ATR rows shifted the pairing. Recomputed on
aligned triples: **corr(X2, raw relative return) = +0.9134.**

That number matters for the X1-versus-X2 decision, and it weakens my earlier
argument. X2 and X1 share a numerator and are 91% correlated, so volatility
normalisation adjusts the ranking at the margin rather than measuring a
different thing. It is still the better choice — it removes a systematic
component rather than leaving it — but I over-sold it in Step 14 by implying X1
would essentially be a beta ranking. corr(raw relative return, volatility) is
+0.039, so the beta contamination in X1 is small to begin with.

**Cross-symbol comparability: adequate.** Per-symbol mean X2 spans −0.169
(INDUSTOWER) to +0.423 (TITAN), stdev 0.110. Two checks on whether that is a
problem:

* the symbol accounts for **0.28% of X2's total variance**;
* 6 of 125 symbols sit more than 2 SE from the universe mean — 5% of 125 is 6.25,
  i.e. exactly the chance rate;
* split-half correlation of per-symbol mean X2 across the first and second half
  of the period is **+0.187** — weak.

Symbol-level persistence is real but small, and not a material threat to the
design. It should still be reported alongside any future result rather than
assumed away.

### G. Null / control design

**Within-timestamp label permutation.**

```
for each sampled timestamp t:
    hold the 125 scores and the 125 forward relative returns fixed
    randomly re-pair them across stocks
```

**Hypothesis tested:** at a given instant, the assignment of cross-sectional
scores to stocks carries no information about which stocks subsequently
outperform the cross-section. Formally, scores are exchangeable across stocks
within a timestamp.

**Preserved exactly, not approximately:** the timestamp; the universe; the
market-wide move at that instant; the volatility environment; the marginal
distribution of scores; the marginal distribution of forward returns. **Destroyed:**
only the score-to-stock pairing.

No model of the market factor is needed, because it is common to every
observation in the permuted set and cancels identically.

**Why the test stays valid with many symbols observed at one timestamp.** This
is exactly the case the permutation is built for. Ordinary tests assume
independent observations and break when 125 stocks share a market move; the
permutation makes no independence assumption *across stocks within a timestamp*,
because it conditions on that timestamp's realised cross-section and only
re-pairs labels. Any common factor — market or sector — is identical in every
permutation and therefore contributes nothing to the null distribution.

Two dependence caveats remain and are handled outside the permutation:

* **Across timestamps within a day**, scores are autocorrelated. Handled by
  sampling non-overlapping timestamps (every 12 bars) and blocking significance
  at the day level.
* **Sector clustering within a timestamp** is *not* removed by the permutation:
  if IT moves together, shuffling labels breaks the stock-score link but a
  sector-driven score would still correlate with sector-driven returns. This is
  the strongest argument for deciding the sector question before registration.

Control A (same bar, random direction) is retained unchanged as the direction
test.

### H. Power assessment

**The appropriate dependence unit is the trading day, not the stock-timestamp.**
54,986 stock-timestamps is emphatically not 54,986 independent observations.

The layers of dependence, measured:

| level | evidence |
|---|---|
| across stocks at one instant | mean pairwise correlation 0.175 |
| within sector at one instant | 0.334, and still 0.177 after market removal |
| across timestamps within a day | autocorrelated; mitigated by non-overlapping sampling |
| across days | the safest independent block |

| universe | timestamps/day | stock-observations/day | effective independent units per timestamp |
|---|---|---|---|
| 39 stocks | ~7 | ~270 | bounded by 39; with sector clustering realistically ~10-16 |
| **125 stocks** | ~7 | **~875** | bounded by 125; with 16 sectors realistically **~16-40** |

The honest summary: expanding from 39 to 125 roughly triples the raw
cross-section and materially improves rank resolution — deciles now hold 12-13
names instead of 4, and the cross-sectional median is estimated from 125 points
rather than 39. It does **not** triple the independent information, because
sector clustering binds. Significance must still be assessed with day-level
blocking over 63 sessions.

The real power gain remains the one in section C: removing the market factor
eliminates the variance component that does not shrink as observations are
pooled.

### I. Is X2 ready for pre-registration? — **Not yet. One decision first.**

X2 itself is in good shape: well-behaved distribution, stable denominator,
0.23% missingness, demonstrably not a volatility ranking, and only trivial
symbol-level persistence.

The blocker is not X2's construction but a design question the measurements
raised:

> **The sector factor (22.0% of variance) is larger than the market factor
> (16.6%), and market-neutralisation leaves it intact.**

A market-only score will rank sectors substantially, which means a positive
result would be ambiguous between "relative strength predicts" and "sector
rotation predicts" — and the within-timestamp permutation cannot separate them,
because it preserves the sector structure it would need to break.

Three options, for review — I am not choosing one, since it changes what the
hypothesis claims:

1. **Register X2 as-is** and report the sector confound as a stated limitation.
   Simplest, keeps the pre-registration honest, but accepts an ambiguous result.
2. **Register a sector-neutral variant** (subtract the sector median instead of
   the cross-sectional median). One extra piece of reference data, no extra
   fitted parameter, and it tests a sharper claim: relative strength *within* a
   sector.
3. **Register X2 as primary with a sector-neutral secondary**, pre-declared
   together with the multiple-comparison count adjusted from the start.

**Recommendation: option 2**, on the evidence — it targets the larger factor and
asks a question the data can actually answer unambiguously. But this is a
change to what the hypothesis claims, so it is a decision for review rather than
one to make inside an audit.

Also to settle before registration: verify GODREJCP, LICI and MUTHOOTFIN against
reference data, and decide whether to exclude the 15:00-15:30 window given the
now-confirmed feed unreliability there.

---

## Step 16 — Suspicious symbols, 15:00-15:30, and the sector-neutral score

All verdicts frozen. **No forward return computed. Hold-out not loaded.**
H004 remains NOT DEFINED.

### A. Suspicious-symbol investigation — all three are genuine market events

| | GODREJCP 2026-08-12 | LICI 2026-08-04 | MUTHOOTFIN 2026-08-03 |
|---|---|---|---|
| gap | **−10.0000%** | −8.5181% | −8.0004% |
| open/prev-close ratio | 0.900000 | 0.914819 | 0.919996 |
| day range | 4.75% | 3.43% | 4.83% |
| traded below the open? | **yes** (low 908.00) | yes (388.85) | yes (2766.10) |
| bars sharing one close price | 4 of 75 | 5 of 75 | 4 of 75 |
| volume vs typical day | **23.69x** | **13.13x** | **10.32x** |
| recovered above pre-event close within 5 sessions | no | no | no |
| median price 10 sessions before -> after | 1070.80 -> 931.00 (0.869) | 424.40 -> 400.55 (0.944) | 3019.40 -> 2889.95 (0.957) |

**GODREJCP — the exact −10.000% is a coincidence of two round numbers, not a
circuit.** 1025.00 x 0.9 = 922.50 exactly, but the evidence rules out a limit:
the stock traded to a **low of 908.00, which is −11.4% from the prior close** —
below the supposed −10% floor. A lower circuit prevents trading below the band;
this stock traded through it. The day also had a 4.75% range with only 4 of 75
bars sharing a price, so it was not pinned at any level.

**None is a corporate action.** A split or bonus re-levels price permanently at
an exact ratio with ordinary volume. Here all three show 10-24x normal volume,
free two-way trading, and before/after price ratios (0.869, 0.944, 0.957) that
do not match the gap or any standard ratio.

| symbol | classification | confidence |
|---|---|---|
| GODREJCP | genuine market movement (news-driven repricing) | high |
| LICI | genuine market movement | high |
| MUTHOOTFIN | genuine market movement | high |

**Recommended treatment: retain all three, unadjusted and unexcluded.** Removing
them would strip out large idiosyncratic moves, which is precisely the
observation a relative-strength study most needs. They are documented here
rather than deleted.

Caveat kept honest: the environment has no corporate-action reference feed, so
this is an inference from trading behaviour, not a confirmation. The broker's
**adjustment policy remains UNKNOWN / NOT VERIFIED.**

### B. 15:00-15:30 quality audit — price and volume differ, and must be treated differently

| slot | coverage | volume OK% | UNKNOWN% | mean abs return | mean range |
|---|---|---|---|---|---|
| 14:30-14:55 | 98.4-98.9% | **100.0%** | 0.0% | 0.085-0.092% | 0.178-0.182% |
| 15:00 | 98.4% | **100.0%** | 0.0% | 0.1431% | 0.2601% |
| 15:05 | 98.4% | **100.0%** | 0.0% | 0.0960% | 0.2009% |
| 15:10 | 98.4% | **100.0%** | 0.0% | 0.1066% | 0.2236% |
| **15:15** | 98.3% | 91.7% | **8.3%** | 0.0802% | 0.1831% |
| **15:20** | 98.4% | 70.6% | **29.4%** | 0.1647% | 0.3237% |
| **15:25** | 97.9% | 78.9% | **21.1%** | 0.1561% | 0.2554% |
| midday reference | 99.6% | 100% | 0% | 0.0905% | 0.1791% |

**A. Price — trustworthy.** Coverage holds at 97.9-98.4% against a midday 99.6%,
a difference of about one percentage point. Returns and ranges are elevated into
the close (0.16% vs 0.09% at 15:20) but that is ordinary end-of-session
behaviour, not corruption — there is no discontinuity, no missing-bar cliff, and
the elevation is smooth.

**B. Volume — unreliable from 15:15 onward, and only from 15:15.** The break is
sharp: 100% OK through 15:10, then 8.3% / 29.4% / 21.1% UNKNOWN. Everything
before 15:15 is clean.

**C. Timestamps — perfect.** **Zero** bars across all 125 symbols and 596,516
rows sit off a 5-minute boundary.

**Recommended treatment:**

* **Price-based research: include the full 15:00-15:30 window.** Excluding it
  would discard sound data; elevated end-of-session volatility is handled by the
  time-of-day matching the control framework already applies.
* **Volume-based research: exclude bars where `volume_quality != OK`,** which is
  a per-bar rule, not a window rule. Dropping 15:15-15:30 wholesale would throw
  away the 71-92% of bars in that window that are fine.

Since the sector-neutral score uses price only (returns and ATR), **the full
session is usable for H004.**

### C. Final sector-neutral score definition

```
For stock i at timestamp t, with lookback N = 12 bars:

  return_i(t)        = close_i(t) / close_i(t-N) - 1

  sector_return_i(t) = median{ return_j(t) : j in sector(i), j != i }     [leave-one-out]

  sector_relative_return_i(t) = return_i(t) - sector_return_i(t)

  score_i(t) = sector_relative_return_i(t) / ( ATR_i(14, t) / close_i(t) )
```

**Leave-one-out is not cosmetic — the audit shows it is required.** With a plain
sector median, **6.81% of scores are exactly zero**, because the median stock is
its own benchmark and its relative return is zero by construction. Leave-one-out
reduces that to **0.02%**, while correlating **+0.988** with the plain version —
so it removes the degeneracy without distorting the measure.

**Audit results:**

| property | value |
|---|---|
| observations | 51,776 |
| missingness | **0.25%** (118 no ATR, 12 sector below minimum) |
| distribution | p1 −4.914, p25 −1.258, median −0.000, p75 +1.288, p99 +5.215 |
| stdev | 2.046 |
| denominator ATR/close | min **0.0573%**, p1 0.0981%, median 0.2008% |
| denominator below 0.01% of price | **0** |

**Denominator stability: sound.** The smallest observed ATR/close is 0.057% of
price — three orders of magnitude clear of zero. No clipping or winsorising is
needed, and none is proposed.

**Cross-sector comparability: good, with one caveat.** Per-sector means all sit
within +0.001 to +0.071 of zero. Per-sector standard deviations range 1.893
(METAL) to 2.334 (CHEM).

**Sensitivity to sector size — a real effect, quantified:**

| sector size | observations | score stdev | \|score\| p99 |
|---|---|---|---|
| 5 | 4,375 | **2.202** | 6.12 |
| 7 | 9,219 | 2.146 | 6.03 |
| 8 | 7,023 | 2.002 | 5.65 |
| 11 | 9,653 | 2.044 | 5.78 |
| 12 | 10,531 | **1.922** | 5.37 |
| 16 | 7,024 | 2.033 | 5.76 |

Smaller sectors produce more dispersed scores — 2.202 at size 5 against 1.922 at
size 12, a 15% difference — because a median over fewer peers is a noisier
benchmark. If terciles were formed by pooling all stocks globally, small-sector
names would be over-represented at both extremes by roughly that factor.

**Proposed resolution, needing no extra transformation or parameter: form
buckets WITHIN sector, then pool.** A stock is compared against its own sector's
score distribution, so differing dispersion between sectors becomes irrelevant
by construction. This is a bucketing rule, not a change to the score.

### D. Sector reference rule and sizes

**Rule.** Sector membership is assigned by hand from company identity — the line
of business the issuer is in — using the frozen map below. It is reference
information, contains no return data of any kind, is fixed before any forward
test, and is deterministic: every symbol maps to exactly one sector.

| sector | n | | sector | n |
|---|---|---|---|---|
| FIN | 16 | | POWER | 8 |
| BANK | 12 | | METAL | 7 |
| FMCG | 12 | | ENERGY | 7 |
| AUTO | 11 | | CONSUMER | 7 |
| PHARMA | 11 | | CEMENT | 5 |
| CAPGOODS | 9 | | CHEM | 5 |
| IT | 8 | | **TELECOM** | **3** |
| | | | **REALTY** | **2** |
| | | | **CONGLOM** | **2** |

All 125 symbols are labelled; none is unclassified.

**Minimum sector size: 5, proposed a priori and not tuned.** The reasoning is
structural rather than empirical. With leave-one-out, a sector of size *n*
benchmarks each stock against *n−1* peers. At n = 2 the peer group is a single
stock, so the "sector return" is one company's return and the two members become
perfect mirror images. At n = 3 the peer median is again one stock. Five members
gives a four-peer median, the smallest group where the benchmark is a genuine
group statistic rather than an individual. No performance figure was consulted.

**Excluded by the rule: TELECOM (3), REALTY (2), CONGLOM (2) — 7 stocks. Usable
universe: 118.** The alternative, merging small sectors into larger ones, was
rejected: reshaping the map to clear a threshold would be fitting the reference
data to the rule.

### E. Exact cross-sectional null

**Within-timestamp, within-sector label permutation.**

```
for each sampled timestamp t:
    for each sector s with >= 5 members present at t:
        hold the sector's scores and its forward relative returns fixed
        randomly re-pair them across the stocks in that sector
```

**Hypothesis tested.** *Does assigning this relative-strength score to this
particular stock carry information about subsequent relative performance, beyond
the sector structure?* Formally: within a sector at a fixed instant, scores are
exchangeable across its member stocks.

| preserved exactly | destroyed |
|---|---|
| the timestamp | the score-to-stock pairing |
| the universe and which stocks are present | |
| **sector membership** — permutation happens inside a sector, never across | |
| the market-wide move at that instant | |
| each sector's own move at that instant | |
| the volatility environment | |
| the marginal distribution of scores, per sector | |
| the marginal distribution of forward relative returns, per sector | |

Permuting **within** sector rather than across the whole cross-section is the
essential detail. A whole-cross-section shuffle would break sector membership
too, so a purely sector-driven result could still beat that null — which is
exactly the ambiguity flagged in Step 15. Confining the shuffle inside each
sector makes the sector factor identical in every permutation, so it cannot
contribute to the null distribution and cannot be mistaken for signal.

**Why this stays valid with 118 stocks observed simultaneously.** No
independence is assumed across stocks. The test conditions on the realised
cross-section at that instant and only re-pairs labels, so any common factor —
market, sector, or volatility regime — is byte-identical across every
permutation. This is the property that makes the design work where a
conventional test would not.

Remaining dependence, handled outside the permutation: timestamps within a day
are autocorrelated, so sampling is non-overlapping (every 12 bars) and
significance is blocked at the day level.

### F. Market-relative X2 retained as a diagnostic only

| measure | value |
|---|---|
| corr(sector-neutral score, X2) | **+0.8167** |
| corr(sector-relative return, market-relative return) | +0.8575 |
| stdev: sector-neutral vs X2 | 2.046 vs 2.077 |

The two scores share about two thirds of their variance and differ in the rest —
consistent with sector explaining 22.0% of variance beyond the market. X2 is
kept solely to establish later whether sector-neutralisation removed a real
confound. **It is not a competing strategy and the two are not combined.**

### G. Power assessment

**The decisive new measurement — sector-neutralisation actually decorrelates the
residuals:**

| transformation | all pairs | within sector | cross sector |
|---|---|---|---|
| raw return | +0.1732 | +0.3296 | +0.1599 |
| market-neutral | −0.0017 | **+0.1720** | −0.0165 |
| **sector-neutral (LOO)** | **−0.0049** | **−0.0678** | **+0.0005** |

Market-neutralisation leaves within-sector correlation at +0.172. Sector-neutral
residuals are essentially uncorrelated everywhere: cross-sector +0.0005, and the
within-sector −0.068 is the mechanical negative that removing a group median
always induces, not residual structure.

**This is what makes the design worth running.** Both common factors are gone,
so the residuals per timestamp are close to independent.

| quantity | value |
|---|---|
| complete sessions | 63 |
| usable timestamps (non-overlapping, every 12 bars) | **440** (~7 per session) |
| median stocks per timestamp | **118** |
| stock-timestamp observations | **51,776** |
| approximate degrees of freedom per timestamp | 118 − 13 sectors = **~105** |

**The appropriate dependence unit remains the trading day.** 51,776 is
emphatically not 51,776 independent observations, for two reasons that survive
sector-neutralisation: the ~7 timestamps within a session are autocorrelated
even when non-overlapping, and days themselves are the natural outer block.

What has changed is what sits *inside* each block. Previously a day contributed
one aggregate statistic dominated by that day's market move. Now a day
contributes roughly 7 timestamps x ~105 approximately uncorrelated residuals,
with both the market and sector factors removed by construction. The number of
blocks is unchanged at 63 (about 38 in development); the precision within each
block is transformed.

### H. Is the design ready for H004 pre-registration? — **Yes, with the bucketing rule specified**

Everything the audits were meant to settle has been settled:

| check | status |
|---|---|
| suspicious symbols | resolved — genuine market events, retained unadjusted |
| 15:00-15:30 | resolved — price usable, volume excluded per-bar via `volume_quality` |
| denominator stability | sound, min 0.057% of price, no clipping needed |
| missingness | 0.25% |
| self-reference degeneracy | resolved by leave-one-out (6.81% -> 0.02%) |
| sector rule | deterministic, frozen, reference-based, minimum size argued structurally |
| small-sector sensitivity | quantified (15%), resolved by within-sector bucketing |
| null design | within-sector permutation, sector factor provably neutral |
| residual independence | confirmed: cross-sector +0.0005 |

**One specification must be fixed in the pre-registration rather than decided
later: buckets are formed WITHIN sector, then pooled.** Without that, the
15% dispersion difference between size-5 and size-12 sectors would tilt global
extremes toward small sectors.

Two things I am deliberately not deciding, as they change what is claimed:

* the forward horizon set (6/12/24 bars is the established convention and would
  be my proposal, but it should be stated explicitly at registration);
* whether the outcome is sector-relative forward return — which is the
  consistent choice, since the score is sector-relative — or market-relative.
  Mixing the two would test something neither score measures.

The remaining honest limitation is unchanged and worth restating at
registration: 63 sessions, roughly 38 in development, one market regime, and an
adjustment policy that is still UNKNOWN.

---

## Step 17 — H004 v1 executed: **REJECTED**

Frozen specification run unchanged. **Hold-out never loaded.** H004 v1's
definition, universe, sector map, controls, horizons and statistics are exactly
as registered; nothing was altered before, during or after the run.

### Execution parameters

| | |
|---|---|
| universe | 118 stocks, 13 sectors (min size 5) |
| development | 38 days, 2026-06-01 .. 2026-07-23 |
| validation | 12 days, 2026-07-24 .. 2026-08-10 |
| hold-out | 14 days — **NOT READ** |
| timestamps contributing | 348 (~7.0 per session) |
| permutations | 2,000 per horizon, seed 20260828 |
| alpha | 0.0167 (Bonferroni over the three pre-registered horizons) |

Alpha note: the registry fixed the test but not a numeric threshold, so 0.05/3
is applied as the project's established convention. It is stated as an
interpretation, not a registered constant, and was fixed before results were
read.

### Data quality and eligibility

| horizon | development obs | validation obs | skipped, window ineligible | skipped, no sector outcome |
|---|---|---|---|---|
| 6 | 26,663 | 8,496 | 5,896 | 0 |
| 12 | 26,663 | 8,496 | 5,896 | 0 |
| 24 | 22,179 | 7,080 | 11,796 | 0 |

Also skipped: 118 observations with no ATR, 8 with no computable score. All 50
development and validation sessions were complete. Session-end exclusions rise
with horizon exactly as expected — a 24-bar window needs two hours of session
left, so more late-session bars fall out.

### Frozen development tercile boundaries

| sector | n | low < | high >= | | sector | n | low < | high >= |
|---|---|---|---|---|---|---|---|---|
| AUTO | 2,486 | −0.7696 | 0.8088 | | FIN | 3,616 | −0.7843 | 0.7939 |
| BANK | 2,712 | −0.7377 | 0.7757 | | FMCG | 2,712 | −0.7955 | 0.8038 |
| CAPGOODS | 2,034 | −0.7851 | 0.8621 | | IT | 1,808 | −0.7881 | 0.7400 |
| CEMENT | 1,125 | −0.8415 | 0.8315 | | METAL | 1,582 | −0.7039 | 0.7303 |
| CHEM | 1,130 | −1.0357 | 1.0138 | | PHARMA | 2,486 | −0.8050 | 0.7564 |
| CONSUMER | 1,582 | −0.9921 | 0.9693 | | POWER | 1,808 | −0.8665 | 0.8507 |
| ENERGY | 1,582 | −0.8850 | 0.8065 | | | | | |

Computed on development only and applied unchanged to validation.

### Development results

| horizon | low mean | mid mean | high mean | **high−low** | median spread | effect size d | **p** |
|---|---|---|---|---|---|---|---|
| 6 | +0.01220 | +0.00797 | +0.00238 | **−0.00982** | −0.01414 | −0.0266 | 0.0750 |
| 12 | +0.02104 | +0.01126 | +0.00887 | **−0.01218** | −0.01609 | −0.0247 | 0.0980 |
| 24 | +0.02901 | +0.01417 | +0.01264 | **−0.01637** | −0.03177 | −0.0244 | 0.1465 |

All figures in percentage points of sector-relative return.

**The spread is negative at every horizon — the opposite of what H004
predicted** — and none reaches significance.

The tercile means are monotone in the wrong direction: low > mid > high at all
three horizons. A high score is followed by *weaker* sector-relative performance.

### Validation results (frozen boundaries, nothing retuned)

| horizon | low mean | mid mean | high mean | **high−low** | effect size d | **p** |
|---|---|---|---|---|---|---|
| 6 | +0.00983 | +0.00520 | +0.01219 | **+0.00236** | +0.0061 | 0.8270 |
| 12 | +0.00221 | +0.00442 | +0.01987 | **+0.01766** | +0.0355 | 0.1915 |
| 24 | +0.01600 | −0.00035 | +0.04061 | **+0.02461** | +0.0366 | 0.2210 |

**Every horizon reverses sign between development and validation.** Development
is uniformly negative, validation uniformly positive, and neither is
significant. A complete sign reversal across an out-of-sample period is the
signature of noise, not of a relationship that merely weakened.

### Implementation validation

The permutation null is centred on zero at all three horizons — means +0.00058,
+0.00005 and +0.00056 against null standard deviations of 0.0056, 0.0074 and
0.0110. A biased permutation (leaking sector or timestamp structure) would shift
that centre. It did not, which is direct evidence the within-sector,
day-blocked shuffle is implemented correctly.

The day-level blocking was applied by drawing **one relabeling per (day,
sector)** and applying it at every timestamp in that day. Reshuffling
independently at each timestamp would have broken the within-day persistence of
both score and outcome, understating the null variance and inflating
significance.

### Pre-registered gate results

| gate | result | |
|---|---|---|
| **0 — sample** | **PASS** | 22,179–26,663 development observations per horizon |
| **1 — relationship** | **FAIL** | 0 of 3 horizons show the predicted positive spread |
| **2 — significance** | **FAIL** | 0 of 3 reach p < 0.0167; best is 0.0750 |
| **3 — horizon consistency** | **PASS** | all three signs agree — on the *opposite* of the prediction |
| **4 — validation** | **FAIL** | all three signs reverse |
| **5 — hold-out** | **NOT EVALUATED** | hold-out never read |
| **6 — economic** | **NOT EVALUATED** | reached only if the directional gates pass |

Gate 3 passing is worth reading carefully: consistency of sign is only a virtue
when the sign is the predicted one. Here it means the wrong-direction reading
was stable across horizons in development — and then reversed wholesale in
validation.

### POST-HOC / DESCRIPTIVE diagnostics — not gates

Development period only. None of this was pre-registered, and none of it is used
to redefine H004 or select a subset.

**Sector breadth.** 9 of 13 sectors carry a negative spread at 6 bars, 10 of 13
at 12 bars, 9 of 13 at 24 bars. The wrong-signed reading is broad rather than
driven by one group.

**No single sector dominates.** Leave-one-sector-out at 6 bars moves the overall
spread only between −0.0075 (without FIN) and −0.0122 (without FMCG), against
−0.0098 overall. At 24 bars the widest single influences are METAL (−0.0106
without it) and CONSUMER (−0.0241 without it).

**Smallest sectors are not responsible.** Excluding CEMENT and CHEM — the two
five-member sectors — leaves the spread at −0.0066 / −0.0107 / −0.0140 against
−0.0098 / −0.0122 / −0.0164 overall.

**Which side carries it varies by horizon** — the deficit sits on the high side
at 6 bars and on the low side at 12 and 24. A real effect would not migrate
between sides as the horizon lengthens; noise does.

### Final status: **REJECTED**

H004 v1 fails Gate 1 (wrong direction), Gate 2 (no significance) and Gate 4
(validation reverses). The definition is unchanged and remains permanently
recorded. Any revision must be registered as H004 v2 with v1's result intact.

Deliberately not claimed: nothing here says the cross-sectional *direction* is
exhausted, only that this specific pre-registered score failed on this dataset
and configuration. Equally, the negative development spread is **not** evidence
that inverting the score would work — it reverses in validation, which is
precisely the reason not to trade the inverse of a rejected rule.

### Methodology checks performed before declaring

Every failure mode listed in the brief was checked rather than assumed:

| check | result |
|---|---|
| sector benchmark includes the stock itself | no — leave-one-out, pinned by test |
| future data in the score | no — score reads bars <= t only |
| thresholds derived from validation | no — terciles cut on development, applied frozen |
| control leak | the null preserves timestamp, sector and both marginals; null centred on zero |
| forward-window mismatch | signals and outcomes share `SessionIndex.is_forward_window_valid` |
| missing-bar handling differing between arms | same eligibility layer for both |
| one sector dominating through an implementation error | no — leave-one-sector-out is flat |
| timestamp alignment | zero misaligned bars across 596,516 rows |

---

## Step 18 — Compression → Expansion: input-only framing audit

H004 v1 remains **REJECTED**, unmodified. It is not inverted. There is no H004
v2. The validation sign reversal is not evidence for a short/contrarian
cross-sectional rule.

No forward return, MFE, MAE or outcome of any kind was computed. Development
OHLC only (38 sessions, 2026-06-01..2026-07-23 inclusive, 125 symbols).
Validation and hold-out candles were not loaded. H005 is proposed below and is
**not registered**.

### Research question (not a trading rule)

Does a transition from unusually low price movement (compression) into
directional expansion contain predictive information about the subsequent move?

The object of study is the **transition**, not a level being broken. Starting
from "breakout after range" would recreate H002 as a rolling opening range.
The structure that must be measurable is:

```
compressed state  →  expansion state  →  observation
```

### Temporal order, fixed before measuring anything

```
compression window     bars [t-K, t-1]     K = 12, entirely within one session
transition bar         bar t               TR[t], open[t], close[t]
observation            close of bar t      where a forward window would begin
post-transition        bars > t            NOT READ in this audit
```

Bar t is excluded from every compression measure. ATR used for compression is
`ATR(14)[t-1]`, not `ATR(14)[t]`: the project's Wilder ATR at t includes
`TR[t]`, so using it would let the expansion bar contaminate the compression
scale. Windows that would cross a session boundary are dropped, not truncated.

K = 12 and ATR period = 14 are inherited project conventions, not fitted.

### Population (development only)

| | |
|---|---|
| raw development bars | 356,240 |
| excluded, lookback would leave the session | 57,000 (exactly 125 × 38 × 12) |
| excluded, ATR(14)[t-1] unavailable | 375 (series warmup after the first-day lookback) |
| excluded, mean TR of window = 0 | 0 |
| **eligible observations** | **298,865** |
| sessions | 38 (2026-06-01 .. 2026-07-23) |
| symbols | 125 |
| symbol-days | 4,750 |
| imputed values | none |

The 09:15 bar's true range uses the prior session close, so the earliest
eligible windows (t ≈ 10:15) contain one overnight gap in the first TR. That
inflates morning mean TR and therefore biases **against** calling the open
"compressed". It is documented, not corrected.

Volume was not used. Development `volume_quality` in this window is
`OK` (351,490) and `FIRST_BAR` (4,750); no `UNKNOWN`. A volume candidate, if
one were ever added, would use canonical per-bar volume and `volume_quality ==
OK` only, never raw cumulative volume.

### Candidates investigated

Three price measurements were computed. A volume candidate was not forced.

**C1 — unordered window span / (ATR · √K).** Category A, then dropped.

```
C1(t) = (max(high[i]) − min(low[i])) for i in [t-K, t-1]
        / (ATR(14)[t-1] · √K)
```

Lookback 12 bars. Dimensionless. Causal: bars ≤ t-1. Missing: skip if the
session window or ATR[t-1] is unavailable. **Not genuinely new** — see
redundancy.

**C2 — recent mean true range / ATR.** Category B. Retained.

```
C2(t) = mean(TR[i] for i in [t-K, t-1]) / ATR(14)[t-1]
```

Lookback 12 vs Wilder 14. Dimensionless. Causal: bars ≤ t-1. Missing: skip if
ATR[t-1] missing or mean TR is zero (did not occur). Genuinely new as a
*registered-hypothesis input*: no prior hypothesis used a short-window mean TR
ratio. H001–H003 use ATR only to scale an already-large impulse; C2 asks
whether recent bar *size* is small relative to that same scale.

**C3 — transition-bar TR / recent mean TR.** Category C. Retained.

```
C3(t) = TR[t] / mean(TR[i] for i in [t-K, t-1])
```

Lookback 12 bars of compression plus bar t as the transition. Dimensionless.
Causal: bars ≤ t. Missing: same as C2; also requires TR[t]. Genuinely new:
nothing in H001–H004 measures the current bar against the window immediately
behind it.

Mean high-low / ATR was also computed. corr(C2, mean HL/ATR) = **+0.9839**.
It is C2 without overnight gaps. Dropped as a rename.

### Predictor distributions — compression, no outcomes

| | n | p1 | p5 | p10 | p25 | median | p75 | p90 | p95 | p99 |
|---|---|---|---|---|---|---|---|---|---|---|
| C1 | 298,865 | 0.455 | 0.555 | 0.617 | 0.742 | **0.917** | 1.149 | 1.416 | 1.600 | 2.021 |
| C2 | 298,865 | 0.676 | 0.754 | 0.794 | 0.859 | **0.932** | 1.010 | 1.089 | 1.147 | 1.316 |
| C3 | 298,865 | 0.264 | 0.395 | 0.475 | 0.633 | **0.868** | 1.213 | 1.685 | 2.083 | 3.170 |

38 sessions, 125 symbols, 4,750 symbol-days for every row.

C2 = 1 and C3 = 1 are the natural neutrals (recent bar size equal to ATR;
transition bar equal to the window it follows). Observed medians sit slightly
below both. No percentile was promoted to a threshold by looking at returns.

**C2 is tightly concentrated** (std 0.125, p10 = 0.794 vs median 0.932). That
is structural: SMA(12) of TR and Wilder ATR(14) have similar memory, so their
ratio hugs 1. "Unusually compressed" is therefore a left-tail statement about
a narrow distribution, not a dramatic regime split. This is a limitation of
the measurement, recorded before any outcome is read. A shorter recent window
or a longer ATR period would widen it; neither is adopted here, because
changing K or the ATR period to make the tail look more dramatic would be
parameter shopping on the predictor. K = 12 and ATR = 14 stay inherited.

### Redundancy — C1 is an identity, not a moderate correlation

Pairwise correlations on the eligible population. `impulse_score` and
`impulse_bars` / V5 use the existing ordered-impulse definition at t, with
`ATR(14)[t]` as H003 defined it. V1 is terminal close location of that
impulse's end bar; V2 is last-third / first-third canonical bar volume on the
impulse leg, requiring `volume_quality == OK` on every bar of the leg.

| pair | r | n |
|---|---|---|
| **C1 vs impulse_score** | **+0.9934** | 296,198 |
| C2 vs impulse_score | +0.5741 | 296,198 |
| C3 vs impulse_score | −0.1692 | 296,198 |
| C1 vs impulse_bars / V5 | +0.3031 | 296,198 |
| C2 vs impulse_bars / V5 | −0.0068 | 296,198 |
| C3 vs impulse_bars / V5 | +0.0512 | 296,198 |
| C1 vs ATR/close | +0.2318 | 298,865 |
| C2 vs ATR/close | +0.1860 | 298,865 |
| C3 vs ATR/close | −0.1415 | 298,865 |
| C2 vs V1 | −0.0004 | 296,182 |
| C3 vs V1 | +0.0376 | 296,182 |
| C2 vs V2 | +0.0594 | 281,507 |
| C3 vs V2 | +0.0224 | 281,507 |
| C1 vs C2 | +0.5775 | 298,865 |
| **C2 vs C3** | **−0.1572** | 298,865 |
| C2 vs Bollinger bandwidth[t-1] | +0.3172 | 298,240 |
| C2 vs ADX[t-1] | +0.1629 | 297,240 |
| C2 vs mean(high-low)/ATR | +0.9839 | 298,865 |
| C3 vs TR[t]/ATR[t-1] | +0.9684 | 298,865 |
| C2·C3 vs TR[t]/ATR[t-1] | **+1.0000** | 298,865 |

**C1 is dropped as a renamed transformation.** For any window the unordered
span equals `max(UP, DOWN)` ordered impulse range: one of the two directions
always contains the global high-low pair. Scaling by √K then makes
`C1 ≈ impulse_score / √12`. Medians confirm it: 0.917 vs 3.201/3.464 = 0.924.
The leftover discrepancy is `ATR[t]` vs `ATR[t-1]` in the two denominators.
P(stage-A | C1 ≤ p10) = **0.00%** — the bottom decile of C1 cannot satisfy
`R/ATR ≥ 3.5` because it is that ratio, inverted and rescaled. Keeping C1
would re-test H003's Stage A under another name.

**C2 is retained with a stated overlap.** r = +0.57 with `impulse_score`
(r² ≈ 0.33) is real and partly mechanical (shared ATR denominator). It is not
an identity: C2 is average bar *size*, `impulse_score` is net *displacement*.
corr(C2, impulse_bars) = −0.007. A slow grind of ordinary bars can print a
large impulse_score with C2 near 1; a tight coil prints both small. Conditional
on C2 ≤ p10, the stage-A rate falls from 39.0% to **8.3%**, so C2 is not
selecting the same bars as H003/V1/V2/V5.

**C3 is retained.** All correlations with ATR/close, V1, V2, V5 and
impulse_bars are |r| < 0.16. Nothing previously registered measures the current
bar against the window behind it.

**TR[t]/ATR[t-1] is not a third candidate.** It equals C2 × C3 exactly.
Because C2 is concentrated near 1, corr(C3, TR/ATR) = 0.968 — numerically
almost the same series. Conceptually C3 is still the right expansion measure
(the bar versus *this* quiet window). TR/ATR is kept only as a diagnostic that
the expansion is not an artefact of dividing by a small number.

**Bollinger bandwidth and ADX are not candidates.** Bandwidth[t-1] correlates
only +0.32 with C2, so it is not a rename, but a squeeze-then-break reading of
it is a range-breakout story and drifts toward H002. ADX[t-1] at +0.16 is
trend presence, not bar-size compression. Neither is used. Three candidates
were not forced: two distinct measurements remain.

**V1 / V2 / V5 on the stage-A subset** (116,468 bars, the H004 screening
population) stay orthogonal to C2 and C3 (|r| ≤ 0.19). Compression→expansion
does not reconstruct those impulse-end descriptors.

### Expansion, defined separately

Alternatives measured on the same 298,865 bars, still without outcomes:

| measure | median | p90 | role |
|---|---|---|---|
| C3 = TR[t] / mean TR[t-K, t-1] | 0.868 | 1.685 | **expansion definition** |
| TR[t] / ATR(14)[t-1] | 0.809 | 1.558 | diagnostic; identity C2·C3 |
| \|close[t]−open[t]\| / mean TR | 0.356 | 1.096 | body size, not range expansion |
| \|close[t]−open[t]\| / TR[t] | 0.444 | 0.830 | directional conviction of bar t |
| close beyond window high/low |  |  | range *breakout* — not used |

`TR[t]/ATR` as the expansion definition is rejected: it asks whether the bar
is large versus the instrument, not whether it expanded out of the compressed
window. A large-ATR bar after a non-compressed window is a different
phenomenon, and is exactly "any large candle equals expansion".

Body fraction is a descriptor of *directionality*, not of expansion. It is
how direction will be read, not how expansion is defined.

**Is C3 after low C2 just a small denominator?** Input-side check, no returns:

| subset | n | C3 median | TR/ATR median |
|---|---|---|---|
| all eligible | 298,865 | 0.868 | 0.809 |
| C2 ≤ p10 | 29,887 | 0.984 | **0.728** |
| C2 ≤ p10 and C3 ≥ 2.0 | 2,939 | 2.526 | **1.858** |

Conditioning on compression alone does **not** inflate the next bar in ATR
units (TR/ATR median falls to 0.728). The conjunction C2 ≤ p10 and C3 ≥ 2.0
does: TR/ATR median 1.858 versus 0.809 overall. The transition bar is large
against ATR as well as against the quiet window. C3 ≥ 2.0 is shown only as a
round a-priori multiple of the window (twice the recent mean), not as a chosen
threshold.

Range-break of the 12-bar high/low occurs in **57.4%** of those conjunction
bars — not 100%. Expansion as bar-size ratio is not identical to "close
outside the box". The 42.6% that expand without breaking the window high/low
are why the definition stays C3, not a range breakout.

### Why this is not H002 ORB

Structurally unable to fire inside the opening range: K = 12 bars inside one
session puts the earliest observation at 10:15. Hour-09 eligible bars: **0**.
57,000 early-session bars are excluded by that rule.

ORB, replayed on development OHLC only with `rvol_threshold = 0`, overlaps
**2.48%** of C2 ≤ p10 and C3 ≥ 2.0 bars (base rate of ORB among eligible bars:
0.48%). That is a slight elevation, not reconstruction.

Time of day, C2 bottom decile (n = 29,887):

| hour | share |
|---|---|
| 10 | 9.2% |
| 11 | **34.9%** |
| 12 | 28.8% |
| 13 | 15.9% |
| 14 | 9.8% |
| 15 | 1.5% |

Compression is a midday phenomenon. ORB is a 09:15–09:30 level, first close
outside it, once per symbol-day. This is a rolling state at arbitrary times,
repeatable within a session, and does not refer to the opening range.

Defining expansion as close beyond the compression window's high/low would
rebuild a rolling range breakout and is **not** the proposed trigger.

### Frequency and clustering (illustrative cuts only)

Cuts below describe how often a *transition* appears in the input data. They
are not thresholds, were not ranked by any outcome, and must not be read as a
selected specification. Directional events additionally require a non-flat
body (`close ≠ open`); flat bodies are 3.93% overall and 0.48% of
C2 ≤ p10 and C3 ≥ 2.0.

| illustrative cut | events | days | symbols | symbol-days | per symbol-day | episodes | sig/episode |
|---|---|---|---|---|---|---|---|
| C2 ≤ p10 and C3 ≥ 1.5, non-flat | 6,274 | 38 | 125 | 2,917 | 2.151 | 4,725 | 1.33 |
| C2 ≤ p10 and C3 ≥ 2.0, non-flat | 2,925 | 38 | 125 | 1,968 | 1.486 | 2,587 | 1.13 |
| C2 ≤ p25 and C3 ≥ 2.0, non-flat | 6,248 | 38 | 125 | 3,383 | 1.847 | 5,383 | 1.16 |

Episode rule: same symbol, day, direction, gap < 12 bars (the lookback) —
input overlap, not performance. Median episode size is 1; p90 is 2.

**These are not independent observations.** The trading day remains the block.
The binding constraint is 38 development days, as for H001–H004. Event counts
in the thousands clear the project's 300-signal floor; they do not multiply
the degrees of freedom.

The phenomenon is common enough to test on the current dataset: every
illustrative cut appears on all 38 development sessions and all 125 symbols.
A 63-session sample can detect a substantial, stable effect and cannot resolve
a faint one.

### Proposed H005 candidate — **NOT REGISTERED**

**Research question.** After a period of unusually small realized true range
relative to the instrument's ATR, does the first bar whose true range expands
materially beyond that window's own scale carry directional information about
the subsequent move?

**Structural definition.**

```
compression window   [t-K, t-1], K = 12, same session
compression          C2(t) = mean(TR[t-K, t-1]) / ATR(14)[t-1]
                     unusually low on the development predictor distribution
transition bar       bar t, not part of the compression window
expansion            C3(t) = TR[t] / mean(TR[t-K, t-1])
                     large relative to that same window
direction            sign(close[t] − open[t]), body non-degenerate
observation          close of bar t
```

Not a close beyond the window high/low. Not an opening-range break. Not an
impulse, pullback, or cross-sectional rank.

**Why it differs from H001–H004.**

| | conditions on | this differs because |
|---|---|---|
| H001 | EMA(9) crossing EMA(21) | no moving average |
| H002 | first close outside 09:15–09:30 high/low | cannot fire before 10:15; no opening range; repeats; 2.5% ORB overlap |
| H003 | ordered impulse ≥ 3.5 ATR, then pullback, then prior-bar break | opposite selection: C2 ≤ p10 is stage-A only 8.3% of the time |
| H004 | sector-relative 12-bar return / (ATR/close) vs peers | single-instrument time series; no sector, no peers |

H001–H003 and V1/V2/V5 all selected on a large move that had already happened.
This selects on a large move **not** having happened, then on the bar where
that state changes.

**Exact inputs.** Canonical OHLC. True range, Wilder ATR(14), open and close of
bar t. No EMA, RSI, MACD, ADX, VWAP, Supertrend, Bollinger, volume, or sector
map.

**Threshold philosophy.** Compression and expansion cuts are to be taken from
the **development predictor distributions above**, frozen before validation is
read, the same way H003's K = 3.5 was the rounded development p75 of
impulse_score. Round a-priori anchors (C2's left tail; C3 = 1 as neutral, 2 as
"twice the recent mean") exist so a cut can be justified without returns. **No
numeric threshold is chosen in this audit.**

**Matched control / null.**

* **Control A** — same bar, random direction. Does the transition know which
  way?
* **Control C, time-matched** — same symbol, same day, same direction, same
  30-minute session bucket, drawn from bars that meet the **compression**
  condition and **not** the expansion trigger. Holds the quiet state fixed and
  varies only the transition, so "quiet stocks behave differently" cannot
  explain a result. This is the control that rejected H003.

Inference: day-level block permutation. Stock-timestamps are not independent.

**Dependence.** Episodes 1.13–1.33 signals; 38 development blocks. Within-day
runs of neighbouring expansion bars are one event, not many.

**Power limitations, stated in advance.** Thousands of events, 38 independent
development days, one market regime, a ~63-session dataset. Sufficient to
detect a large stable effect; insufficient to resolve a small one. The same
ceiling as every prior hypothesis.

**Open items to settle at registration, still without outcomes:**

1. The exact development-distribution cuts for C2 and C3 (percentile vs round
   multiple), frozen before any forward return is computed.
2. Direction from the transition bar's body, as proposed, versus from which
   side of the compression *range* the close sits. The latter is a range
   breakout and is the path back to H002; the body is the default for that
   reason.

H005 is **not registered**. No generator is written. No forward return has
been computed. The hold-out has not been read.

---

## Step 19 — H005 v1 pre-registered: **DEFINED**

Frozen after the Step 18 input-only audit and before any forward outcome.
Hold-out unread. Generator implemented; experiment **not run**.

### Threshold decision (predictor-side only)

| cut | value | justification |
|---|---|---|
| C2_max | **0.794** | development p10 of C2 (raw 0.7938, n = 298,865). "Unusually compressed" is the left tail, not p25 ("below typical"). Same rounding philosophy as H003's K = 3.5 from p75 = 3.5186. |
| C3_min | **2.0** | a-priori round multiple: twice the compression window's own mean TR. C3 = 1 is the natural neutral. Not a percentile of C3 and not compared to returns. |

Neither cut was selected by trying combinations against forward returns. No
hidden search. Direction is the transition bar's body; close-beyond-window is
rejected as an H002 path.

### Registered event

```
C2(t) <= 0.794
AND C3(t) >= 2.0
AND close[t] != open[t]
direction = sign(close[t] - open[t])
observation = close of bar t
ATR used for C2 = ATR(14)[t-1]
compression window = [t-12, t-1], same session, dropped not truncated
```

Implementation: `app/research/compression_expansion.py`. Registry: H005 v1
DEFINED. A later rule change is H005 v2; v1 is not overwritten.

Controls A and C and day-level block permutation are in the registered rule.
The forward experiment is **not approved to run** until explicitly requested.

---

## Step 20 — H005 v1 executed: **REJECTED**

Frozen specification run unchanged. **Hold-out never loaded. Validation never
run** — Gate 1 failed in development.

Gate operationalization was inherited from H003 (identical registry gate
wording, same primary metric, same day-block permutation) and fixed before
returns were computed: Gate 1 requires edge vs Control A > 0 at ≥ 2 of 3
horizons with p < 0.05 at ≥ 1.

### Development 2026-06-01 .. 2026-07-23 (38 days, 125 symbols)

Primary metric `net_move_pct`. C2 ≤ 0.794, C3 ≥ 2.0, body direction.
2,927 raw events; missing returns = 0. Session-crossing windows dropped by
the generator. Horizon skips are session-end eligibility.

| h | n | days | BUY | SELL | signal % | Control A % | **edge A** | **p A** | d A | Control C % | **edge C** | **p C** | d C | skip h | skip C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 6 | 2,815 | 38 | 1,451 | 1,364 | +0.0018 | +0.0025 | **−0.0007** | 0.9318 | −0.002 | +0.1569 | −0.1552 | 0.0000 | −0.412 | 112 | 501 |
| 12 | 2,620 | 38 | 1,347 | 1,273 | −0.0045 | −0.0157 | **+0.0113** | 0.4873 | +0.023 | +0.1672 | −0.1717 | 0.0000 | −0.338 | 307 | 453 |
| 24 | 2,183 | 38 | 1,114 | 1,069 | +0.0006 | +0.0072 | **−0.0066** | 0.7973 | −0.009 | +0.1611 | −0.1606 | 0.0000 | −0.224 | 744 | 360 |

### Gates

| gate | result |
|---|---|
| **0 — power** | **PASS** 2,815 / 2,620 / 2,183 eligible; 38 signal-days |
| **1 — direction vs Control A** | **FAIL** edge > 0 at 1 of 3 horizons (12 only); p < 0.05 at 0 |
| **2 — structure vs Control C** | **NOT REACHED** |
| **3 — validation** | **NOT RUN** |
| **4 — hold-out** | **NOT RUN / NOT READ** |
| **5 — economic** | **NOT EVALUATED** |

### POST-HOC / DESCRIPTIVE — not gates

Control C means sit near +0.16% while the expansion-triggered signal sits at
zero. The expansion condition selects a *worse* subset of the compressed
state (p = 0.0000 at every horizon). That is the same shape as H003 vs its
impulse-only control. It is **not** evidence that trading the compressed
state, or inverting H005, would work — those would be new hypotheses.

Control C match: ~63% exact bucket, ~19% widened, ~17% skipped. Mean bucket
offset ≈ −0.13 (under four minutes). Skips were counted, not filled from
another day.

MFE/MAE ratios 1.07 / 1.03 / 1.02 are uninformative without the control
comparison and are not an economic claim.

**H005 v1 → REJECTED.** Rule, thresholds, controls, universe, horizons and
statistics unchanged. No H005 v2. Validation not opened. Hold-out unread.

---

## Step 21 — Market tradeability census (descriptive; not a strategy)

Strategy hunting paused after five rejected directional hypotheses. New
question: when is there enough realised intraday movement to matter next to
the existing round-trip cost floor?

**Not done:** H006, any BUY/SELL rule, threshold search, hold-out read,
validation read, cost optimisation.

Development only: 2026-06-01 .. 2026-07-23, 38 sessions, 125 symbols,
356,240 bars. Implementation: `app/research/tradeability.py` (descriptors)
and `app/research/tradeability_evaluate.py` (census). Cost floor is
`hypothesis_lab.cost_floor_pct` wrapping `paper_engine.estimate_charges`.
Canonical 1-lakh round-trip floor at typical prices = **0.1831%**
(charges ≈ 0.0831% + slippage 0.10%).

Regime (predictor-side, frozen before forwards): market-wide median
ATR(14)/close, development p25 = 0.001802, p75 = 0.002430 → LOW / NORMAL /
HIGH. Persistence definition frozen a priori: |close[t]−close[t−1]| /
ATR[t−1] ≥ 1.0.

Headline: a 5-minute close-to-close move is usually below cost (median
coverage 0.40×). A 30-minute move is about 1× cost at the median. Direction
after a 1-ATR bar is slightly more often a reversal than a continuation
(~52% vs ~47%) in every pre-frozen vol regime. Liquidity terciles barely
differ. H001–H005 remain REJECTED.

Hold-out: **NOT READ**.

---

## Step 22 — Tradeability gate within-bucket test: **REJECTED**

Approved one-off experiment. Not registered. Not wired live. Hold-out unread.
H006 not created.

Frozen: HIGH iff mkt_rel_atr ≥ 0.002430. Primary target cost_coverage at h=6.
Primary inference: day-level mean of same-day, same-bucket (median HIGH −
median STANDBY), labels shuffled within bucket.

Development: mean day effect **−0.099**, one-sided p = **1.00** (38-day
block / 4,000 within-bucket shuffles). 41 comparable day-bucket cells, 30
days. Pooled-across-days clock-slot medians are positive but are not the
pre-specified test: they compare HIGH days to STANDBY days inside a bucket.

**Do not pre-register. Do not implement.**

---

## Step 23 — Extreme-move / fade: input-only design (H006 not created)

Question only: after a 1-ATR 5m close-to-close, does the next move tend
to reverse? Not a strategy. Hold-out unread. No forward experiment.

Frozen event (already on the predictor side of the tradeability census):
`|close[t]−close[t−1]| / ATR[t−1] ≥ 1.0`. Development 38 sessions, 125
symbols: **43,128** events (12.7% of scored bars). Not rare. Median event
move **0.30%**. 8.6% are session-open gaps. Median opposite-session-room
**0.74**. Volume typically elevated (median RVOL 2.1×). Event rate similar
across LOW/NORMAL/HIGH; highest at the open (31%) and last hour (~17%).

Recommendation: **NEEDS METHODOLOGY FIX**. Do not pre-register until a
session-position matched control exists and session-open bars are split
out. Do not search thresholds. H006 not created.

---

## Step 24 — Control C (mechanical room): **DO NOT PURSUE**

Exploratory. H006 not created, not registered. Hold-out unread.

Same-session events only: 43,128 → 39,404 (8.6% opening-gap excluded) →
30,504 after requiring a valid 12-bar forward window *before* sampling.
Control C = non-event bar, same symbol / day / 30-min bucket, nearest
session position, caliper 0.10, without replacement. Matched 27,595
(90.5%; 81.2% exact bucket; |Δposition| p50 = 0.026).

Primary h=6: event P(reversion) **49.67%** vs control **49.14%**; event
mean signed **+0.0224%** vs control **+0.0159%**. Day-level paired
randomisation (38 days, 4,000 iters): **+0.0056%**, one-sided p(event more
reverting) = **0.997**. Events revert *no more* than same-place non-event
bars, at every horizon.

Coarse tercile matching flips the sign (−0.092% at h=6). That sensitivity
to matching error is itself the finding: the apparent fade is session
position, not the 1-ATR event.

Control A (direction shuffle) leaves a real but tiny reverting residual
(−0.0015 vs null +0.0077 at h=6, p=0.0002) — about **0.009%**, versus the
0.1831% floor.

**Do not pre-register. Do not pursue this event definition.**

---

## Step 25 — Information-source audit (no code, no strategy)

Every rejected hypothesis (H001–H005, the gate, the fade) took its inputs
from one place: the past OHLCV of the same 125 NSE cash symbols. They are
not five independent failures; they are one statement tested six ways.

Current information set: price, derived price, per-bar volume, a
stock-median market proxy, a hand-assigned sector map, and the clock.
Definitively absent: bid/ask sizes, depth, trade prints and aggressor,
open interest, index levels, India VIX, futures basis, corporate
announcements, macro calendar.

Order flow is the most causal missing input and the least researchable:
Groww exposes only a live top-of-book price inside `get_quote`, and there
is no historical depth endpoint anywhere. A live-only feed cannot be
validated against a hold-out.

Recommended next source (NOT integrated): **NSE corporate-announcement
archive with exchange dissemination timestamps** — the only candidate that
is exogenous to price and historically timestampable. First step is a
timestamp-fidelity feasibility probe, not an integration.

H006 not created. No forward test. Hold-out unread.

## Step 26 — Corporate-announcement feasibility probe: **CONDITIONAL GO**

NSE's own endpoint (`/api/corporate-announcements`) carries what the
information audit hoped for: `exchdisstime`, an exchange dissemination
stamp at genuine second precision (`second == 0` on 1.7% of records, the
uniform rate — no minute rounding), ISIN on 100% of records, and a unique
`seq_id` per filing. Archive depth reaches back years with timestamps
retained. Timezone is undeclared but empirically IST.

The population is the constraint, not the source. For our 125 symbols the
window holds ~2,500 announcements, ~750 in-hours, of which only ~15% are
potentially material by NSE's own category field — roughly 100 independent
symbol-day episodes across 38 sessions. The archive goes back years; our
5-minute candles start 2026-06-01, so the event count cannot be grown
backwards.

Held back from GO on one unresolved item: point-in-time fidelity.

## Step 27 — Point-in-time fidelity: **CONDITIONAL GO** (temporal only)

Baseline frozen outside the repo at `research_artefacts/announcements/`:
2,571 records, all 125 symbols, 53 days, 1,868,727 bytes, records digest
`b72259626a5553652eed6d26b8c3e2782ecb3b5ba09c88730aec0ead6225490e`,
write-once and read-only. Per-symbol sweep matched date-range queries
exactly on all three verification days (56/56, 85/85, 97/97).

Two comparisons, both finding zero mutation of anything research uses:

* **+4.5 min, same path** — records digest byte-identical, 2,571 unchanged.
* **+47 min, different HTTP client and query shape** — 658 shared records,
  zero changes to `exchdisstime`, `an_dt`, `seq_id`, ISIN, `symbol`,
  `desc` or attachment URL. 64 `attchmntText` differences were all our own
  earlier fetch tool: 63 collapsed whitespace runs, 1 HTML entity
  (`&#64258;`) decoded to its ligature. All one-directional.

Attachments: 40 fingerprinted, 15 fully hashed — **0 content or byte-length
changes**. ETag and Last-Modified drifted on 40 of 40, by milliseconds to
2 s, with byte length identical: `nsearchives` serves from replicas and its
ETag is the weak `W/"<bytes>-<mtime_ms>"`. Those two fields are excluded
from mutation detection by design; only length and SHA-256 count.

`seq_id` is chronological in the main filing channel — 1 violation in
2,476. All 47 order violations concentrate in SAST takeover disclosures
(95 records, a separate lower-numbered ID pool), a population already
classed procedural. Deterministically isolatable via `desc`.

`old_new`, `bflag`, `csvName`, `orgid` are null on all 2,571: the API
exposes no amendment flag, so the snapshot-and-diff instrument is the only
available evidence. It now exists. The one thing missing is elapsed time —
47 minutes cannot establish stability over the months an event study spans.

New: `app/research/announcement_snapshot.py` (pure: canonicalisation,
deterministic hashing, write-once artefacts, A–G change taxonomy) and
`announcement_snapshot_capture.py` (retrieval). 34 tests. Registry
untouched; H001–H005 REJECTED; no H006.

Provenance correction: `research_5m_v2` registered alongside the stale
`research_5m_v1` (39 symbols/63 days/184,261 candles, left exactly as it
is because H001–H005 ran on it). v2 records the actual store — 125 symbols,
2026-06-01 → 2026-08-28, 64 days, 596,516 candles, commit `e0b73b9`.
Status WARNING: 0 duplicates, 0 invalid OHLC, 0 invalid volume, 0 timestamp
issues; all 1,240 missing bars are the trailing partial session of
2026-08-28, outside the development window.

## Step 28 — Fidelity re-check: **CONDITIONAL GO — WAIT FOR LONGER INTERVAL**

Same-path per-symbol re-query of 2026-06-01 → 2026-07-23 against the frozen
baseline `baseline_20260831T084533Z.json`. Baseline not overwritten
(`file_sha256` still `b8225a8f…`). Recheck snapshot
`recheck_20260831T090555Z.json`. Elapsed **20 min 22 s** (1,222 s).

Records digest identical (`b7225962…`). 2,571 unchanged, 0 added, 0
removed, 0 timestamp / identity / category / document-reference /
document-content mutations, 0 late-arriving historical records.
Document sample: 40 HEAD, 15 SHA-256, 0 content or length changes; 37
advisory ETag/Last-Modified drifts (class I, not mutation). Completeness
cross-check still agrees 56/56, 85/85, 97/97.

Classifier now splits category mutation from headline text and attributes
whitespace/HTML-entity headline diffs as client artefacts only after
normalisation proof. 38 snapshot tests; 493 backend tests green.

No mutation observed in this interval. Interval is still far too short
to establish point-in-time fidelity. H006 not created. Hold-out unread.
Validation unread.

## Step 29 — Same-day 2-hour fidelity re-check: still **CONDITIONAL GO**

`recheck_20260831T104813Z.json` vs frozen baseline. Elapsed **2 h 2 m 39 s**
(7,359 s). Longer than the 20-minute check, still the same calendar day —
not weeks, not a month-end, not a results season.

Records digest still identical. 2,571 unchanged, 0 mutations of any
research-relevant field, 0 late arrivals, 0 removals. Document sample:
0 content/length changes; 36 advisory ETag/Last-Modified drifts.

H006 not created. Hold-out unread. Validation unread.

## Step 30 — Waiting-period preparation (no new archive fetch)

Baseline re-verified hash-identical and read-only. Comparison
classification digest is deterministic across repeated dry-runs of the
existing 2-hour recheck (no files written). Next live compare is deferred
until several weeks have elapsed, including 2026-09-30. Provenance note:
`research_artefacts/announcements/WAITING_PERIOD.md`. H006 not created.

Waiting-period tests now also pin: comparison artefacts cannot overwrite the
baseline; capture window equals development 2026-06-01 → 2026-07-23; capture
loads symbol names only, never candles, never validation/hold-out dates.


