import numpy as np
import pandas as pd

from src.load import load_config
from src.strategies.smc_sweep import smc_sweep_bos

P = load_config()["params"]
LONG = {"prior_close": 101.0, "daily_sma": 100.0, "prior_high": 130.0, "prior_low": 90.0}


def day(rows, date="2021-03-02"):
    rows = list(rows) + [(rows[-1][3],) * 4] * (78 - len(rows))       # flat at the last close
    idx = pd.date_range(f"{date} 09:30", periods=78, freq="5min", tz="America/New_York")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


# swing high 106 (bar 3), swing low 100 (bar 8); bar 12 sweeps 100 (low 99) and reclaims;
# bar 14 closes above 106 (BOS, high 108): leg 99 -> 108, equilibrium 103.5.
# zones: order block = bar 11 (101.5..102.6) and a fair value gap 101.8..104.3 (bar 14 low above bar 12
# high). The gap straddles equilibrium, so its discount part tops out AT 103.5: entry 103.5, stop 98.75
BASE = [(103, 104, 102.5, 103.5), (103.5, 104.5, 103, 104), (104, 105, 103.5, 104.5), (104.5, 106, 104, 105),
        (105, 105.5, 104, 104.2), (104.2, 104.5, 103, 103.2), (103.2, 103.5, 102, 102.2), (102.2, 102.5, 101, 101.2),
        (101.2, 101.5, 100, 100.8), (100.8, 102, 100.5, 101.8), (101.8, 102.8, 101.5, 102.5),
        (102.5, 102.6, 101.5, 101.6), (101.6, 101.8, 99, 101.5), (101.5, 104.5, 101.4, 104.4),
        (104.4, 108, 104.3, 107.5)]


def test_long_setup_fills_in_discount_and_reaches_leg_high():
    r = smc_sweep_bos(day(BASE + [(107.5, 107.6, 102.4, 103.0), (103, 108.5, 102.9, 108.2)]), LONG, P)
    t = r.trades[0]
    assert (t.side, t.zone, t.entry, t.stop, t.target) == ("long", "FVG", 103.5, 98.75, 108.0)
    assert (t.entry_bar, t.exit_bar, t.exit_reason, t.exit) == ("10:45", "10:50", "target", 108.0)


def test_stop_and_no_lookahead():
    a = smc_sweep_bos(day(BASE + [(107.5, 107.6, 102.4, 103.0), (103, 103.2, 98.0, 98.5)]), LONG, P).trades[0]
    assert (a.exit_reason, a.exit) == ("stop", 98.75)
    b = smc_sweep_bos(day(BASE + [(107.5, 107.6, 102.4, 103.0), (103, 108.5, 102.9, 108.2)]), LONG, P).trades[0]
    assert (a.entry_bar, a.entry, a.stop, a.target) == (b.entry_bar, b.entry, b.stop, b.target)


def test_bias_blocks_the_other_side_and_statuses():
    short_bias = {**LONG, "prior_close": 99.0}
    r = smc_sweep_bos(day(BASE + [(107.5, 107.6, 102.4, 103.0)]), short_bias, P)
    assert r.detail["side"] == "short" and not [t for t in r.trades if t.side == "long"]
    assert smc_sweep_bos(day(BASE), {**LONG, "daily_sma": float("nan")}, P).status == "no-levels"
    flat = [(100, 100.2, 99.8, 100)] * 20
    assert smc_sweep_bos(day(flat), LONG, P).status == "no-sweep"


def test_short_is_the_mirror_of_long():
    rows = BASE + [(107.5, 107.6, 102.4, 103.0), (103, 108.5, 102.9, 108.2)]
    flip = [(-o, -l, -h, -c) for o, h, l, c in rows]
    lv = {"prior_close": -101.0, "daily_sma": -100.0, "prior_high": -90.0, "prior_low": -130.0}
    t = smc_sweep_bos(day(flip), lv, P).trades[0]
    assert (t.side, t.entry, t.stop, t.target, t.exit_reason) == ("short", -103.5, -98.75, -108.0, "target")
