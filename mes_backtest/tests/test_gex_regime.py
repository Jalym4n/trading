import pandas as pd

from src.backtest import gex_context
from src.strategies.gex_regime import _regime, crt_hourly_high_gamma, noise_band_low_gamma
from src.load import load_config

P = load_config()["params"]


def test_regime_and_gating():
    assert _regime({"gex_prev": 2.0, "gex_median": 1.0}) == "high" and _regime({"gex_prev": 0.5, "gex_median": 1.0}) == "low"
    assert _regime({"gex_prev": float("nan"), "gex_median": 1.0}) is None
    idx = pd.date_range("2021-03-02 09:30", periods=78, freq="5min", tz="America/New_York")
    bars = pd.DataFrame({"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1}, index=idx)
    assert crt_hourly_high_gamma(bars, {"gex_prev": 0.5, "gex_median": 1.0}, P).status == "no-bias"
    assert crt_hourly_high_gamma(bars, {"gex_prev": 2.0, "gex_median": 1.0}, P).status == "no-sweep"
    assert noise_band_low_gamma(bars, {"gex_prev": 2.0, "gex_median": 1.0}, P).status == "no-bias"
    assert noise_band_low_gamma(bars, {}, P).status == "no-levels"


def test_gex_context_is_lagged():
    days = pd.bdate_range("2021-03-01", periods=5)
    prev, med = gex_context(days)
    if len(prev):                                   # only when the data file is present
        raw = pd.read_csv("data/external/squeezemetrics_dix.csv", parse_dates=["date"]).set_index("date")["gex"]
        assert prev[days[2]] == raw[raw.index < days[2]].iloc[-1]
        assert med[days[2]] == raw[raw.index < days[2]].iloc[-253:-1].median()
