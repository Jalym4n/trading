"""Trial log (append-only, hash-chained), holdout firewall, regime split, scorecard, windows."""
import json

import numpy as np
import pandas as pd
import pytest

from src import backtest as B
from src import guards as G
from src import walkforward as W
from src.load import load_config

CFG = load_config()


def test_trial_log_is_append_only_and_chained(tmp_path):
    log = tmp_path / "trial_log.jsonl"
    a = G.append_trial({"strategy": "s1", "params": {"x": 1}, "instruments": ["MES"], "phase": "training",
                        "trial_key": G.trial_key("s1", {"x": 1}, ["MES"]), "results": {}}, log)
    b = G.append_trial({"strategy": "s1", "params": {"x": 1}, "instruments": ["MES"], "phase": "walkforward",
                        "trial_key": G.trial_key("s1", {"x": 1}, ["MES"]), "results": {}}, log)
    c = G.append_trial({"strategy": "s2", "params": {}, "instruments": ["MES"], "phase": "training",
                        "trial_key": G.trial_key("s2", {}, ["MES"]), "results": {}}, log)
    assert (a["trial_line"], b["trial_line"], c["trial_line"]) == (1, 2, 3) and b["prev_hash"] == a["hash"]
    assert G.trial_count(log) == {"lines": 3, "distinct": 2, "strategies": 2}     # same params re-run = 1 trial
    assert G.reminder(log).startswith("2 strategies/params tested so far. At p<0.05, expect ~0.1 false positives")
    G.verify_chain(log)
    # tampering: edit a value in line 2 -> the chain breaks and appends are refused
    lines = log.read_text().splitlines()
    rec = json.loads(lines[1]); rec["phase"] = "HOLDOUT"; lines[1] = json.dumps(rec, sort_keys=True)
    log.write_text("\n".join(lines) + "\n")
    with pytest.raises(RuntimeError):
        G.append_trial({"strategy": "s3", "params": {}, "instruments": [], "phase": "training", "trial_key": "k",
                        "results": {}}, log)
    # removing the LAST line leaves a valid shorter chain: the chain cannot see that on its own,
    # which is why the log is committed to git (the diff shows a shrinking file)
    log.write_text("\n".join(lines[:2]) + "\n")
    with pytest.raises(RuntimeError):
        G.verify_chain(log)                  # line 2 is still the edited one: still broken
    log.write_text(lines[0] + "\n")
    G.verify_chain(log)                      # a one-line valid prefix verifies; git is the backstop


def test_holdout_firewall(tmp_path, monkeypatch):
    ledger, log = tmp_path / "ledger.jsonl", tmp_path / "trials.jsonl"
    calls = []

    def fake_run(cfg, strategy, instruments, s, e, params, mult, name, _allow_holdout=False):
        calls.append((s, e, _allow_holdout))
        tl = pd.DataFrame([{"instrument": "MES", "date": "2026-02-02", "entry_bar": "10:00", "side": "long",
                            "net_usd": 10.0, "gross_usd": 12.49, "r_net": 0.4, "exit_reason": "target",
                            "risk_usd": 25.0}])
        dl = pd.DataFrame([{"instrument": "MES", "date": "2026-02-02", "category": "traded", "n_trades": 1,
                            "n_risk_skips": 0}])
        return B.RunResult(name, ["MES"], (s, e), dict(cfg["params"]), tl, dl)
    monkeypatch.setattr(B, "run", fake_run)
    strat = type("S", (), {"name": "finalist"})()
    with pytest.raises(G.HoldoutRefused):                                  # no confirm
        G.run_final_holdout(CFG, strat, ledger=ledger, trial_log=log)
    with pytest.raises(G.HoldoutRefused):                                  # confirm but no real reason
        G.run_final_holdout(CFG, strat, confirm=True, reason="test", ledger=ledger, trial_log=log)
    assert calls == [] and not ledger.exists()
    out = G.run_final_holdout(CFG, strat, confirm=True, reason="single finalist after all walk-forward windows",
                              ledger=ledger, trial_log=log, _print=lambda *a, **k: None)
    assert calls == [("2026-01-01", "2026-11-06", True)]                   # the only call that may enter 2026
    assert out["stats"]["MES"]["n_trades"] == 1 and len(G.read_ledger(ledger)) == 1
    assert G.read_trials(log)[-1]["phase"] == "HOLDOUT" and G.trial_count(log)["distinct"] == 0   # not a search trial
    with pytest.raises(G.HoldoutRefused):                                  # second time, same or any strategy
        G.run_final_holdout(CFG, strat, confirm=True, reason="trying again after a tweak, please",
                            ledger=ledger, trial_log=log)
    other = type("S", (), {"name": "another"})()
    with pytest.raises(G.HoldoutRefused):
        G.run_final_holdout(CFG, other, confirm=True, reason="a different finalist this time, really",
                            ledger=ledger, trial_log=log)
    assert len(calls) == 1


def test_windows_from_config():
    assert W.training_window(CFG) == ("2020-01-01", "2021-12-31")
    w = W.windows(CFG)
    assert [x["test"] for x in w] == [(f"{y}-01-01", f"{y}-12-31") for y in (2022, 2023, 2024, 2025)]
    assert w[0]["train"] == ("2020-01-01", "2021-12-31") and w[-1]["train"] == ("2023-01-01", "2024-12-31")
    assert all(x["test"][1] <= B.development_end(CFG).isoformat() for x in w)


def test_regime_terciles_are_lagged():
    rng = np.random.default_rng(0)
    n = 120
    ret = rng.normal(0, 1, n) * np.linspace(0.002, 0.03, n)          # volatility rises steadily through time
    close = pd.Series(100 * np.exp(np.cumsum(ret)), index=pd.bdate_range("2021-01-04", periods=n))
    r = G.regime_from_closes(close, 10)
    assert r["regime"].iloc[:10].isna().all()                        # no tag until a full LAGGED window exists
    counts = r["regime"].value_counts()
    assert set(counts.index) == {"low", "mid", "high"} and counts.max() - counts.min() <= 2   # terciles
    assert r["regime"].iloc[-15:-1].eq("high").all() and r["regime"].iloc[11:25].eq("low").all()
    # lag: the tag on day t does not use day t's own close
    bumped = close.copy(); bumped.iloc[60] *= 1.5
    assert G.regime_from_closes(bumped, 10)["realized_vol"].iloc[60] == r["realized_vol"].iloc[60]


def test_scorecard_fails_gate1_and_marks_unmeasured_gates_na():
    tr = {"n_trades": 127, "trades_per_week": 1.26, "max_dd_usd": 300.0, "expectancy_net_usd": 4.0,
          "best_day_pct_of_target": 12.0, "trades_to_target": 312.0, "worst_streak_loss_usd": -150.0,
          "max_consec_losses": 6}
    rows = G.scorecard(CFG, tr, None, training_doubled={"expectancy_net_usd": 1.0})
    v = {r["gate"] + " " + r["rule"][:12]: r["verdict"] for r in rows}
    assert [r["verdict"] for r in rows if r["gate"] == "GATE 1"] == ["FAIL"]
    assert [r["verdict"] for r in rows if r["gate"] == "GATE 2"] == ["PASS"]
    assert [r["verdict"] for r in rows if r["gate"] in ("GATE 3", "GATE 4")] == ["n/a", "n/a"]
    assert "NOT VIABLE: fails GATE 1" in G.format_scorecard(rows)
    wf = {"test_pooled": dict(tr, trades_per_week=3.4), "positive_test_windows": 3, "n_test_windows": 4, "t_pooled": 3.4,
          "test_pooled_doubled_slippage": {"expectancy_net_usd": -0.5}}
    rows = G.scorecard(CFG, tr, wf)
    assert [r["verdict"] for r in rows if r["gate"].startswith("GATE")] == ["PASS", "PASS", "PASS", "n/a"]
    assert [r["verdict"] for r in rows if "doubled" in r["rule"]] == ["FAIL"]
    assert "pending: GATE 4" in G.format_scorecard(rows)


def test_negative_training_expectancy_fails_gate3():
    tr = {"n_trades": 127, "trades_per_week": 1.26, "max_dd_usd": 883.0, "expectancy_net_usd": -6.33,
          "best_day_pct_of_target": 8.8, "trades_to_target": None, "worst_streak_loss_usd": -262.0,
          "max_consec_losses": 7}
    rows = G.scorecard(CFG, tr, None, training_doubled={"expectancy_net_usd": -8.27})
    assert [r["verdict"] for r in rows if r["gate"] == "GATE 3"] == ["FAIL"]
    assert [r["verdict"] for r in rows if "weeks to" in r["rule"]] == ["FAIL"]
    assert [r["verdict"] for r in rows if r["gate"] == "GATE 2"] == ["PASS"]          # $883 < $1,800 on the 100K
    assert "fails GATE 1, GATE 3" in G.format_scorecard(rows)


def test_rescore_reads_only(tmp_path):
    log = tmp_path / "t.jsonl"
    G.append_trial({"strategy": "s", "params": {}, "instruments": ["MES"], "phase": "training", "trial_key": "k",
                    "results": {"training": {"MES": {"n_trades": 481, "trades_per_week": 4.76, "max_dd_usd": 2263.09,
                                                     "expectancy_net_usd": -2.7, "best_day_usd": 335.0,
                                                     "worst_streak_loss_usd": -448.0, "max_consec_losses": 10}}}}, log)
    before = log.read_text()
    r = G.rescore(CFG, log)
    assert log.read_text() == before and G.trial_count(log)["distinct"] == 1
    assert [x["verdict"] for x in r[0]["scorecard"] if x["gate"] == "GATE 2"] == ["FAIL"]   # $2,263 > $1,800


def test_gate3_needs_t_of_3_under_the_raised_budget():
    tr = {"n_trades": 400, "trades_per_week": 4.0, "max_dd_usd": 900.0, "expectancy_net_usd": 5.0,
          "best_day_pct_of_target": 5.0, "trades_to_target": 1200.0, "worst_streak_loss_usd": -300.0, "max_consec_losses": 8}
    weak = {"test_pooled": tr, "positive_test_windows": 3, "n_test_windows": 4, "t_pooled": 1.4,
            "test_pooled_doubled_slippage": {"expectancy_net_usd": 2.0}}
    assert [r["verdict"] for r in G.scorecard(CFG, tr, weak) if r["gate"] == "GATE 3"] == ["FAIL"]
    assert [r["verdict"] for r in G.scorecard(CFG, tr, dict(weak, t_pooled=3.2)) if r["gate"] == "GATE 3"] == ["PASS"]
