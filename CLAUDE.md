# Project context

Carry-over context from a long design conversation. Two tracks: a quantitative
backtesting framework (built, untested on real data) and a biotech catalyst
research process (designed, not built).

---

## Operator constraints

- Account size: **$500 to $1,000**. Treat capital as the binding constraint on
  every design decision.
- Jurisdiction: **Ontario, Canada**. CIRO-regulated brokers. Crypto cannot be
  held directly in a TFSA and every disposal is a taxable event. FINRA's
  pattern-day-trader rule generally does not apply to Canadian brokers; confirm
  per broker.
- Background: **clinical / nursing placement, A&P coursework, research
  methodology training**. This matters for track 2.
- Formatting preference: no long dashes, compact output, bold key terms.

### Established economics (do not relitigate)

- $25/day on $500 is **5% daily**, which compounds to ~$109M in a year. The
  target is a capital problem, not a strategy problem. $6,300/yr needs roughly
  $30k to $60k of capital at realistic returns.
- Realistic ceiling on $500 with a genuine edge: **$50 to $150 per year**.
- Options require 100% cash; margin buying power cannot be used for long
  options.
- Friction dominates at this size. Required edge per trade: ~0.01% on SPY
  shares, >1% on small caps, 5-10% on cheap options.

---

## Track 1: `fvg_lab` backtesting framework

### Status

Code is **written, debugged, and verified on synthetic data**. It has **never
been run on real market data**. That is the single open blocker.

### Files

```
fvg_lab/
  data.py       loading, validation, session tagging, synthetic null data, holdout split
  strategy.py   Params, Bars (numpy view), FVG detection, AMD detection, entry/exit rules
  engine.py     event-driven loop, cost model, position sizing, trade records
  metrics.py    expectancy, PSR, deflated Sharpe, drawdown, randomization test
  trials.py     TrialLog, parameter sweep, purged walk-forward
run_example.py  worked example on synthetic data (MES costs)
run_spy.py      stage-1 SPY harness, USE_REAL_DATA flag
README.md
fvg_lab_v3.pine       TradingView FVG + iFVG indicator, mirrors strategy.py
amd_power_of_3.pine   AMD indicator with a self-scoring predicted-vs-opposite table
```

### Design rules (load-bearing, do not break)

1. **No lookahead.** A decision made at the CLOSE of bar `t` fills at the OPEN
   of bar `t+1`. ATR is shifted. Intents never cross a session boundary.
2. **Pessimistic fills.** When a bar's range contains both stop and target, the
   STOP is assumed to hit first. Bars that gap through the stop fill at the open.
3. **Costs before metrics.** Half-spread plus slippage per side, plus commission.
   `costs.double()` is the stress test.
4. **The trial log never resets.** `deflated_sharpe` uses the cumulative count.
   Every variant tested raises the bar.
5. **Purge and embargo.** Standard k-fold is invalid on time series and is not
   offered. `purged_walk_forward` drops sessions between train and test.
6. **Synthetic null first.** `data.synth()` is a random walk. Anything
   profitable there is measuring the search, not an edge.

### `Params` fields

```
entry_mode            fvg_retest | first_candle_break | fvg_plus_break | amd
first_candle_n, require_first_candle_bias, min_gap_atr, fvg_max_age
entry_start_bar, entry_end_bar
amd_cons_bars, amd_max_range_atr, amd_min_break_atr, amd_reclaim_bars
invalidation_mode (wick|close), invert_on_violation, max_inversions
ema_period, ema_mode (with|against)
stop_mode (fvg_far_edge|atr|opening_range|amd_extreme)
stop_atr_mult, stop_buffer_atr, min_stop_atr, max_stop_atr
target_r, exit_at_session_end, max_trades_per_session
allow_long, allow_short, atr_period
```

`Costs` presets: `spy_shares()`, `es()`, `mes()`.

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
  roughly round-trip cost ÷ stop distance, so ~0.85% round trip needs stops
  near **8%** to get drag under 0.1 R. Use crypto 5m to validate the pipeline
  and to ask "is there signal at zero cost", not as a tradable venue.

### Bugs found and fixed (watch for regressions)

- `floor(qty)` silently dropped ~99% of signals when one contract risked more
  than the risk budget. Now surfaced as `skipped_undersized`.
- Stops tighter than one bar's range made the pessimistic stop-first rule fire
  on nearly every trade. Fixed with `stop_buffer_atr` and `min_stop_atr`;
  diagnosed via `pct_same_bar_exit`.
- `df.iloc[i]` in the bar loop made sweeps take hours. Hot path is numpy now;
  indicators are cached across variants sharing `first_candle_n`, `atr_period`,
  `ema_period`, `amd_cons_bars`.

### Known gaps / TODO

- [ ] **No real data loaded.** Primary blocker.
- [ ] No time stop (exit after N bars unresolved)
- [ ] No daily loss limit
- [ ] No `retest_depth` parameter (require price to penetrate into the zone
      rather than graze its near edge). This is the highest-value addition
      given the selectivity finding above.
- [x] Percentage fees: `Costs` now takes `commission_pct`, `spread_bps`,
      `slippage_bps`; preset `Costs.crypto_spot(fee_pct=...)`. 24/7 sessions via
      `add_session(df, "00:00", "24:00")`. Tests in `backtesting/tests/`.
- [ ] Pine v3 arms on the break bar and checks reclaim from the next bar;
      Python allows same-bar reclaim. One-line divergence, intentional, flagged.

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

### Gate before any real money

- Pooled out-of-sample expectancy positive **after doubled costs**
- Deflated Sharpe **> 0.95** against the full cumulative trial count
- `pct_same_bar_exit` low (result is not a bar-size artifact)
- Not concentrated in one year or one volatility regime

---

## Track 2: biotech catalyst research (designed, not built)

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
÷ (net cash used in operating activities over last 2 quarters ÷ 2)
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
EV = market cap + debt − cash
implied PoS ≈ EV ÷ (peak sales estimate × multiple)      # multiple ~2-4x
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

## Standing principles (apply to both tracks)

1. **Write the rule before looking at data.** If it needs a judgment call in the
   moment, it is not a rule.
2. **Every variant tested is a trial.** Count them. The bar rises with the count.
3. **A pattern defined by its outcome is not testable.** "It swept liquidity and
   reversed" is a description of the past. `close[i] < H` is a rule.
4. **Assume the backtest is wrong and work to disprove that.** Most ideas should
   die in testing. If none are dying, the testing is broken.
5. **Paper trade before funding.** Backtests do not capture fills, partial
   executions, or what a drawdown does to judgment.
6. **Case studies have survivorship bias too.** Three vivid examples are three
   draws from an unsampled distribution.

---

## Immediate next actions

1. **Unblock track 1.** Either buy the FirstRate SPY sample and wire
   `data.load_csv`, or pull free BTC/ETH data and add the percentage-fee cost
   model. The second is free and available today.
2. **Add `retest_depth`** to `Params`. The selectivity finding says the current
   entry condition is nearly empty; this is the targeted fix.
3. **Add time stop and daily loss limit** so the written setups are fully
   representable in code.
4. **Build the biotech screener** (ClinicalTrials.gov + EDGAR join).
5. **Start the thesis journal** with 10 names, zero capital, six-month
   calibration window.
