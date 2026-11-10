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

Raw files go in `data/raw/` (git-ignored: licensed, not redistributable).
`ts_event` is converted from UTC to `America/New_York` **before** any session
logic.

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

## How to run (once modules exist)

`pip install -r requirements.txt && python -m src.backtest --config config.json`

Built so far: `python -m src.load` (front month, 5-min RTH, skip flags),
`python -m src.profile` (prior-session POC / VAH / VAL, plot in `results/`), and
`python -m pytest -q tests`.

## Result

Not yet run. This section will state, in one sentence, whether the edge survives.
