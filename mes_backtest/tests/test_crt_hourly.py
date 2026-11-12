import pandas as pd

from src.load import load_config
from src.strategies.crt_hourly import crt_hourly

P = load_config()["params"]
HOUR1 = [(100, 105, 99.5, 104)] + [(104, 104.5, 101, 102)] * 11          # range: CRH 105, CRL 99.5


def day(rows):
    rows = list(rows) + [(rows[-1][3],) * 4] * (78 - len(rows))
    idx = pd.date_range("2021-03-02 09:30", periods=78, freq="5min", tz="America/New_York")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


SWEEP = (102, 102.2, 98.5, 100.5)          # 10:30: trades below 99.5, closes back inside
SHIFT = (100.5, 103, 100.4, 102.8)         # 10:35: closes above the sweep bar's high (102.2) -> long


def test_long_after_sweep_and_shift_to_range_high():
    t = crt_hourly(day(HOUR1 + [SWEEP, SHIFT, (102.8, 105.5, 102.5, 105.2)]), {}, P).trades[0]
    assert (t.side, t.entry_bar, t.entry, t.stop, t.target) == ("long", "10:35", 102.8, 98.25, 105.0)
    assert (t.exit_reason, t.exit, t.exit_bar) == ("target", 105.0, "10:40")


def test_breakout_close_drops_the_range_and_no_shift_means_no_trade():
    brk = (102, 102.2, 98.5, 99.0)                                    # closes below CRL: genuine breakout
    assert crt_hourly(day(HOUR1 + [brk, SHIFT]), {}, P).status == "no-sweep"
    stall = [(100.5, 101.5, 100.2, 101)] * 4                           # never closes above 102.2
    assert crt_hourly(day(HOUR1 + [SWEEP] + stall), {}, P).status == "no-trigger"


def test_stop_and_entry_ignores_later_bars():
    a = crt_hourly(day(HOUR1 + [SWEEP, SHIFT, (102.8, 103, 98.0, 98.2)]), {}, P).trades[0]
    assert (a.exit_reason, a.exit) == ("stop", 98.25)
    b = crt_hourly(day(HOUR1 + [SWEEP, SHIFT, (102.8, 105.5, 102.5, 105.2)]), {}, P).trades[0]
    assert (a.entry_bar, a.entry, a.stop, a.target) == (b.entry_bar, b.entry, b.stop, b.target)


def test_short_mirror():
    flip = lambda rows: [(-o, -l, -h, -c) for o, h, l, c in rows]     # noqa: E731
    t = crt_hourly(day(flip(HOUR1 + [SWEEP, SHIFT, (102.8, 105.5, 102.5, 105.2)])), {}, P).trades[0]
    assert (t.side, t.entry, t.stop, t.target, t.exit_reason) == ("short", -102.8, -98.25, -105.0, "target")
