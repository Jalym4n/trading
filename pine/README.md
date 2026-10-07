# Pine Scripts

Drop TradingView scripts here, one per file.

Expected from track 1:
- `fvg_lab_v3.pine` : FVG + iFVG indicator, mirrors `backtesting/fvg_lab/strategy.py`
- `amd_power_of_3.pine` : AMD indicator with self-scoring predicted-vs-opposite table

Known divergence: Pine v3 arms on the break bar and checks reclaim from the next
bar; Python allows same-bar reclaim. Intentional, flagged.
