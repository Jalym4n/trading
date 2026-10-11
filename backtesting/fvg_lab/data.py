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

    bar_in_session == 0 is the 'first candle'. Session boundaries are read in
    the frame's own timezone.

    For 24/7 markets pass session_start="00:00", session_end="24:00": every
    bar is kept and each calendar day (in the frame's tz) is one session.
    Load crypto with tz="UTC" for UTC-midnight sessions, or keep New York
    time and use 09:30-16:00 to test whether the equity open matters.
    """
    out = df.copy()
    t = out.index.time
    start = pd.to_datetime(session_start).time()
    if session_end == "24:00":
        in_session = t >= start
    else:
        end = pd.to_datetime(session_end).time()
        in_session = (t >= start) & (t < end)
    out = out[in_session].copy()
    out["session"] = out.index.normalize()
    out["bar_in_session"] = out.groupby("session").cumcount()
    out["bars_in_session"] = out.groupby("session")["bar_in_session"].transform("size")
    return out


def trade_date(idx: pd.DatetimeIndex, roll_hour: int = 18) -> pd.DatetimeIndex:
    """Map each timestamp to the RTH session it belongs to.

    Futures trade nearly 23 hours. A bar at 20:00 Monday belongs to Tuesday's
    session, because the Globex day opens at 18:00 ET the prior calendar day.
    Everything from 18:00 onward rolls forward to the next weekday.
    """
    d = idx.normalize()
    roll = idx.hour >= roll_hour
    out = d.where(~roll, d + pd.Timedelta(days=1))
    # Friday evening rolls to Monday
    dow = out.dayofweek
    out = out.where(dow != 5, out + pd.Timedelta(days=2))
    out = out.where(dow != 6, out + pd.Timedelta(days=1))
    return pd.DatetimeIndex(out)


def add_overnight_levels(
    df: pd.DataFrame,
    on_start: str = "18:00",
    rth_start: str = "09:30",
    rth_end: str = "16:00",
) -> pd.DataFrame:
    """Compute the overnight auction's high/low, then return the RTH frame.

    ONH/ONL are built from 18:00 ET the prior evening to the RTH open, so they
    are fully determined BEFORE the first RTH bar. Post-close bars (16:00 to
    18:00 ET) are excluded: they trade after the session they would otherwise
    be grouped with, which would be lookahead. Using them at any RTH bar is
    not lookahead. This is the structure futures have and cash equities do not:
    a real overnight auction instead of a gap.

    Input must be a 24h frame. Output is RTH only, with onh/onl/on_range added.
    """
    out = df.copy()
    out["session"] = trade_date(out.index, roll_hour=int(on_start.split(":")[0]))

    t = out.index.time
    r0 = pd.to_datetime(rth_start).time()
    r1 = pd.to_datetime(rth_end).time()
    is_rth = (t >= r0) & (t < r1)

    # Overnight = from on_start the prior evening up to the RTH open. Bars
    # between the RTH close and on_start (16:00-17:00 ET futures trading)
    # happen AFTER this session's RTH and must not leak into its ONH/ONL.
    o0 = pd.to_datetime(on_start).time()
    is_on = (t >= o0) | (t < r0)
    on = out[is_on]
    if on.empty:
        raise ValueError(
            "no overnight bars found. add_overnight_levels needs a 24h frame; "
            "use synth(include_overnight=True) or an unfiltered vendor file."
        )
    levels = on.groupby("session").agg(onh=("high", "max"), onl=("low", "min"))

    rth = out[is_rth].copy()
    rth = rth.join(levels, on="session")
    rth = rth.dropna(subset=["onh", "onl"])
    rth["on_range"] = rth["onh"] - rth["onl"]
    rth["bar_in_session"] = rth.groupby("session").cumcount()
    rth["bars_in_session"] = rth.groupby("session")["bar_in_session"].transform("size")
    return rth


def assert_no_roll_gaps(df: pd.DataFrame, max_gap_atr: float = 5.0,
                        atr_period: int = 14) -> None:
    """Reject a continuous futures series that still contains roll gaps.

    A quarterly roll discontinuity satisfies low[i] > high[i-2] exactly, so an
    unadjusted or badly stitched series manufactures a phantom FVG four times a
    year at a price that never traded. Call this before any futures backtest.
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(atr_period, min_periods=atr_period).mean().shift(1)
    jump = (df["open"] - prev_close).abs()
    bad = jump > max_gap_atr * atr
    if bad.any():
        where = df.index[bad][:5].tolist()
        raise ValueError(
            f"{int(bad.sum())} bar(s) gap more than {max_gap_atr}x ATR from the "
            f"prior close, e.g. {where}. If this is a futures series, the roll "
            f"is not adjusted. Back-adjust it or exclude the roll sessions."
        )


def synth(
    n_days: int = 500,
    bars_per_day: int = 78,
    seed: int = 0,
    start: str = "2023-01-03",
    drift: float = 0.0,
    vol: float = 0.0009,
    tick: float = 0.25,
    start_price: float = 4000.0,
    include_overnight: bool = False,
    overnight_bars: int = 90,
) -> pd.DataFrame:
    """Random-walk bars with no exploitable structure.

    This is the null-hypothesis dataset. Any strategy that looks profitable
    here is measuring your search process, not the market. Run every new rule
    set against this before you run it against real data.

    For a SPY-shaped series: synth(tick=0.01, start_price=550.0).

    include_overnight=True prepends `overnight_bars` 5m bars running back from
    the open, so the frame has the 24h shape futures actually have. Required to
    null-test any overnight-range rule: the ON high/low of a random walk carries
    no information, so a positive result here is a bug.
    """
    rng = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range(start, periods=n_days, tz="America/New_York"):
        if include_overnight:
            on_start = d + pd.Timedelta("9h30m") - pd.Timedelta(minutes=5 * overnight_bars)
            idx.extend(pd.date_range(on_start, periods=overnight_bars, freq="5min"))
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
