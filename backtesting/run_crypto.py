"""Crypto stage 1: run the FVG / first-candle rules on real BTC or ETH bars.

Two questions, asked in this order:
  A. Is there ANY signal? Sweep, walk-forward and deflated Sharpe at ZERO
     cost. If nothing survives here, the rules carry no information on this
     market and the cost question is moot.
  B. Does it survive friction? The variant chosen in A is re-run at your real
     taker fee and at double that.

Expect B to fail on 5m bars: round-trip retail fees (~0.85%) are a multiple of
a typical 5m stop (~0.3%). See CLAUDE.md, "Retail crypto fees kill 5m".

Get data first (on a machine with internet access):
    python fetch_crypto.py --source coinbase --symbol BTC-USD --interval 5m --start 2019-01-01 --out btc_5m.csv
Then:
    python run_crypto.py --csv btc_5m.csv --fee 0.004
Without --csv it runs on a 24/7 random walk, which should show nothing.

Sessions are UTC calendar days. Pass --ny-session to use only 09:30-16:00
New York time instead, which tests whether the equity open matters to crypto.
"""

import argparse

import numpy as np
import pandas as pd

from fvg_lab import data
from fvg_lab.engine import Costs, run
from fvg_lab.metrics import summarize, randomization_test, deflated_sharpe
from fvg_lab.strategy import Params
from fvg_lab.trials import TrialLog, sweep, purged_walk_forward, params_from_row

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 60)

ap = argparse.ArgumentParser()
ap.add_argument("--csv", default=None, help="output of fetch_crypto.py; omit for synthetic null")
ap.add_argument("--fee", type=float, default=0.004, help="taker fee per side, 0.004 = 0.40%%")
ap.add_argument("--since", default=None, help="drop bars before this date, e.g. 2021-01-01")
ap.add_argument("--ny-session", action="store_true", help="09:30-16:00 New York only")
ap.add_argument("--equity", type=float, default=1_000.0)
ap.add_argument("--risk", type=float, default=0.01, help="fraction of equity risked per trade")
a = ap.parse_args()


def show(title, d):
    print(f"\n=== {title} ===")
    for k, v in d.items():
        print(f"  {k:<24} {v:>12.4f}" if isinstance(v, float) else f"  {k:<24} {v:>12}")


def synth_24h(days=400, seed=7, start_price=60_000.0, vol=0.0015):
    """24/7 random walk, BTC-shaped. Nothing to find here, by construction."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=288 * days, freq="5min", tz="UTC")
    close = start_price * np.exp(np.cumsum(rng.normal(0, vol, len(idx))))
    prev = np.concatenate([[close[0]], close[:-1]])
    wig = np.abs(rng.normal(0, vol, len(idx))) * close
    return pd.DataFrame({"open": prev, "high": np.maximum(prev, close) + wig,
                         "low": np.minimum(prev, close) - wig, "close": close,
                         "volume": 1.0}, index=idx).round(2)


# ---------------------------------------------------------------- data ----
if a.csv:
    raw = data.load_csv(a.csv, tz="America/New_York" if a.ny_session else "UTC")
else:
    raw = synth_24h()
    if a.ny_session:
        raw = raw.tz_convert("America/New_York")
if a.since:
    raw = raw[raw.index >= pd.Timestamp(a.since, tz=raw.index.tz)]
df = data.add_session(raw, "09:30", "16:00") if a.ny_session else data.add_session(raw, "00:00", "24:00")

dev, holdout = data.split_holdout(df, holdout_frac=0.30)
print(f"{'REAL: ' + a.csv if a.csv else 'SYNTHETIC (null)'} | sessions: "
      f"{'NY 09:30-16:00' if a.ny_session else 'UTC day'}")
print(f"dev: {dev.session.nunique()} sessions ({dev.index[0]:%Y-%m-%d} -> {dev.index[-1]:%Y-%m-%d})"
      f"   |   HOLDOUT (untouched): {holdout.session.nunique()} sessions")

zero = Costs.crypto_spot(fee_pct=0.0, spread_bps=0.0, slippage_bps=0.0)
real = Costs.crypto_spot(fee_pct=a.fee)
rk = dict(starting_equity=a.equity, risk_per_trade=a.risk, allow_fractional=True)

base = Params(entry_mode="fvg_retest", first_candle_n=1, require_first_candle_bias=True,
              min_gap_atr=0.25, fvg_max_age=20, stop_mode="fvg_far_edge",
              stop_buffer_atr=0.25, min_stop_atr=0.25, target_r=2.0,
              max_trades_per_session=2)

# ------------------------------------------------- A. signal at zero cost --
res0 = run(dev, base, zero, **rk)
show("A. baseline, ZERO cost", summarize(res0))
show("A. randomization test", randomization_test(res0))

log = TrialLog("trials_crypto.jsonl")  # never delete: the count is the bar
grid = {
    "entry_mode": ["fvg_retest", "first_candle_break", "fvg_plus_break"],
    "min_gap_atr": [0.0, 0.25],
    "target_r": [1.5, 2.0, 3.0],
    "fvg_max_age": [10, 20],
    "require_first_candle_bias": [True, False],
    "max_trades_per_session": [1, 2],
}
ranked = sweep(dev, base, grid, zero, log=log, tag="crypto_explore_zero", min_trades=30, run_kwargs=rk)
print(f"\n=== A. sweep at zero cost: {len(ranked)} eligible | {log.n_trials} cumulative trials ===")
cols = ["entry_mode", "min_gap_atr", "target_r", "require_first_candle_bias",
        "max_trades_per_session", "n_trades", "win_rate", "expectancy_R",
        "sharpe_per_trade", "pct_same_bar_exit"]
print(ranked[cols].head(6).to_string(index=False))

best = params_from_row(ranked.iloc[0])
best0 = run(dev, best, zero, **rk)
dsr = deflated_sharpe(best0["trades"]["r_multiple"].to_numpy(),
                      n_trials=log.n_trials, sr_variance=log.sharpe_variance())
print(f"\nbest variant, zero cost : {ranked.iloc[0]['expectancy_R']:+.4f} R")
print(f"deflated Sharpe vs {log.n_trials} trials: {dsr:.4f}   (want > 0.95)")

wf = purged_walk_forward(dev, base, grid, zero, train_days=120, test_days=40,
                         embargo_days=3, min_trades=15, log=log, run_kwargs=rk)
print("\n=== A. purged walk-forward, zero cost ===")
if not wf["folds"].empty:
    print(wf["folds"][["test_start", "test_end", "is_expectancy_R",
                       "oos_expectancy_R", "oos_trades"]].to_string(index=False))
    print(f"\npooled OOS expectancy : {wf['oos_expectancy_R']:+.4f} R")
    print(f"in-sample -> OOS decay: {wf['is_oos_decay']:+.4f} R")

# --------------------------------------------- B. the same variant, real --
print(f"\n=== B. best zero-cost variant under friction (taker {a.fee:.2%}/side) ===")
for name, c in [("zero", zero), ("real", real), ("real x2", real.double())]:
    r = run(dev, best, c, **rk)
    t = r["trades"]
    if t.empty:
        print(f"  {name:8s} no trades")
        continue
    stop_pct = (t["risk_points"] / t["entry"]).median() * 100
    end_eq = r["equity"].iloc[-1]
    print(f"  {name:8s} n={len(t):5d}  expectancy {t['r_multiple'].mean():+.3f} R"
          f"   median stop {stop_pct:.2f}%   equity ${a.equity:,.0f} -> ${end_eq:,.2f}"
          + (f"   ({r['skipped_undersized']} signals skipped: account too small to size)"
             if r["skipped_undersized"] else ""))

rt = 2 * (a.fee + (real.spread_bps / 2 + real.slippage_bps) / 1e4) * 100
print(f"""
--------------------------------------------------------------------
Round-trip friction at this fee: ~{rt:.2f}% of price. Fee drag in R is about
round-trip % / stop %. For drag under 0.1 R you need stops near {rt / 0.1:.0f}%.
A pass needs ALL of: deflated Sharpe > 0.95, positive pooled OOS expectancy,
and B still positive at "real x2". Then, and only then, touch the holdout.
HOLDOUT REMAINS UNTOUCHED.
--------------------------------------------------------------------""")
