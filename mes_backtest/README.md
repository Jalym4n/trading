# mes_backtest

Mechanical backtest of the MES volume-profile + fib confluence strategy defined
in `lucid-25k-backtest-spec.md`, with walk-forward validation and overfitting
guards. The deliverable is an **honest answer to "does this edge exist"**. A
result of "no edge" is a successful outcome. Historical backtest only: no live
trading, no broker connection, no order routing.

## Data (reproducible order, from `config.json`)

| Field | Value |
|---|---|
| Provider | Databento (batch download) |
| Dataset | `GLBX.MDP3` (CME Globex) |
| Symbol | `MES`, continuous front-month |
| Schema | `ohlcv-1m` (1-minute OHLC + exchange volume) |
| Range | 2020-01-01 to 2026-11-07 |
| Encoding / compression | CSV / zstd (`.csv.zst`) |
| Prices / timestamps | decimal / ISO 8601 **UTC** |
| Columns | `ts_event, rtype, publisher_id, instrument_id, open, high, low, close, volume, symbol` |

Raw files go in `data/raw/` (git-ignored: licensed, not redistributable), one
Databento file per instrument (the MES file is there; MNQ lands next to it,
same order: `GLBX.MDP3`, `ohlcv-1m`, raw contracts, 2020-2026). `ts_event` is
converted from UTC to `America/New_York` **before** any session logic.

## Instruments

`config.json` `params.instruments` lists the active instruments; every one goes
through the identical pipeline (drop spreads, front month by volume crossover,
skip roll day + first new-contract session, skip missing-bar days, UTC to ET)
into `data/processed/<INSTRUMENT>/`. Point value, tick size, tick value and
commission are read per symbol from `instrument_specs` (MES $5/pt, MNQ $2/pt,
both 0.25 tick); slippage is `slippage_ticks` x the instrument's tick, on the
entry and on market exits (stop, time), never on the limit target. Results are
reported per instrument AND pooled. To add an instrument: drop its file in
`data/raw/`, add its spec, add it to `params.instruments`, run `src.load` and
`src.profile` for it.

## The bar every strategy must clear

`lucid-100k-viability-target.md` is the pass/fail standard (LucidFlex 100K since 2026-11-10; the 25K figures are kept in `config.json` `account.previous`): four hard gates
(>= 3 trades/week; max drawdown under ~$1,800 against a $3,000 wall; net
expectancy positive in a majority of walk-forward windows; positive on the
2026 holdout) plus soft targets. Its numbers are in `config.json`
`viability_gates`, and every result is scored against them. A confident "no
strategy clears these" is a real answer.

## Parameters

Every parameter (sessions, profile bin, confluence tolerance, stop pad, costs,
Lucid limits, walk-forward windows, wiggle values) lives in `config.json` at the
project root. Modules read it; nothing is hardcoded in `src/`.

## Build order (spec section 4; each validated before the next)

1. `src/load.py`: read raw, UTC to New York, handle the roll, save
   `data/processed/mes_1m.parquet` and `mes_5m_rth.parquet`. Check: 78 five-minute
   and 390 one-minute bars per full RTH day, first bar at 09:30 ET.
2. `src/profile.py`: prior-session POC / VAH / VAL from 1-minute bars. Check:
   histogram, POC is the mode, value area holds ~70% of volume.
3. `src/strategy.py`: pure function `(day_bars, prior_levels) -> trades`. Bars are
   evaluated strictly in sequence (no look-ahead). Check: 3 days by hand.
4. `src/backtest.py`: loop days, apply commission and slippage, write the trade log.
5. `src/stats.py`: expectancy, profit factor, drawdown ($ and R), streaks, R
   distribution, worst day.
6. `src/guards.py` + `src/walkforward.py`: trial counter, OOS holdout (calendar 2026,
   run once), walk-forward windows, regime split, confluence on/off A/B.

## The hypothesis loop (phase 4)

Any mechanical strategy, yours or another model's, goes through the identical
harness: same costs, same windows, same statistics, same scorecard, same trial
counter.

1. **Propose** a falsifiable hypothesis, and write down what result would
   disprove it BEFORE running.
2. **Register** it: one file in `src/strategies/` with a function
   `(day_bars, prior_levels, params) -> DayResult`, decorated
   `@register(name, hypothesis=..., disproof=...)`. Pure, deterministic, bars
   consumed in order; `tests/test_registry.py` runs a no-look-ahead probe on
   every registered strategy automatically.
3. **Run** it on training, then walk-forward:
   `python -m src.harness run --strategy NAME --phase training` and
   `--phase walkforward`. The holdout is unreachable from here.
4. It is **auto-logged** to `results/trial_log.jsonl` (append-only,
   hash-chained, committed to git): strategy, effective params, instruments,
   window, git commit, results, scorecard. Every result prints the reminder:
   *N strategies/params tested so far. At p<0.05, expect ~N x 0.05 false
   positives by chance. A winner here is a CANDIDATE, not a confirmed edge.*
5. **Compare** against the current best on the same windows:
   `python -m src.harness compare A B --phase walkforward`.
6. Only after ONE finalist is chosen: `python -m src.harness holdout
   --strategy NAME --confirm --reason "..."`, once.

### Trial budget and stopping rule (set 2026-11-10, at 3 trials, before trial 4 was run)

- **Ceiling: 10 distinct trials in total.** The harness refuses an 11th
  (`guards.check_budget`). A tweak is a new trial, counted.
- **The bar does not move.** A strategy advances only with net expectancy > 0
  on training AND across the walk-forward windows. No "close enough".
- **Stopping rule.** If nothing clears all four gates on walk-forward within
  the budget, the conclusion is: *no edge I can build trades often enough and
  survives costs on a $1,000 buffer.* Nothing is funded. That is the deliverable.

**Budget raised on 2026-11-12, after the first 10 were spent:** ceiling 15, and for
trials 11-15 the bar is higher: pooled walk-forward net expectancy > 0 with **t >= 3**
(on daily sums) and a majority of windows positive.

### The holdout rule (enforced in code)

The 2026 holdout (2026-01-01 to 2026-11-06) is run **once, on one finalist,
after all searching is done**. Peeking and then tweaking burns it: the second
look is no longer out of sample. In code: `backtest.run` clamps every window
to end on 2025-12-31 and refuses a start inside 2026; the only road in is
`guards.run_final_holdout`, which refuses without `confirm=True` and a written
reason, refuses if `results/holdout_ledger.jsonl` already has any entry, prints
a banner, and records the consumption in the ledger and the trial log. The
ledger is committed, so a second attempt is a visible edit in git history.

## How to run (once modules exist)

`pip install -r requirements.txt && python -m src.backtest --config config.json`

Built so far (all take `--instrument`): `python -m src.load` (front month,
5-min RTH, skip flags), `python -m src.profile` (prior-session POC / VAH /
VAL, plot in `results/`), `python -m src.strategy --pick` (hand-check traces
from 2020-2021), `python -m src.backtest --strategy confluence_off --start
2020-01-01 --end 2021-12-31 --set min_leg_points=0` (trade log with costs;
counts only, statistics come with stats.py), and `python -m pytest -q tests`.
`src.backtest` clamps every window to end before the 2026 holdout. The
harness (`python -m src.harness list | run | log | compare | holdout`) is the
front door for everything from phase 5 on.

## Result

**No strategy has cleared the viability gates. The opening-range family is closed; late-day momentum is disproved.**
Training window only (2020-2021, MES, 1 contract); no walk-forward window and
no holdout was run for it, because nothing earned one.

| Trial | Strategy | Trades | /week | Gross $/trade | Net $/trade | PF net | Max DD | Gates failed (100K) |
|---|---|---|---|---|---|---|---|---|
| 1 | `or_fib_base` (nearest-node target, min_leg 0) | 127 | 1.26 | -3.15 | -6.33 | 0.50 | $883 | 1, 3 |
| 2 | `or_fib_confluence` (min_leg 10) | 3 | 0.03 | +1.67 | -1.24 | 0.85 | $25 | 1, 3 (not testable) |
| 3 | `or_fib_base_2r` (target = 2x stop distance) | 160 | 1.58 | -5.06 | -8.70 | 0.48 | $1,461 | 1, 3 |
| 4 | `late_day_momentum` (15:30 entry with the day's move, exit 15:55, 10-pt stop) | 481 | 4.76 | +1.04 | -2.70 | 0.87 | $2,263 | 2, 3 |
| 5 | `noise_band_trend` (outside the 14-day noise band at half-hour checks, 30-pt stop) | 372 | 3.68 | +10.52 | +6.78 | 1.25 | $835 | training: none. Walk-forward 2022-2025: Gate 2 (max DD $1,944) |
| 6 | `smc_sweep_bos` (liquidity sweep, break of structure, order block / FVG in discount, daily bias) | 66 | 0.65 | -0.70 | -3.91 | 0.78 | $453 | 1, 3 |
| 7 | `amt_value_reentry` (open outside prior value area, accepted back in, VWAP side; the '80% rule') | 68 | 0.67 | +12.54 | +9.31 | 1.47 | $452 | training: 1. Walk-forward 2022-2025: 1, 3 (net -$5.16, 2 of 4 windows) |
| 8 | `crt_hourly` (5-min sweep of the prior hour's range, closes back in, structure shift, target the other side) | 321 | 3.18 | +1.51 | -1.69 | 0.91 | $1,331 | 3 |
| 9 | `crt_hourly_high_gamma` (trial 8 only on days after SPX GEX above its 252-day median) | 196 | 1.94 | +5.11 | +1.97 | 1.13 | $552 | training: 1. Walk-forward: 1, 3 (net -$2.12, 2 of 4 windows) |
| 10 | `noise_band_low_gamma` (trial 5 only on days after GEX below its median) | 191 | 1.89 | +9.12 | +5.38 | 1.16 | $868 | training: 1. Walk-forward: 1, 3 (net +$3.95, t 0.6, 2 of 4 windows) |

Scored against the LucidFlex 100K gates (Gate 2: max drawdown < $1,800). The
account was switched from the 25K on 2026-11-10 after these four trials; they
were re-scored from the logged statistics (`python -m src.harness rescore`),
not re-run and not re-logged. Under the 25K's $600 limit trials 1, 3 and 4 also
failed Gate 2. The switch changed no verdict: every trial still fails Gate 3.

Conclusion: the opening-range family fails on GROSS expectancy. With the
nearest-node target the payoff geometry is upside-down (median target 3.25 pts
away, median stop 4.75; it needs ~62% winners and gets 52%). Trial 3 kept the
entry and moved the target to 2x the stop distance: it loses more before costs
(-$5.06/trade, t = -2.3), with 13 targets against 75 stops and 72 exits at the
11:00 close. The entry carries no usable directional information; it is noise.

Lessons:

1. **"First opposite-colour bar" makes legs too short to be useful.** The
   median leg is 5.9 points, one or two 5-minute bars. Fibs of a leg that size
   sit within ~2 points of each other, so fib precision is an illusion, and a
   10-point minimum leg leaves 1.5 confluence trades a year.
2. **"Nearest volume node" is a losing target rule: it is too close.** The
   target is typically nearer than the stop, so even a better-than-coin-flip
   entry loses.
3. A wider target does not rescue it inside a 09:30-11:00 window: most trades
   time out before reaching 2R.

Trial 4 (late-day momentum) is the first to pass Gate 1, and it is disproved
by its own pre-written condition: a gross edge of about $1 a trade that is not
distinguishable from zero and is smaller than the ~$3.74 round-trip cost.

Trial 5 walk-forward (2022-2025, no rule changes): net $/trade +11.32, +7.79,
+4.35, -3.83 by year; pooled out of sample +$5.18 (t = 1.3, 854 trades), 3 of 4
windows positive (Gate 3 passes), but max drawdown $1,944 at 1 contract fails
Gate 2, the edge shrinks every year and 2025 is negative. At 1 contract $6,000
takes about 5 years; no contract size is both inside the $1,800 limit and fast
enough to fund. Not a finalist; the holdout stays unspent. (Trial-log line 6
carries a wrong pooled doubled-slippage figure, +$4.14; line 7 is the corrected
re-run, +$2.68. Same trades, same trial.)

Every run is in `results/trial_log.jsonl` (10 of the 10-trial budget used: the budget set on 2026-11-10 is spent).
