import numpy as np
import pandas as pd
import pytest

from src.backtest import noise_sigma
from src.load import load_config
from src.strategies.noise_band_trend import CHECKS, noise_band_trend

P = dict(load_config()["params"], nbt_stop_points=30.0)
SIG = {k: 0.01 for k in CHECKS}                      # band: 100 +/- 1%  ->  99 .. 101
LV = {"prior_close": 100.0, "noise_sigma": SIG}


def day(closes: dict, base=100.0, date="2021-03-02"):
    """78 flat bars at `base`; closes = {label: close} moves the market from that bar on."""
    idx = pd.date_range(f"{date} 09:30", periods=78, freq="5min", tz="America/New_York")
    px, rows = base, []
    for lab in idx.strftime("%H:%M"):
        new = closes.get(lab, px)
        rows.append((px, max(px, new) + 0.1, min(px, new) - 0.1, new))
        px = new
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_checks_are_the_half_hours():
    assert CHECKS[0] == "09:55" and CHECKS[-1] == "15:25" and len(CHECKS) == 12


def test_long_outside_band_held_to_the_close():
    r = noise_band_trend(day({"10:25": 102.0, "15:55": 104.0}), LV, P)
    t = r.trades[0]
    assert (t.side, t.entry_bar, t.entry, t.stop, t.exit_bar, t.exit, t.exit_reason) == \
        ("long", "10:25", 102.0, 72.0, "15:55", 104.0, "time")


def test_exit_when_back_inside_band_and_no_entry_between_checks():
    r = noise_band_trend(day({"10:05": 103.0, "10:25": 102.0, "11:00": 100.5}), LV, P)
    t = r.trades[0]                                   # 10:05 is not a check: entry waits for 10:25
    assert (t.entry_bar, t.exit_bar, t.exit, t.exit_reason) == ("10:25", "11:25", 100.5, "band")
    assert len(r.trades) == 1


def test_short_and_stop():
    r = noise_band_trend(day({"09:55": 98.0, "10:40": 130.0}), LV, P)
    t = r.trades[0]
    assert (t.side, t.entry, t.stop, t.exit_reason, t.exit, t.exit_bar) == ("short", 98.0, 128.0, "stop", 128.0, "10:40")


def test_entry_ignores_later_bars():
    a = noise_band_trend(day({"10:25": 102.0}), LV, P).trades[0]
    b = noise_band_trend(day({"10:25": 102.0, "10:30": 80.0, "12:00": 150.0}), LV, P).trades[0]
    assert (a.side, a.entry_bar, a.entry, a.stop) == (b.side, b.entry_bar, b.entry, b.stop)


def test_skips():
    assert noise_band_trend(day({}), LV, P).status == "no-signal"
    assert noise_band_trend(day({}).iloc[:42], LV, P).status == "short-day"
    assert noise_band_trend(day({}), {"prior_close": 100.0, "noise_sigma": {}}, P).status == "no-levels"


def test_noise_sigma_uses_only_earlier_days():
    frames = []
    for k, d in enumerate(pd.bdate_range("2021-01-04", periods=5)):
        f = day({"09:55": 100.0 + (k + 1)}, date=str(d.date()))        # day k moves (k+1)% by 09:55
        f["date"] = pd.Timestamp(d.date())
        frames.append(f)
    m5 = pd.concat(frames)
    s = noise_sigma(m5, 2)
    assert np.isnan(s["09:55"].iloc[:2]).all()
    assert s["09:55"].iloc[2] == pytest.approx(0.015) and s["09:55"].iloc[4] == pytest.approx(0.035)
    m5.loc[m5["date"] == m5["date"].max(), "close"] *= 3               # today's bars cannot move today's sigma
    assert noise_sigma(m5, 2)["09:55"].iloc[4] == pytest.approx(0.035)


def test_dollar_stop_scales_with_the_contract():
    from src.strategies.noise_band_trend import noise_band_trend_usd
    d = day({"10:25": 102.0, "15:55": 104.0})
    mes = noise_band_trend_usd(d, LV, dict(P, nbt_stop_usd=150.0, _point_value_usd=5.0, _tick_size=0.25)).trades[0]
    mgc = noise_band_trend_usd(d, LV, dict(P, nbt_stop_usd=150.0, _point_value_usd=10.0, _tick_size=0.1)).trades[0]
    assert (mes.entry - mes.stop, round(mgc.entry - mgc.stop, 6)) == (30.0, 15.0)
