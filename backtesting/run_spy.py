"""Stage 1: does the FVG + first-candle signal predict SPY at all?

Run this on SPY SHARES, not options. The signal is about direction in the
underlying; options are a way to express it with leverage. Leverage multiplies
an edge, it does not create one -- so test for the edge where costs are
smallest and the accounting is clean, then decide whether options are worth
the extra friction.

The cost model here (penny spread, one tick slippage) is the friendliest you
will ever get. A signal that cannot clear it will not clear an option spread.

To use real data, replace the synth() line:
    df = data.add_session(data.load_csv("spy_5min.csv"))
"""

import pandas as pd

from fvg_lab import data
from fvg_lab.engine import Costs, run
from fvg_lab.metrics import summarize, randomization_test, deflated_sharpe
from fvg_lab.strategy import Params
from fvg_lab.trials import TrialLog, sweep, purged_walk_forward, params_from_row

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 60)

USE_REAL_DATA = False  # flip once you have spy_5min.csv


def show(title, d):
    print(f"\n=== {title} ===")
    for k, v in d.items():
        print(f"  {k:<24} {v:>12.4f}" if isinstance(v, float) else f"  {k:<24} {v:>12}")


# ---------------------------------------------------------------- data ----
if USE_REAL_DATA:
    df = data.add_session(data.load_csv("spy_5min.csv"))
else:
    # SPY-shaped random walk. Nothing to find in here -- that is the point.
    df = data.add_session(data.synth(n_days=500, seed=11, tick=0.01, start_price=550.0))

dev, holdout = data.split_holdout(df, holdout_frac=0.30)
print(f"{'REAL' if USE_REAL_DATA else 'SYNTHETIC (null)'} data")
print(f"dev: {dev.session.nunique()} sessions   |   HOLDOUT (untouched): {holdout.session.nunique()} sessions")

costs = Costs.spy_shares(commission_per_share=0.0)

# Stops are sized in ATR, so nothing here is hardcoded to a price level.
base = Params(entry_mode="fvg_retest", first_candle_n=1, require_first_candle_bias=True,
              min_gap_atr=0.25, fvg_max_age=20, stop_mode="fvg_far_edge",
              stop_buffer_atr=0.25, min_stop_atr=0.25, target_r=2.0,
              max_trades_per_session=2)

# 1 share of SPY is ~$550, so a $25k account at 0.5% risk can always size in.
res = run(dev, base, costs, starting_equity=25_000, risk_per_trade=0.005)
show("baseline (SPY shares)", summarize(res))
show("DOUBLED costs", summarize(run(dev, base, costs.double(), 25_000, 0.005)))
show("randomization test", randomization_test(res))

# ---------------------------------------------------------------- sweep ---
log = TrialLog("trials_spy.jsonl")
grid = {
    "entry_mode": ["fvg_retest", "first_candle_break", "fvg_plus_break"],
    "min_gap_atr": [0.0, 0.25],
    "target_r": [1.5, 2.0, 3.0],
    "fvg_max_age": [10, 20],
    "require_first_candle_bias": [True, False],
    "max_trades_per_session": [1, 2],
}
ranked = sweep(dev, base, grid, costs, log=log, tag="spy_explore", min_trades=30)
print(f"\n=== sweep: {len(ranked)} eligible variants | {log.n_trials} cumulative trials ===")
cols = ["entry_mode", "min_gap_atr", "target_r", "require_first_candle_bias",
        "max_trades_per_session", "n_trades", "win_rate", "expectancy_R",
        "sharpe_per_trade", "pct_same_bar_exit", "skipped_undersized"]
print(ranked[cols].head(6).to_string(index=False))

best = params_from_row(ranked.iloc[0])
best_res = run(dev, best, costs, 25_000, 0.005)
dsr = deflated_sharpe(best_res["trades"]["r_multiple"].to_numpy(),
                      n_trials=log.n_trials, sr_variance=log.sharpe_variance())
print(f"\nbest variant expectancy : {ranked.iloc[0]['expectancy_R']:+.4f} R")
print(f"deflated Sharpe vs {log.n_trials} trials: {dsr:.4f}   (want > 0.95)")

# -------------------------------------------------------- walk-forward ----
wf = purged_walk_forward(dev, base, grid, costs, train_days=100, test_days=40,
                         embargo_days=3, min_trades=15, log=log)
print("\n=== purged walk-forward ===")
if not wf["folds"].empty:
    print(wf["folds"][["test_start", "test_end", "is_expectancy_R",
                       "oos_expectancy_R", "oos_trades"]].to_string(index=False))
    print(f"\npooled OOS expectancy : {wf['oos_expectancy_R']:+.4f} R")
    print(f"in-sample -> OOS decay: {wf['is_oos_decay']:+.4f} R")

print("""
--------------------------------------------------------------------
GATE TO STAGE 2 (options). Go on only if ALL of these hold on REAL data:
  * pooled OOS expectancy positive after doubled costs
  * deflated Sharpe > 0.95 against the full trial count
  * pct_same_bar_exit low (the result is not a bar-size artifact)
  * result is not concentrated in one regime or one year

If the signal cannot clear a penny spread on shares, it will not clear
an option spread. Options add theta, vega and 10-100x the friction.
--------------------------------------------------------------------""")
