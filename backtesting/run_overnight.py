"""Overnight-range strategy: null test first, always.

Two opposite hypotheses on the same non-arbitrary level:
  on_sweep_reversal  a poke beyond ONH/ONL that gets reclaimed is liquidity
                     taking, and price reverts
  on_break           a close beyond ONH/ONL is real and continues

On a random walk the overnight high is just the running max of noise, so
neither should show an edge. If one does, the implementation leaks.
"""
import numpy as np
import pandas as pd

from fvg_lab import data
from fvg_lab.engine import Costs, run
from fvg_lab.metrics import summarize, randomization_test
from fvg_lab.strategy import Params
from fvg_lab.trials import TrialLog, sweep

# MNQ: $2/point, 0.25 tick, ~$0.50 commission per side
MNQ = Costs(tick_size=0.25, point_value=2.0, spread_ticks=1.0,
            slippage_ticks=0.5, commission_per_side=0.50)

print("building 24h synthetic null (500 sessions, 90 overnight + 78 RTH bars)")
raw = data.synth(n_days=500, include_overnight=True, overnight_bars=90,
                 tick=0.25, start_price=20_000.0, vol=0.0012, seed=7)
df = data.add_overnight_levels(raw)
print(f"  {len(df):,} RTH bars across {df['session'].nunique()} sessions")
print(f"  median ON range: {df.groupby('session')['on_range'].first().median():.1f} pts\n")

base = Params(
    entry_mode="on_sweep_reversal",
    stop_mode="sweep_extreme",
    stop_buffer_atr=0.25,
    min_stop_atr=0.30,
    max_stop_atr=6.0,
    target_r=2.0,
    entry_start_bar=0,
    entry_end_bar=24,          # 09:30 to 11:30 only
    max_trades_per_session=1,
    require_first_candle_bias=False,
    ema_period=0,
    atr_period=14,
)

grid = {
    "entry_mode": ["on_sweep_reversal", "on_break"],
    "sweep_buffer_atr": [0.05, 0.10, 0.25],
    "sweep_reclaim_bars": [2, 4, 8],
    "on_range_min_atr": [0.5, 1.5],
    "target_r": [1.5, 2.0, 3.0],
}

log = TrialLog("trials_overnight.jsonl")
ranked = sweep(df, base, grid, MNQ, log=log, tag="on_null", min_trades=40)

print(f"NULL SWEEP: {len(ranked)} variants, {log.n_trials} cumulative trials\n")
cols = ["entry_mode", "sweep_buffer_atr", "sweep_reclaim_bars", "target_r",
        "n_trades", "expectancy_R", "win_rate", "pct_same_bar_exit"]
cols = [c for c in cols if c in ranked.columns]
print(ranked[cols].head(8).to_string(index=False))

print("\nBy entry_mode (all variants pooled):")
print(ranked.groupby("entry_mode")[["expectancy_R", "n_trades"]]
      .agg({"expectancy_R": ["mean", "max", "min"], "n_trades": "mean"})
      .round(3).to_string())

best = ranked.iloc[0]
print(f"\nBEST CELL: {best['entry_mode']} expectancy {best['expectancy_R']:+.3f} R "
      f"on {int(best['n_trades'])} trades")

from fvg_lab.trials import params_from_row
res = run(df, params_from_row(best), MNQ)
rt = randomization_test(res, n_iter=1000, seed=0)
print(f"randomization test p = {rt if isinstance(rt, float) else rt.get('p_value', rt)}")
print("\nA best cell near zero with a high p-value is the correct result here.")
