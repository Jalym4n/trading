"""Unit tests for src/strategy.py on synthetic days. Run: python -m pytest -q"""
import numpy as np
import pandas as pd

from src.load import load_config
from src.strategy import run_day

P = load_config()["params"]
LV = {"poc": 107.0, "vah": 120.0, "val": 90.0}


def day(rows, fill=(115, 115.25, 114.75, 115), date="2024-05-14"):
    """rows: (o, h, l, c) from 09:30 every 5 min; padded to 10:55 with `fill` (a doji)."""
    rows = list(rows) + [fill] * (18 - len(rows))
    idx = pd.date_range(f"{date} 09:30", periods=18, freq="5min", tz="America/New_York")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


OR = [(101, 104, 100, 103), (103, 104, 101, 102), (102, 103.5, 100.5, 103)]       # OR 100..104
LEG = [(103.5, 106.5, 103.5, 106),     # 09:45 breakout: closes 106 > 104 -> long
       (106, 110, 105.5, 109.5),        # 09:50 green, high 110
       (109.5, 111, 108.5, 109)]        # 09:55 red -> confirms; leg end 111
TRIGGER = (106.8, 108, 106.5, 107.9)    # 10:00 dips to 106.5 (zone top 107), green, close in top third


def test_long_trade_to_target():
    r = run_day(day(OR + LEG + [TRIGGER, (108, 121, 107.5, 120)]), LV, P)
    assert r.status == "traded" and len(r.trades) == 1
    t = r.trades[0]
    assert (t.side, t.zone, t.node, t.fib) == ("long", "POC", 107.0, 0.618)
    assert (t.breakout_time, t.leg_confirm_time, t.leg_start, t.leg_end) == ("09:45", "09:55", 104, 111)
    assert (t.entry_bar, t.entry, t.stop, t.target, t.target_source) == ("10:00", 107.9, 104.0, 120.0, "VAH")
    assert (t.exit_bar, t.exit, t.exit_reason) == ("10:05", 120.0, "target")


def test_stop_and_target_in_one_bar_is_a_stop():
    r = run_day(day(OR + LEG + [TRIGGER, (108, 121, 103.5, 110)]), LV, P)
    t = r.trades[0]
    assert (t.exit_reason, t.exit, t.same_bar_stop_and_target) == ("stop", 104.0, True)


def test_gap_through_stop_fills_at_open():
    r = run_day(day(OR + LEG + [TRIGGER, (103, 103.5, 102, 102.5)]), LV, P)
    assert (r.trades[0].exit_reason, r.trades[0].exit) == ("stop_gap", 103.0)


def test_time_exit_at_last_bar_close():
    r = run_day(day(OR + LEG + [TRIGGER], fill=(112, 112.25, 111.75, 112.1)), LV, P)
    t = r.trades[0]
    assert (t.exit_reason, t.exit_bar, t.exit) == ("time", "10:55", 112.1)


def test_no_breakout_after_deadline():
    inside = [(102, 103, 101, 102)] * 9                              # 09:45..10:25 stay inside
    late = [(102, 106, 102, 105.5)]                                  # 10:30 bar closes outside: too late
    r = run_day(day(OR + inside + late, fill=(105, 105.5, 104.5, 105)), LV, P)
    assert r.status == "no_breakout"


def test_no_zone_without_confluence_but_zones_with_filter_off():
    far = {"poc": 150.0, "vah": 160.0, "val": 50.0}
    assert run_day(day(OR + LEG + [TRIGGER]), far, P).status == "no_zone"
    r = run_day(day(OR + LEG + [TRIGGER]), far, P, confluence=False)
    assert r.status == "traded" and r.trades[0].zone.startswith("FIB_")


def test_entry_never_depends_on_later_bars():
    base = day(OR + LEG + [TRIGGER, (108, 121, 107.5, 120)])
    alt = base.copy()
    alt.iloc[7:] = [(50, 200, 40, 60)] * (len(alt) - 7)                # rewrite every bar after the entry bar
    a, b = run_day(base, LV, P).trades[0], run_day(alt, LV, P).trades[0]
    for f in ("side", "zone", "entry_bar", "entry", "stop", "target", "leg_end", "leg_confirm_time"):
        assert getattr(a, f) == getattr(b, f), f


def test_leg_end_includes_the_confirming_bar_and_ignores_later_highs():
    later_high = (110, 130, 109, 112)                                  # after confirmation: ignored
    r = run_day(day(OR + LEG + [later_high]), LV, P)
    assert r.detail["leg_end"] == 111


def test_two_losses_end_the_day_and_deeper_zone_is_used():
    lv = {"poc": 107.0, "vah": 120.0, "val": 105.0}                    # VAL 105 ~ fib 0.786 (105.5)
    rows = OR + LEG + [TRIGGER,
                       (107.5, 107.6, 103.9, 104.5),                   # 10:05 stops trade 1 (104), touches VAL zone
                       (104.5, 106, 104.4, 105.9),                     # 10:10 green, top third -> trade 2 (stop 102)
                       (105.5, 105.6, 101.5, 102)]                     # 10:15 stops trade 2
    r = run_day(day(rows, fill=(102, 102.25, 101.75, 102)), lv, P)
    assert [t.zone for t in r.trades] == ["POC", "VAL"]
    assert [t.exit_reason for t in r.trades] == ["stop", "stop"] and r.trades[1].entry_bar == "10:10"


def test_no_entry_on_the_last_bar():
    waiting = [(109, 109.5, 108.5, 109)] * 9                           # 10:00..10:40 above the zone
    late = [(109, 109.2, 108.8, 109), (107.2, 108, 106.5, 107.9)]     # 10:45 nothing; 10:50... see below
    r = run_day(day(OR + LEG + waiting + late + [(106.8, 108, 106.5, 107.9)]), LV, P)
    # 10:50 bar is the last allowed entry bar (index 16); 10:55 (index 17) never enters
    assert all(t.entry_bar <= "10:50" for t in r.trades)
    only_last = OR + LEG + waiting + [(109, 109.2, 108.8, 109)] * 2 + [(106.8, 108, 106.5, 107.9)]
    assert run_day(day(only_last), LV, P).status == "no_trigger"


def test_short_mirror():
    flip = lambda rows: [(-o, -l, -h, -c) for o, h, l, c in rows]     # noqa: E731
    lv = {"poc": -107.0, "vah": -90.0, "val": -120.0}
    r = run_day(day(flip(OR + LEG + [TRIGGER, (108, 121, 107.5, 120)]), fill=(-115, -114.75, -115.25, -115)), lv, P)
    t = r.trades[0]
    assert (t.side, t.entry, t.stop, t.target, t.exit_reason) == ("short", -107.9, -104.0, -120.0, "target")


def test_missing_levels_or_bars():
    assert run_day(day(OR + LEG), {"poc": np.nan, "vah": 1.0, "val": 0.0}, P).status == "no_levels"
    assert run_day(day(OR + LEG).iloc[:-1], LV, P).status == "short_day"
