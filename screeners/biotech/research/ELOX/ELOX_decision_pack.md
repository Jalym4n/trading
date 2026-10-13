# ELOX (Eloxx Pharmaceuticals) decision pack

Compiled 2026-10-13 from SEC EDGAR filings (2025-01 to 2026-09), XBRL, ClinicalTrials.gov and daily prices. Every figure is from a filing or is arithmetic on filed numbers (marked *computed*). Detailed, cited notes:

- `notes_10K_history.md`: FY2025 10-K, 2023-2025 history, the dark period
- `notes_2026_filings.md`: 2026 10-Qs, 8-Ks, the 14C information statement
- `notes_offerings.md`: June 2026 offering, September resale S-1, capitalization
- `ELOX-NCT07523581.md`: the protocol dossier for the EXACT trial
- `filings/`: every filing since 2025, with an index in `filings/manifest.json`

This pack presents facts. It is not a recommendation.

---

## 1. Snapshot (2026-10-12)

| Item | Value | Source |
|---|---|---|
| Price | **$13.89** (52-week $8.20 to $23.40; June offering $11.00) | Yahoo daily |
| Common shares | 4,036,459 | Q2 10-Q cover (8/7/26) |
| Pre-funded warrants ($0.01 to $0.11 exercise) | 7,383,919 | Q2 10-Q / Sept S-1, *computed* |
| Common + pre-funded (company counts both in EPS) | **11,420,378** | *computed* |
| Fully diluted (+ options, warrants, RSUs) | 12,521,254 | *computed* |
| Market cap: common only / + pre-funded / fully diluted | $56M / **$159M** / $174M | *computed* |
| Cash (6/30/26), all money-market | **$61.96M** | Q2 10-Q |
| Debt | **$0** (last $1.0M lender loan swapped for warrants 2/26/26) | Q2 10-Q |
| Cystic Fibrosis Foundation advances (liability, repayable under conditions) | $11.83M | Q2 10-Q |
| Enterprise value (mcap incl. pre-funded − cash) | **$97M** ($109M counting the CFF advances as debt) | *computed* |
| Operating cash burn | Q1 2026 −$5.45M, Q2 −$4.79M | XBRL |
| Company runway statement | "sufficient to reach **mid-2028**" | Sept S-1 |
| 30-day average dollar volume | $436k (Oct to date: $368k) | Yahoo, *computed* |

**Screener caveat.** biocat showed a market cap of $55M because it counts only common shares, which leaves out ~7.4M pre-funded warrants. The company itself treats those warrants as outstanding shares. The true figure is about 3x higher.

## 2. What happened, 2023 to 2026

| Date | Event | Source |
|---|---|---|
| 2023-10-16 | Nasdaq suspends ELOX (market value of listed securities below $35M) | 10-K |
| Nov 2023 to Mar 2026 | **No periodic reports filed.** The company cites no money for auditors. NT 10-K and NT 10-Q notices in 2025 | 10-K, NT filings |
| 2025-02-21 | SEC declares registration statement 333-276686 abandoned (no response to its notice) | SEC staff action |
| 2024 to 2026 | Lender Domicilium converts **$9.5M** of debt into pre-funded warrants at $0.49. Coastlands puts in **$15.0M** and Domicilium $2.0M at $0.49 | 10-K |
| Oct 2024 | Options repriced to **$0.0001** | 10-K |
| 2026-03-17 | One 10-K covering FY2023, 2024 and 2025. Going-concern doubt from both auditor and management | 10-K |
| 2026-04-28 | Holders of **57.1%** approve the split and charter changes by written consent, so there was no shareholder meeting | DEF 14C |
| **2026-05-29** | **1-for-11 reverse split**; authorized shares cut from 500M to 100M | 8-K 6/1/26 |
| 2026-06-08 | Nasdaq listing; offering of 2.975M shares at $11.00 plus 3.025M pre-funded warrants at $10.99, **$58.3M net** (Leerink, Guggenheim) | 424B4 |
| 2026-08-11 | Q2 10-Q drops the going-concern language | 10-Q |
| **2026-09-18** | **Resale S-1 effective: 4,973,204 shares registered for Domicilium and Coastlands** | S-1, EFFECT |

The pre-split OTC price was $0.084 on 2026-04-17, about $0.92 adjusted for the split (*computed*).

## 3. The catalyst

- **Drug:** exaluren (formerly ELX-02), a ribosomal read-through agent for **nonsense-mutation** genetic disease. Main patent expires 2031 (10-K).
- **Trial:** EXACT, NCT07523581. Phase 2b, **24 patients**, randomized, quadruple-masked, placebo-controlled, sequential design, 11 sites in the US and UK. Started 2026-06-30. Primary completion 2027-06-30 (estimated); **topline mid-2027**, final readout by end of 2027 (S-1).
- **Primary endpoint:** change in **filtration slit density (FSD) on kidney biopsy** at week 16 and week 32. Two primary endpoints, with no multiplicity terms in the registration.
- **Regulatory status of that endpoint, in the company's words:** "FSD has not been validated as a surrogate endpoint for clinical benefit in AS, and regulatory authorities have not determined that changes in FSD are sufficient to support approval." The FDA **did not support** repeat biopsies in US pediatric patients (10-K Item 1A). An FDA working group (ASSENT) is reviewing new Alport surrogate endpoints (10-K).
- **Start date slipped:** from the first half of 2026 to Q3 2026 (10-Q).
- **Prior human data in Alport syndrome:** NCT05448755, open-label, **3 patients**, 60 days, results posted on CT.gov.

| Patient | Proteinuria (UPCR) baseline → end, mg/g | Collagen IV α3/α4 change | FSD change (µm⁻¹) |
|---|---|---|---|
| 1 | 1,299.5 → **2,465.7** | +38.5% | +0.496 |
| 2 | 1,646.3 → **1,028.1** | +8.3% | +0.207 |
| 3 | 1,645.3 → **2,634.7** | +170.8% | +0.931 |

In that pilot, the biopsy measures rose in all 3 patients, while proteinuria rose in 2 of the 3. There were no serious adverse events; reported side effects included headache (2), injection-site erythema (2) and oropharyngeal pain (2).

- **Other programs:**
  - Cystic fibrosis (G542X): two Phase 2 trials completed in 2022, **no results posted** on CT.gov.
  - Cystinosis: Phase 2 terminated after 3 patients ("study design limitations").
  - nmADPKD: Phase 2 planned for 2027, topline mid-2028.
  - ZKN-013: Phase 1 with Almirall (terminable on 90 days' notice).

## 4. Supply and dilution

- **Resale overhang:** **4,973,204 shares** registered for resale since 9/18/26.
  - That is 123% of common shares, 44% of common plus pre-funded warrants, and about **181 days** of current volume (*computed*).
  - The holders paid **$5.39** per share, 2.6x below today's price.
  - Only ownership caps limit selling: Domicilium 19.99%, Coastlands 9.99%.
  - A clause in the amended warrants lets holders swap exercised shares back into warrants after 15 days (S-1).
- **The 90-day offering lock-up expired about 2026-09-06** (*computed*).
- **Equity plan:** 685,613 shares available, plus a 5% annual top-up extended to 2036 (S-1, 14C).
- **No S-3 shelf yet.** The company cannot use Form S-3 until it has a year of timely filings (S-1). New money therefore needs an S-1 or a private placement. Note: biocat's `dilution_risk=True` flag counts filings that merely mention a shelf or ATM; it does not mean an effective shelf exists.
- **Use of proceeds:** $13.2M for the Alport Phase 2b through topline; $19.2M for the nmADPKD Phase 2 (424B4).

## 5. Who owns it (latest 13G filings)

| Holder | % | Filed |
|---|---|---|
| Domicilium (lender turned investor) | 19.99% | 6/17/26 |
| Coastlands | 9.99% | 6/17/26 |
| EcoR1 | 9.9% | 6/16/26 |
| Stonepine | 9.9% | 6/16/26 |
| Aberdeen | 9.91% | 7/27/26 |
| Nantahala | 9.91% | 8/14/26 |
| Invus | 8.8% | 6/15/26 |
| Samsara | 5.0% | 8/14/26 |
| CEO Sumit Aggarwal | ~5.3% after the offering (24.1% at the 14C record date) | S-1 |

**Insider transactions:** no open-market buys or sells by insiders since 2025. Every Form 4 is a grant or exercise. Directors were granted options at $11.00 on 6/8/26; the CEO got 328,833 options at **$1.65** on 6/10/26, granted Sept 2025 with performance-based vesting.

## 6. Company-specific red flags (from the filings)

- Material weakness in internal controls not remediated; the CFO is an interim consultant (10-K, 10-Q).
- The cover-page box for timely filing of all required reports is checked **"No"** (10-Q).
- **Inconsistencies across documents:**
  - The press release gives Q2 loss per share as $(0.39); its own table and the 10-Q give $(0.66).
  - The press release claims EU orphan status for Alport syndrome; the 10-Q says the EU designation is for ADPKD.
  - Pre-funded warrant totals differ by 2,040,816 shares between sections of the 10-K.
  - The 10-K claims covenant compliance, while a May 2025 exhibit acknowledges loan defaults.
  - The 14C, filed a day after the offering S-1, says there is "no plan" to issue stock.
- **Missed CFF milestone:** a CFF royalty reduction required $15M raised by 2025-12-15. That was missed, and the deadline was moved retroactively in Feb 2026 (10-K exhibits).

## 7. Against your own screen rules (CLAUDE.md, track 2)

| Rule | ELOX | Result |
|---|---|---|
| Market cap $50M to $400M | $159M (with pre-funded warrants) | pass |
| Cash runway > quarters to catalyst + 2 | ~12 quarters at the current burn vs 4.9 needed; company says mid-2028 | pass (burn will rise with two trials funded) |
| Catalyst 3 to 12 months out | topline mid-2027, about 8.6 months | pass |
| **No reverse split in 24 months** | **1-for-11 on 2026-05-29** | **FAIL** |
| Average dollar volume > $300k | $436k over 30 days, $368k in October | pass, falling |

The screener originally passed ELOX by mistake because the price feed's history starts at the June listing. That has been fixed; ELOX now drops out of the screen.

## 8. Questions to answer before deciding (where your clinical training matters)

1. Is a biopsy FSD change at 16 and 32 weeks something that could ever support approval, or does approval need proteinuria or an eGFR slope? Check FDA Alport guidance and the ASSENT working group's output.
2. What share of Alport patients carry nonsense mutations? That sets the addressable market, which you need for the implied-probability formula below.
3. Would a positive FSD result coexist with rising proteinuria, as in 2 of the 3 pilot patients? Would the market care?
4. Read-through drugs (ataluren is the main precedent) have a regulatory track record. What does it say about this class?
5. With 24 patients and two co-primary endpoints, what effect size counts as a "win"? The registration states no statistical plan.

## 9. Thesis template (facts pre-filled; judgment fields left for you)

```
TICKER:               ELOX
CATALYST + DATE:      EXACT (NCT07523581) Phase 2b topline, guided mid-2027
CLAIM:                ___ (one falsifiable sentence)
WHAT THE MARKET BELIEVES:  EV ~$97M on a single-asset Phase 2b
WHY I DISAGREE:       ___
WHAT WOULD PROVE ME WRONG: ___ (write before the event)
MY PROBABILITY:       ___%
MARKET-IMPLIED:       EV / (peak sales x 2-4) = $97M / (___ x 3) = ___%
POSITION / SIZE:      none, watching   (journal first: zero capital, six months)
```

Legal boundary: use only published sources like these. Nothing from your placement.

## 10. Sources

| Filed | Form | Document | Local copy |
|---|---|---|---|
| 2026-09-11 | S-1 | [d152186ds1.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526389422/d152186ds1.htm) | `filings/2026-09-11_S-1_000119312526389422/` |
| 2026-08-11 | 8-K | [d167833d8k.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526345032/d167833d8k.htm) | `filings/2026-08-11_8-K_000119312526345032/` |
| 2026-08-11 | 10-Q | [elox-20260630.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526344964/elox-20260630.htm) | `filings/2026-08-11_10-Q_000119312526344964/` |
| 2026-06-09 | 424B4 | [d35071d424b4.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526263914/d35071d424b4.htm) | `filings/2026-06-09_424B4_000119312526263914/` |
| 2026-06-04 | S-1/A | [d35071ds1a.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526257750/d35071ds1a.htm) | `filings/2026-06-04_S-1-A_000119312526257750/` |
| 2026-06-01 | 8-A12B | [d75472d8a12b.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526251780/d75472d8a12b.htm) | `filings/2026-06-01_8-A12B_000119312526251780/` |
| 2026-06-01 | S-1/A | [d35071ds1a.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526251655/d35071ds1a.htm) | `filings/2026-06-01_S-1-A_000119312526251655/` |
| 2026-06-01 | 8-K | [d261568d8k.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526249692/d261568d8k.htm) | `filings/2026-06-01_8-K_000119312526249692/` |
| 2026-05-08 | DEF 14C | [d125964ddef14c.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526214720/d125964ddef14c.htm) | `filings/2026-05-08_DEF_14C_000119312526214720/` |
| 2026-05-07 | S-1 | [d35071ds1.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526212324/d35071ds1.htm) | `filings/2026-05-07_S-1_000119312526212324/` |
| 2026-05-05 | 10-Q | [elox-20260331.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526204722/elox-20260331.htm) | `filings/2026-05-05_10-Q_000119312526204722/` |
| 2026-05-01 | 8-K | [d151139d8k.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526201408/d151139d8k.htm) | `filings/2026-05-01_8-K_000119312526201408/` |
| 2026-04-28 | PRE 14C | [d125964dpre14c.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526187787/d125964dpre14c.htm) | `filings/2026-04-28_PRE_14C_000119312526187787/` |
| 2026-03-20 | DRS | [filename1.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526116406/filename1.htm) | `filings/2026-03-20_DRS_000119312526116406/` |
| 2026-03-17 | 10-K | [elox-20251231.htm](https://www.sec.gov/Archives/edgar/data/1035354/000119312526108687/elox-20251231.htm) | `filings/2026-03-17_10-K_000119312526108687/` |
| 2025-08-14 | NT 10-Q | [tm2523390d1_nt10q.htm](https://www.sec.gov/Archives/edgar/data/1035354/000110465925078543/tm2523390d1_nt10q.htm) | `filings/2025-08-14_NT_10-Q_000110465925078543/` |
| 2025-08-12 | 8-K | [tm2523236d1_8k.htm](https://www.sec.gov/Archives/edgar/data/1035354/000110465925076933/tm2523236d1_8k.htm) | `filings/2025-08-12_8-K_000110465925076933/` |
| 2025-05-15 | NT 10-Q | [tm2515283d1_nt10q.htm](https://www.sec.gov/Archives/edgar/data/1035354/000110465925049378/tm2515283d1_nt10q.htm) | `filings/2025-05-15_NT_10-Q_000110465925049378/` |
| 2025-04-01 | NT 10-K | [tm2511064d1_nt10k.htm](https://www.sec.gov/Archives/edgar/data/1035354/000110465925030543/tm2511064d1_nt10k.htm) | `filings/2025-04-01_NT_10-K_000110465925030543/` |
| 2025-02-21 | SEC STAFF ACTION | [filename1.pdf](https://www.sec.gov/Archives/edgar/data/1035354/999999999725000521/filename1.pdf) | `filings/2025-02-21_SEC_STAFF_ACTION_999999999725000521/` |

Also: XBRL companyfacts `data.sec.gov/api/xbrl/companyfacts/CIK0001035354.json`; trials `ctgov_NCT07523581.json` and `ctgov_NCT05448755.json`; prices `price_daily.csv`; Forms 3 and 4 and 13Gs in `filings/` (parsed in section 5).
