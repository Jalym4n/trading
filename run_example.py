"""Worked example on synthetic (structureless) data.

Purpose: show the whole loop, and demonstrate what a NULL result looks like.
The synthetic series is a random walk -- there is nothing in it to find.
Anything that appears profitable here is measuring your search process, not an
edge. Run every new rule set through this before you run it on real data.

Swap in real data with:
    df = data.add_session(data.load_csv("es_5min.csv"))
"""

import pandas as pd

from fvg_lab import data
from fvg_lab.engine import Costs, run
from fvg_lab.metrics import summarize, by_year, randomization_test, deflated_sharpe
from fvg_lab.strategy import Params
from fvg_lab.trials import TrialLog, sweep, purged_walk_forward, params_from_row

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 60)


def show(title, d):
    print(f"\n=== {title} ===")
    for k, v in d.items():
        print(f"  {k:<24} {v:>12.4f}" if isinstance(v, float) else f"  {k:<24} {v:>12}")


# ---------------------------------------------------------------- data ----
df = data.add_session(data.synth(n_days=500, seed=7))
dev, holdout = data.split_holdout(df, holdout_frac=0.30)
print(f"dev: {dev.session.nunique()} sessions   |   HOLDOUT (untouched): {holdout.session.nunique()} sessions")

costs = Costs(tick_size=0.25, point_value=5.0, spread_ticks=1.0,
              slippage_ticks=0.5, commission_per_side=0.52)  # MES (micro E-mini)

base = Params(entry_mode="fvg_retest", first_candle_n=1, require_first_candle_bias=True,
              min_gap_atr=0.25, fvg_max_age=20, stop_mode="fvg_far_edge",
              min_stop_atr=0.5, target_r=2.0, max_trades_per_session=2)

# ------------------------------------------------------------ baseline ----
res = run(dev, base, costs)
show("baseline", summarize(res))
show("same params, DOUBLED costs", summarize(run(dev, base, costs.double())))

print("\n=== by year ===")
print(by_year(res))

show("randomization test (sign-shuffled null)", randomization_test(res))

# ---------------------------------------------------------------- sweep ---
log = TrialLog("trials.jsonl")
grid = {
    "entry_mode": ["fvg_retest", "first_candle_break", "fvg_plus_break"],
    "min_gap_atr": [0.0, 0.25],
    "target_r": [1.5, 2.0, 3.0],
    "fvg_max_age": [10, 20],
    "require_first_candle_bias": [True, False],
    "max_trades_per_session": [1, 2],
}
ranked = sweep(dev, base, grid, costs, log=log, tag="explore", min_trades=30)
print(f"\n=== sweep: {len(ranked)} eligible variants | {log.n_trials} cumulative trials logged ===")
cols = ["entry_mode", "min_gap_atr", "target_r", "fvg_max_age",
        "require_first_candle_bias", "max_trades_per_session",
        "n_trades", "win_rate", "expectancy_R", "sharpe_per_trade", "pct_same_bar_exit"]
print(ranked[cols].head(8).to_string(index=False))
print("\n...and the worst:")
print(ranked[cols].tail(3).to_string(index=False))

# The best variant, judged against EVERY trial that produced it
best = params_from_row(ranked.iloc[0])
best_res = run(dev, best, costs)
dsr = deflated_sharpe(best_res["trades"]["r_multiple"].to_numpy(),
                      n_trials=log.n_trials, sr_variance=log.sharpe_variance())
print(f"\nbest variant raw expectancy : {ranked.iloc[0]['expectancy_R']:+.4f} R")
print(f"deflated Sharpe vs {log.n_trials} trials: {dsr:.4f}   (want > 0.95)")

# -------------------------------------------------------- walk-forward ----
wf = purged_walk_forward(dev, base, grid, costs, train_days=100, test_days=40,
                         embargo_days=3, min_trades=15, log=log)
print("\n=== purged walk-forward (params chosen in-sample, applied forward) ===")
if not wf["folds"].empty:
    print(wf["folds"][["test_start", "test_end", "is_expectancy_R",
                       "oos_expectancy_R", "oos_trades"]].to_string(index=False))
    print(f"\npooled OOS expectancy : {wf['oos_expectancy_R']:+.4f} R")
    print(f"in-sample -> OOS decay: {wf['is_oos_decay']:+.4f} R   (large decay = selection was fitting noise)")
else:
    print("no folds produced enough trades")

print(f"\ntotal trials logged this session: {log.n_trials}")
print("HOLDOUT REMAINS UNTOUCHED. Open it once, after you commit to one rule set.")
