"""Synthetic bars shared by the tests: one scripted day, and a many-day dataset for the harness."""
import datetime as dt

import numpy as np
import pandas as pd

NY = "America/New_York"
LV = {"poc": 107.0, "vah": 120.0, "val": 90.0}
OR = [(101, 104, 100, 103), (103, 104, 101, 102), (102, 103.5, 100.5, 103)]       # OR 100..104
LEG = [(103.5, 106.5, 103.5, 106), (106, 110, 105.5, 109.5), (109.5, 111, 108.5, 109)]
TRIGGER = (106.8, 108, 106.5, 107.9)
TO_TARGET = (108, 121, 107.5, 120)


def day(rows, fill=(115, 115.25, 114.75, 115), date="2024-05-14"):
    rows = list(rows) + [fill] * (18 - len(rows))
    idx = pd.date_range(f"{date} 09:30", periods=18, freq="5min", tz=NY)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def dataset(start="2020-01-02", n_days=120, seed=3):
    """{m1 (unused), m5, sessions, levels} for a fake instrument: a random walk at 5-min
    resolution, 78 bars per weekday, with prior-session levels around the open."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_days)
    frames, sess, lv = [], [], []
    px = 4000.0
    for i, d in enumerate(days):
        idx = pd.date_range(f"{d.date()} 09:30", periods=78, freq="5min", tz=NY)
        steps = rng.normal(0, 1.2, 78)
        close = px + steps.cumsum()
        open_ = np.concatenate([[px], close[:-1]])
        high = np.maximum(open_, close) + rng.uniform(0.25, 1.5, 78)
        low = np.minimum(open_, close) - rng.uniform(0.25, 1.5, 78)
        f = pd.DataFrame({"open": open_.round(2), "high": high.round(2), "low": low.round(2), "close": close.round(2),
                          "volume": 100, "n_1m": 5, "contract": "SYNH0"}, index=idx)
        f.index.name = "ts"
        f["date"] = pd.Timestamp(d.date())
        frames.append(f)
        sess.append({"date": pd.Timestamp(d.date()), "contract": "SYNH0", "n_5m": 78, "half_day": False,
                     "roll_skip": i % 60 == 30, "data_skip": False, "skip_reason": None, "tradeable": i % 60 != 30})
        lv.append({"date": pd.Timestamp(d.date()), "poc": round(px) + 2.0, "vah": round(px) + 9.0,
                   "val": round(px) - 6.0})
        px = float(close[-1])
    m5 = pd.concat(frames)
    return {"m1": None, "m5": m5, "sessions": pd.DataFrame(sess).set_index("date"),
            "levels": pd.DataFrame(lv).set_index("date"), "rolls": pd.DataFrame()}


def synth_cfg(cfg):
    """A config copy pointing at the synthetic instrument and its date windows."""
    c = {k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg.items()}
    c["instrument_specs"] = dict(cfg["instrument_specs"], SYN={"root": "SYN", "point_value_usd": 5.0, "tick_size": 0.25,
                                                               "tick_value_usd": 1.25, "commission_rt_usd": 1.24})
    c["params"] = dict(cfg["params"], instruments=["SYN"], min_leg_points=0.0)
    c["data_source"] = dict(cfg["data_source"], start="2020-01-02")
    c["validation"] = dict(cfg["validation"], training={"start": "2020-01-02", "end": "2020-03-31"},
                           walk_forward=[{"train": ["2020"], "test": "2020"}],
                           oos_holdout={"start": "2026-01-01", "end": "2026-11-06"})
    return c
