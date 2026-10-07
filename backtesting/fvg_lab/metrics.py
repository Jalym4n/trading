"""Performance metrics, with the ones that discount for search built in.

The headline number you should care about is not Sharpe. It is the Deflated
Sharpe Ratio: the probability your Sharpe is real given how many variants you
tried and how non-normal the returns are.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

EULER = 0.5772156649015329


def _sr(returns: np.ndarray) -> float:
    r = returns[np.isfinite(returns)]
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1))


def probabilistic_sharpe(returns: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """P(true SR > benchmark), adjusted for skew and kurtosis. Bailey/LdP."""
    r = returns[np.isfinite(returns)]
    t = len(r)
    if t < 3:
        return float("nan")
    sr = _sr(r)
    g3 = float(stats.skew(r))
    g4 = float(stats.kurtosis(r, fisher=False))
    denom = 1 - g3 * sr + ((g4 - 1) / 4) * sr**2
    if denom <= 0:
        return float("nan")
    z = (sr - sr_benchmark) * np.sqrt(t - 1) / np.sqrt(denom)
    return float(stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected best Sharpe from n_trials of PURE NOISE.

    This is the bar your strategy has to clear. It rises with every variant
    you test, which is exactly the point.
    """
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    a = stats.norm.ppf(1 - 1 / n_trials)
    b = stats.norm.ppf(1 - 1 / (n_trials * np.e))
    return float(np.sqrt(sr_variance) * ((1 - EULER) * a + EULER * b))


def deflated_sharpe(returns: np.ndarray, n_trials: int, sr_variance: float) -> float:
    """PSR against the expected-max-of-noise benchmark. Want > 0.95."""
    return probabilistic_sharpe(returns, expected_max_sharpe(n_trials, sr_variance))


def drawdown(equity: pd.Series) -> tuple[float, int]:
    """Max drawdown fraction and longest underwater stretch in bars."""
    eq = equity.dropna()
    if eq.empty:
        return 0.0, 0
    peak = eq.cummax()
    dd = eq / peak - 1
    under = (dd < 0).astype(int)
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return float(dd.min()), int(longest)


FIELDS = [
    "n_trades", "win_rate", "expectancy_R", "avg_win_R", "avg_loss_R",
    "profit_factor", "total_return", "sharpe_per_trade", "sharpe_annual",
    "max_drawdown", "longest_underwater_bars", "psr", "deflated_sharpe",
    "n_trials_assumed", "avg_bars_held", "pct_same_bar_exit", "pct_stopped",
    "pct_target", "pct_session_end", "skipped_undersized",
]


def _empty_row() -> dict:
    """Uniform shape even with zero trades, so sweep frames stay rectangular."""
    row = {k: float("nan") for k in FIELDS}
    row["n_trades"] = 0
    return row


def summarize(result: dict, n_trials: int = 1, sr_variance: float = 0.0, bars_per_year: int = 19_656) -> dict:
    """One row of honest metrics for a single run."""
    tdf, eq = result["trades"], result["equity"].dropna()
    if tdf.empty:
        row = _empty_row()
        row["skipped_undersized"] = result.get("skipped_undersized", 0)
        return row

    r = tdf["r_multiple"].to_numpy()
    bar_ret = eq.pct_change().dropna().to_numpy()

    mdd, under = drawdown(eq)
    wins, losses = r[r > 0], r[r <= 0]
    gross_win = float(tdf.loc[tdf.pnl_dollars > 0, "pnl_dollars"].sum())
    gross_loss = float(-tdf.loc[tdf.pnl_dollars <= 0, "pnl_dollars"].sum())

    # trade-level Sharpe is the relevant sample for a low-frequency strategy
    sr_trade = _sr(r)

    return {
        "n_trades": int(len(tdf)),
        "win_rate": float(len(wins) / len(r)),
        "expectancy_R": float(r.mean()),
        "avg_win_R": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_R": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else np.inf,
        "total_return": float(eq.iloc[-1] / result["starting_equity"] - 1),
        "sharpe_per_trade": sr_trade,
        "sharpe_annual": float(_sr(bar_ret) * np.sqrt(bars_per_year)),
        "max_drawdown": mdd,
        "longest_underwater_bars": under,
        "psr": probabilistic_sharpe(r),
        "deflated_sharpe": deflated_sharpe(r, n_trials, sr_variance),
        "n_trials_assumed": n_trials,
        "avg_bars_held": float(tdf["bars_held"].mean()),
        # data-quality flag, not a performance stat: trades that open and close
        # inside one bar are unresolvable with OHLC data. A high share here
        # means the result is measuring bar size, not the strategy.
        "pct_same_bar_exit": float((tdf["bars_held"] == 0).mean()),
        "pct_stopped": float((tdf.exit_reason.isin(["stop", "gap_stop"])).mean()),
        "pct_target": float((tdf.exit_reason.isin(["target", "gap_target"])).mean()),
        "pct_session_end": float((tdf.exit_reason == "session_end").mean()),
        # signals that never became trades because one unit risked more than
        # the budget. A large number means the account is too small for the
        # instrument, not that the strategy is selective.
        "skipped_undersized": int(result.get("skipped_undersized", 0)),
    }


def by_entry_kind(result: dict) -> pd.DataFrame:
    """Split performance by signal type (fvg vs ifvg vs or_break).

    Running both hypotheses in one sweep is only useful if you can tell them
    apart afterwards.
    """
    tdf = result["trades"]
    if tdf.empty:
        return pd.DataFrame()
    kind = tdf["entry_reason"].str.split("_").str[0].str.split(" ").str[0]
    g = tdf.assign(kind=kind).groupby("kind")
    return pd.DataFrame(
        {
            "n_trades": g.size(),
            "win_rate": g["r_multiple"].apply(lambda s: (s > 0).mean()),
            "expectancy_R": g["r_multiple"].mean(),
            "total_R": g["r_multiple"].sum(),
        }
    )


def by_year(result: dict) -> pd.DataFrame:
    """A strategy that made everything in one stretch is one observation."""
    tdf = result["trades"]
    if tdf.empty:
        return pd.DataFrame()
    g = tdf.assign(year=pd.to_datetime(tdf.entry_time).dt.year).groupby("year")
    return pd.DataFrame(
        {
            "n_trades": g.size(),
            "expectancy_R": g["r_multiple"].mean(),
            "total_R": g["r_multiple"].sum(),
            "win_rate": g["r_multiple"].apply(lambda s: (s > 0).mean()),
            "pnl": g["pnl_dollars"].sum(),
        }
    )


def randomization_test(result: dict, n_iter: int = 1000, seed: int = 0) -> dict:
    """Where does the real expectancy sit against shuffled-sign trades?

    Keeps the trade count and the magnitude distribution, destroys the
    directional skill. If the real result sits inside the null cloud, the
    'edge' is trade sizing and luck.
    """
    tdf = result["trades"]
    if tdf.empty:
        return {"p_value": float("nan")}
    r = tdf["r_multiple"].to_numpy()
    rng = np.random.default_rng(seed)
    obs = r.mean()
    mags = np.abs(r)
    null = np.array([(mags * rng.choice([-1, 1], len(mags))).mean() for _ in range(n_iter)])
    return {
        "observed_expectancy_R": float(obs),
        "null_mean": float(null.mean()),
        "null_p95": float(np.percentile(null, 95)),
        "p_value": float((null >= obs).mean()),
    }
