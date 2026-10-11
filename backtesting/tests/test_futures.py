"""Back-adjustment and resampling checks (no network, no Databento)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_futures as ff  # noqa: E402
from fvg_lab import data  # noqa: E402


def _two_contracts(spread=50.0, n=120, roll_at=60):
    """Old contract for bars < roll_at, new contract (old + spread) after."""
    idx = pd.date_range("2026-03-17 22:00", periods=n, freq="1min", tz="UTC")
    rng = np.random.default_rng(3)
    base = 6800 + np.round(np.cumsum(rng.normal(0, 0.5, n)) * 4) / 4   # 0.25 grid
    iid = np.where(np.arange(n) < roll_at, 111, 222)
    px = base + np.where(iid == 222, spread, 0.0)
    df = pd.DataFrame({"instrument_id": iid, "open": px, "high": px + 0.5,
                       "low": px - 0.5, "close": px, "volume": 10}, index=idx)
    new_full = pd.DataFrame({"close": base + spread}, index=idx)  # new contract, all minutes
    return df, new_full, base


def test_roll_detected_and_spread_measured_at_common_minute():
    raw, new_full, _ = _two_contracts(spread=50.0)
    rolls = ff.find_rolls(raw)
    assert len(rolls) == 1 and rolls["old_id"][0] == 111 and rolls["new_id"][0] == 222
    ts = rolls["ts"][0]
    old = raw[raw["instrument_id"] == 111]
    s, t = ff.spread_at_overlap(old, new_full, ts)
    assert s == 50.0 and t == ts - pd.Timedelta(minutes=1)   # last minute BEFORE the roll


def test_back_adjust_removes_jump_and_keeps_grid():
    raw, new_full, base = _two_contracts(spread=50.0)
    rolls = ff.find_rolls(raw)
    rolls["spread"] = [ff.spread_at_overlap(raw[raw.instrument_id == 111], new_full, rolls.ts[0])[0]]
    adj = ff.back_adjust(raw, rolls)
    # whole series now equals the new contract's price path: no roll jump
    assert np.allclose(adj["close"], base + 50.0)
    # bars after the roll are untouched, bars before shifted by exactly the spread
    after = raw.index >= rolls.ts[0]
    assert (adj.loc[after, "close"] == raw.loc[after, "close"]).all()
    assert (adj.loc[~after, "adj"] == 50.0).all() and (adj.loc[after, "adj"] == 0).all()
    assert ((adj["close"] * 4) % 1 == 0).all()                  # still on the 0.25 grid
    # point distances unchanged (what ATR and tick costs see)
    assert np.allclose((adj.high - adj.low), (raw.high - raw.low))


def test_cumulative_adjustment_over_two_rolls():
    idx = pd.date_range("2026-01-01", periods=9, freq="1min", tz="UTC")
    raw = pd.DataFrame({"instrument_id": [1, 1, 1, 2, 2, 2, 3, 3, 3],
                        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                        "volume": 1}, index=idx)
    rolls = ff.find_rolls(raw)
    rolls["spread"] = [10.0, 5.0]
    adj = ff.back_adjust(raw, rolls)
    assert adj["adj"].tolist() == [15, 15, 15, 5, 5, 5, 0, 0, 0]


def test_missing_overlap_refuses_to_guess():
    raw, _, _ = _two_contracts()
    rolls = ff.find_rolls(raw)
    rolls["spread"] = [float("nan")]
    try:
        ff.back_adjust(raw, rolls)
        raise AssertionError("adjusted without a measured spread")
    except ValueError as e:
        assert "refusing to guess" in str(e)


def test_resample_left_labelled_and_offset():
    idx = pd.date_range("2026-01-05 09:30", periods=10, freq="1min", tz="America/New_York")
    df = pd.DataFrame({"open": np.arange(10.0) + 100, "high": np.arange(10.0) + 101,
                       "low": np.arange(10.0) + 99, "close": np.arange(10.0) + 100.5,
                       "volume": 1}, index=idx)
    r = data.resample_ohlcv(df, "5min")
    assert [t.strftime("%H:%M") for t in r.index] == ["09:30", "09:35"]
    first = r.iloc[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (100, 105, 99, 104.5, 5)
    r1 = data.resample_ohlcv(df, "5min", offset="1min")
    assert [t.strftime("%H:%M") for t in r1.index] == ["09:26", "09:31", "09:36"]
    # gaps are dropped, not filled
    r2 = data.resample_ohlcv(df.drop(df.index[5:10]), "5min")
    assert len(r2) == 1


def test_roll_gap_check_ignores_reopens_but_catches_rolls():
    # Friday 16:59 close, Sunday 18:00 reopen with a real 40-point gap
    fri = pd.date_range("2026-03-06 15:00", "2026-03-06 16:59", freq="1min", tz="America/New_York")
    sun = pd.date_range("2026-03-08 18:00", "2026-03-08 21:00", freq="1min", tz="America/New_York")
    idx = fri.append(sun)
    px = np.where(idx >= sun[0], 6840.0, 6800.0)
    df = pd.DataFrame({"open": px, "high": px + 0.5, "low": px - 0.5, "close": px,
                       "volume": 1}, index=idx)
    data.assert_no_roll_gaps(df)                         # weekend reopen: allowed
    try:
        data.assert_no_roll_gaps(df, skip_reopens=False)
        raise AssertionError("old behaviour should flag the reopen")
    except ValueError:
        pass
    # a mid-session jump one minute after the previous bar: a roll, must raise
    j = df.copy()
    j.loc[j.index >= sun[90], ["open", "high", "low", "close"]] += 50.0
    try:
        data.assert_no_roll_gaps(j)
        raise AssertionError("mid-session roll jump not caught")
    except ValueError as e:
        assert "2026-03-08 19:30" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
