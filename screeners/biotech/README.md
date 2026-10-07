# Biotech catalyst screener

**Build target:** join ClinicalTrials.gov upcoming readouts to the latest EDGAR
10-Q, compute cash runway, flag S-3 shelf / ATM presence, output a weekly ranked
list.

| Filter | Threshold |
|---|---|
| Market cap | $50M to $400M |
| Cash runway | > catalyst + 2 quarters |
| Catalyst window | 3 to 12 months out |
| Reverse split | none in 24 months |
| Avg daily volume | > $300k |

Trade only on **published** material. See the legal boundary in CLAUDE.md.
