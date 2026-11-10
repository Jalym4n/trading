"""Training window and rolling walk-forward windows (config validation.*), all before the holdout.

training_window(cfg)      ("2020-01-01", "2021-12-31"): the only window phase 5 reads.
windows(cfg)              the yearly train/test pairs from config validation.walk_forward.
run_training(...)         one backtest on the training window, stats per instrument + pooled.
run_walk_forward(...)     every window: stats on train AND test, per instrument + pooled,
                          plus the test trades tagged by volatility regime.

The strategies here have no fitted parameters, so a "train" result is just the
in-sample period; the test windows are what Gate 3 reads (net expectancy > 0 in
a majority of them). Every window passes through backtest.clamp_window, so none
can reach 2026.
"""

from __future__ import annotations

import pandas as pd

from . import backtest as B
from . import stats as S


def training_window(cfg: dict) -> tuple[str, str]:
    t = cfg["validation"]["training"]
    return t["start"], t["end"]


def windows(cfg: dict) -> list[dict]:
    out = []
    for i, w in enumerate(cfg["validation"]["walk_forward"], 1):
        tr = sorted(w["train"])
        out.append({"name": f"wf{i}", "train": (f"{tr[0]}-01-01", f"{tr[-1]}-12-31"),
                    "test": (f"{w['test']}-01-01", f"{w['test']}-12-31")})
    return out


def _stats_by_instrument(res: B.RunResult, cfg: dict) -> dict[str, dict]:
    out = {}
    for inst in res.instruments:
        dl = res.day_log[res.day_log["instrument"] == inst]
        tl = res.trade_log[res.trade_log["instrument"] == inst]
        out[inst] = S.summarize(tl, cfg, B.weeks_in(dl, cfg) if len(dl) else None)
    if len(res.instruments) > 1:
        out["POOLED"] = S.summarize(res.trade_log, cfg, B.weeks_in(res.day_log, cfg) if len(res.day_log) else None)
    return out


def pooled(stats: dict[str, dict]) -> dict:
    return stats.get("POOLED") or next(iter(stats.values()))


def run_training(cfg: dict, strategy, instruments=None, params=None, slippage_mult: float = 1.0,
                 name: str = "strategy") -> dict:
    s, e = training_window(cfg)
    res = B.run(cfg, strategy, instruments, s, e, params, slippage_mult, name)
    doubled = B.recost(res, cfg, slippage_mult * 2.0)
    return {"window": (s, e), "result": res, "stats": _stats_by_instrument(res, cfg),
            "stats_doubled_slippage": _stats_by_instrument(doubled, cfg), "counts": B.counts(res, cfg)}


def run_walk_forward(cfg: dict, strategy, instruments=None, params=None, slippage_mult: float = 1.0,
                     name: str = "strategy", regimes: pd.DataFrame | None = None) -> dict:
    out = {"windows": [], "test_trades": []}
    for w in windows(cfg):
        row = {"name": w["name"], "train_window": w["train"], "test_window": w["test"]}
        for part in ("train", "test"):
            res = B.run(cfg, strategy, instruments, *w[part], params, slippage_mult, name)
            row[part] = _stats_by_instrument(res, cfg)
            if part == "test":
                row["test_doubled_slippage"] = _stats_by_instrument(B.recost(res, cfg, slippage_mult * 2.0), cfg)
                out["test_trades"].append(res.trade_log.assign(window=w["name"]))
                row["test_day_log"] = res.day_log
        out["windows"].append(row)
    tt = pd.concat(out["test_trades"], ignore_index=True) if out["test_trades"] else pd.DataFrame()
    days = pd.concat([w["test_day_log"] for w in out["windows"]], ignore_index=True)
    out["test_trades"] = tt
    out["test_pooled"] = S.summarize(tt, cfg, B.weeks_in(days, cfg) if len(days) else None)
    out["test_pooled_doubled_slippage"] = S.summarize(
        B.recost_frame(tt, cfg, slippage_mult * 2.0, params), cfg, B.weeks_in(days, cfg) if len(days) else None)
    out["positive_test_windows"] = sum(
        1 for w in out["windows"] if (pooled(w["test"]).get("expectancy_net_usd") or 0) > 0)
    out["n_test_windows"] = len(out["windows"])
    if regimes is not None and not tt.empty:
        tagged = tt.merge(regimes, on=["instrument", "date"], how="left")
        out["by_regime"] = {str(k): S.summarize(g, cfg) for k, g in tagged.groupby("regime", dropna=False)}
    return out
