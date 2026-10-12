# Project context

Carry-over context from a long design conversation. Three tracks: a quantitative
backtesting framework (built, run on real BTC data, SPY/futures untested), a biotech catalyst
research process (screener built as `screeners/biotech/biocat`, first live run 2026-10-12), and an execution-vehicle decision
(analysed, blocked on track 1).

---

## Operator constraints

- Account size: **$500 to $1,000**. Treat capital as the binding constraint on
  every design decision.
- Jurisdiction: **Ontario, Canada**. CIRO-regulated brokers. Crypto cannot be
  held directly in a TFSA and every disposal is a taxable event. FINRA's
  pattern-day-trader rule generally does not apply to Canadian brokers; confirm
  per broker. Futures and prop-firm payouts are **business income** (100%
  taxable), not capital gains. US Section 1256 60/40 treatment does NOT apply.
- Background: **clinical / nursing placement, A&P coursework, research
  methodology training**. This matters for track 2.
- Time: placement hours conflict with 09:30-11:00 ET. Any strategy requiring
  continuous screen presence during the US open is not operable.
- Formatting preference: no long dashes, compact output, bold key terms.

### Established economics (do not relitigate)

- $25/day on $500 is **5% daily**, which compounds to ~$109M in a year. The
  target is a capital problem, not a strategy problem.
- Realistic ceiling on $500 with a genuine edge: **$50 to $150 per year**.
- Options require 100% cash; margin buying power cannot be used for long
  options.

---

## Cross-cutting: friction and instrument selection

**This is the most transferable finding in the project.** Every market examined
was ranked by round-trip friction, and that single number decided more than any
strategy question.

| Instrument | Round-trip friction | Notes |
|---|---|---|
| SPY shares | **0.002%** | 1c spread on $774 |
| **MES futures** | **~0.007%** | 1 tick = $1.25 on $39k notional. **But ~0.71 pts round trip = ~0.3 R on a 2.4-pt 5m FVG stop.** % of price is the wrong unit; use friction / stop |
| ES futures | ~0.007% | 1 tick = $12.50 on $390k notional |
| SPY options | ~2% | 2-3c spread on a $3.00 contract, at 0.5 delta |
| Low-float small cap | 1 to 3% | plus LULD halt risk, unmodellable |
| Memecoin, $1k into $30k pool | ~8% | AMM slippage, not a spread |
| Memecoin, $1k into $5k pool | ~40% | |

### The AMM friction law (constant product)

For a buy of fraction `f` of the pool's quote side:

```
price after       = (1 + f)^2  x  price before
your average fill = (1 + f)    x  price before
→ slippage = f exactly
```

Selling fraction `g` of the token side fills at `P / (1 + g)`.
**Round-trip friction ≈ 2f plus fees.** It is a function of position size
against pool depth, not a fixed spread.

### Position sizing is the real constraint, not strategy quality

Minimum viable position vs a 1% risk budget:

| Instrument | Capital needed for a correctly sized trade |
|---|---|
| **MES futures** | **$2,000 to $4,000** (8-point stop = $40 risk) |
| SPY options | $15,000 to $30,000 (min loss at stop ≈ $150) |
| SPY shares | works at any size but no leverage |

**MES is the only instrument examined that offers meaningful leverage without
catastrophic friction.** This is the target vehicle.

---

## Track 1: `fvg_lab` backtesting framework

### Status

Code is **written, debugged, and verified on synthetic data**. It has been run
on **real BTC 5m data** (Binance + Coinbase, 2019-2026); see "Findings on REAL
data". It has **not** been run on real SPY or futures data. That is now the
open blocker for every decision in track 3.

### Files

```
backtesting/
  fvg_lab/
    data.py       loading, validation, sessions (incl. 24/7), overnight levels,
                  roll-gap check, synthetic null data, holdout split
    strategy.py   Params, Bars, FVG / AMD / overnight-sweep detection, entry/exit
    engine.py     event-driven loop, cost model (fixed + % fees), sizing, trades
    metrics.py    expectancy, PSR, deflated Sharpe, drawdown, randomization test
    trials.py     TrialLog, parameter sweep, purged walk-forward (run_kwargs)
  run_example.py    worked example on synthetic data (MES costs)
  run_spy.py        stage-1 SPY harness, USE_REAL_DATA flag
  run_overnight.py  ONH/ONL sweep vs break, synthetic 24h null (MNQ costs)
  run_crypto.py     stage-1 BTC/ETH harness: zero-cost signal, then real fees
  fetch_crypto.py   free BTC/ETH downloader (Coinbase API, Binance archive)
  fetch_futures.py  Databento ohlcv-1m downloader, quotes first, back-adjusts rolls
  check_coverage.py Databento session-coverage probe (~$0.01)
  instrument_select.py  friction-in-R ranking of CME index micros
  propfirm/
    combine_sim.py  Monte Carlo of a Topstep-style evaluation rule stack
    funnel_sim.py   full evaluation -> funded -> first payout funnel
  tests/            cost model, downloader, overnight-level lookahead tests
  trials_*.jsonl    trial logs, COMMITTED (rule 4: the count never resets)
pine/
  fvg_lab_v3.pine       TradingView FVG + iFVG indicator, mirrors strategy.py
  amd_power_of_3.pine   AMD indicator with a self-scoring predicted-vs-opposite table
  market_structure_qm.pine  swings, BOS/CHoCH, quasimodo; mirrors choch/qm modes
```

**Market structure rules (fixed before any real data):** swing high at p =
`high[p]` strictly above the `swing_left` bars before and >= the `swing_right`
bars after; known only at p + swing_right. Close beyond the last unbroken swing
= BOS with the trend, CHoCH against it. Bearish QM: swings H1, L1, H2 > H1,
then a close below L1 arms a short at H1 (within `qm_tolerance_atr`), only on
a later bar still closing below H2, cancelled if price trades above H2 or after
`qm_max_age` bars, one entry per setup. Tests in `tests/test_structure.py` are
mutation-checked: removing any rule fails a test, except the H2 > H1 check,
which is behaviourally covered by the cancel rule (a lower head is crossed
before the shoulder is reached).


### Design rules (load-bearing, do not break)

1. **No lookahead.** A decision made at the CLOSE of bar `t` fills at the OPEN
   of bar `t+1`. ATR is shifted. Intents never cross a session boundary.
2. **Pessimistic fills.** When a bar's range contains both stop and target, the
   STOP is assumed to hit first. Bars that gap through the stop fill at the open.
3. **Costs before metrics.** Half-spread plus slippage per side, plus commission.
   `costs.double()` is the stress test.
4. **The trial log never resets.** `deflated_sharpe` uses the cumulative count.
   Every variant tested raises the bar. Each timeframe and each bar-offset is a
   separate trial, not a free re-roll.
5. **Purge and embargo.** Standard k-fold is invalid on time series and is not
   offered. `purged_walk_forward` drops sessions between train and test.
6. **Synthetic null first.** `data.synth()` is a random walk. Anything
   profitable there is measuring the search, not an edge.

### `Params` fields

```
entry_mode            fvg_retest | first_candle_break | fvg_plus_break | amd
                      | on_sweep_reversal | on_break | choch | qm
swing_left, swing_right, qm_tolerance_atr, qm_max_age
sweep_buffer_atr, sweep_reclaim_bars, on_range_min_atr, on_range_max_atr
first_candle_n, require_first_candle_bias, min_gap_atr, fvg_max_age
entry_start_bar, entry_end_bar
amd_cons_bars, amd_max_range_atr, amd_min_break_atr, amd_reclaim_bars
invalidation_mode (wick|close), invert_on_violation, max_inversions
ema_period, ema_mode (with|against)
stop_mode (fvg_far_edge|atr|opening_range|amd_extreme|sweep_extreme|structure|qm_head)
stop_atr_mult, stop_buffer_atr, min_stop_atr, max_stop_atr
target_r, exit_at_session_end, max_trades_per_session
allow_long, allow_short, atr_period
```

`Costs` presets: `spy_shares()`, `es()`, `mes()`, `crypto_spot(fee_pct=...)`.
`Costs` takes fixed terms (ticks, $/unit) and proportional terms
(`spread_bps`, `slippage_bps`, `commission_pct`); `double()` doubles all.

### Findings so far (all on synthetic data)

- **The FVG retest condition has almost no selectivity.** ~90% of gaps get
  retested, median wait **1 bar**, independent of gap size. The gap's near edge
  IS the current bar's low, so any downtick touches it. `fvg_retest` is
  functionally "enter on the bar after a gap forms."
- **`fvg_max_age` barely matters** given 97% of retests occur within 20 bars.
- **A hypothesis was tested and refuted:** gap size does NOT correlate with
  time-to-retest. The proposed `min_gap_atr` x `fvg_max_age` interaction does
  not exist.
- **`invalidation_mode="close"` produces MORE trades than `"wick"`**, not fewer,
  because zones survive longer and get more retest opportunities.
- **AMD on random data produced a +0.49 R headline** from a 108-variant sweep.
  Randomization test p=0.25, deflated Sharpe **0.0000**, in-sample to
  out-of-sample decay **+0.65 R**. This is the framework working correctly.
- **Retail crypto fees kill 5m intraday outright.** On synthetic BTC 5m bars
  (median stop ~0.33% of price), zero-cost expectancy 0.00 R becomes
  **-2.8 R/trade at 0.40% taker** and -1.8 R at 0.25% maker. Fee drag in R is
  roughly round-trip cost / stop distance, so ~0.85% round trip needs stops
  near **8%** to get drag under 0.1 R.
- **Overnight sweep/break null (run_overnight.py, MNQ costs):** best of 108
  variants +0.14 R on 120 trades, randomization p=0.096; pooled means
  on_sweep_reversal -0.06 R, on_break -0.17 R. Correct null behaviour.
  `on_range_min_atr` 0.5 vs 1.5 changed nothing on synthetic data, so half
  that grid is duplicate trials; drop the dimension or widen it.

### Findings on REAL data (BTC 5m, 2019-01 to 2024-06 dev; holdout untouched)

Run via `run_crypto.py`, UTC-day sessions, two venues (Binance BTCUSDT archive,
Coinbase BTC-USD), 0.40% taker.

| | Binance | Coinbase |
|---|---|---|
| Baseline zero-cost expectancy, randomization p | +0.089 R, p=0.000 | +0.089 R, p=0.000 |
| Best of 144 (fvg_retest, bias=True, target 3R) | +0.106 R | +0.124 R |
| Purged walk-forward pooled OOS, zero cost | **+0.089 R** | **+0.101 R** (27/46 folds positive) |
| Deflated Sharpe | 0.79 vs 145 trials | **0.00 vs 6,959 trials** |
| Same variant at 0.40% taker | -5.2 R, $1k to $0.04 | -8.9 R, $1k to $0.01 |

- **A small zero-cost signal replicates across two venues.** It is the first
  thing that has survived the null and a walk-forward. It is NOT a pass: the
  deflated Sharpe fails, and the gate requires doubled costs.
- **The edge is ~0.017% of price per trade** (0.1 R on a 0.17% median stop).
  Retail crypto round trip is ~0.85%, about 50x larger. Untradable on crypto.
- The trial count jumped to 6,959 because each walk-forward fold logs its
  144-variant sweep. That is rule 4 working as designed.
- `pct_same_bar_exit` is 14-17%, not low. Stops are tight relative to 5m bars.
- **Venue matters for FVG:** only 83% of Coinbase bullish FVGs also appear on
  Binance over the same bars. Gap identity is partly a venue artifact. Same
  lesson as the IEX warning.
- ~~Implication for SPY / MES: friction below the 0.017% edge.~~ **Wrong
  comparison, refuted on ES (below).** Comparing friction to the edge as a %
  of price assumes the stop is the same % of price on both markets. It is not:
  ES FVG stops are ~0.07% of price vs BTC's ~0.17%. The only valid unit is
  **friction in R = round-trip cost / stop distance.**

### Findings on REAL data: market structure on ES (2010-06 to 2021-11 dev)

`run_structure.py --csv es_1m.csv`: 5m RTH bars, MES costs, fractional sizing,
2,937 dev sessions, holdout (1,263 sessions from 2021-11) untouched.

| | CHoCH | Quasimodo |
|---|---|---|
| Synthetic null baseline / best of grid | -0.06 R / +0.01 R | -0.13 R / +0.12 R |
| **ES baseline, MES costs** | **-0.152 R** (2,272 trades) | **-0.485 R** (259 trades) |
| ES baseline, 2x costs | -0.273 R | -0.691 R |
| ES best of grid (12 / 48 variants) | -0.148 R | -0.323 R |
| Deflated Sharpe | 0.00 | 0.00 vs 633 trials |
| Walk-forward @2x costs, pooled OOS | -0.271 R, 3/44 folds positive | -0.591 R, 7/44 folds positive |
| Median stop | 5.6 pts = $28/MES | 2.9 pts = $14/MES |
| Fits 1% risk: $1k / $4k | 2% / 70% | 32% / 91% |

- **Both are closed on ES.** Every variant in both grids is negative; there is
  no parameter to rescue. Worse than the synthetic null in both cases.
- Friction is ~0.12 R/trade (CHoCH) and ~0.2 R/trade (QM) at MES costs, so
  gross before costs is about -0.03 R and -0.28 R. Fading CHoCH would pay the
  same costs on a ~+0.03 R gross: also negative. QM is negative even gross.
- QM is rare: 259 setups in 2,937 sessions, and 12% same-bar exits.
- **Whole-contract sizing hides signals.** On the null, CHoCH measured 208
  trades with 562 skipped as undersized (wide structural stops), and the best
  variant read +0.18 R; with fractional sizing it is 656 trades and +0.01 R.
  Measure in R with `allow_fractional=True`; report affordability separately.

### Findings on REAL data: FVG retest on ES, PRE-REGISTERED (2010-06 to 2021-11 dev)

`run_fvg.py --csv es_1m.csv`, committed (b84f6c3) BEFORE the first run.
Three variants fixed from the BTC trial log; pass = all positive at zero AND
MES costs, plus walk-forward at 2x costs positive OOS. 5m RTH, 14 holiday
sessions (<30 bars) dropped, 2,925 dev sessions, holdout untouched.

| | Zero cost | MES costs | MES 2x | Trades | Median stop |
|---|---|---|---|---|---|
| P1 BTC baseline (gap 0.25, 2R) | **+0.052 R** | -0.293 R | -0.551 R | 3,931 | 2.38 pts |
| P2 Binance best (gap 0.25, 3R) | **+0.051 R** | -0.295 R | -0.542 R | 3,758 | 2.36 pts |
| P3 Coinbase best (gap 0, 3R) | **+0.056 R** | -0.314 R | -0.652 R | 4,556 | 2.19 pts |

- **VERDICT: FAIL.** Walk-forward over the 144-variant BTC grid at 2x costs:
  44 folds, 3 positive, pooled OOS **-0.421 R**. Every year 2010-2021 is
  negative at MES costs for all three (best year 2020, about -0.1 R). All 144
  exploratory variants negative (best -0.195 R); deflated Sharpe 0.00 vs 6,531.
- **The zero-cost signal replicates on a third market**, same sign, about half
  the BTC size (+0.05 R vs +0.09 R), on ~4,000 trades per variant. The pattern
  is probably real; it is just far too small.
- **Why MES did not save it:** MES round trip is ~0.71 pts (1 tick spread +
  slippage per side plus commission). Gross edge = 0.05 R x 2.4 pts ~= 0.12
  pts. Friction is ~6x the edge, ~0.35 R per trade.
- **What would be needed:** for friction under the 0.05 R gross edge, stops
  must exceed ~14 pts, about 6x wider than 5m gaps produce. That points at
  30m/60m bars (signal-to-friction scales with sqrt(t)), but the +0.05 R may
  not survive the change: it is a new strategy with its own trial count.
- **Affordability was not the binding constraint:** 38-44% of trades fit 1%
  risk on $1k, 95% on $4k.

### Bugs found and fixed (watch for regressions)

- `floor(qty)` silently dropped ~99% of signals when one contract risked more
  than the risk budget. Now surfaced as `skipped_undersized`. Root cause was
  **instrument selection** (ES at $50/pt), not code; MES at $5/pt fixed it.
- Stops tighter than one bar's range made the pessimistic stop-first rule fire
  on nearly every trade. Fixed with `stop_buffer_atr` and `min_stop_atr`;
  diagnosed via `pct_same_bar_exit`.
- **`add_overnight_levels` lookahead.** Post-close futures bars (16:00-17:00
  ET) were grouped into the SAME day's overnight range, so the 09:30 ONH/ONL
  included prices from 7 hours later. Invisible on `synth(include_overnight=
  True)` (no post-close bars); fatal on real MES/MNQ files. Fixed: overnight
  is `on_start` to `rth_start` only. Regression test in
  `tests/test_overnight.py`, verified to fail on the old code.
- `sweep()` / `purged_walk_forward()` never passed `allow_fractional` to `run()`,
  so every crypto signal was skipped as undersized. Fixed with `run_kwargs`.
- **`assert_no_roll_gaps` fired on every weekend reopen.** On real 24h data the
  Sunday 18:00 open gaps by more than 5x a 1m ATR for real reasons, so the
  check raised on correctly adjusted data and would have been ignored. It now
  skips the first bar after a pause (time step > 2x median spacing). Rolls on
  Databento continuous symbols happen mid-session at 00:00 UTC, one bar after
  the previous print, so they are still caught. Verified on real ES data: raw
  raises only at the roll, adjusted passes.
- `df.iloc[i]` in the bar loop made sweeps take hours. Hot path is numpy now;
  indicators are cached across variants sharing `first_candle_n`, `atr_period`,
  `ema_period`, `amd_cons_bars`.

### Known gaps / TODO

- [x] Real BTC data: Binance + Coinbase 5m, 2019 to 2026, via `run_crypto.py`.
      CSVs are git-ignored; re-fetch with `fetch_crypto.py`. **Holdout
      (2024-06 onward) has NOT been opened.**
- [ ] **Real SPY / MES / MNQ data.** Now the primary blocker. The BTC
      zero-cost signal is the reason to test where friction is below 0.017%.
- [x] **Roll gaps.** `fetch_futures.py` back-adjusts every roll by the
      old/new spread measured at a common minute (difference method, stays on
      the tick grid), then runs `assert_no_roll_gaps` on the 1m and 5m output.
      Live-tested on ESH6 -> ESM6: raw jump 51.5 pts, adjusted 0.25 pts.
      `assert_no_roll_gaps` is still NOT called by `load_csv()`; any futures
      file from another source must be checked by hand.
- [x] `data.resample_ohlcv(df, rule, offset)`: 1m to 5m/15m/30m, left-labelled,
      empty bins dropped. The `offset` argument is what the bar-offset test needs.
- [ ] **Exclude CME holiday sessions.** On ES 2010-2026, 52 of 4,200 RTH
      "sessions" are US holidays (Jul 4, Labor Day, Thanksgiving, MLK,
      Presidents Day) where Globex trades ~2 hours and the cash market is
      shut (24 bars). 80 half-days (~42 bars) are real. Filter with an
      exchange calendar. 1.2% of sessions; did not change the structure result.
- [ ] **`bars_per_year` is hardcoded to 19,656** in `metrics.summarize()`
      (78 x 252, i.e. 5m). On 30m the correct value is 3,276. Leaving it alone
      inflates annualized Sharpe by sqrt(6) ≈ 2.45x. Make it a parameter derived
      from the data's bar spacing.
- [ ] **No bar-offset robustness test.** Resample the same 1m source at offsets
      0..4 min (5m) or 0..5 (30m) and compare expectancy. If it swings across
      offsets the pattern is an aggregation artifact, not structure. Cheap now
      that the hot path is numpy. Counts as N trials, not 1.
- [ ] No time stop (exit after N bars unresolved)
- [ ] No daily loss limit
- [ ] No `retest_depth` parameter (require price to penetrate into the zone
      rather than graze its near edge). Highest-value addition given the
      selectivity finding above.
- [x] Percentage fees: `commission_pct`, `spread_bps`, `slippage_bps`,
      `Costs.crypto_spot()`. 24/7 sessions via `add_session(df, "00:00", "24:00")`.
- [ ] Single-series only. A universe loop is the biggest structural change
      available and the one that most expands what can be learned (breadth
      substitutes for frequency, see below).
- [ ] Pine v3 arms on the break bar and checks reclaim from the next bar;
      Python allows same-bar reclaim. One-line divergence, intentional, flagged.

### Timeframe mechanics (settled)

- A bar destroys **path information**. Larger bars = more ambiguity about
  whether stop or target hit first = more of the backtest is the pessimistic-fill
  assumption rather than measurement.
- Move size scales with **sqrt(t)**; friction is **constant per round trip**.
  So signal-to-friction scales with sqrt(t). Lower timeframes are always a worse
  friction deal, non-linearly.
- **Statistical power:** `t ≈ expectancy x sqrt(n)`. For t > 2 you need ~400
  trades at 0.10R expectancy, ~1,600 at 0.05R. At one instrument and 1-3 trades
  per session that is 6 months to 1.6 years of calendar time.
- **The resolution is breadth, not frequency.** 20 instruments x 1 trade/session
  gives 400 trades in 20 sessions on a timeframe where friction is negligible.
  Discount for correlation: 20 US equities on one day is ~3-5 effective
  independent observations, not 20.
- **Research at 5m.** Then check 15m and 30m. Surviving across timeframes is
  evidence; working at exactly one is usually overfit.
- **Every bar-count parameter in `Params` is secretly a time parameter.**
  Changing chart timeframe silently reinterprets all of them. Either express in
  minutes and convert, or treat each timeframe as a separate strategy with its
  own grid and trial count.


### Data situation

- **SPY intraday costs money.** FirstRate Data sells 1m/5m back to 2000 as a
  one-time CSV purchase and offers a free two-week sample. Polygon and Databento
  are the API alternatives.
- **Avoid IEX-only free feeds.** FVG is defined by exact highs and lows; a
  partial-venue feed manufactures gaps that never existed. Consolidated (SIP)
  data or nothing.
- **Crypto data is free and complete.** Kraken, Coinbase and Binance all expose
  public OHLCV endpoints with no key. If the goal is to unblock the framework
  today, BTC and ETH are the path.
- Test **BTC and ETH only** if going crypto: no survivorship bias, deepest books,
  longest clean history. Altcoin universes are catastrophically survivorship-biased
  because most tokens from any cohort are delisted or dead within a few years.
- **Databento GLBX.MDP3 ohlcv-1m coverage verified** (2026-09-29/30, via
  `backtesting/check_coverage.py`, $0.01 per probe): NQ, MNQ, MES continuous
  `.v.0` each resolve to one outright (NQZ6, MNQZ6, MESZ6), no spreads; 23 of
  24 hours populated, 17:00 halt empty as expected; **16:00-17:00 post-close
  is fully populated (60 bars)**, which confirms the `add_overnight_levels`
  lookahead fix was required on real data. API key lives in the
  `DATABENTO_API_KEY` env var only, never in the repo.
- **Databento ohlcv-1m quotes (2026-10-11, to date):** ES $20.86 and NQ $20.22
  from 2010-06; YM $20.09 from 2010-06; RTY $11.17 from 2017-07; MES $9.56,
  MNQ $9.57, MYM $9.31, M2K $9.07 from 2019-05 (micros launched then).
  Micros track the minis within a tick, so test on **ES/NQ prices with
  `Costs.mes()`**; micro history adds nothing before 2019. ES first.
- **Databento continuous `.v.0` is NOT back-adjusted** and rolls at 00:00 UTC.
  Some days are flagged degraded (e.g. 2026-03-15/16); `fetch_futures.py`
  lists them.
- **Futures files must include the 16:00-17:00 ET post-close and the 18:00 ET
  Globex reopen** for `add_overnight_levels`; a file filtered to RTH has no
  overnight range at all and the function raises.

---

## Track 2: biotech catalyst research (screener built: `screeners/biotech/biocat`)

**Status (2026-10-12):** `python -m biocat screen` runs end to end on free data
(CT.gov v2, SEC EDGAR, unofficial Yahoo/Nasdaq quotes). First live run: 1,494
Phase 2/3 industry trials, 769 sponsors, 183 resolved to a ticker (24%) after
an SIC gate, **64 trials / 48 tickers** pass the size filters, one dossier
each. The tool **extracts and presents, never judges**: no score, no rating,
mechanical sort only. Revision history comes from the undocumented
`/api/int/studies/{nct}/history` route (needs an `ncbi_sid` cookie). Thesis
journal (`biocat journal`) is append-only and hash-chained; scored entries are
final. Audit `identity.csv` before using a row: the first run had four false
joins (Vertex, Thryv, Polaris, Vantage), now fixed and regression-tested. See
`screeners/biotech/README.md`.

The reasoning: technical analysis on SPY intraday is the lowest-prior corner of
the market. Domain expertise in healthcare is a real structural advantage. This
track is closer to investing than trading and fits a small account better.

### Base rates (BIO/Informa/QLS, 9,704 programs, 2011-2020)

| Transition | Rate |
|---|---|
| Phase 1 to 2 | 52.0% |
| **Phase 2 to 3** | **28.9%** |
| Phase 3 to NDA/BLA | 57.8% |
| NDA/BLA to approval | 90.6% |
| **Phase 1 to approval** | **7.9%** |

Range by area: 23.9% (haematology) to 3.6% (urology).

**At the base rate you lose**, because it is priced in. Profitability requires
shifting selection by roughly 15 points, repeatedly.

### The two mechanical screens almost nobody runs

**Cash runway**, from the 10-Q:

```
(cash + equivalents + short-term investments)
/ (net cash used in operating activities over last 2 quarters / 2)
= quarters of runway
```

If runway < (quarters to catalyst) + 2, a dilutive raise is coming.

**Shelf and ATM check**, via EDGAR full-text search: an effective S-3 shelf plus
an at-the-market offering agreement means they can sell stock any trading day
with no announcement.

The dilution asymmetry: good data leads to a raise into strength (upside taxed);
bad data leads to inability to raise (downside not capped).

### Screen thresholds

| Filter | Threshold |
|---|---|
| Market cap | $50M to $400M |
| Cash runway | > catalyst + 2 quarters |
| Catalyst window | 3 to 12 months out |
| Reverse split | none in 24 months |
| Avg daily volume | > $300k |

### Substitute for the option chain

Most sub-$300M biotechs have no usable options, so implied probability must be
derived from valuation:

```
EV = market cap + debt - cash
implied PoS ~= EV / (peak sales estimate x multiple)      # multiple ~2-4x
```

### Thesis template

```
TICKER:
CATALYST + DATE:
CLAIM (one falsifiable sentence):
WHAT THE MARKET BELIEVES:
WHY I DISAGREE (specific domain insight):
WHAT WOULD PROVE ME WRONG:
MY PROBABILITY:        ___%
MARKET-IMPLIED:        ___%
POSITION / SIZE:       (or "none, watching")
```

The two load-bearing fields are **"what would prove me wrong"** (written before
the event, defends against thesis drift) and **"my probability"** (enables
calibration scoring afterward).

### Free data sources

- **ClinicalTrials.gov** API: trial registrations, completion dates, full
  revision history (endpoint switching is visible here)
- **SEC EDGAR full-text search**: 10-Q financials, S-3 shelves, ATM agreements,
  8-Ks
- **FDA advisory committee calendar**: meetings posted weeks ahead; briefing
  documents typically drop a couple of days before
- **FDA warning letters / Form 483 database**

### Legal boundary (non-negotiable)

Operator is in a clinical setting. Trading on non-public information encountered
through placement is **insider trading** under provincial securities law. Rule:
**trade only on published material, and be able to point at where it was
published.**

### Build target

A screener that joins ClinicalTrials.gov upcoming readouts to the latest EDGAR
10-Q financials, computes cash runway automatically, flags shelf/ATM presence,
and outputs a weekly ranked candidate list.

---

## Track 3: execution vehicle (analysed, blocked on track 1)

### Futures mechanics

- Obligation, not right. No premium. Symmetric payoff. Counterparty is the
  clearinghouse.
- **Margin is a performance bond, not a loan.** No interest. Initial margin is
  roughly 5-7% of notional; brokers offer much lower day-trade margin
  (commonly $50-200 for MES). Verify per broker, it changes with volatility.
- **Marked to market daily.** Losses leave the account in cash the same day.
  Loss is unbounded, unlike a long option where max loss is the premium.
- `exit_at_session_end = True` exists to eliminate overnight gap risk.
  **Do not relax it.**
- Contract specs: ES = $50/point, MES = $5/point, both 0.25 tick, so one tick
  is $12.50 and $1.25 respectively. This is exactly `Costs.es()` / `Costs.mes()`.

### Prop firm evaluation (analysed in detail, verdict: not yet)

Topstep $50K Combine: $49/month, $149 activation, $3,000 target, **$2,000
trailing max loss (end-of-day ratchet, never moves down)**, $1,000 daily loss
limit, best day < 50% of total profit.

The trailing MLL is the binding rule. After a good day the floor ratchets up, so
**you can be eliminated while net up relative to your starting balance.**

Topstep's own published 2025 disclosures:

| Metric | Rate |
|---|---|
| Combines passed, per attempt | **16.8%** |
| People funded at least once | 51.8% (implies ~4 attempts each) |
| Funded traders who received a payout | 33.3% |
| Reached a **Live** account | **0.71%** |

→ ~17% of entrants ever receive any payout. 99.3% of "funded" traders never
trade real capital; the Express account is a simulator.

`propfirm/combine_sim.py` and `funnel_sim.py` model this rule stack. **The model
reproduces all three published figures at an input of -0.05R expectancy**, i.e.
the published pass rate is what a population with a small negative edge produces.

Simulated Combine pass rates:

| Expectancy | $100/trade | $200/trade |
|---|---|---|
| -0.05 R | 11.0% | 21.2% |
| **0.00 R** | **27.5%** | **32.8%** |
| +0.05 R | 50.8% | 45.8% |
| +0.10 R | 71.7% | 58.9% |

Three conclusions:

1. **A zero-edge trader passes 27-33% of the time.** Passing is weak evidence of
   skill and a dangerous source of false confidence.
2. **With no edge the rules reward sizing up** (fewer bets, fewer chances for the
   ratchet to catch you). With a real edge they reward sizing down. The
   evaluation pays you to gamble precisely when you should not.
3. **Breakeven requires an average payout above ~$1,800** at realistic inputs,
   ~$960 at zero edge. First payouts are typically far below that.

**The subscription is the capital.** 10 months plus activation is $639, which is
a quarter to a third of the $2,000-4,000 needed to fund an MES account outright.
Decision deferred until track 1 produces a measured out-of-sample expectancy;
then read it off the table above.

---

## Closed questions (do not reopen without new evidence)

- **Low-float momentum ("Ross Cameron method").** Real phenomenon, worst friction
  profile examined (1-3% spreads plus unmodellable LULD halts), and the written
  rule has 6+ undefined parameters. FTC action against Warrior Trading, $3M,
  2022, finding that most customers lost money.
- **Memecoins.** Strictly negative-sum: aggregate participant P&L = -(fees + MEV).
  Pump.fun graduation 0.2-2%; ~3% of wallets cleared $1,000 profit. Four possible
  edges: issuance/distribution (legal exposure under OSC), speed (below capital
  floor; infra is a fixed cost and the variance needs ~2,000 attempts/month),
  attention detection (only one buildable at $1k, forward-testable but not
  backtestable), forensics (defensive constraint, generates no expectancy).
- **$TRUMP as case study.** 80% supply retained, single-sided Meteora DLMM
  position deposited 17h pre-launch (a programmatic sell ladder, not market
  making), ~$100M in fees on $39B of volume, 813,294 wallets down ~$2B. The
  entire 416x ($0.18 to $75) existed in the first hours. Demonstrates every
  mechanism above simultaneously.
- **Forex.** Reviewed; no structural advantage over MES at this size.
- **Altcoin universes.** Catastrophic survivorship bias. BTC/ETH only if crypto.

---

## Standing principles (apply to all tracks)

1. **Write the rule before looking at data.** If it needs a judgment call in the
   moment, it is not a rule.
2. **Every variant tested is a trial.** Count them. The bar rises with the count.
   Timeframes and bar offsets count.
3. **A pattern defined by its outcome is not testable.** "It swept liquidity and
   reversed" is a description of the past. `close[i] < H` is a rule.
4. **Assume the backtest is wrong and work to disprove that.** Most ideas should
   die in testing. If none are dying, the testing is broken.
5. **Paper trade before funding.** Backtests do not capture fills, partial
   executions, or what a drawdown does to judgment.
6. **Case studies have survivorship bias too.** Three vivid examples are three
   draws from an unsampled distribution.
7. **Check friction before strategy, in R.** The instrument's round-trip cost
   has decided more questions in this project than any signal has. Measure it
   as round-trip cost / typical stop, never as % of price: the ES FVG test
   failed because a "0.007%" market still costs 0.3 R on a tight stop.
8. **Forward-testing beats backtesting where survivorship bias is fatal.**
   Recording the universe as it exists, including what dies, sidesteps the
   problem entirely. Costs only time.

---

## Gate before any real money

- Pooled out-of-sample expectancy positive **after doubled costs**
- Deflated Sharpe **> 0.95** against the full cumulative trial count
- `pct_same_bar_exit` low (result is not a bar-size artifact)
- Stable across bar offsets and across at least two timeframes
- Not concentrated in one year or one volatility regime

---

## Immediate next actions

1. ~~Unblock track 1 with free BTC data.~~ Done; small zero-cost signal, gate
   failed, untradable on crypto. **Next: real SPY or MES/MNQ data** (FirstRate
   sample first). Everything in track 3 waits on this.
2. **Add the `bars_per_year` parameter, and wire `assert_no_roll_gaps` into
   the futures load path,** before any futures data is loaded. Both are latent
   bugs, not enhancements.
3. **Add `retest_depth`** to `Params`. The selectivity finding says the current
   entry condition is nearly empty; this is the targeted fix.
4. **Run the bar-offset robustness test.** Cheap, and it can invalidate
   everything downstream, so run it early.
5. **Add time stop and daily loss limit** so written setups are fully
   representable in code.
6. ~~Build the biotech screener.~~ Done (`biocat`). Next: read the 64 dossiers,
   pick 10 names for the journal.
7. **Start the thesis journal** with 10 names, zero capital, six-month
   calibration window.
