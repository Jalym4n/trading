# MES Confluence Strategy — Backtest Build Spec (Claude Code Handoff)

**Purpose:** Backtest a mechanical volume-profile + fib-retracement intraday
strategy on MES, with walk-forward validation and overfitting guards. The goal
is to find out whether the edge is REAL before risking money on a Lucid $25k
evaluation.

**Read this whole file first. Do not start coding until the data is in place.**

---

## Data source (what the user actually bought)

- **Provider:** Databento, batch download.
- **Dataset:** `GLBX.MDP3` (CME Globex).
- **Symbol:** MES (Micro E-mini S&P 500), continuous front-month.
- **Schema:** `ohlcv-1m` — 1-minute bars: open, high, low, close, and REAL
  exchange-traded volume.
- **Range:** 2020-01-01 → 2026-11-07.
- **Encoding:** CSV.
- **Compression:** zstd (`.csv.zst`). Decompress with `zstandard` (pip) or the
  `zstd` CLI; pandas can also read `.zst` directly via `pd.read_csv(path)` when
  `zstandard` is installed.
- **Price format:** Decimal (prices are real decimals, e.g. 5805.25 — NOT fixed-
  point integers, so no 1e-9 scaling needed).
- **Timestamp format:** ISO 8601 (e.g. `2026-11-07T14:30:00.000000000Z`), in
  **UTC**. MUST be converted to America/New_York before any session logic.
- **Delivery:** ~4 files, ~200 MB total. Concatenate them in time order on load.

### Expected CSV columns (ohlcv-1m)
`ts_event, rtype, publisher_id, instrument_id, open, high, low, close, volume,
symbol`
- `ts_event` = bar timestamp (UTC, ISO 8601).
- `open/high/low/close` = decimal prices.
- `volume` = contracts traded in that 1-min bar (THIS is the real volume the
  profile needs).
- `symbol` = the contract (e.g. `MESZ5`); useful to see roll boundaries.

**First thing load.py must do:** parse `ts_event` as UTC, convert to
`America/New_York`, THEN filter sessions. Getting this wrong shifts every bar
by 4–5 hours and silently breaks the opening-range logic.

---

## 0. Context for the coding agent

- User is validating a discretionary setup by making it mechanical and testing it.
- The deliverable is NOT a profitable strategy. It is an HONEST ANSWER to
  "does this edge exist, or is it noise?"
- Build the overfitting guards FIRST-CLASS, not as an afterthought. The whole
  point is to resist fooling ourselves.
- No live trading, no broker connection, no order routing in this project.
  Historical backtest only.

---

## 1. The strategy (mechanical rule set)

**Instrument:** MES (Micro E-mini S&P 500)
**Session:** 09:30–11:00 ET only. No entries after 11:00. Flat by 11:00.
**Bar size:** 5-minute, Regular Trading Hours.

### Levels (computed before each session)
From the PRIOR full RTH session (09:30–16:00 previous day), compute a
volume profile and extract:
- `POC`  — price of highest traded volume
- `VAH`  — value area high (top of 70% volume band)
- `VAL`  — value area low (bottom of 70% volume band)

Value area = 70% of session volume, standard. Build the profile at a price-bin
resolution of 1 MES point (0.25 is too fine, 1.0 is standard for the profile).

### Impulse leg (fixed, no discretion)
- `OR_high`, `OR_low` = high/low of 09:30–09:45 (first three 5-min bars).
- Impulse = first 5-min bar that CLOSES outside [OR_low, OR_high].
- Leg start = the OR boundary that was broken.
- Leg end = extreme price reached before the first opposite-color 5-min close
  after the breakout bar.
- If no close outside the OR by 10:30, NO TRADE that day.

### Fib levels on the leg
0.382, 0.5, 0.618, 0.786 (retracement from leg end back toward leg start).

### Confluence filter (THE EDGE — test with and without this)
Valid zone exists only if a fib level in {0.5, 0.618, 0.786} is within
`CONFLUENCE_TOL` points of {POC, VAH, VAL}.
- Default `CONFLUENCE_TOL = 3.0` MES points.
- If no fib lands near a node → NO TRADE.

### Entry trigger
Price retraces into the confluence zone, then a 5-min bar CLOSES back in the
trade direction with close in the favorable third of its range
(upper third for longs, lower third for shorts). Enter at that bar's close.

### Stop
3.0 points beyond the volume node being traded (parameter `STOP_PAD = 3.0`).

### Target
Next volume node in the trade direction (VAL→POC→VAH and reverse).
If no node beyond entry, use the impulse leg extreme as target.
Full exit at target. No trailing.

### Daily rule
Max 2 trades per day. Stop for the day after 2 losses OR 1 target hit.

---

## 2. Parameters to expose (for sensitivity testing, NOT optimization)

```python
PARAMS = {
    "session_start": "09:30",
    "session_end":   "11:00",
    "or_end":        "09:45",
    "source_tz":      "UTC",            # Databento ts_event is UTC
    "market_tz":      "America/New_York",
    "bar_size_source":"1min",           # bought 1-min; resample up to 5-min
    "value_area_pct": 0.70,
    "profile_bin":    1.0,    # points
    "confluence_tol": 3.0,    # points  <-- wiggle test: 2,3,4
    "stop_pad":       3.0,    # points  <-- wiggle test: 2,3,4
    "fib_levels":     [0.382, 0.5, 0.618, 0.786],
    "max_trades_day": 2,
    "commission_rt":  1.24,   # $ per MES round turn (see §6)
    "slippage_pts":   0.25,   # 1 tick each side, applied on entry AND stop
}
```

Lucid $25k risk layer (used in §7):
```python
LUCID_25K = {
    "account":          "LucidFlex 25K",
    "profit_target":    1250,
    "max_loss_limit":   1000,   # $ TOTAL room — EOD trailing. VERIFY on help center.
    "per_trade_risk_cap": 50,   # $  (~10 losses = ruin; do NOT size up)
    "daily_loss_stop":  100,    # $  (2 trades)
    "max_contracts":    1,      # until +$625 (half target)
    "consistency_pct":  50,     # best day <= 50% of total profit (eval)
}
```
Note: MLL is only $1,000 of total room on the $25k Flex. This is a SMALL buffer.
The strategy's max drawdown (from §7) must sit comfortably under $1,000, or the
account is not viable at this size regardless of long-run expectancy.

**Wiggle test rule:** if the strategy only works at tol=3 but dies at tol=2 and
tol=4, it is curve-fit. A real edge survives small parameter nudges.

---

## 3. Project structure

```
mes_backtest/
├── data/
│   ├── raw/              # Databento CSV.zst files land here (4 files)
│   └── processed/        # concatenated 1-min + resampled 5-min RTH, parquet
├── src/
│   ├── load.py          # ingest raw, handle contract roll, resample to 5m RTH
│   ├── profile.py       # volume profile: POC/VAH/VAL from prior session
│   ├── strategy.py      # the rule set above -> list of trades
│   ├── backtest.py      # run strategy over date range, apply costs
│   ├── stats.py         # expectancy, profit factor, DD, R-dist, streaks
│   ├── walkforward.py   # rolling train/test windows
│   └── guards.py        # trial counter, OOS holdout, regime split
├── notebooks/
│   └── explore.ipynb
├── results/
├── requirements.txt
└── README.md
```

---

## 4. Build order (do these in sequence, test each before moving on)

1. **`load.py`** — read the Databento CSV.zst files from `data/raw/`:
   - Read all 4 with `pd.read_csv` (zstandard installed → reads `.zst`
     directly), concatenate, sort by `ts_event`.
   - Parse `ts_event` as UTC, convert to `America/New_York`.
   - The continuous symbol is already front-month-stitched by Databento, but the
     `symbol` column shows the roll. OPTIONAL sanity check: confirm volume
     doesn't gap oddly at roll dates; no manual stitching needed for the
     continuous symbol.
   - Keep the 1-min bars (the volume profile is built from these). ALSO produce
     a 5-min RTH resample for the strategy: resample 1-min → 5-min, label bars
     by their OPEN time, keep only 09:30–16:00 ET, drop weekends/holidays.
   - Save both to parquet: `data/processed/mes_1m.parquet` (profile input) and
     `data/processed/mes_5m_rth.parquet` (strategy input).
   **Validate:** print 10 random days; a full RTH day = 78 five-min bars and
   390 one-min bars. Confirm first bar of each day opens at 09:30 ET.

2. **`profile.py`** — build prior-session volume profile from the **1-min**
   bars (`mes_1m.parquet`), 09:30–16:00 ET of the prior trading day. Assign each
   1-min bar's `volume` to the bin of its typical price (H+L+C)/3, bin width =
   `profile_bin` (1.0 pt). Return POC/VAH/VAL.
   **Validate:** plot one day's profile as a histogram, confirm POC is the
   mode and VA brackets ~70% of volume.

3. **`strategy.py`** — pure function: `(day_bars, prior_levels) -> trades[]`.
   Deterministic. Same input → same output, always.
   **Validate:** hand-check 3 days against the logic manually.

4. **`backtest.py`** — loop days, apply commission + slippage, output trade log
   (entry, exit, side, R-multiple, $P&L, reason-for-exit).

5. **`stats.py`** — from the trade log compute:
   - total trades, win %, avg win, avg loss (in points, R, and $)
   - **expectancy per trade** = (win% × avgWin) − (loss% × avgLoss)
   - profit factor = gross win / gross loss
   - max drawdown (in $ and in R)
   - max consecutive losses
   - distribution of R-multiples (histogram)
   - **worst single day** (critical for Lucid drawdown fit — see §7)

6. **`guards.py`** + **`walkforward.py`** — see §5. Build these LAST but do NOT
   skip them. A result without them is meaningless.

---

## 5. The anti-fooling-yourself layer (non-negotiable)

**a) Out-of-sample holdout.**
Reserve the most recent 12 months. Never touch it during development.
Run it exactly ONCE, at the very end, after everything else is final.

**b) Walk-forward.**
On the remaining data, roll yearly windows:
```
train 2020-2021 -> test 2022
train 2021-2022 -> test 2023
train 2022-2023 -> test 2024
train 2023-2024 -> test 2025
```
Report expectancy for EACH test window separately. We want it positive across
MULTIPLE windows, not one good blob.

**c) Regime split.**
Tag each test period by volatility (e.g. VIX terciles or realized vol of MES).
Report expectancy in high / medium / low vol separately. An edge that only
works in one regime is fragile — note it, don't hide it.

**d) Trial counter.**
Log every parameter combination tested, with a running count. If we test 50
variants and the best shows p<0.05, that is EXPECTED by chance (~2–3 false
positives at that count). Print the trial count next to every "winning" result
as a reminder.

**e) Confluence on/off A/B.**
Run the whole strategy WITH and WITHOUT the confluence filter. If confluence
doesn't improve expectancy, the "edge" is just the opening-range mechanics and
the volume/fib story is decoration. This is the single most important test in
the project.

---

## 6. Costs — do not skip, many "edges" are fee-sized

- Commission: MES round-turn ≈ $1.24 (varies by broker; use the param).
- Slippage: 1 tick (0.25 pt = $1.25) on entry and 1 tick on stop fills.
  Targets are limit orders → no slippage on target exits.
- Apply BOTH. Report results gross and net. If the edge only exists gross,
  it doesn't exist.

---

## 7. Lucid $25k fit check (the practical output)

After stats, answer these explicitly in the README:
- What is the **worst single day** in $ (on 1 MES)? Must stay well under the
  $25k plan's **$1,000** room.
- What is the **max drawdown** across the whole test, in $? Compare to the
  **$1,000 EOD trailing MLL** on the $25k Flex. If max DD approaches or exceeds
  $1,000, the account is NOT viable at this size — full stop.
- At this expectancy, how many trades to reach the **$1,250 profit target**?
- Check the **50% consistency rule**: does any single day exceed 50% of the
  profit needed to pass? If the strategy makes its money in a few big days, it
  fails consistency even while hitting the target.
- Simulate the Lucid EOD trailing drawdown over the equity curve: does the
  strategy EVER breach **$1,000** trailing from a prior EOD high (locking at
  start + $100 after the initial trail)? If yes, it fails the eval even if
  profitable long-term. THIS is the make-or-break number.

---

## 8. Definition of done

- [ ] All 6 modules built and individually validated
- [ ] Walk-forward run, per-window expectancy reported
- [ ] Confluence A/B run, result stated plainly
- [ ] Regime split reported
- [ ] Costs applied, net expectancy reported
- [ ] Lucid $25k drawdown simulation run over equity curve
- [ ] OOS holdout run ONCE, final number reported
- [ ] README states, in one sentence: does this edge survive, yes or no?

**If net expectancy is negative or only positive in one window: the honest
answer is "no edge." That is a successful project, not a failed one — it saved
a $25k evaluation fee.**
