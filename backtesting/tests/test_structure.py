"""Market structure (swings, BOS/CHoCH) and quasimodo rules on hand-built paths."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fvg_lab import data  # noqa: E402
from fvg_lab.strategy import Bars, FVGStrategy, Params  # noqa: E402

# up to H1, down to L1, up to the head H2 > H1, down through L1, back up to H1
KEYS = [(0, 100), (8, 110), (14, 104), (22, 114), (30, 100), (38, 110), (45, 108)]


def _frame(keys=KEYS, n=46):
    x, y = zip(*keys)
    close = np.interp(np.arange(n), x, y)
    idx = pd.date_range("2026-01-05 09:30", periods=n, freq="5min", tz="America/New_York")
    df = pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                       "close": close, "volume": 1}, index=idx)
    return data.add_session(df)


def _arm_bar(df, level=103.5, after=22):
    """First close below L1 AFTER the head formed (the path starts below L1)."""
    c = df["close"].to_numpy()
    return int(np.argmax((np.arange(len(c)) > after) & (c < level)))


def _walk(df, p):
    b = Bars(df, p)
    s = FVGStrategy(p)
    out = {}
    for i in range(b.n):
        it = s.decide(i, b, in_position=False)
        if it is not None:
            out[i] = it
            s.note_trade_opened()
    return b, s, out


def test_swing_is_not_known_before_confirmation():
    df = _frame()
    p = Params(entry_mode="qm")
    b = Bars(df, p)
    s = FVGStrategy(p)
    for i in range(8 + 3):                      # through bar 10: peak at 8 not yet known
        s.decide(i, b, in_position=False)
    assert s._swings == []
    s.decide(11, b, in_position=False)          # bar 8 + swing_right(3) = 11
    assert s._swings == [(8, 110.5, 1)]


def test_bearish_quasimodo_short_at_left_shoulder_with_head_stop():
    df = _frame()
    p = Params(entry_mode="qm", stop_mode="qm_head", max_trades_per_session=5)
    b, s, out = _walk(df, p)
    arm = _arm_bar(df)                                             # first close below L1
    assert len(out) == 1, out
    (i, it), = out.items()
    assert i > arm and it.direction == -1 and it.reason.startswith("qm_short")
    assert b.high[i] >= 110.5 - 0.1 * b.atr[i]                     # touched H1 (left shoulder)
    assert np.isclose(it.stop, 114.5 + 0.25 * b.atr[i])            # beyond the head + buffer


def test_quasimodo_expires():
    _, _, out = _walk(_frame(), Params(entry_mode="qm", stop_mode="qm_head", qm_max_age=3))
    assert out == {}


def test_quasimodo_cancelled_when_a_wick_runs_past_head():
    # a single bar wicks above the head (114.5) but CLOSES back low, so no
    # opposite structure forms; only the cancel rule can stop the later entry
    df = _frame()
    spike = 31
    df.iloc[spike, df.columns.get_loc("high")] = 116.0
    _, _, base = _walk(_frame(), Params(entry_mode="qm", stop_mode="qm_head"))
    _, _, out = _walk(df, Params(entry_mode="qm", stop_mode="qm_head"))
    assert len(base) == 1 and min(base) > spike      # without the wick it trades later
    assert out == {}


def test_no_entry_on_the_arming_bar():
    # the arming bar (first close below L1) also wicks up into the left shoulder
    df = _frame()
    arm = _arm_bar(df)
    df.iloc[arm, df.columns.get_loc("high")] = 111.0
    _, _, out = _walk(df, Params(entry_mode="qm", stop_mode="qm_head"))
    assert len(out) == 1 and min(out) > arm


def test_bos_is_not_choch_and_choch_trades_the_flip():
    df = _frame()
    p = Params(entry_mode="choch", stop_mode="structure", max_trades_per_session=5)
    b, s, out = _walk(df, p)
    c = df["close"].to_numpy()
    first_up_break = int(np.argmax(c > 110.5))   # trend unknown -> BOS, not a trade
    flip_down = int(np.argmax((np.arange(len(c)) > 22) & (c < 103.5)))
    assert first_up_break not in out
    assert flip_down in out and out[flip_down].direction == -1
    assert np.isclose(out[flip_down].stop, 114.5 + 0.25 * b.atr[flip_down])   # last swing high


def test_bullish_quasimodo_is_the_mirror():
    keys = [(k, 200 - v) for k, v in KEYS]
    df = _frame(keys)
    _, _, out = _walk(df, Params(entry_mode="qm", stop_mode="qm_head"))
    assert len(out) == 1 and next(iter(out.values())).direction == 1


def test_one_entry_per_setup():
    # the retrace keeps trading at the left shoulder for several bars
    keys = [(0, 100), (8, 110), (14, 104), (22, 114), (30, 100), (38, 110), (45, 110.2)]
    _, _, out = _walk(_frame(keys), Params(entry_mode="qm", stop_mode="qm_head",
                                           max_trades_per_session=5))
    assert len(out) == 1, sorted(out)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
