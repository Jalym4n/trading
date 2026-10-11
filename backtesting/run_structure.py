"""Market structure stage 1: CHoCH and quasimodo on ES (MES costs), null first.

Grids are written here BEFORE any real data was run, per standing principle 1.
Each mode gets its own grid so parameters that only touch one mode do not
create duplicate trials for the other.

    python run_structure.py                    # synthetic null: should find nothing
    python run_structure.py --csv es_1m.csv    # real ES, dev period only

Bars: 1m resampled to 5m, RTH 09:30-16:00 ET sessions, flat at the close.
Costs: Costs.mes() (ES prices, micro contract costs) and doubled.
Sizing: fractional, so EVERY signal is measured in R. With whole contracts,
wide-stop signals are dropped as undersized and only tight-stop trades get
measured, a hidden filter (on the null, 562 CHoCH signals vs 208 trades).
Whether a trade is affordable is reported separately: stop in points and in $
for one MES contract, and the share that fit a 1% risk budget at $1k and $4k.
Holdout (last 30% of sessions) is never touched here.
"""

import argparse

import pandas as pd

from fvg_lab import data
from fvg_lab.engine import Costs, run
from fvg_lab.metrics import summarize, randomization_test, deflated_sharpe
from fvg_lab.strategy import Params
from fvg_lab.trials import TrialLog, sweep, purged_walk_forward, params_from_row

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)

ap = argparse.ArgumentParser()
ap.add_argument("--csv", default=None, help="fetch_futures.py output; omit for synthetic null")
ap.add_argument("--log", default="trials_structure.jsonl")
a = ap.parse_args()

# ---------------------------------------------------------------- data ----
if a.csv:
    df = data.add_session(data.resample_ohlcv(data.load_csv(a.csv), "5min"))
    label = f"REAL {a.csv}"
else:
    df = data.add_session(data.synth(n_days=1500, seed=21, tick=0.25, start_price=5000.0))
    label = "SYNTHETIC (null)"
dev, holdout = data.split_holdout(df, holdout_frac=0.30)
print(f"{label} | dev {dev.session.nunique()} sessions "
      f"({dev.index[0]:%Y-%m-%d} -> {dev.index[-1]:%Y-%m-%d}) | "
      f"HOLDOUT untouched: {holdout.session.nunique()} sessions")

costs = Costs.mes()
rk = dict(starting_equity=25_000.0, risk_per_trade=0.005, allow_fractional=True)
MES_PT = 5.0   # $ per point, one MES contract


def feasibility(trades):
    if trades.empty:
        return "  feasibility: no trades"
    usd = trades["risk_points"] * MES_PT
    return (f"  feasibility: median stop {trades['risk_points'].median():.2f} pts = "
            f"${usd.median():.0f}/MES contract | fits 1% of $1k: {(usd <= 10).mean():.0%} | "
            f"fits 1% of $4k: {(usd <= 40).mean():.0%}")
log = TrialLog(a.log)

base = Params(stop_mode="structure", stop_buffer_atr=0.25, min_stop_atr=0.25,
              max_stop_atr=10.0, target_r=2.0, max_trades_per_session=2,
              entry_start_bar=1, entry_end_bar=60, require_first_candle_bias=False,
              swing_left=3, swing_right=3, qm_tolerance_atr=0.10, qm_max_age=24)
grids = {
    "choch": {"swing_left": [3, 5], "swing_right": [3, 5], "target_r": [1.5, 2.0, 3.0]},
    "qm": {"swing_left": [3, 5], "swing_right": [3, 5], "target_r": [1.5, 2.0, 3.0],
           "qm_tolerance_atr": [0.10, 0.25], "qm_max_age": [12, 24]},
}
cols = ["entry_mode", "swing_left", "swing_right", "target_r", "qm_tolerance_atr",
        "qm_max_age", "n_trades", "win_rate", "expectancy_R", "pct_same_bar_exit",
        "skipped_undersized"]


def show(title, d):
    keep = ["n_trades", "win_rate", "expectancy_R", "profit_factor", "max_drawdown",
            "pct_same_bar_exit", "skipped_undersized", "p_value"]
    print(f"  {title:<28}" + "  ".join(
        f"{k}={d[k]:.3f}" if isinstance(d.get(k), float) else f"{k}={d.get(k)}"
        for k in keep if k in d))


for mode, grid in grids.items():
    print(f"\n================ {mode} ================")
    p0 = Params(**{**base.to_dict(), "entry_mode": mode})
    r0 = run(dev, p0, costs, **rk)
    show("baseline, MES costs", summarize(r0))
    show("baseline, DOUBLED costs", summarize(run(dev, p0, costs.double(), **rk)))
    print(feasibility(r0["trades"]))
    show("randomization test", randomization_test(r0))

    ranked = sweep(dev, p0, grid, costs, log=log, tag=f"structure_{mode}",
                   min_trades=30, run_kwargs=rk)
    print(f"\n  sweep: {len(ranked)} eligible | {log.n_trials} cumulative trials in {a.log}")
    print(ranked[[c for c in cols if c in ranked]].head(5).to_string(index=False))

    best = params_from_row(ranked.iloc[0])
    rb = run(dev, best, costs, **rk)
    dsr = deflated_sharpe(rb["trades"]["r_multiple"].to_numpy(),
                          n_trials=log.n_trials, sr_variance=log.sharpe_variance())
    show("best, MES costs", summarize(rb))
    show("best, DOUBLED costs", summarize(run(dev, best, costs.double(), **rk)))
    print(f"  deflated Sharpe vs {log.n_trials} trials: {dsr:.4f}   (want > 0.95)")
    print(feasibility(rb["trades"]))

    # The gate is OOS after DOUBLED costs, so the walk-forward selects and
    # tests at doubled costs. Conservative: selection never sees cheap fills.
    wf = purged_walk_forward(dev, p0, grid, costs.double(), train_days=250, test_days=60,
                             embargo_days=3, min_trades=20, log=log, run_kwargs=rk)
    if not wf["folds"].empty:
        f = wf["folds"]
        print(f"  walk-forward @2x costs: {len(f)} folds, {int((f.oos_expectancy_R > 0).sum())} "
              f"positive OOS | pooled OOS {wf['oos_expectancy_R']:+.4f} R | "
              f"IS->OOS decay {wf['is_oos_decay']:+.4f} R")
    else:
        print("  walk-forward: no eligible folds")

print(f"""
--------------------------------------------------------------------
Gate (CLAUDE.md): pooled OOS expectancy positive after DOUBLED costs, deflated
Sharpe > 0.95 vs the cumulative trial count, low pct_same_bar_exit, stable
across bar offsets and timeframes, not one regime. HOLDOUT REMAINS UNTOUCHED.
Trial log: {a.log} ({log.n_trials} trials). Commit it.
--------------------------------------------------------------------""")
