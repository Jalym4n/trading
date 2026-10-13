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
| `candidates.csv` | SURVIVORS only: rows that pass stage 4 and every gate below, mechanical sort |
| `dossiers/<TICKER>-<NCT>.md` | sections A to G (below), for every row in every file |
| `identity.csv` | every sponsor, its match status and why (audit trail) |
| `dropped.csv` | trials dropped on a KNOWN out-of-range market cap, ADV or reverse split |
| `integrity_report.md` | consistency checks, the two hard invariants, the funnel, outcomes needing a human read, unresolved pre-funded mentions, success-reaction rows (below) |
| `excluded_delisted.csv` | `company_status = delisted`: a Form 25 / 25-NSE filed on or after the newest periodic report, or no 10-K/10-Q/20-F for > 270 days (455 for an annual-only 20-F/40-F filer) |
| `excluded_underfunded.csv` | `funded_through = False`, with `runway_q`, `runway_needed_q`, `runway_source`, `runway_shortfall_months` first |
| `excluded_dilution_risk.csv` | KILL if `dilution_at_current_price_pct >= 50`, OR `runway_q < 2.0`, OR `must_raise_before_catalyst` AND dilution >= 35; with `kill_dates`. 20-35% is a `dilution_warning` on a kept row. Share price is not a test (`sub_dollar`, `prior_bid_price_deficiency` stay as context) |
| `excluded_not_evidence.csv` | `candidate_evidence_grade = single_arm` OR `primary_is_safety_only`. When the row is not the sponsor's lead program and the lead trial is `controlled_masked`, `candidate_switched_to` names it: the company stays a candidate on that trial, which is its own row |
| `watchlist_repeat_failure.csv` | unchanged: `repeat_failure` with >= 2 prior controlled failures, plus a `note` citing the evidence |

Gates apply in that order (delisted, underfunded, dilution, not evidence,
watchlist); a row lands in the file of the first gate it fails and
`fails_gates` lists every gate it fails. `row_warnings` carries the warnings
that never exclude: Form 15 without a Form 25 (`company_status =
check_manually`), a 20-35% dilution shortfall, `success_reaction_flag`.

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
| `true_market_cap`, `reported_market_cap`, `pre_funded_warrants`, `pre_funded_method`, `share_count_source` | true = (dei cover common + pre-funded warrants) x price, and the $50M-$400M filter uses it; reported = common only. Pre-funded count, first method that succeeds (named in `pre_funded_method` and `share_count_source`): 1 `xbrl` (warrant classes reconciling to the total; or `ClassOfWarrantOrRightNumberOfSecuritiesCalledByWarrantsOrRights` / `WarrantsAndRightsOutstanding` on a pre-funded member, or a company tag for pre-funded warrants outstanding, at the balance date), 2 `balance_sheet_parenthetical` ("excluding N pre-funded warrants"), 3 `footnote_table` (HTML tables: a pre-funded row in an outstanding-securities table, or the last Outstanding row of a roll-forward's pre-funded column), 4 `as_of_statement` (cover note, "as of DATE ... outstanding"). Issuance and exercise rows and sentences never count. Mentions are counted in the visible text only (the inline-XBRL header repeats member names: RNTX's "22 mentions" were mostly those). `not_found` after all four: integrity_report.md prints 300 characters around each mention |
| `market_cap_confidence` | `verified`: count reconciled to a filing (XBRL classes, a stated balance, or no pre-funded class AND no mention in the filing). `unverified`: price-feed cap, an EPS weighted average, or the filing mentions pre-funded warrants without stating a balance. On unverified rows `cash_to_mcap_est` and `ev_est` are withheld |
| `reverse_split_date`, `reverse_split_ratio`, `months_since_reverse_split`, `reverse_split_source`, `reverse_split_cover_check` | most recent split in 36 months: 8-K Item 5.03 text (a single 1-for-N ratio AND an effect sentence; the date is the stated effective date) merged with the price feed; the cover-page share count step-down is printed as corroboration. The 24-month rule is enforced on `reverse_split_date` |
| `ev_est` | `market_cap + total_debt_usd - cash_today_est`; empty if any input is |
| `company_status`, `delisting_signal`, `form_25_*`, `form_15_*` | from the SEC submissions JSON. `form_25_filed`: a Form 25 / 25-NSE (Notification of Removal from Listing) on or after the newest periodic financial report; an older one (a warrant class) is shown in the dossier, not counted. `form_15_filed`: same timing, demoted. `delisting_signal` form_25 > no_recent_financial_filing > form_15_only > none. `company_status` delisted (form_25 or no_recent_financial_filing) / check_manually (form_15_only; stays in candidates.csv) / active / unknown |
| `shelf_count`, `atm_count`, `days_since_latest_atm_filing` | counts of filings mentioning a shelf / an ATM in 3 years. The old shelf-AND-ATM boolean was True on 94% of rows and was removed |
| `n_endpoint_changes`, `endpoint_changes` | primary outcomes compared after lowercasing and stripping punctuation and whitespace; each change as JSON `{version, date, from, to, kind, status_at_change}` with the before/after text |
| `is_efficacy_scale` | enrollment >= 20 |
| `patients_per_site`, `site_list_maturity` | enrollment / sites; `new` when the registration has <= 2 versions (site lists are often not filled in yet) |
| `is_single_arm_open_label` | allocation NA or NON_RANDOMIZED, control_arm != yes, masking NONE |
| `enrollment_stale` | enrollment type ESTIMATED and start date > 24 months ago |
| `pcd_slip_months` | first to latest primary completion date; month precision when either date has no day, so 2027-07-14 to 2027-07 is 0, not -0.4 |

Rows are never dropped by these columns; filtering on them is the reader's call,
except the gates above, which move a row to its own file (never delete it).

### Intervention track record (dossier section G)

| Column | Rule |
|---|---|
| `primary_molecule_key`, `molecule_synonyms`, `combination_partners`, `widely_used_excluded` | ONE key per trial: the sponsor's own investigational molecule. Every non-control intervention is normalised (dose, route, frequency, salt and arm labels stripped, codes joined: `EVO-756 100 mg` -> `evo756`). An intervention any of whose names is in > 150 registered trials (CT.gov count) or FDA-approved before the trial started by another holder (openFDA Drugs@FDA) is excluded as a unit. The rest rank by sponsor code (letters + digits) or a mention in the sponsor's latest 10-Q/10-K, then type DRUG/BIOLOGICAL, then filing mentions. The top one is the key; its other names are synonyms; the rest are combination partners |
| `prior_trials_total` | trials of the key or a synonym, any sponsor, status COMPLETED / TERMINATED / WITHDRAWN / SUSPENDED, excluding the screen's own candidate trials. Computed ONCE per key: every row with the same key has identical track-record fields (a hard invariant) |
| `prior_controlled_*` | prior trials with a comparator arm. An outcome counts only if the trial has a clinical or biomarker primary AND a source states it: whyStopped futility; a posted CT.gov primary analysis with a direction (p < 0.05 favouring the experimental arm = met; favouring control = missed, a significant harm; direction undeterminable = the sponsor's statement decides; all p >= 0.05 = missed); or an unblocked sponsor 8-K sentence saying the primary endpoint was met / not met. A sentence that names a trial (NCT, acronym, protocol number) belongs to that trial; otherwise the indication named closest before the outcome phrase decides, and a tie attributes to nobody. A release whose headline or first 500 characters say "passes futility", "interim analysis", "DSMB recommends", "enrollment complete", "first patient", "alignment with FDA", "end-of-phase", "Type B/C", "breakthrough", "fast track", "orphan designation", "patent", "presented at" (full list in `track.NON_OUTCOME`) establishes nothing. A TERMINATED or WITHDRAWN trial is never a success. Everything else is `unknown`, counted, never guessed |
| `prior_success_reactions`, `worst_prior_success_1d`, `success_reaction_flag` | for each stated success, the 1-day return of the announcing release (for a posted-results success, the first unblocked sponsor release attributable to it). Flag when any is worse than -15%: the market judged a statistical win insufficient. Never excludes |
| `prior_trials_with_clinical_primary`, `prior_trials_safety_primary_only`, `largest_prior_clinical_primary_n` | from each prior trial's `primaryOutcomes[]` classes |
| `molecule_track_record` | naive (no prior controlled trial) / validated / mixed / repeat_failure / controlled_outcomes_unknown / **untested** (>= 3 prior trials, none with a clinical primary; overrides naive, unknown and validated, with `track_record_warning`) |
| `candidate_evidence_grade` | controlled_masked (comparator + >= double masking) / controlled_open / single_arm (no comparator arm, includes dose comparisons) / unknown |

Validation (`tests/live_evmn.py`, `tests/live_cgtx.py`): EVO756 has 2 failures,
CSU (8-K 2026-06-29) and AD (8-K 2026-09-09), so `repeat_failure` and the
watchlist; EVO301 "met primary endpoint" in the same releases is a different
molecule and is not counted. CT1812 has 11 prior trials, 0 clinical primaries,
so `untested`; 0 successes and 8 controlled trials of unknown outcome (the old
"success" was SHIMMER's "met its primary endpoint of safety and tolerability",
quoted inside a "Passes Pre-defined Futility Analysis" release). Pelareorep
(ONCY, both rows): trifluridine/tipiracil, atezolizumab, bevacizumab, FOLFIRI
and gemcitabine/nab-paclitaxel are combination partners excluded as widely
used. The old row's 2 "failures" were trifluridine/tipiracil trials (Servier
NCT02743221, AGITG NCT04879368), and its "success" NCT01622543 (CCTG, PFS 7.33
vs 9.13 months, HR 1.59, p 0.046) favoured CONTROL: it is now pelareorep's one
sourced failure. UPB: VALIANT (2026-02-11) met its primary and fell 47.18% that
day, so `success_reaction_flag`. Validation: `tests/live_round3.py`.

### Primary outcome class (candidate trial)

`primary_outcome_classes` from `primaryOutcomes[]` ONLY: clinical (a named
scale; a physiologic measure with a clinical target: blood pressure, HbA1c,
eGFR, FEV1, visual acuity, body weight, lesion count; an event rate:
exacerbations, hypoglycaemia, relapses, seizures, flares, hospitalisations,
remission, response, survival, progression, recurrence; "responder",
"success", "clear / almost clear") / safety (incl. dose escalation / expansion,
MTD, RP2D, DLT) / pk_pd / biomarker (incl. tracer activity: TSPO, SUVR,
amyloid PET, "as measured by [F-18]...") / unclassified, which
integrity_report.md lists in full for a human read. `primary_outcome_is_clinical`,
`primary_is_safety_only`, verbatim `primary_outcome_text`. SHINE NCT03507790
and SHIMMER NCT05225415 are safety-only; START NCT05531656 is clinical (CDR-SB).

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
primary completion 2027-05-31 + 3 months, margin 1.0 month, so
`must_raise_before_catalyst`; the shortfall is ~14% of market cap, so under the
shortfall gate it is not a dilution kill. 2026-06-24 "Aligns on Key Aspects of
Pivotal Study" -38.18%.

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
