"""Data loading, session handling, and synthetic bar generation.

Contract for every OHLCV frame used in this project:
  - tz-aware DatetimeIndex, sorted ascending, no duplicates
  - columns: open, high, low, close, volume
  - each bar's timestamp is its OPEN time (this matters for no-lookahead logic)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLS = ["open", "high", "low", "close", "volume"]


def load_csv(path: str, tz: str = "America/New_York", ts_col: str = "timestamp") -> pd.DataFrame:
    """Load an OHLCV csv into the canonical frame.

    Assumes the timestamp column is the bar's OPEN time. If your vendor stamps
    bars with the close time, shift it yourself before calling this -- getting
    this wrong is a silent one-bar lookahead.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index(ts_col).tz_convert(tz)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    df = df[REQUIRED_COLS].sort_index()
    df = df[~df.index.duplicated(keep="first")]
    validate(df)
    return df


def validate(df: pd.DataFrame) -> None:
    """Fail loudly on the data problems that silently fake a good backtest."""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("index must be a DatetimeIndex")
    if df.index.tz is None:
        raise ValueError("index must be tz-aware")
    if not df.index.is_monotonic_increasing:
        raise ValueError("index must be sorted ascending")
    if df.index.has_duplicates:
        raise ValueError("duplicate timestamps")
    bad = (
        (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
    )
    if bad.any():
        raise ValueError(f"{int(bad.sum())} bars violate OHLC bounds")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("non-positive prices")


def add_session(
    df: pd.DataFrame,
    session_start: str = "09:30",
    session_end: str = "16:00",
) -> pd.DataFrame:
    """Tag each bar with its session date and its index within the session.

    bar_in_session == 0 is the 'first candle'.
    """
    out = df.copy()
    t = out.index.time
    start = pd.to_datetime(session_start).time()
    end = pd.to_datetime(session_end).time()
    in_session = (t >= start) & (t < end)
    out = out[in_session].copy()
    out["session"] = out.index.normalize()
    out["bar_in_session"] = out.groupby("session").cumcount()
    out["bars_in_session"] = out.groupby("session")["bar_in_session"].transform("size")
    return out


def synth(
    n_days: int = 500,
    bars_per_day: int = 78,
    seed: int = 0,
    start: str = "2023-01-03",
    drift: float = 0.0,
    vol: float = 0.0009,
    tick: float = 0.25,
    start_price: float = 4000.0,
) -> pd.DataFrame:
    """Random-walk bars with no exploitable structure.

    This is the null-hypothesis dataset. Any strategy that looks profitable
    here is measuring your search process, not the market. Run every new rule
    set against this before you run it against real data.

    For a SPY-shaped series: synth(tick=0.01, start_price=550.0).
    """
    rng = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range(start, periods=n_days, tz="America/New_York"):
        idx.extend(pd.date_range(d + pd.Timedelta("9h30m"), periods=bars_per_day, freq="5min"))
    idx = pd.DatetimeIndex(idx)

    n = len(idx)
    rets = rng.normal(drift, vol, n)
    close = start_price * np.exp(np.cumsum(rets))
    # build plausible OHLC around the close path
    prev = np.concatenate([[close[0]], close[:-1]])
    wiggle = np.abs(rng.normal(0, vol, n)) * close
    high = np.maximum(prev, close) + wiggle
    low = np.minimum(prev, close) - wiggle
    o, h, l, c = (np.round(x / tick) * tick for x in (prev, high, low, close))
    return pd.DataFrame(
        {"open": o, "high": h, "low": l, "close": c, "volume": rng.integers(500, 5000, n)},
        index=idx,
    )


def split_holdout(df: pd.DataFrame, holdout_frac: float = 0.3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split. Touch the holdout ONCE, at the very end.

    Every time you look at it, it stops being out-of-sample.
    """
    cut = int(len(df) * (1 - holdout_frac))
    cut_ts = df.index[cut].normalize()
    return df[df.index < cut_ts].copy(), df[df.index >= cut_ts].copy()
