"""Backward confirmation: ONE registered strategy, unchanged, on a pre-holdout window that
no earlier trial has read (config validation.backward_confirmation). Logged as a trial.

    python -m src.confirm
"""
from . import backtest as B, guards as G, registry as R, stats as S
from .load import load_config


def main() -> None:
    cfg = load_config()
    bc = cfg["validation"]["backward_confirmation"]
    strat = R.get(bc["strategy"].split(",")[0])
    inst, (s, e) = [bc["instrument"]], bc["window"]
    params = dict(strat.defaults)
    eff = dict(cfg["params"], **params)
    G.check_budget(cfg, G.trial_key(strat.name, eff, inst))
    res = B.run(cfg, strat, inst, s, e, params, 1.0, strat.name)
    tl = res.trade_log
    st = S.summarize(tl, cfg, B.weeks_in(res.day_log, cfg))
    st2 = S.summarize(B.recost_frame(tl, cfg, 2.0, params), cfg, B.weeks_in(res.day_log, cfg))
    daily = tl.groupby("date")["net_usd"].sum()
    t = float(daily.mean() / (daily.std(ddof=1) / len(daily) ** 0.5))
    by_year = tl.assign(year=tl["date"].astype(str).str[:4]).groupby("year")["net_usd"].agg(["size", "mean", "sum"]).round(2)
    print(S.table({"costs x1": st, "slippage x2": st2}).to_string())
    print(f"\nt (daily sums) = {t:.2f}\n\n{by_year.to_string()}")
    full = by_year[by_year.index >= "2011"]
    ok = st["expectancy_net_usd"] > 0 and t >= 3.0 and (full["mean"] > 0).sum() > len(full) / 2
    print(f"\npositive years 2011+: {(full['mean'] > 0).sum()} of {len(full)}   VERDICT: {'PASS' if ok else 'FAIL'}  ({bc['pass']})")
    keep = ("n_trades", "win_rate", "expectancy_gross_usd", "expectancy_net_usd", "net_total_usd", "max_dd_usd", "profit_factor")
    rec = G.append_trial({"strategy": strat.name, "params": eff, "overrides": params, "instruments": inst,
                          "phase": "backward_confirmation", "slippage_mult": 1.0, "git": G.git_state(),
                          "trial_key": G.trial_key(strat.name, eff, inst), "hypothesis": strat.hypothesis,
                          "disproof": bc["pass"],
                          "results": {"window": [s, e], "stats": {k: st[k] for k in keep},
                                      "stats_doubled_slippage": {k: st2[k] for k in keep}, "t_daily": t,
                                      "by_year": by_year.reset_index().to_dict("records"), "verdict": "PASS" if ok else "FAIL"},
                          "scorecard": [], "note": "backward confirmation of the trial 11 MNQ finding"})
    print(f"logged as trial line {rec['trial_line']}")


if __name__ == "__main__":
    main()
