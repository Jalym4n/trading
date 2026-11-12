import pandas as pd

from src.load import load_config
from src.strategies.amt_value_reentry import amt_value_reentry

P = load_config()["params"]
LV = {"vah": 110.0, "val": 100.0, "poc": 105.0}


def day(closes: dict, open_=112.0, vol=100):
    idx = pd.date_range("2021-03-02 09:30", periods=78, freq="5min", tz="America/New_York")
    px, rows = open_, []
    for lab in idx.strftime("%H:%M"):
        new = closes.get(lab, px)
        rows.append((px, max(px, new) + 0.1, min(px, new) - 0.1, new, vol))
        px = new
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def test_short_after_two_accepted_periods_to_val():
    # opens 112 (above VAH 110); inside the value area at the 10:00 and 10:30 closes; below VWAP
    r = amt_value_reentry(day({"09:50": 108.0, "11:00": 99.0}), LV, P)
    t = r.trades[0]
    assert (t.side, t.entry_bar, t.entry, t.stop, t.target) == ("short", "10:25", 108.0, 112.0, 100.0)
    assert (t.exit_reason, t.exit, t.exit_bar) == ("target", 100.0, "11:00")


def test_needs_consecutive_acceptance_and_ignores_later_bars():
    one = amt_value_reentry(day({"09:50": 108.0, "10:20": 111.0, "10:35": 108.0, "11:20": 111.0}), LV, P)
    assert one.status == "no-trigger" or one.trades[0].entry_bar > "10:25"
    a = amt_value_reentry(day({"09:50": 108.0}), LV, P).trades[0]
    b = amt_value_reentry(day({"09:50": 108.0, "10:30": 90.0, "12:00": 140.0}), LV, P).trades[0]
    assert (a.entry_bar, a.entry, a.stop, a.target) == (b.entry_bar, b.entry, b.stop, b.target)


def test_stop_long_mirror_and_statuses():
    s = amt_value_reentry(day({"09:50": 108.0, "10:40": 113.0}), LV, P).trades[0]
    assert (s.exit_reason, s.exit) == ("stop", 112.0)
    lg = amt_value_reentry(day({"09:50": 102.0, "11:00": 111.0}, open_=98.0), LV, P).trades[0]
    assert (lg.side, lg.stop, lg.target, lg.exit_reason) == ("long", 98.0, 110.0, "target")
    assert amt_value_reentry(day({}, open_=105.0), LV, P).status == "no-signal"          # opens inside value
    assert amt_value_reentry(day({}), LV, P).status == "no-trigger"                      # never accepted
    assert amt_value_reentry(day({}), {"vah": float("nan"), "val": 1.0}, P).status == "no-levels"
