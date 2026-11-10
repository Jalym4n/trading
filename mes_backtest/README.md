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

`lucid-25k-viability-target.md` is the pass/fail standard: four hard gates
(>= 3 trades/week; max drawdown under ~$600 against a $1,000 wall; net
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

Not yet run. This section will state, in one sentence, whether the edge survives.
