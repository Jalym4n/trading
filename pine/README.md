# Pine Scripts

TradingView indicators, Pine v6. Each mirrors logic in `backtesting/fvg_lab/`.

| File | What it is |
|---|---|
| `fvg_lab_v3.pine` | **Current.** FVG + iFVG (inversion), wick/close invalidation, counter |
| `fvg_lab_v2.pine` | Diagnostic build: dimmed dead zones, detection counter |
| `fvg_lab_v1.pine` | Original FVG detector |
| `amd_power_of_3.pine` | AMD / Power of 3, self-scoring predicted vs opposite table |

**Parity rules:** inputs share names with `Params` fields. ATR is
`ta.sma(ta.tr(true), n)[1]` to match `tr.rolling(n).mean().shift(1)`, not
`ta.atr()` (Wilder).

**Known divergence:** the Pine AMD script arms on the break bar and checks
reclaim from the next bar; Python allows same-bar reclaim. Intentional, flagged.
