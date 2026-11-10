import numpy as np
import pandas as pd
import pytest

from src import guards as G
from src.load import load_config
from src.strategies.late_day_momentum import late_day_momentum

CFG = load_config()
P = dict(CFG["params"], ldm_stop_points=10.0)


def full_day(last7, base=100.0):
    idx = pd.date_range("2021-03-02 09:30", periods=78, freq="5min", tz="America/New_York")
    rows = [(base, base + 0.5, base - 0.5, base)] * 71 + list(last7)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


FLAT = (102, 102.5, 101.5, 102)
UP = [(101, 102, 100.5, 102)] + [FLAT] * 5 + [(102, 105, 101.5, 104)]          # 15:25 close 102; exit 104


def test_long_when_day_is_up_and_exit_at_1555_close():
    r = late_day_momentum(full_day(UP), {"prior_close": 100.0}, P)
    t = r.trades[0]
    assert (t.side, t.entry_bar, t.entry, t.stop, t.exit_bar, t.exit, t.exit_reason) == \
        ("long", "15:30", 102.0, 92.0, "15:55", 104.0, "time")


def test_short_when_day_is_down_and_stop():
    r = late_day_momentum(full_day(UP), {"prior_close": 110.0}, P)                 # 102 < 110 -> short, stop 112
    assert (r.trades[0].side, r.trades[0].stop, r.trades[0].exit_reason) == ("short", 112.0, "time")
    hit = [(101, 102, 100.5, 102), FLAT, (102, 113, 101.5, 112.5)] + [FLAT] * 4
    t = late_day_momentum(full_day(hit), {"prior_close": 110.0}, P).trades[0]
    assert (t.exit_reason, t.exit, t.exit_bar) == ("stop", 112.0, "15:35")


def test_signal_ignores_bars_after_1525():
    a = late_day_momentum(full_day(UP), {"prior_close": 100.0}, P).trades[0]
    alt = full_day(UP)
    alt.iloc[73:] = [(102, 300, 99, 250)] * 5                                      # rewrite 15:35 onward
    b = late_day_momentum(alt, {"prior_close": 100.0}, P).trades[0]
    assert (a.side, a.entry, a.stop) == (b.side, b.entry, b.stop)


def test_skips():
    assert late_day_momentum(full_day(UP), {"prior_close": float("nan")}, P).status == "no-levels"
    assert late_day_momentum(full_day(UP), {"prior_close": 102.0}, P).status == "no-signal"
    assert late_day_momentum(full_day(UP).iloc[:42], {"prior_close": 100.0}, P).status == "short-day"


def test_trial_budget(tmp_path):
    log = tmp_path / "t.jsonl"
    cfg = dict(CFG, validation=dict(CFG["validation"], trial_budget=dict(CFG["validation"]["trial_budget"], ceiling=2)))
    for k in ("a", "b"):
        G.append_trial({"strategy": k, "params": {}, "instruments": [], "phase": "training", "trial_key": k,
                        "results": {}}, log)
    G.check_budget(cfg, "a", log)                                # an already-logged trial may continue
    with pytest.raises(G.TrialBudgetExhausted):
        G.check_budget(cfg, "c", log)
