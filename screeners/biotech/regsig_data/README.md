Backup of the public registry data built by `regsig` (fetched 2026-11-11). No price data here.

- `universe.csv.gz`: 34,195 industry Phase 2/3 trials updated since 2018-05-01 (ClinicalTrials.gov).
- `listed_sponsors.csv`: sponsors matched to SEC periodic filers 2018Q2-2025Q4.
- `ticker_map.csv`: sponsor -> SEC entity -> trading symbol, with the price-bar window found.
- `snapshots.jsonl.gz`: per-version registry snapshots for 6,031 trials of 933 sponsors.
- `events.jsonl.gz`: events E1-E6 derived from them (rules in `../regsig/EVENTS.md`).

Restore: gunzip into `../regsig_out/`.
