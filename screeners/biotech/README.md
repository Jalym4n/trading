# biocat: biotech catalyst screener

Screens micro-cap biotechs with an upcoming Phase 2/3 primary completion and
writes one protocol dossier per candidate trial.

**This tool EXTRACTS and PRESENTS. It does not JUDGE.** There is no score, no
rating and no buy/avoid column. Every dossier line is a field value, a count, a
presence/absence, or a mechanical derivation that states its inputs. Ranking is
mechanical only: phase, then months to catalyst, then market cap.

Trade only on **published** material. See the legal boundary in CLAUDE.md.

## Run

```bash
cd screeners/biotech
export BIOCAT_UA="Your Name you@example.com"   # SEC requires a contact UA; the tool refuses to run without one
python3 -m biocat screen --out biocat_out       # ~1 to 3 h cold, minutes when cached
python3 -m biocat journal add --ticker ... (all 11 fields)   # see `journal add -h`
python3 -m biocat journal score --id TICK-YYYYmmddHHMMSS --outcome 1
python3 -m biocat journal report
```

Standard library only (Python 3.10+). Responses are cached under
`biocat_out/.cache` (`--cache-hours`, default 24). Tests: `python3 tests/test_*.py`.

## Outputs

| File | Contents |
|---|---|
| `candidates.csv` | one row per trial that passes stage 4, mechanical sort |
| `dossiers/<TICKER>-<NCT>.md` | sections A to F (below) |
| `identity.csv` | every sponsor, its match status and why (audit trail) |
| `dropped.csv` | trials dropped on a KNOWN out-of-range market cap, ADV or reverse split |
| `integrity_report.md` | rows failing internal-consistency checks, null shares, boolean True shares (below) |
| `watchlist_repeat_failure.csv` | demoted, not dropped: `repeat_failure` with >= 2 prior controlled failures, plus a `note` citing the evidence |
| `excluded_dilution_risk.csv` | KILL: `must_raise_before_catalyst` AND `sub_dollar`, with `kill_dates` |
| `excluded_delisted.csv` | `company_status = likely_delisted_or_acquired` (moved out of `candidates.csv`) |

Dossier sections: **A** primary endpoint verbatim (+ multiplicity terms if >1
primary), **B** bias controls (+ open-label / subjective-vocabulary
observation), **C** enrollment and sites, **D** registration revision history as
a dated timeline, **E** statistical-plan terms with the sentence they occur in,
**F** other trials of the same intervention.

### Derived columns (all facts, each from named fields; none is a score)

| Column | Rule |
|---|---|
| `control_arm` | `yes` / `no` / `unknown`. yes: an arm typed PLACEBO, ACTIVE or SHAM_COMPARATOR or NO_INTERVENTION, or an arm whose interventions are ALL control vocabulary (placebo, vehicle, sham, saline, standard of care, ...), or an arm with no interventions and a control label. unknown: armGroups absent, or untyped arms and no control found. Never inferred from missing data |
| `control_arm_types` | raw `armGroups[].type` values, `|`-joined; the dossier also prints the rule that fired |
| `burn_status` | burning / cash_positive / stale / no_data / **burn_implausible**: average quarterly burn < $250k with cash > $10M, or runway > 40 quarters. Burn and runway are then withheld; `burn_quarters` in the dossier still shows the inputs |
| `days_since_cash_asof`, `cash_today_est`, `cash_to_mcap_est` | `cash_today_est = cash_usd - burn_q_usd x days/91.25`, an ESTIMATE (linear burn, no financing since the balance date); empty unless burning |
| `total_debt_usd`, `debt_asof`, `has_convertible` | sum of LongTermDebtNoncurrent, LongTermDebtCurrent, ConvertibleNotesPayable(Current), OperatingLeaseLiabilityNoncurrent at the cash date (XBRL companyfacts). Empty when no tag exists (unknown, not zero). Components print in the dossier, so a tag that double-counts another is visible |
| `true_market_cap`, `reported_market_cap`, `pre_funded_warrants`, `share_count_source` | true = (dei cover common + pre-funded warrants) x price, and the $50M-$400M filter uses it; reported = common only. Pre-funded count: XBRL warrant classes at the balance date, else a filing-text sentence stating the CURRENT balance (cover-page note, "as of DATE, N ... remained outstanding", reserved-shares table row). Issuance and exercise sentences never count |
| `market_cap_confidence` | `verified`: count reconciled to a filing (XBRL classes, a stated balance, or no pre-funded class AND no mention in the filing). `unverified`: price-feed cap, an EPS weighted average, or the filing mentions pre-funded warrants without stating a balance. On unverified rows `cash_to_mcap_est` and `ev_est` are withheld |
| `reverse_split_date`, `reverse_split_ratio`, `months_since_reverse_split`, `reverse_split_source`, `reverse_split_cover_check` | most recent split in 36 months: 8-K Item 5.03 text (a single 1-for-N ratio AND an effect sentence; the date is the stated effective date) merged with the price feed; the cover-page share count step-down is printed as corroboration. The 24-month rule is enforced on `reverse_split_date` |
| `ev_est` | `market_cap + total_debt_usd - cash_today_est`; empty if any input is |
| `company_status` | `likely_delisted_or_acquired` when market cap is empty AND (newest financial filing and newest filing of any type both > 270 days old, OR a Form 15/25 filed on or after the newest financial filing); otherwise `active` |
| `shelf_count`, `atm_count`, `days_since_latest_atm_filing` | counts of filings mentioning a shelf / an ATM in 3 years. The old shelf-AND-ATM boolean was True on 94% of rows and was removed |
| `n_endpoint_changes`, `endpoint_changes` | primary outcomes compared after lowercasing and stripping punctuation and whitespace; each change as JSON `{version, date, from, to, kind, status_at_change}` with the before/after text |
| `is_efficacy_scale` | enrollment >= 20 |
| `patients_per_site`, `site_list_maturity` | enrollment / sites; `new` when the registration has <= 2 versions (site lists are often not filled in yet) |
| `is_single_arm_open_label` | allocation NA or NON_RANDOMIZED, control_arm != yes, masking NONE |
| `enrollment_stale` | enrollment type ESTIMATED and start date > 24 months ago |
| `pcd_slip_months` | first to latest primary completion date; month precision when either date has no day, so 2027-07-14 to 2027-07 is 0, not -0.4 |

Rows are never dropped by these columns; filtering on them is the reader's call,
except the three routing rules above (delisted, dilution kill, repeat-failure
watchlist), which move a row to its own file.

### Intervention track record (dossier section G)

| Column | Rule |
|---|---|
| `molecule_keys` | candidate's experimental interventions (or every non-control intervention when the drug arms are mistyped) normalised: dose, route, frequency, salt and arm labels stripped, codes joined (`EVO-756 100 mg` -> `evo756`), plus parenthetical codes and code-like `otherNames`. A key with >= 200 registered trials is a backbone (pembrolizumab) and is not tracked |
| `prior_trials_total` | trials of that molecule, any sponsor, status COMPLETED / TERMINATED / WITHDRAWN / SUSPENDED, started before the candidate |
| `prior_controlled_*` | prior trials with a comparator arm, and the outcome of each AS STATED BY A SOURCE: whyStopped futility; a posted CT.gov primary analysis (p < 0.05 met, else missed); or the sponsor's own 8-K press release sentence naming the molecule, where the indication named closest before "did / did not meet the primary endpoint" is that trial's. One sentence is attributed to one trial: ties must be broken by the NCT ID, acronym or protocol number. Otherwise `unknown`, counted, never guessed |
| `prior_trials_with_clinical_primary`, `prior_trials_safety_primary_only`, `largest_prior_clinical_primary_n` | from each prior trial's `primaryOutcomes[]` classes |
| `molecule_track_record` | naive (no prior controlled trial) / validated / mixed / repeat_failure / controlled_outcomes_unknown / **untested** (>= 3 prior trials, none with a clinical primary; overrides naive, unknown and validated, with `track_record_warning`) |
| `candidate_evidence_grade` | controlled_masked (comparator + >= double masking) / controlled_open / single_arm (no comparator arm, includes dose comparisons) / unknown |

Validation (`tests/live_evmn.py`, `tests/live_cgtx.py`): EVO756 has 2 failures,
CSU (8-K 2026-06-29) and AD (8-K 2026-09-09), so `repeat_failure` and the
watchlist; EVO301 "met primary endpoint" in the same releases is a different
molecule and is not counted. CT1812 has 11 prior trials, 0 clinical primaries,
so `untested`.

### Primary outcome class (candidate trial)

`primary_outcome_classes` from `primaryOutcomes[]` ONLY: clinical (a named
scale or patient outcome) / safety / pk_pd / biomarker / unclassified.
`primary_outcome_is_clinical`, `primary_is_safety_only`, verbatim
`primary_outcome_text`. Replaces `is_efficacy_scale` (True on 97% of rows).
SHINE NCT03507790 and SHIMMER NCT05225415 are safety-only; START NCT05531656
is clinical (CDR-SB).

### Cash, runway and dilution

| Column | Rule |
|---|---|
| `total_liquid_cash` | cash and equivalents + short-term + long-term investments, one tag per category (a holding tagged twice counts once); restricted cash reported, excluded. EVMN 2026-06-30: $288.0M (the old figure, $200.8M, omitted long-term investments) |
| `cash_confidence` | partial: computed and company-stated runway differ by > 2 quarters; single_tag: only a cash tag; else verified |
| `runway_q` | min(`runway_q_computed`, `runway_q_stated`). Stated = the company's own sentence in the latest 10-Q/10-K or earnings release; its end is the START of the stated period ("into the fourth quarter of 2027" -> 2027-10-01). "At least ..." statements are floors and never cap. `runway_source` stated / computed / agreed; `runway_gap_flag` when computed exceeds stated by > 2 quarters. The kill-screen (`funded_through`) uses `runway_q` |
| `readout_est_date` | primary completion + `--readout-lag-months` (default 3: database lock and topline analysis follow last data collection). A stated convention; set 0 to use primary completion |
| `months_runway_minus_months_to_catalyst`, `must_raise_before_catalyst` | runway left from today minus months to the estimated readout; must raise when <= 2 |
| `dilution_at_current_price_pct` | shortfall to readout + 6 months at the implied burn (total cash / runway months) as % of market cap |
| `share_price`, `sub_dollar`, `nasdaq_tier`, `prior_bid_price_deficiency`, `reverse_split_last_36mo` | price feed; 8-K Item 3.01 notices naming the bid price (36 months); SEC split events |
| `press_releases`, `headline_price_divergence`, `divergence_events` | last 8 exhibit-99 releases with the 1-day return of the first session that could react (after 16:00 ET -> next day); flagged when a headline with positive / aligns / achieves / successfully / advances / granted is followed by < -15% |

CGTX: runway computed 7.91 q, stated 5.02 q (into Q4 2027), used 5.02; START
primary completion 2027-05-31 + 3 months, margin 1.0 month at $0.877, so it
is a dilution KILL. With `--readout-lag-months 0` the margin is 4.0 months and
it is not. 2026-06-24 "Aligns on Key Aspects of Pivotal Study" -38.18%.

### integrity_report.md

Lists every row that fails a check, with the values: masked but control_arm
!= yes; randomized but control_arm = no; runway > 200 or burn < $250k with
cash > $10M or burn_implausible; enrollment < 20 on P2, P3 or P2/P3; cash older
than 150 days; likely delisted or acquired; primary completion moved earlier;
<= 1 site with >= 50 patients. Then fields null on > 25% of rows, the True
share of each boolean column (flagged below 5% or above 80% as low
discrimination), and a counts table.

Masking without a control arm is possible: a blinded dose or schedule
comparison (DVAX NCT07207408 vaccine regimens; UPB NCT06966479 double-dummy
extension). The report lists such rows to inspect; it does not change them.

## Pipeline

1. **Trials.** CT.gov v2: Phase 2 or 3, industry lead sponsor, primary
   completion today+3 to +12 months, active statuses.
2. **Identity.** Sponsor name to (CIK, ticker). Exact equality of normalised
   names, never fuzzy. `company_tickers.json` first, then EDGAR full-text search
   display names. Ambiguous is a rejection. Every accepted match must then pass
   an **SIC gate**: the filer's SEC SIC code must be healthcare (2833-2836,
   3826, 3841-3845, 3851, 5047, 5122, 80xx, 8731-8734).
3. **Financials.** XBRL companyfacts: cash + short-term investments at the same
   date, operating cash flow converted from YTD to quarterly, runway in
   quarters vs `months_out / 3 + 2`. Cash-generating firms get
   `burn_status = cash_positive` and an empty runway, never infinity. S-3 shelf
   and ATM counts over 3 years via full-text search.
4. **Size.** Market cap $50M to $400M, ADV > $300k, no reverse split in 24
   months. A feed failure leaves the field empty; a name is never dropped for
   missing data.
5. **Dossiers.**

## APIs: all free, none needs a key

| Source | Route | Status |
|---|---|---|
| ClinicalTrials.gov | `/api/v2/studies` | official, documented |
| CT.gov revision history | `/api/int/studies/{nct}/history[/{N}]` | **undocumented** web-UI API, needs an `ncbi_sid` cookie |
| SEC tickers | `www.sec.gov/files/company_tickers.json` | official |
| SEC full-text search | `efts.sec.gov/LATEST/search-index` | official, 10 req/s cap |
| SEC XBRL | `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | official |
| SEC submissions (SIC) | `data.sec.gov/submissions/CIK##########.json` | official |
| Yahoo chart (price, volume, splits) | `query1.finance.yahoo.com/v8/finance/chart/{t}` | **unofficial** |
| Nasdaq historical (fallback) | `api.nasdaq.com/api/quote/{t}/historical` | **unofficial**, no splits |
| Nasdaq summary (market cap) | `api.nasdaq.com/api/quote/{t}/summary` | **unofficial**; used only when XBRL has no share count |

Unofficial routes can change or block without notice; when they fail the
fields go empty and the dossier says so.

### Revision history route (verified 2026-10-12)

`/api/v2/studies/{nct}/history` is a 404 and the public spec (`/api/oas/v2`)
has no history route. The web UI's History tab calls:

```bash
curl -s -c jar https://clinicaltrials.gov/api/v2/version >/dev/null      # sets ncbi_sid
curl -s -b jar https://clinicaltrials.gov/api/int/studies/NCT03548935/history
#  -> {"changes":[{"version":0,"date":"2018-06-07","status":"RECRUITING","moduleLabels":[...]}, ...]}  (82 versions)
curl -s -b jar https://clinicaltrials.gov/api/int/studies/NCT03548935/history/0
#  -> {"studyVersion":0,"study":{"protocolSection":{...}}}   full record as first registered
curl -s -b jar "https://clinicaltrials.gov/api/int/studies/NCT03548935/history/5?patchToVersion=6"
#  -> adds an RFC 6902 "patch" between the two versions
```

Without the cookie: HTTP 403. Versions are 0-based. Historical text is
HTML-entity-escaped and is unescaped before comparison.

## Known limits

- **Identity coverage is ~25% of sponsors**, by design. Private firms,
  subsidiaries whose names differ from the listed parent, and non-US issuers
  are missed rather than guessed. Subsidiary-to-parent joins do occur when the
  normalised names are equal (e.g. Grifols Therapeutics LLC to Grifols SA).
  Audit `identity.csv` before relying on a row.
- **Name collisions.** The first live run (2026-10-12) produced four false
  joins: Vertex Pharmaceuticals to Vertex Inc (VERX), Thryv Therapeutics to
  Thryv Holdings, Polaris Group to Polaris Inc, Vantage Biosciences to Vantage
  Corp. Fixed by stripping SEC state tags (`/ MA`) and by the SIC gate; both
  have regression tests. A collision between two healthcare firms would still
  pass the SIC gate.
- **Market cap** = `dei:EntityCommonStockSharesOutstanding` x price. 20-F
  filers often omit that fact; their cap comes from the Nasdaq summary instead
  (never from IFRS share counts, because the ADS ratio is unknown). The source
  is printed in each dossier as `market_cap_source`.
- **Cash** is the first non-zero of `CashAndCashEquivalentsAtCarryingValue`,
  `Cash`, `CashCashEquivalentsRestrictedCash...` at the latest balance-sheet
  date: some filers tag the first as 0 every quarter.
- **Burn** is the mean of the last two quarters, and each quarter is printed
  (`burn_quarters`). One inflow quarter can make runway very large (ZURA,
  2026-10-12: -18.9M then +18.9M, runway 10,302 quarters; VTVT +9.2M then
  -11.4M, 75 quarters). Both are now `burn_implausible` with no runway.
- **No quote, stale filings.** Acquired or delisted issuers still sponsor
  trials under their old names. With no price their market cap is unknown, so
  they are kept (never drop on missing data) and labelled
  `company_status = likely_delisted_or_acquired`. Run of 2026-10-13: 14 rows,
  7 tickers (ITCI, BPMC, FOLD, DAWN, APLS, CNTA, DVAX), each with a Form 15-12G.
- **Market cap includes pre-funded warrants** (`true_market_cap`; `reported_market_cap`
  is the common-only figure). Pre-funded warrants are shares in all but name (strike
  $0.0001 to $0.11) and companies count them in basic EPS. The count comes from
  the latest 10-Q/10-K XBRL instance at its balance-sheet date: a class is
  pre-funded if its member name says so, or, on the class axis that reconciles
  to the filing's total warrants, its strike is <= $0.15. Classes reported
  outside the total (IRD) are added once; facts on other axes are events or
  duplicates and are ignored (ELOX). Tagged only at an old date (NKTX), or no
  instance (20-F/40-F filers): count unknown, `market_cap` is common only, and
  integrity_report.md lists the row. Common shares are as of the cover date,
  warrants as of the balance date; an exercise in between is counted twice.
  Run of 2026-10-13: ZURA ($383M to $500M) and IRD ($387M to $461M) now exceed
  the $400M cap and drop out; ANNX, KRRO and PALI rise but stay in. 31 of 61
  rows are `unverified`: most mention pre-funded warrants without stating a
  balance (VTVT, BMEA, RNTX, EYPT), the rest have no readable 10-Q/10-K.
  ELOX end to end: `tests/live_elox.py` asserts 7,383,919 pre-funded, ~$159M,
  split 2026-05-29 1-for-11, excluded.
- Stage 1 returned 1,494 trials, not ~3,000: the industry-sponsor and
  primary-completion window filters are strict.
- Some issuers have no ticker on SEC and are
  rejected as `no_ticker`.
- The history route is internal; if CT.gov changes it, section D reports
  "Not available" and the rest of the dossier is unaffected.
