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
| `ev_est` | `market_cap + total_debt_usd - cash_today_est`; empty if any input is |
| `company_status` | `likely_delisted_or_acquired` when market cap is empty AND (newest financial filing and newest filing of any type both > 270 days old, OR a Form 15/25 filed on or after the newest financial filing); otherwise `active` |
| `shelf_count`, `atm_count`, `days_since_latest_atm_filing` | counts of filings mentioning a shelf / an ATM in 3 years. The old shelf-AND-ATM boolean was True on 94% of rows and was removed |
| `n_endpoint_changes`, `endpoint_changes` | primary outcomes compared after lowercasing and stripping punctuation and whitespace; each change as JSON `{version, date, from, to, kind, status_at_change}` with the before/after text |
| `is_efficacy_scale` | enrollment >= 20 |
| `patients_per_site`, `site_list_maturity` | enrollment / sites; `new` when the registration has <= 2 versions (site lists are often not filled in yet) |
| `is_single_arm_open_label` | allocation NA or NON_RANDOMIZED, control_arm != yes, masking NONE |
| `enrollment_stale` | enrollment type ESTIMATED and start date > 24 months ago |
| `pcd_slip_months` | first to latest primary completion date; month precision when either date has no day, so 2027-07-14 to 2027-07 is 0, not -0.4 |

Rows are never dropped by these columns; filtering on them is the reader's call.

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
- **Market cap counts common shares only** (`dei:EntityCommonStockSharesOutstanding`).
  Pre-funded warrants are left out, which can understate the cap several-fold
  (ELOX: $56M common only, $159M with its 7.4M pre-funded warrants). Not yet fixed.
- Stage 1 returned 1,494 trials, not ~3,000: the industry-sponsor and
  primary-completion window filters are strict.
- Some issuers have no ticker on SEC and are
  rejected as `no_ticker`.
- The history route is internal; if CT.gov changes it, section D reports
  "Not available" and the rest of the dossier is unaffected.
