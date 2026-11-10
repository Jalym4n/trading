import pandas as pd
import pytest

from src import stats as S
from src.load import load_config

CFG = load_config()


def _log(nets, dates=None, risk=25.0):
    n = len(nets)
    return pd.DataFrame({"instrument": "X", "date": dates or [f"2020-01-{i + 1:02d}" for i in range(n)],
                         "entry_bar": "10:00", "net_usd": nets, "gross_usd": [v + 2.49 for v in nets],
                         "r_net": [v / risk for v in nets], "exit_reason": ["target" if v > 0 else "stop" for v in nets],
                         "risk_usd": risk})


def test_summary_numbers():
    s = S.summarize(_log([50, -25, -25, -25, 100, -25]), CFG, weeks=2.0)
    assert s["n_trades"] == 6 and s["trades_per_week"] == 3.0
    assert s["win_rate"] == 2 / 6 and s["avg_win_usd"] == 75.0 and s["avg_loss_usd"] == -25.0
    assert s["expectancy_net_usd"] == pytest.approx(50 / 6) and s["expectancy_gross_usd"] == pytest.approx(50 / 6 + 2.49)
    assert s["profit_factor"] == 150 / 100
    assert s["max_dd_usd"] == 75.0                      # 50 -> -25: peak 50, trough -25
    assert (s["max_consec_losses"], s["worst_streak_loss_usd"]) == (3, -75.0)
    assert (s["worst_day_usd"], s["best_day_usd"]) == (-25.0, 100.0)
    assert s["best_day_pct_of_target"] == 100 * 100 / 6000
    assert s["trades_to_target"] == pytest.approx(6000 / (50 / 6))


def test_empty_and_no_wins():
    e = S.summarize(_log([]), CFG, weeks=1.0)
    assert e["n_trades"] == 0 and e["expectancy_net_usd"] is None and e["max_dd_usd"] == 0.0
    z = S.summarize(_log([-10, -10]), CFG, weeks=1.0)
    assert z["profit_factor"] == 0.0 and z["trades_to_target"] is None and z["win_rate"] == 0.0


def test_drawdown_uses_time_order_not_row_order():
    df = _log([100, -60, -60], dates=["2020-01-03", "2020-01-01", "2020-01-02"])
    assert S.max_drawdown(S.ordered(df)["net_usd"]) == 120.0      # -60, -60 first, then +100
    h = S.r_histogram(pd.Series([-1.1, 0.2, 2.5]))
    assert (h["-1.5..-1"], h["0..0.5"], h["2..3"], sum(h.values())) == (1, 1, 1, 3)
