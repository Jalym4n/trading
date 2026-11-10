"""Statistics from a trade log (src.backtest.run output). Pure functions, no I/O.

summarize(trade_log, cfg, weeks) -> dict with, gross AND net of costs:
  n_trades, trades_per_week, win_rate, avg_win_usd, avg_loss_usd,
  expectancy_{gross,net}_usd  = (win% x avgWin) - (loss% x avgLoss) = mean P&L per trade,
  expectancy_net_r, profit_factor, max_dd_usd (peak-to-trough on the net equity
  curve, trades in time order), max_consec_losses, worst_streak_loss_usd (the
  worst run of consecutive losses, summed), worst_day_usd, best_day_usd,
  best_day_pct_of_target (vs the Lucid profit target: the consistency input),
  trades_to_target (= target / net expectancy), exits by reason, r_hist.
Every figure is for 1 contract. Nothing here decides anything; guards.scorecard does.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

R_EDGES = [-math.inf, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0, math.inf]


def ordered(trade_log: pd.DataFrame) -> pd.DataFrame:
    """Trades in the order they happened (date, entry bar, instrument): the equity-curve order."""
    if trade_log.empty:
        return trade_log
    return trade_log.sort_values(["date", "entry_bar", "instrument"], kind="stable").reset_index(drop=True)


def daily_pnl(trade_log: pd.DataFrame, col: str = "net_usd") -> pd.Series:
    if trade_log.empty:
        return pd.Series(dtype=float)
    return trade_log.groupby("date")[col].sum().sort_index()


def max_drawdown(pnl: pd.Series) -> float:
    """Largest peak-to-trough fall of the cumulative P&L (starting from 0), as a positive number."""
    if pnl.empty:
        return 0.0
    eq = np.concatenate([[0.0], pnl.to_numpy(float).cumsum()])
    return float((np.maximum.accumulate(eq) - eq).max())


def loss_streaks(net: pd.Series) -> tuple[int, float]:
    """(max consecutive losses, worst summed loss over a run of consecutive losses)."""
    best_n, best_sum, n, s = 0, 0.0, 0, 0.0
    for v in net.to_numpy(float):
        if v < 0:
            n, s = n + 1, s + v
            best_n, best_sum = max(best_n, n), min(best_sum, s)
        else:
            n, s = 0, 0.0
    return best_n, float(best_sum)


def r_histogram(r: pd.Series, edges: list[float] = R_EDGES) -> dict[str, int]:
    if r.empty:
        return {}
    counts, _ = np.histogram(r.to_numpy(float), bins=edges)
    labels = [f"{'<' if lo == -math.inf else f'{lo:g}..'}{'' if hi == math.inf else f'{hi:g}'}"
              + ("+" if hi == math.inf else "") for lo, hi in zip(edges[:-1], edges[1:])]
    return {lab: int(c) for lab, c in zip(labels, counts)}


def summarize(trade_log: pd.DataFrame, cfg: dict, weeks: float | None = None) -> dict:
    target = float(cfg["lucid_25k"]["profit_target"])
    t = ordered(trade_log)
    n = len(t)
    out = {"n_trades": n, "weeks": weeks, "trades_per_week": (n / weeks) if (weeks and n is not None) else None}
    if n == 0:
        return {**out, "win_rate": None, "avg_win_usd": None, "avg_loss_usd": None, "expectancy_gross_usd": None,
                "expectancy_net_usd": None, "expectancy_net_r": None, "profit_factor": None, "max_dd_usd": 0.0,
                "max_consec_losses": 0, "worst_streak_loss_usd": 0.0, "worst_day_usd": None, "best_day_usd": None,
                "best_day_pct_of_target": None, "trades_to_target": None, "exits": {}, "r_hist": {},
                "net_total_usd": 0.0, "gross_total_usd": 0.0}
    net, gross = t["net_usd"].astype(float), t["gross_usd"].astype(float)
    wins, losses = net[net > 0], net[net <= 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    exp_net = float(net.mean())
    day = daily_pnl(t)
    streak_n, streak_sum = loss_streaks(net)
    return {
        **out,
        "win_rate": float(len(wins) / n),
        "avg_win_usd": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_usd": float(losses.mean()) if len(losses) else 0.0,
        "expectancy_gross_usd": float(gross.mean()),
        "expectancy_net_usd": exp_net,
        "expectancy_net_r": float(t["r_net"].astype(float).mean()),
        "profit_factor": (gp / gl) if gl > 0 else (math.inf if gp > 0 else None),
        "gross_total_usd": float(gross.sum()),
        "net_total_usd": float(net.sum()),
        "max_dd_usd": max_drawdown(net),
        "max_consec_losses": streak_n,
        "worst_streak_loss_usd": streak_sum,
        "worst_day_usd": float(day.min()),
        "best_day_usd": float(day.max()),
        "best_day_pct_of_target": float(100 * day.max() / target),
        "trades_to_target": (target / exp_net) if exp_net > 0 else None,
        "exits": {k: int(v) for k, v in t["exit_reason"].value_counts().items()},
        "r_hist": r_histogram(t["r_net"].astype(float)),
    }


def table(summaries: dict[str, dict], keys: list[str] | None = None) -> pd.DataFrame:
    """{label: summary} -> one column per label, rounded for printing."""
    keys = keys or ["n_trades", "trades_per_week", "win_rate", "avg_win_usd", "avg_loss_usd", "expectancy_gross_usd",
                    "expectancy_net_usd", "expectancy_net_r", "profit_factor", "net_total_usd", "max_dd_usd",
                    "max_consec_losses", "worst_streak_loss_usd", "worst_day_usd", "best_day_usd",
                    "best_day_pct_of_target", "trades_to_target"]
    df = pd.DataFrame({lab: {k: s.get(k) for k in keys} for lab, s in summaries.items()})
    return df.map(lambda v: round(v, 2) if isinstance(v, float) and math.isfinite(v) else v)
