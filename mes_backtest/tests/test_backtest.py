"""Unit tests for src/backtest.py plumbing. Run: python -m pytest -q"""
import pytest

from src.backtest import apply_costs, clamp_window, development_end, instrument_spec
from src.load import load_config

CFG = load_config()
P = CFG["params"]


def _trade(side="long", entry=100.0, exit_=110.0, risk=5.0, reason="target"):
    return {"side": side, "entry": entry, "exit": exit_, "risk_pts": risk, "exit_reason": reason}


def test_costs_use_the_instruments_point_value_and_tick():
    mes, mnq = instrument_spec(CFG, "MES"), instrument_spec(CFG, "MNQ")
    a = apply_costs(_trade(), mes, P)                    # +10 pts, limit target: slippage on entry only
    assert (a["gross_pts"], a["slippage_pts"], a["net_pts"]) == (10.0, 0.25, 9.75)
    assert a["gross_usd"] == 50.0 and a["net_usd"] == pytest.approx(9.75 * 5 - 1.24)
    b = apply_costs(_trade(), mnq, P)
    assert b["gross_usd"] == 20.0 and b["net_usd"] == pytest.approx(9.75 * 2 - 1.24)
    assert a["risk_usd"] == 25.0 and b["risk_usd"] == 10.0


def test_market_exits_pay_slippage_twice_and_doubled_slippage_scales():
    mes = instrument_spec(CFG, "MES")
    stop = apply_costs(_trade(exit_=95.0, reason="stop"), mes, P)
    assert (stop["gross_pts"], stop["slippage_pts"], stop["net_pts"]) == (-5.0, 0.5, -5.5)
    assert stop["r_net"] == pytest.approx((-5.5 * 5 - 1.24) / 25.0)
    time_exit = apply_costs(_trade(exit_=101.0, reason="time"), mes, P)
    assert time_exit["slippage_pts"] == 0.5
    doubled = apply_costs(_trade(exit_=95.0, reason="stop"), mes, P, slippage_mult=2.0)
    assert doubled["slippage_pts"] == 1.0
    short = apply_costs(_trade(side="short", entry=100.0, exit_=92.0), mes, P)
    assert short["gross_pts"] == 8.0


def test_unknown_instrument_is_an_error():
    with pytest.raises(KeyError):
        instrument_spec(CFG, "ES")


def test_development_window_never_touches_the_holdout():
    assert development_end(CFG).isoformat() == "2025-12-31"
    s, e, note = clamp_window(CFG, "2020-01-01", None)
    assert (s, e, note) == ("2020-01-01", "2025-12-31", None)
    s, e, note = clamp_window(CFG, "2024-01-01", "2026-06-30")       # end inside the holdout: clamped, said so
    assert e == "2025-12-31" and "clamped" in note
    with pytest.raises(ValueError):
        clamp_window(CFG, "2026-02-01", "2026-03-01")                 # start inside the holdout: refused
    s, e, _ = clamp_window(CFG, "2026-01-01", "2026-11-06", _allow_holdout=True)   # the guarded path only
    assert (s, e) == ("2026-01-01", "2026-11-06")
