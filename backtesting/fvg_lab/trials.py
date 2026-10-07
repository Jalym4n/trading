"""Trial logging, parameter sweeps, and purged walk-forward validation.

The trial log is the point of this file. Every variant you ever run gets
appended, permanently. The count feeds the deflated Sharpe, so the more you
search the higher the bar your result has to clear. This is the only defence
against fooling yourself, and it only works if you never reset the log.
"""

from __future__ import annotations

import itertools
import json
import os
from dataclasses import replace

import numpy as np
import pandas as pd

from .engine import Costs, run
from .metrics import summarize
from .strategy import Bars, Params


def _jsonable(o):
    """numpy scalars leak in whenever params are rebuilt from a pandas row."""
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


def params_from_row(row) -> Params:
    """Rebuild Params from a sweep row, coercing numpy scalars back to python.

    Without this, a Params carrying np.int64 silently poisons the trial log and
    any dataclass comparison downstream.
    """
    fields = Params().to_dict()
    vals = {}
    for k, default in fields.items():
        if k not in row:
            continue
        v = row[k]
        if isinstance(default, bool):
            vals[k] = bool(v)
        elif isinstance(default, int):
            vals[k] = int(v)
        elif isinstance(default, float):
            vals[k] = float(v)
        else:
            vals[k] = str(v)
    return Params(**vals)


class TrialLog:
    """Append-only JSONL. Counts are cached in memory so a sweep does not
    re-read the whole file on every variant."""

    def __init__(self, path: str = "trials.jsonl"):
        self.path = path
        self._rows: list[dict] | None = None

    def _ensure(self) -> list[dict]:
        if self._rows is None:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    self._rows = [json.loads(l) for l in f if l.strip()]
            else:
                self._rows = []
        return self._rows

    def append(self, params: dict, stats: dict, tag: str = "") -> None:
        row = {"tag": tag, "params": params, "stats": stats}
        with open(self.path, "a") as f:
            f.write(json.dumps(row, default=_jsonable) + "\n")
        self._ensure().append(row)

    def load(self) -> pd.DataFrame:
        rows = self._ensure()
        return pd.json_normalize(rows) if rows else pd.DataFrame()

    @property
    def n_trials(self) -> int:
        return len(self._ensure())

    def sharpe_variance(self) -> float:
        """Variance of per-trade Sharpe across all logged trials.

        This is the dispersion of results your search produced. Feeding it to
        deflated_sharpe is what turns 'I tried a lot of things' into a number.
        """
        s = [
            r["stats"].get("sharpe_per_trade")
            for r in self._ensure()
            if r["stats"].get("sharpe_per_trade") is not None
        ]
        s = np.array([x for x in s if np.isfinite(x)], dtype=float)
        return float(s.var(ddof=1)) if len(s) > 1 else 0.0


def grid_params(base: Params, grid: dict) -> list[Params]:
    keys = list(grid)
    return [replace(base, **dict(zip(keys, combo))) for combo in itertools.product(*grid.values())]


def sweep(
    df: pd.DataFrame,
    base: Params,
    grid: dict,
    costs: Costs = Costs(),
    log: TrialLog | None = None,
    tag: str = "",
    min_trades: int = 30,
) -> pd.DataFrame:
    """Run every combination, log every one, return a ranked frame.

    Ranking is deliberately by expectancy with a minimum trade count, not by
    total return. Total return rewards the variant that got lucky once.
    """
    log = log or TrialLog()
    rows = []
    # indicators only depend on these, so build Bars once per group
    cache: dict[tuple, Bars] = {}
    for p in grid_params(base, grid):
        key = (p.first_candle_n, p.atr_period, p.ema_period, p.amd_cons_bars)
        if key not in cache:
            cache[key] = Bars(df, p)
        res = run(df, p, costs, bars=cache[key])
        st = summarize(res)
        log.append(p.to_dict(), st, tag=tag)
        rows.append({**p.to_dict(), **st})

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["n_trials_assumed"] = log.n_trials
    out["sr_variance"] = log.sharpe_variance()
    eligible = out[out["n_trades"] >= min_trades]
    return (eligible if not eligible.empty else out).sort_values("expectancy_R", ascending=False)


def purged_walk_forward(
    df: pd.DataFrame,
    base: Params,
    grid: dict,
    costs: Costs = Costs(),
    train_days: int = 120,
    test_days: int = 40,
    embargo_days: int = 3,
    min_trades: int = 20,
    log: TrialLog | None = None,
) -> dict:
    """Select parameters in-sample, apply them forward, never look back.

    The embargo drops sessions between train and test so that serial
    correlation in volatility cannot leak across the boundary. For intraday
    strategies that flatten at the close, a few days is enough; widen it if
    your holding period ever spans sessions.
    """
    log = log or TrialLog()
    if "session" not in df:
        raise ValueError("call data.add_session(df) first")
    sessions = np.array(sorted(df["session"].unique()))

    fold_rows, oos_trades = [], []
    start = 0
    while start + train_days + embargo_days + test_days <= len(sessions):
        tr = sessions[start : start + train_days]
        te_start = start + train_days + embargo_days
        te = sessions[te_start : te_start + test_days]
        tr_df = df[df["session"].isin(tr)]
        te_df = df[df["session"].isin(te)]

        ranked = sweep(tr_df, base, grid, costs, log=log, tag="wf_train", min_trades=min_trades)
        if ranked.empty:
            start += test_days
            continue
        best = ranked.iloc[0]
        chosen = params_from_row(best)

        res = run(te_df, chosen, costs)
        st = summarize(res)
        log.append(chosen.to_dict(), st, tag="wf_test")
        fold_rows.append(
            {
                "train_start": tr[0], "train_end": tr[-1],
                "test_start": te[0], "test_end": te[-1],
                "is_expectancy_R": float(best["expectancy_R"]),
                "oos_expectancy_R": st.get("expectancy_R", np.nan),
                "oos_trades": st.get("n_trades", 0),
                **{f"chosen.{k}": v for k, v in chosen.to_dict().items() if k in grid},
            }
        )
        if not res["trades"].empty:
            oos_trades.append(res["trades"])
        start += test_days

    folds = pd.DataFrame(fold_rows)
    all_oos = pd.concat(oos_trades) if oos_trades else pd.DataFrame()
    return {
        "folds": folds,
        "oos_trades": all_oos,
        "oos_expectancy_R": float(all_oos["r_multiple"].mean()) if not all_oos.empty else np.nan,
        "is_oos_decay": float(folds["is_expectancy_R"].mean() - folds["oos_expectancy_R"].mean())
        if not folds.empty
        else np.nan,
    }
