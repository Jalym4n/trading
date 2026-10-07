# trading

Pine Scripts, screeners, and backtesting work. Project context and design rules
live in **`CLAUDE.md`**; read it before changing anything.

## Layout

```
pine/                TradingView indicators and strategies (.pine)
screeners/           Screeners (TradingView screens, Python scanners)
  biotech/           ClinicalTrials.gov + EDGAR catalyst screener (track 2)
backtesting/         fvg_lab framework and run harnesses (track 1)
data/                Local market data (git-ignored, never committed)
journal/             Thesis journal entries (one file per ticker/catalyst)
CLAUDE.md            Carry-over context for Claude Code sessions
```

## Conventions

- **Pine:** `//@version=5` or later, one indicator per file, filename in
  `snake_case`. Note any intentional divergence from the Python logic in a
  header comment.
- **Screeners:** write the rule before looking at data. Thresholds live in a
  config block at the top of the file, not inline.
- **Data:** never commit raw market data. Keep CSVs in `data/`.
