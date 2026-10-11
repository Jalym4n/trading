"""Overnight-range (ONH/ONL) checks: no post-close leakage, correct grouping."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fvg_lab import data  # noqa: E402

TZ = "America/New_York"


def _globex(start, end):
    idx = pd.date_range(start, end, freq="5min", tz=TZ)
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.0, "volume": 1}, index=idx)


def test_post_close_bars_do_not_leak_into_same_morning():
    # Mon 18:00 -> Tue 16:55: overnight, Tue RTH, then Tue post-close
    df = _globex("2024-01-08 18:00", "2024-01-09 16:55")
    late = df.index >= pd.Timestamp("2024-01-09 16:30", tz=TZ)
    df.loc[late, "high"] = 500.0   # traded AFTER Tuesday's close
    df.loc[late, "low"] = 1.0
    out = data.add_overnight_levels(df)
    assert out["onh"].eq(101.0).all(), out["onh"].max()
    assert out["onl"].eq(99.0).all(), out["onl"].min()


def test_evening_bars_roll_to_next_session():
    # Mon 20:00 spike belongs to TUESDAY's overnight range, not Monday's
    df = pd.concat([_globex("2024-01-08 09:30", "2024-01-08 15:55"),
                    _globex("2024-01-08 18:00", "2024-01-09 15:55")])
    df = df[~df.index.duplicated()]
    df.loc[pd.Timestamp("2024-01-08 20:00", tz=TZ), "high"] = 150.0
    out = data.add_overnight_levels(df)
    tue = out[out["session"] == pd.Timestamp("2024-01-09", tz=TZ)]
    assert len(tue) == 78 and tue["onh"].eq(150.0).all()
    # Monday has no overnight bars before its open in this frame, so it is dropped
    assert (out["session"] == pd.Timestamp("2024-01-08", tz=TZ)).sum() == 0


def test_sunday_open_rolls_to_monday():
    # Globex opens Sunday 18:00; those bars belong to Monday's session
    df = _globex("2024-01-07 18:00", "2024-01-08 15:55")
    out = data.add_overnight_levels(df)
    assert set(out["session"].dt.date.astype(str)) == {"2024-01-08"}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
