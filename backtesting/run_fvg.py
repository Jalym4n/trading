"""FVG retest on ES: does the BTC zero-cost signal replicate where friction is low?

PRE-REGISTERED (committed before this script was first run on ES).

The only rule that survived a null and a walk-forward anywhere in this project
is fvg_retest with first-candle bias on BTC 5m (+0.09 to +0.10 R pooled OOS at
zero cost, two venues). It was untradable there: retail crypto round trip is
~50x the edge. ES/MES friction is ~0.007% round trip, below the ~0.017% edge,
so ES is where the claim can actually be tested.

Confirmatory variants, fixed from the BTC trial log, NOT re-selected on ES:
  P1  BTC baseline   min_gap_atr 0.25  target_r 2.0
  P2  Binance best   min_gap_atr 0.25  target_r 3.0
  P3  Coinbase best  min_gap_atr 0.00  target_r 3.0
  all: fvg_retest, first_candle_n 1, require_first_candle_bias True,
       fvg_max_age 20, max_trades_per_session 2, stop fvg_far_edge,
       stop_buffer_atr 0.25, min_stop_atr 0.25, entry bars 1..60

PASS (replication) requires ALL of:
  1. each of P1-P3 positive expectancy at zero cost AND at MES costs
  2. purged walk-forward over the BTC 144-variant grid, selecting and testing
     at DOUBLED costs, has positive pooled OOS expectancy
Anything else is a FAIL, reported as such. The exploratory sweep is reported
but cannot turn a fail into a pass.

Data: es_1m.csv (fetch_futures.py, back-adjusted), 5m RTH sessions. Sessions
with fewer than 30 bars are dropped: these are the ~2h Globex holiday sessions
(Jul 4, Labor Day, ...) when the cash market is shut. Half-days (~42 bars) stay.
Dev = first 70% of sessions; holdout untouched.

    python run_fvg.py --csv es_1m.csv
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
ap.add_argument("--csv", required=True)
ap.add_argument("--log", default="trials_fvg_es.jsonl")
a = ap.parse_args()

df = data.add_session(data.resample_ohlcv(data.load_csv(a.csv), "5min"))
n = df.groupby("session")["open"].transform("size")
dropped = df.loc[n < 30, "session"].nunique()
df = df[n >= 30].copy()
dev, holdout = data.split_holdout(df, holdout_frac=0.30)
print(f"ES 5m RTH | dropped {dropped} holiday sessions (<30 bars) | dev {dev.session.nunique()} "
      f"sessions ({dev.index[0]:%Y-%m-%d} -> {dev.index[-1]:%Y-%m-%d}) | "
      f"HOLDOUT untouched: {holdout.session.nunique()}")

mes = Costs.mes()
zero = Costs(tick_size=0.25, point_value=5.0, spread_ticks=0.0, slippage_ticks=0.0,
             commission_per_side=0.0)
rk = dict(starting_equity=25_000.0, risk_per_trade=0.005, allow_fractional=True)
log = TrialLog(a.log)

core = dict(entry_mode="fvg_retest", first_candle_n=1, require_first_candle_bias=True,
            fvg_max_age=20, max_trades_per_session=2, stop_mode="fvg_far_edge",
            stop_buffer_atr=0.25, min_stop_atr=0.25, entry_start_bar=1, entry_end_bar=60)
P = {
    "P1 BTC baseline": Params(**core, min_gap_atr=0.25, target_r=2.0),
    "P2 Binance best": Params(**core, min_gap_atr=0.25, target_r=3.0),
    "P3 Coinbase best": Params(**core, min_gap_atr=0.00, target_r=3.0),
}


def line(title, st):
    keys = ["n_trades", "win_rate", "expectancy_R", "profit_factor", "max_drawdown",
            "pct_same_bar_exit"]
    return f"  {title:<34}" + "  ".join(f"{k}={st[k]:.3f}" if isinstance(st[k], float)
                                         else f"{k}={st[k]}" for k in keys)


# ------------------------------------------------ 1. confirmatory --------
print("\n=== 1. CONFIRMATORY (pre-registered, 3 trials) ===")
ok1 = True
for name, p in P.items():
    out = {}
    for cname, c in [("zero", zero), ("MES", mes), ("MES x2", mes.double())]:
        r = run(dev, p, c, **rk)
        st = summarize(r)
        out[cname] = (r, st)
        print(line(f"{name} | {cname}", st))
        if cname in ("zero", "MES"):
            log.append(p.to_dict(), st, tag=f"fvg_es_confirm_{cname}")
    t = out["MES"][0]["trades"]
    usd = t["risk_points"] * 5.0
    rt = randomization_test(out["MES"][0])
    print(f"    randomization p (MES) = {rt['p_value']:.3f} | median stop {t['risk_points'].median():.2f} pts "
          f"= ${usd.median():.0f}/MES | fits 1% of $1k: {(usd <= 10).mean():.0%}, $4k: {(usd <= 40).mean():.0%}")
    yrs = t.groupby(pd.to_datetime(t["entry_time"]).dt.year)["r_multiple"].mean()
    print(f"    by year (MES): " + " ".join(f"{y}:{v:+.2f}" for y, v in yrs.items()))
    ok1 &= out["zero"][1]["expectancy_R"] > 0 and out["MES"][1]["expectancy_R"] > 0

# ------------------------------------------- 2. walk-forward @2x ---------
grid = {
    "entry_mode": ["fvg_retest", "first_candle_break", "fvg_plus_break"],
    "min_gap_atr": [0.0, 0.25],
    "target_r": [1.5, 2.0, 3.0],
    "fvg_max_age": [10, 20],
    "require_first_candle_bias": [True, False],
    "max_trades_per_session": [1, 2],
}
base = P["P1 BTC baseline"]
print("\n=== 2. PURGED WALK-FORWARD, BTC grid, select + test at DOUBLED MES costs ===")
wf = purged_walk_forward(dev, base, grid, mes.double(), train_days=250, test_days=60,
                         embargo_days=3, min_trades=20, log=log, run_kwargs=rk)
f = wf["folds"]
ok2 = (not f.empty) and wf["oos_expectancy_R"] > 0
if not f.empty:
    print(f"  {len(f)} folds, {int((f.oos_expectancy_R > 0).sum())} positive | pooled OOS "
          f"{wf['oos_expectancy_R']:+.4f} R | IS->OOS decay {wf['is_oos_decay']:+.4f} R")
    print("  chosen entry_mode per fold:", f["chosen.entry_mode"].value_counts().to_dict())
else:
    print("  no eligible folds (too little data or too few trades per fold)")

# ---------------------------------------------- 3. exploratory -----------
print("\n=== 3. EXPLORATORY sweep (cannot change the verdict) ===")
ranked = sweep(dev, base, grid, mes, log=log, tag="fvg_es_explore", min_trades=30, run_kwargs=rk)
cols = ["entry_mode", "min_gap_atr", "target_r", "fvg_max_age", "require_first_candle_bias",
        "max_trades_per_session", "n_trades", "expectancy_R", "pct_same_bar_exit"]
print(ranked[cols].head(6).to_string(index=False))
by_mode = ranked.groupby("entry_mode")["expectancy_R"].agg(["mean", "max", "min"]).round(3)
print(by_mode.to_string())
best = params_from_row(ranked.iloc[0])
rb = run(dev, best, mes, **rk)
dsr = deflated_sharpe(rb["trades"]["r_multiple"].to_numpy(), n_trials=log.n_trials,
                      sr_variance=log.sharpe_variance())
print(f"  best {ranked.iloc[0]['expectancy_R']:+.4f} R | deflated Sharpe vs {log.n_trials} "
      f"ES trials: {dsr:.4f} (want > 0.95; the BTC search adds 13,628 more)")

verdict = "PASS" if (ok1 and ok2) else "FAIL"
print(f"""
--------------------------------------------------------------------
REPLICATION VERDICT: {verdict}
  1. P1-P3 positive at zero AND MES costs: {'yes' if ok1 else 'NO'}
  2. walk-forward pooled OOS > 0 at 2x costs: {'yes' if ok2 else 'NO'}
A PASS is a reason to run the remaining gate items (bar offsets, 15m/30m,
regimes), not a reason to trade. HOLDOUT REMAINS UNTOUCHED.
Trial log: {a.log} ({log.n_trials} trials). Commit it.
--------------------------------------------------------------------""")
