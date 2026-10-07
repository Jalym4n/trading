# Backtesting (fvg_lab)

```
fvg_lab/
  engine.py     event-driven loop, Costs presets, position sizing, Trade records  [in repo]
  data.py       loading, validation, session tagging, synth null                  [missing]
  strategy.py   Params, Bars, FVGStrategy                                         [missing]
  metrics.py    expectancy, PSR, deflated Sharpe, randomization test              [missing]
  trials.py     TrialLog, sweep, purged walk-forward                              [missing]
run_example.py, run_spy.py                                                        [missing]
```

`engine.py` imports from `.strategy`, so it will not run until `strategy.py` is
added. Design rules, TODOs, and the gate before real money are in CLAUDE.md.
