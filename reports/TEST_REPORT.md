# Trading research: full test report (as of 2026-10-12)

Self-contained summary of every test run in the `jalym4n/trading` repo, for planning next steps. All numbers come from committed trial logs and run outputs. **No holdout set has been opened on any market.**

---

## 1. Context and constraints

- **Account:** $500 to $1,000. **Jurisdiction:** Ontario (CIRO; futures and prop payouts are business income).
- **Time:** placement hours conflict with 09:30 to 11:00 ET, so anything needing live screen time at the US open is not operable.
- **Established economics:** realistic ceiling with a genuine edge is **$50 to $150/yr** on $500. The target income is a capital problem, not a strategy problem.
- **Target vehicle (pre-testing view):** MES futures, the only instrument with leverage and low friction.

## 2. Method (applies to every test below)

- **No lookahead:** decide at bar close, fill at the next bar's open; ATR uses past bars only; no position crosses a session boundary.
- **Pessimistic fills:** if one bar touches both stop and target, the stop is assumed to hit first.
- **Costs:** half-spread, slippage and commission on every fill. Every test is also re-run at **doubled costs** (stress test).
- **Null first:** each idea is run on a synthetic random walk. Anything "profitable" there measures the search, not an edge.
- **Trial counting:** every variant tested is logged permanently. The **deflated Sharpe** discounts results by the cumulative trial count (want > 0.95).
- **Purged walk-forward:** parameters are chosen on a training window, tested on the next unseen window, with an embargo gap between them. Pooled out-of-sample (OOS) expectancy is the honest number.
- **Holdout:** the last 30% of each dataset is held back untouched.
- **Units:** results are in **R** (1 R = the amount risked on a trade). +0.10 R means average profit of 10% of risk per trade.
- **Fractional sizing for measurement:** every signal counts, so stop size doesn't silently filter trades. Affordability at $1k/$4k is reported separately.

### Gate before any real money (none has passed)

1. Pooled OOS expectancy positive **after doubled costs**
2. Deflated Sharpe > 0.95 against the full cumulative trial count
3. Low same-bar exits (the result isn't a bar-size artifact)
4. Stable across bar offsets and at least two timeframes
5. Not concentrated in one year or regime

## 3. Data acquired

| Dataset | Source | Span | Cost | Quality notes |
|---|---|---|---|---|
| BTCUSDT 5m | Binance public archive | 2019-01 to 2026-09, 814,226 bars | free | 0.10% missing (exchange outages), checksum-verified |
| BTC-USD 5m | Coinbase public API | 2019-01 to 2026-10, 816,929 bars | free | 0.056% missing |
| ES 1m, 24h | Databento GLBX.MDP3 `ohlcv-1m` | 2010-06 to 2026-10, 5,714,493 bars | **$20.86** (+ ~$0.25 probes) | 66 quarterly rolls back-adjusted (raw median jump 7.12 pts, adjusted 0.00); 32 days flagged degraded by Databento |

- **Venue matters:** only **83%** of Coinbase bullish fair value gaps also appear on Binance over the same bars. Gap identity is partly a venue artifact.
- **Databento coverage check** (NQ, MNQ, MES): 23 of 24 hours populated, 16:00 to 17:00 ET post-close fully populated, no calendar spreads.
- **ES sessions:** of 4,200 regular-hours sessions, 4,068 are full, 80 are half-days (real), and 52 are short; 14 of those are ~2h CME holiday sessions (excluded in the last test).
- **Data quotes for later:** NQ $20.22, YM $20.09 (from 2010); RTY $11.17 (from 2017); MES/MNQ/MYM/M2K about $9 to $10 each (from 2019 only).

## 4. Strategies tested

### Strategy A: fair value gap retest with opening-candle bias (`fvg_retest`)

- **Bias:** the first 5m candle of the session closes up = longs only, down = shorts only.
- **Gap:** bullish if `low[i] > high[i-2]`, bearish if `high[i] < low[i-2]`, optionally at least 0.25 ATR wide, live for 20 bars.
- **Entry:** a later bar trades back into a live gap in the bias direction; fill at the next open. Entries only on bars 1 to 60 of the session; up to 2 trades per session.
- **Exit:** stop beyond the gap's far edge plus 0.25 ATR (stops under 0.25 ATR skipped); target 2R or 3R; flat at session end.
- **Known weakness:** about 90% of gaps get retested within 1 bar, so in practice this is close to "trade in the bias direction right after a gap forms."

### Strategy B: market structure, change of character (`choch`)

- **Swing:** a high strictly above the 3 bars before and at least as high as the 3 after; only known 3 bars later.
- **Entry:** first close through the last unbroken swing **against** the current trend (the trend flips). Stop at the opposite swing.

### Strategy C: quasimodo (`qm`)

- **Setup:** swings H1, L1, then H2 > H1 (head); a close below L1 arms a short at H1. Bullish is the mirror.
- **Entry:** a later bar returns to H1 (within 0.1 ATR) while still closing below H2. Stop beyond the head.
- **Cancel:** price trades above the head first, or after 24 bars. One entry per setup.

## 5. Results

### Test 1: synthetic nulls (random walk; correct result is "nothing")

| Idea | Best of grid | Deflated Sharpe | Walk-forward OOS |
|---|---|---|---|
| FVG / first-candle family (MES costs) | -0.067 R (144 variants) | 0.00 | -0.138 R |
| AMD / power of 3 | +0.49 R headline (108 variants) | 0.00 | decay +0.65 R |
| Overnight high/low sweep vs break | +0.14 R (108 variants), p=0.096 | n/a | pooled means -0.06 / -0.17 R |
| Change of character | +0.01 R | 0.18 | -0.07 R |
| Quasimodo | +0.12 R | 0.06 | +0.01 R |

**Verdict:** the framework rejects noise correctly, and the "best of grid" numbers show how much the search alone can manufacture.

### Test 2: Strategy A on real BTC 5m (dev 2019-01 to 2024-06; UTC-day sessions)

| | Binance | Coinbase |
|---|---|---|
| Baseline at zero cost (randomization p) | +0.089 R (p=0.000) | +0.089 R (p=0.000) |
| Best of 144 variants, zero cost | +0.106 R | +0.124 R |
| Walk-forward pooled OOS, zero cost | **+0.089 R** | **+0.101 R** (27/46 periods positive) |
| Deflated Sharpe | 0.79 (145 trials) | 0.00 (6,959 trials) |
| At 0.40% taker fee | -5.2 R/trade, $1k to $0.04 | -8.9 R/trade, $1k to $0.01 |

**Verdict: FAIL (untradable).**
- **The good news:** it's the first rule to survive a null and a walk-forward.
- **The problem:** the edge is about 0.017% of price per trade, while a retail crypto round trip costs about 0.85%, roughly 50x larger.

### Test 3: Strategies B and C on real ES (dev 2010-06 to 2021-11, 5m regular hours, MES costs)

| | Change of character | Quasimodo |
|---|---|---|
| Baseline, MES costs | **-0.152 R** (2,272 trades) | **-0.485 R** (259 trades) |
| Baseline, 2x costs | -0.273 R | -0.691 R |
| Best of grid | -0.148 R (12 variants) | -0.323 R (48 variants) |
| Walk-forward OOS at 2x costs | -0.271 R, 3 of 44 periods positive | -0.591 R, 7 of 44 positive |
| Deflated Sharpe | 0.00 | 0.00 (633 trials) |
| Median stop | 5.6 pts ($28/MES contract) | 2.9 pts ($14) |
| Gross before costs (approx.) | about -0.03 R | about -0.28 R |

**Verdict: both CLOSED.** Every variant is negative and worse than the synthetic null.
- **Change of character:** about zero before costs; fading it pays the same costs, so it's negative too.
- **Quasimodo:** negative even before costs, rare (one setup per ~11 sessions), with 12% same-bar exits.

### Test 4: Strategy A on real ES, PRE-REGISTERED (dev 2010-06 to 2021-11)

- **Pre-registration:** three variants were fixed from the BTC results and the pass rule was committed to git before the first run.
- **Pass rule:** all three positive at zero AND MES costs, plus a positive walk-forward at 2x costs.
- **Data handling:** 14 holiday sessions dropped; 2,925 dev sessions.

| Variant | Zero cost | MES costs | 2x costs | Trades | Median stop |
|---|---|---|---|---|---|
| P1 BTC baseline (gap ≥0.25 ATR, 2R) | **+0.052 R** | -0.293 R | -0.551 R | 3,931 | 2.38 pts |
| P2 Binance best (gap ≥0.25 ATR, 3R) | **+0.051 R** | -0.295 R | -0.542 R | 3,758 | 2.36 pts |
| P3 Coinbase best (any gap, 3R) | **+0.056 R** | -0.314 R | -0.652 R | 4,556 | 2.19 pts |

- **Walk-forward** (144-variant grid, choosing and testing at 2x costs): 3 of 44 periods positive, pooled OOS **-0.421 R**.
- **By year:** every year 2010 to 2021 is negative at MES costs for all three variants (best year 2020, about -0.1 R).
- **Exploratory sweep:** all 144 variants negative, the best -0.195 R; deflated Sharpe 0.00 against 6,530 trials.
- **Affordability:** 38 to 44% of trades fit a 1% risk budget at $1k, and 95% at $4k. Account size was not the binding constraint.

**Verdict: FAIL.** The zero-cost signal **replicates on a third market** (same direction, about half the BTC size), but:
- **Gross edge:** about 0.05 R × 2.4 pts ≈ **0.12 pts** per trade.
- **MES round trip:** about **0.71 pts** (1 tick spread and slippage per side, plus commission).
- **Result:** friction is about **6x the edge**, roughly 0.35 R per trade.

## 6. Cross-cutting findings

1. **Friction must be measured in R, not % of price.** The earlier reasoning ("MES 0.007% friction is below the 0.017% BTC edge") was wrong, because ES gap stops are about 0.07% of price against BTC's 0.17%. The only valid unit is **round-trip cost ÷ stop distance**. This one number has decided every real-data test so far.
2. **5-minute stops are too small for retail friction, on every market tested.** For MES costs to drop below a 0.05 R gross edge, stops need to be about 14 pts or more, roughly 6x wider than 5m gaps produce.
3. **The one persistent pattern (FVG retest + bias, about +0.05 to +0.10 R before costs) is real but tiny.** It appeared on three datasets across two asset classes.
4. **The market-structure ideas (CHoCH, quasimodo) show no signal even before costs on ES.**
5. **The trial count is now large:** 23,812 logged trials in total (crypto 13,628, FVG-on-ES 6,530, structure 2,788, structure null 866). Any future "discovery" in this family faces a very high deflated-Sharpe bar.

## 7. Bugs found and fixed (each would have produced a false result)

| Bug | Effect if left in |
|---|---|
| Overnight high/low counted 16:00 to 17:00 ET post-close bars in the same morning's range | Lookahead: 09:30 levels included prices from 7 hours later; inflated sweep/break results on real futures |
| Sweep and walk-forward never passed fractional sizing | Every crypto signal skipped silently |
| Whole-contract sizing dropped wide-stop signals | Hidden filter: null CHoCH read +0.18 R instead of +0.01 R |
| Roll-gap check fired on every weekend reopen and on 1-tick overnight moves | The check would have been ignored; now roll-specific checks are the hard gate |
| Contract check confused reused CME instrument IDs | Full ES build aborted; now resolved per active date range |
| Unadjusted futures rolls (fixed by back-adjustment) | Phantom gaps four times a year at prices that never traded |

## 8. What has NOT been tested yet

- Any timeframe other than 5m (15m, 30m, 60m)
- The bar-offset robustness test (same 1m data resampled at offsets 0 to 4 min)
- `retest_depth` (requiring price to push into the gap, not just touch it)
- Time stop, daily loss limit
- NQ, YM, RTY, or a multi-instrument universe
- SPY shares (data not bought)
- Any holdout set
- Track 2 (biotech catalyst screener): designed, not built
- Pine indicators: written, but not yet compiled/verified in TradingView

## 9. Candidate next steps (for discussion)

| Option | What it answers | Cost | Notes |
|---|---|---|---|
| **A. FVG retest on 30m bars, pre-registered single shot** | Does the +0.05 R survive when stops are ~6x wider? | ES data owned; ~30 min compute | New strategy, new trial count. sqrt(t) scaling argues friction drops; the edge may also vanish |
| **B. Track 2: biotech catalyst screener** | Can domain expertise create an edge that intraday friction can't touch? | free data (ClinicalTrials.gov, EDGAR) | Fits the account size and schedule; forward-testable with a zero-capital thesis journal |
| C. Bar-offset test on the ES FVG signal | Is the +0.05 R gross real structure or an artifact of where the 5m bars start? | compute only | Cheap; could kill the one remaining signal |
| D. Add NQ ($20.22) for breadth | More trades and a second market | $20 | Doesn't fix friction in R; only worth it if A or C succeed |
| E. Stop intraday technical work | Accept the result | none | Consistent with every real-data test so far |

**My recommendation:** **B**, optionally preceded by **A** as a single pre-registered shot for closure on the FVG idea. Every intraday test has failed for the same reason (friction ÷ stop size), and nothing in options C to E changes that unit.
