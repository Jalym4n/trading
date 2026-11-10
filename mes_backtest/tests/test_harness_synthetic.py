"""The whole loop on a synthetic instrument: run -> stats -> walk-forward -> scorecard -> trial log.
No real data, no real P&L: phase 5 is the first real read."""
import pandas as pd

from src import backtest as B
from src import guards as G
from src import registry as R
from src import walkforward as W
from src.load import load_config
from synth import dataset, synth_cfg


def test_end_to_end_on_synthetic_instrument(tmp_path, monkeypatch):
    cfg = synth_cfg(load_config())
    data = dataset(n_days=120)
    monkeypatch.setattr(B, "read_processed", lambda inst, root=None: data)
    monkeypatch.setattr(G, "read_processed", lambda inst, root=None: data)
    strat = R.get("or_fib_base")
    tr = W.run_training(cfg, strat, ["SYN"], {}, 1.0, strat.name)
    c = tr["counts"].loc["SYN"]
    n_days = len(data["sessions"].loc["2020-01-02":"2020-03-31"])
    assert c["days"] == n_days and c["roll-skip"] == 1 and c["days"] == sum(c[k] for k in B.DAY_CATEGORIES)
    st = tr["stats"]["SYN"]
    assert st["n_trades"] == c["trades"] and st["weeks"] == n_days * 0.2
    if st["n_trades"]:
        assert tr["stats_doubled_slippage"]["SYN"]["expectancy_net_usd"] < st["expectancy_net_usd"]
    wf = W.run_walk_forward(cfg, strat, ["SYN"], {}, 1.0, strat.name)
    assert wf["n_test_windows"] == 1 and set(wf["windows"][0]) >= {"train", "test", "test_doubled_slippage"}
    rows = G.scorecard(cfg, W.pooled(tr["stats"]), wf, training_doubled=W.pooled(tr["stats_doubled_slippage"]))
    assert [r["gate"] for r in rows][:4] == ["GATE 1", "GATE 2", "GATE 3", "GATE 4"]
    log = tmp_path / "t.jsonl"
    rec = G.append_trial({"strategy": strat.name, "params": cfg["params"], "instruments": ["SYN"], "phase": "both",
                          "trial_key": G.trial_key(strat.name, cfg["params"], ["SYN"]), "results": {}}, log)
    assert rec["trial_line"] == 1 and G.trial_count(log)["distinct"] == 1
    # the harness never produced a window inside the holdout
    for w in wf["windows"]:
        assert w["test_window"][1] < cfg["validation"]["oos_holdout"]["start"]
