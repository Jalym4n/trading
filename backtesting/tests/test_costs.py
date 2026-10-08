"""Cost model checks. Run from backtesting/:  python -m pytest tests  (or python tests/test_costs.py)"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fvg_lab import data  # noqa: E402
from fvg_lab.engine import Costs, run  # noqa: E402
from fvg_lab.strategy import Params  # noqa: E402


def test_fixed_presets_unchanged():
    # proportional terms default to zero, so fixed-cost math is untouched
    c = Costs.mes()
    assert c.edge(5000.0) == (1.0 / 2 + 0.5) * 0.25
    assert c.commission(5000.0, 3) == 0.52 * 3
    assert c.double().commission(5000.0, 3) == 0.52 * 3 * 2


def test_crypto_fill_and_commission_by_hand():
    c = Costs.crypto_spot(fee_pct=0.004, spread_bps=1.0, slippage_bps=2.0)
    px = 60_000.0
    # half of 1bp spread + 2bp slippage = 2.5bp of price
    assert np.isclose(c.edge(px), px * 2.5e-4)
    assert np.isclose(c.fill_price(px, +1, opening=True), px * (1 + 2.5e-4))
    assert np.isclose(c.fill_price(px, +1, opening=False), px * (1 - 2.5e-4))
    assert np.isclose(c.fill_price(px, -1, opening=True), px * (1 - 2.5e-4))
    # 0.01 BTC at 60k = $600 notional, 0.40% = $2.40
    assert np.isclose(c.commission(px, 0.01), 2.40)
    d = c.double()
    assert np.isclose(d.edge(px), px * 5e-4)
    assert np.isclose(d.commission(px, 0.01), 4.80)


def test_crypto_engine_pnl_matches_hand_calc():
    # 24/7 synthetic BTC-shaped data, UTC-day sessions
    rng = np.random.default_rng(1)
    idx = pd.date_range("2024-01-01", periods=288 * 60, freq="5min", tz="UTC")
    close = 60_000 * np.exp(np.cumsum(rng.normal(0, 0.0015, len(idx))))
    prev = np.concatenate([[close[0]], close[:-1]])
    wig = np.abs(rng.normal(0, 0.0015, len(idx))) * close
    df = pd.DataFrame(
        {"open": prev, "high": np.maximum(prev, close) + wig,
         "low": np.minimum(prev, close) - wig, "close": close, "volume": 1.0},
        index=idx,
    ).round(2)
    data.validate(df)
    df = data.add_session(df, "00:00", "24:00")
    assert len(df) == len(idx)                       # nothing dropped
    assert df["bars_in_session"].eq(288).all()       # one UTC day per session

    costs = Costs.crypto_spot()
    res = run(df, Params(), costs, starting_equity=1_000, risk_per_trade=0.01,
              allow_fractional=True)
    t = res["trades"]
    assert len(t) > 0
    # pnl = gross on the (already adverse) fills, minus 0.40% of notional per side
    fees = 0.004 * t["qty"] * (t["entry"] + t["exit"])
    gross = (t["exit"] - t["entry"]) * t["direction"] * t["qty"]
    assert np.allclose(t["pnl_dollars"], gross - fees)

    # doubled costs must be strictly worse in aggregate
    res2 = run(df, Params(), costs.double(), starting_equity=1_000,
               risk_per_trade=0.01, allow_fractional=True)
    assert res2["trades"]["pnl_dollars"].sum() < t["pnl_dollars"].sum()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
