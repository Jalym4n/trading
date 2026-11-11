# Registry change events (fixed 2026-11-11, before any price data was loaded)

Universe: industry-sponsored interventional Phase 2 / Phase 3 trials on ClinicalTrials.gov
whose lead sponsor was a listed company at the time. Published registry data only.

An event is a difference between two consecutive registry versions of one trial.
**Event date = the later version's public posting date** (`lastUpdatePostDate`); returns are
measured from the next trading day's close. "Open" means the earlier version's status is
RECRUITING, ACTIVE_NOT_RECRUITING, ENROLLING_BY_INVITATION or NOT_YET_RECRUITING.

| ID | Event | Exact rule | Predicted direction |
|---|---|---|---|
| E1 | completion-date slip | trial open; earlier primary completion date is ESTIMATED; new date is later by >= 3.0 months | negative |
| E2 | completion-date pull-in | same, new date earlier by >= 3.0 months | positive |
| E3 | endpoint swap | earlier status RECRUITING / ACTIVE_NOT_RECRUITING / ENROLLING_BY_INVITATION (enrolment started); at least one earlier primary outcome measure has no counterpart among the new primary measures (best normalised-text similarity < 0.60). Rewording (>= 0.60) and purely added outcomes do not count | negative |
| E4 | enrollment cut | trial open; enrollment ESTIMATED before and after; new count <= 75% of the earlier count | negative |
| E5 | enrollment increase | trial open; enrollment ESTIMATED before and after; new count >= 125% of the earlier count | none (measured, no prediction) |
| E6 | terminated / withdrawn / suspended | status becomes TERMINATED, WITHDRAWN or SUSPENDED from any other status | negative (control: usually announced) |

Outcome: stock return minus the biotech benchmark over the next 20 and 60 trading days.
Events within 2 trading days of a company 8-K are reported separately (already public news).
Disproof, per event: mean excess return not distinguishable from zero (|t| < 2), or sign
differs between development (through 2023-12-31) and holdout (2024-01-01 to 2025-12-31).
Fewer than 100 development events: "not testable". Trial budget: 10 (E1-E6 + 4 variants).
Text similarity = difflib.SequenceMatcher ratio on lowercase, punctuation-stripped measure text.
