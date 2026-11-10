"""The hypothesis-testing loop: one command runs any registered strategy through the
identical costs, windows, statistics and scorecard, and logs it as a trial.

    python -m src.harness list
    python -m src.harness run --strategy or_fib_base --phase training
    python -m src.harness run --strategy or_fib_base --phase walkforward --set confluence_tol=2
    python -m src.harness log
    python -m src.harness compare or_fib_base or_fib_confluence
    python -m src.harness holdout --strategy NAME --confirm --reason "..."   # once, at the very end

Workflow (README): propose a hypothesis -> register it (a file in src/strategies/)
-> `run` on training, then walkforward (never the holdout) -> it is auto-logged
-> `compare` against the current best on the same windows -> only after ONE
finalist is chosen, `holdout` once.
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from . import backtest as B
from . import guards as G
from . import registry as R
from . import stats as S
from . import walkforward as W
from .load import ROOT, load_config


def _print_stats(title: str, stats: dict[str, dict]) -> None:
    print(f"\n{title}")
    print(S.table(stats).to_string())


def _results_summary(training: dict | None, wf: dict | None) -> dict:
    out = {}
    if training:
        out["training"] = {k: _slim(v) for k, v in training["stats"].items()}
        out["training_doubled_slippage"] = {k: _slim(v) for k, v in training["stats_doubled_slippage"].items()}
    if wf:
        out["walk_forward"] = {w["name"]: {"test": _slim(W.pooled(w["test"])), "train": _slim(W.pooled(w["train"]))}
                               for w in wf["windows"]}
        out["walk_forward_test_pooled"] = _slim(wf["test_pooled"])
        out["walk_forward_test_pooled_doubled_slippage"] = _slim(wf["test_pooled_doubled_slippage"])
        out["positive_test_windows"] = f"{wf['positive_test_windows']}/{wf['n_test_windows']}"
        if "by_regime" in wf:
            out["by_regime"] = {k: _slim(v) for k, v in wf["by_regime"].items()}
    return out


def _slim(s: dict) -> dict:
    keep = ("n_trades", "trades_per_week", "win_rate", "expectancy_gross_usd", "expectancy_net_usd", "expectancy_net_r",
            "profit_factor", "net_total_usd", "max_dd_usd", "max_consec_losses", "worst_streak_loss_usd",
            "worst_day_usd", "best_day_usd", "best_day_pct_of_target", "trades_to_target", "exits")
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items() if k in keep}


def cmd_run(a, cfg) -> None:
    strat = R.get(a.strategy)
    params = dict(strat.defaults, **B._parse_set(a.set))
    instruments = a.instrument or cfg["params"]["instruments"]
    training = wf = None
    if a.phase in ("training", "both"):
        training = W.run_training(cfg, strat, instruments, params, a.slippage_mult, strat.name)
        print(f"\n{strat.name}  TRAINING {training['window'][0]} -> {training['window'][1]}  instruments {instruments}"
              f"  overrides {params or 'none'}")
        print(training["counts"].T.to_string())
        _print_stats("training statistics (1 contract, gross and net of costs)", training["stats"])
        _print_stats("training with DOUBLED slippage", training["stats_doubled_slippage"])
        p = W.pooled(training["stats"])
        print(f"\nexits: {p['exits']}   R distribution (net): {p['r_hist']}")
    if a.phase in ("walkforward", "both"):
        regimes = G.regime_tags(cfg, instruments)
        wf = W.run_walk_forward(cfg, strat, instruments, params, a.slippage_mult, strat.name, regimes)
        print(f"\n{strat.name}  WALK-FORWARD  instruments {instruments}  overrides {params or 'none'}")
        for w in wf["windows"]:
            print(f"\n{w['name']}: train {w['train_window'][0]}..{w['train_window'][1]}  test {w['test_window'][0]}.."
                  f"{w['test_window'][1]}")
            print(S.table({"train (pooled)": W.pooled(w["train"]), "TEST (pooled)": W.pooled(w["test"]),
                           "TEST doubled slip": W.pooled(w["test_doubled_slippage"])}).to_string())
        _print_stats("all TEST windows pooled", {"test pooled": wf["test_pooled"],
                                                  "test pooled, doubled slippage": wf["test_pooled_doubled_slippage"]})
        print(f"\ntest windows with net expectancy > 0: {wf['positive_test_windows']} of {wf['n_test_windows']}")
        if "by_regime" in wf:
            _print_stats("test trades by volatility regime (terciles of lagged 20-day realized vol)", wf["by_regime"])
    rows = G.scorecard(cfg, W.pooled(training["stats"]) if training else None, wf,
                       training_doubled=W.pooled(training["stats_doubled_slippage"]) if training else None)
    print("\nSCORECARD (lucid-25k-viability-target.md)")
    print(G.format_scorecard(rows))
    eff = dict(cfg["params"], **params)
    rec = G.append_trial({"strategy": strat.name, "params": eff, "overrides": params, "instruments": instruments,
                          "phase": a.phase, "slippage_mult": a.slippage_mult, "git": G.git_state(),
                          "trial_key": G.trial_key(strat.name, eff, instruments),
                          "hypothesis": strat.hypothesis, "disproof": strat.disproof,
                          "results": _results_summary(training, wf),
                          "scorecard": [(r["gate"], r["verdict"], r["actual"]) for r in rows], "note": a.note})
    out = ROOT / "results" / "trades"
    out.mkdir(parents=True, exist_ok=True)
    if training:
        training["result"].trade_log.to_csv(out / f"trial{rec['trial_line']:04d}_{strat.name}_training.csv", index=False)
    if wf is not None and not wf["test_trades"].empty:
        wf["test_trades"].to_csv(out / f"trial{rec['trial_line']:04d}_{strat.name}_wf_test.csv", index=False)
    print(f"\nlogged as trial line {rec['trial_line']} (key {rec['trial_key']}, commit {rec['git']['commit']}"
          f"{' DIRTY' if rec['git']['dirty'] else ''}) -> {G.TRIAL_LOG.relative_to(ROOT)}")
    print("REMINDER: " + G.reminder())


def cmd_list(a, cfg) -> None:
    for s in R.all_strategies().values():
        print(f"{s.name}  ({s.module})\n  hypothesis: {s.hypothesis}\n  disproof:   {s.disproof}\n"
              f"  defaults:   {s.defaults or 'none'}")


def cmd_log(a, cfg) -> None:
    G.verify_chain()
    rows = []
    for t in G.read_trials():
        r = t.get("results", {})
        tp = r.get("walk_forward_test_pooled") or W.pooled(r.get("training", {"x": {}}))
        rows.append({"line": t["trial_line"], "when": t["timestamp_utc"][:16], "strategy": t["strategy"],
                     "phase": t["phase"], "overrides": json.dumps(t.get("overrides", {})), "inst": ",".join(t["instruments"]),
                     "commit": (t["git"] or {}).get("commit"), "n": tp.get("n_trades"),
                     "exp_net": tp.get("expectancy_net_usd"), "wf+": r.get("positive_test_windows"),
                     "gates": " ".join(f"{g[0][-1]}:{g[1][0]}" for g in t.get("scorecard", []) if g[0].startswith("GATE"))})
    print(pd.DataFrame(rows).to_string(index=False) if rows else "trial log is empty")
    print("\n" + G.reminder() + "  (chain verified)")


def cmd_compare(a, cfg) -> None:
    """Latest logged run per strategy, side by side, on the same phase."""
    latest = {}
    for t in G.read_trials():
        if t["strategy"] in a.strategies and t["phase"] == a.phase:
            latest[t["strategy"]] = t
    if not latest:
        print(f"no logged {a.phase} runs for {a.strategies}; run them first")
        return
    cols = {}
    for name, t in latest.items():
        r = t["results"]
        cols[name] = r.get("walk_forward_test_pooled") if a.phase == "walkforward" else W.pooled(r["training"])
    print(S.table(cols).to_string())
    for name, t in latest.items():
        print(f"\n{name}: " + "; ".join(f"{g[0]} {g[1]}" for g in t.get("scorecard", []) if g[0].startswith("GATE")))
    print("\n" + G.reminder())


def cmd_holdout(a, cfg) -> None:
    strat = R.get(a.strategy)
    out = G.run_final_holdout(cfg, strat, confirm=a.confirm, reason=a.reason or "",
                              params=dict(strat.defaults, **B._parse_set(a.set)), instruments=a.instrument)
    _print_stats("HOLDOUT statistics (final)", out["stats"])
    rows = G.scorecard(cfg, None, None, holdout=W.pooled(out["stats"]))
    print("\n" + G.format_scorecard([r for r in rows if r["gate"] == "GATE 4"]))
    print(f"\nrecorded in {G.HOLDOUT_LEDGER.relative_to(ROOT)}; this strategy cannot be run on the holdout again.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--strategy", required=True)
    r.add_argument("--phase", default="training", choices=["training", "walkforward", "both"])
    r.add_argument("--instrument", action="append")
    r.add_argument("--set", action="append", metavar="PARAM=VALUE")
    r.add_argument("--slippage-mult", type=float, default=1.0)
    r.add_argument("--note", default="")
    sub.add_parser("list")
    sub.add_parser("log")
    c = sub.add_parser("compare")
    c.add_argument("strategies", nargs="+")
    c.add_argument("--phase", default="training", choices=["training", "walkforward"])
    h = sub.add_parser("holdout")
    h.add_argument("--strategy", required=True)
    h.add_argument("--confirm", action="store_true")
    h.add_argument("--reason", default="")
    h.add_argument("--instrument", action="append")
    h.add_argument("--set", action="append", metavar="PARAM=VALUE")
    a = ap.parse_args(argv)
    cfg = load_config()
    {"run": cmd_run, "list": cmd_list, "log": cmd_log, "compare": cmd_compare, "holdout": cmd_holdout}[a.cmd](a, cfg)


if __name__ == "__main__":
    main()
