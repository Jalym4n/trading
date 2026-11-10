"""Unit tests for src/profile.py. Run from the project root: python -m pytest -q"""
import numpy as np
import pandas as pd

from src.load import load_config
from src.profile import prior_session_levels, volume_profile

NY = "America/New_York"


def _vp(prices, vols, va=0.70):
    p = np.asarray(prices, float)
    return volume_profile(p, p, p, np.asarray(vols, float), 1.0, va)


def test_poc_is_the_mode_and_value_area_expands_to_70pct():
    # bins 100..104 with volumes 10, 20, 50, 15, 5 (total 100)
    r = _vp([100, 101, 102, 103, 104], [10, 20, 50, 15, 5])
    assert r["poc"] == 102.0
    # 50 -> +20 (101, larger neighbour) = 70 -> stop at >= 70%
    assert (r["val"], r["vah"]) == (101.0, 102.0) and r["va_share"] == 0.70


def test_typical_price_binning_and_empty_bins_count_as_zero():
    # typical price 100.4 -> bin 100; 100.5 -> bin 101 (round half up); a gap at 102
    hi, lo, cl = np.array([100.4, 100.5, 103.0]), np.array([100.4, 100.5, 103.0]), np.array([100.4, 100.5, 103.0])
    r = volume_profile(hi, lo, cl, np.array([5.0, 7.0, 1.0]), 1.0, 0.70)
    assert list(r["hist"].index) == [100.0, 101.0, 102.0, 103.0] and list(r["hist"]) == [5.0, 7.0, 0.0, 1.0]


def test_poc_tie_goes_to_bin_nearest_vwap():
    r = _vp([100, 101, 105], [30, 10, 30])           # vwap 102.29: bin 100 is 2.29 away, bin 105 is 2.71
    assert r["poc"] == 100.0


def _bars(day, contract, base, n=390):
    idx = pd.date_range(f"{day} 09:30", periods=n, freq="1min", tz=NY)
    px = base + np.arange(n) % 10
    return pd.DataFrame({"open": px, "high": px + 0.25, "low": px - 0.25, "close": px, "volume": 100,
                         "contract": contract}, index=idx)


def _sessions(days, contracts):
    return pd.DataFrame({"contract": contracts, "half_day": False, "roll_skip": False},
                        index=pd.to_datetime(days))


def test_levels_use_only_the_prior_session():
    cfg = load_config()
    m1 = pd.concat([_bars("2024-03-12", "MESM4", 5000), _bars("2024-03-13", "MESM4", 6000)])
    s = _sessions(["2024-03-12", "2024-03-13", "2024-03-14"], ["MESM4"] * 3)
    lv = prior_session_levels(m1, s, cfg)
    assert 5000 <= lv.loc["2024-03-13", "poc"] < 5010          # from 03-12, not 03-13
    m1b = m1.copy()
    m1b.loc["2024-03-13", "close"] += 500                        # change day D's own bars
    assert lv.loc["2024-03-13", "poc"] == prior_session_levels(m1b, s, cfg).loc["2024-03-13", "poc"]


def test_no_levels_across_a_contract_change():
    cfg = load_config()
    m1 = _bars("2024-03-12", "MESH4", 5000)
    s = _sessions(["2024-03-12", "2024-03-13"], ["MESH4", "MESM4"])
    lv = prior_session_levels(m1, s, cfg)
    assert np.isnan(lv.loc["2024-03-13", "poc"]) and "MESH4" in lv.loc["2024-03-13", "levels_reason"]
