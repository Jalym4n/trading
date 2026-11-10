"""Unit tests for src/load.py. Run from the project root: python -m pytest -q"""
import datetime as dt

import numpy as np
import pandas as pd

from src.load import (choose_front, expiry_key, is_outright, nyse_half_days, nyse_holidays, rth_5m,
                      session_dates, to_market_tz)

NY = "America/New_York"


def test_utc_converted_to_new_york_with_dst():
    df = pd.DataFrame({"ts_event": ["2024-03-08T14:30:00.000000000Z",     # EST: 09:30
                                    "2024-03-11T13:30:00.000000000Z",     # EDT: 09:30
                                    "2024-07-01T14:30:00.000000000Z"],    # EDT: 10:30
                       "open": [1.0, 2.0, 3.0]})
    out = to_market_tz(df, "UTC", NY)
    assert [t.strftime("%H:%M %Z") for t in out.index] == ["09:30 EST", "09:30 EDT", "10:30 EDT"]
    assert "ts_event" not in out.columns


def test_session_date_is_globex_day():
    idx = pd.DatetimeIndex(["2024-03-10 18:00", "2024-03-11 16:59", "2024-03-11 17:00",
                            "2024-03-11 18:00"]).tz_localize(NY)
    assert [d.date().isoformat() for d in session_dates(idx)] == \
        ["2024-03-11", "2024-03-11", "2024-03-11", "2024-03-12"]


def test_outrights_and_expiry():
    assert is_outright("MESZ5", "MES") and not is_outright("MESZ5-MESH6", "MES")
    assert expiry_key("MESH0", "MES", 2020) == (2020, 3) and expiry_key("MESH7", "MES", 2020) == (2027, 3)


def test_front_uses_prior_session_volume_and_never_goes_back():
    days = pd.to_datetime(["2024-03-11", "2024-03-12", "2024-03-13", "2024-03-14"])
    vol = pd.DataFrame({"MESH4": [100, 40, 90, 10], "MESM4": [10, 60, 20, 5]}, index=days)
    front, rolls = choose_front(vol, "MES", 2024)
    # the crossover in session 03-12 switches the front from 03-13 on, and 03-13's
    # higher MESH4 volume never switches it back
    assert list(front) == ["MESH4", "MESH4", "MESM4", "MESM4"]
    assert rolls.to_dict("records") == [{"roll_session": days[1], "old": "MESH4", "new": "MESM4",
                                         "first_new_session": days[2]}]


def test_front_ignores_future_volume():
    days = pd.to_datetime(["2024-03-11", "2024-03-12", "2024-03-13"])
    a = pd.DataFrame({"MESH4": [100, 100, 100], "MESM4": [10, 10, 10]}, index=days)
    b = a.copy()
    b.loc[days[2], "MESM4"] = 10_000          # change only the LAST session's volume
    assert list(choose_front(a, "MES", 2024)[0]) == list(choose_front(b, "MES", 2024)[0])


def test_nyse_calendar():
    h = nyse_holidays(2025)
    assert dt.date(2025, 1, 9) in h and dt.date(2025, 4, 18) in h and dt.date(2025, 6, 19) in h
    assert dt.date(2026, 7, 3) in nyse_holidays(2026)          # July 4 on a Saturday
    assert dt.date(2022, 12, 31) not in nyse_holidays(2022)    # Saturday, never Friday-observed for New Year
    assert nyse_half_days(2024) == {dt.date(2024, 7, 3), dt.date(2024, 11, 29), dt.date(2024, 12, 24)}


def test_rth_5m_labels_by_open_and_cuts_half_days():
    idx = pd.date_range("2024-11-29 09:25", "2024-11-29 13:14", freq="1min", tz=NY)
    m1 = pd.DataFrame({"open": np.arange(len(idx), dtype=float), "high": 1.0, "low": 0.0, "close": 0.5,
                       "volume": 1, "contract": "MESZ4"}, index=idx)
    out = rth_5m(m1, "09:30", "16:00", "13:00", {dt.date(2024, 11, 29)}, set(), "5min")
    assert out.index[0].strftime("%H:%M") == "09:30" and out.index[-1].strftime("%H:%M") == "12:55"
    assert len(out) == 42 and (out["n_1m"] == 5).all() and (out["volume"] == 5).all()
    assert out["open"].iloc[0] == m1.loc["2024-11-29 09:30", "open"]       # first minute of the bar
