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

## Analysis rules (fixed 2026-11-12, before the first return was computed)

- Sample: small-cap (< $1B at the event), pharma/biotech SIC (2833-2836, 8731), development
  period only, and the whole measurement window must END on or before 2023-12-31.
- Return: entry at the close of the first trading day after the event date; exit at the close
  20 (primary) and 60 (secondary) of the stock's own trading bars later. A stock that stops
  trading before the exit uses its last traded close (a bankruptcy is already near zero there).
- Excess return = stock return minus XBI return over the same calendar dates.
- Abnormal return = excess return minus the SAME company's average excess return over every
  possible entry day in the development period (its placebo). This removes the fact that
  small biotechs as a group drift against XBI.
- PRIMARY test, one per event type: mean 20-day abnormal return, events with no 8-K within 3
  days, significance clustered by company (t of the company means). |t| >= 2 with the
  predicted sign is a pass for E1, E2, E3, E6; E5 has no predicted sign.
- Secondary (reported, not decisive): 60-day horizon; the all-events sample; medians;
  windows without a split or suspect jump.

## Variant V1: rank test for E1 and E6 (fixed 2026-11-12, before it was run; trial 7 of 10)

Reason: the primary test compares means, and small-biotech returns have extreme outliers, so
it has little power. V1 uses the same sample, window and events and changes only the statistic.

- For each event, take the stock's 20-day excess return (vs XBI) and express it as its
  PERCENTILE within the same company's own distribution of 20-day excess returns over every
  valid entry day in the development period (0 = worst the stock ever did, 1 = best).
- With no effect the expected percentile is 0.50. Statistic: mean over companies of the
  company's average event percentile, minus 0.50; t across companies.
- Pass: t <= -2 (both events predict negative). Sample: no 8-K within 3 days, as before.
- Two tests are run (E1, E6), so one passing by chance has roughly a 5% probability.
