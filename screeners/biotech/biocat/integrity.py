"""integrity_report.md: rows whose values fail internal-consistency checks.

Run one produced wrong numbers that looked plausible (a 10,302-quarter
runway, randomized trials with no control arm, a flag true on 94% of rows)
and they were found by eye. These checks find that class mechanically. A row
listed here is not wrong by definition; it is a value to look at. Nothing is
dropped and nothing is scored.
"""

from __future__ import annotations

import datetime as dt

MASKED = {"SINGLE", "DOUBLE", "TRIPLE", "QUADRUPLE"}
EFFICACY_PHASES = {"P2", "P3", "P2/P3"}
NULL_SHARE_LIMIT = 0.25
LOW_DISCRIMINATION = (0.05, 0.80)


def _id(r: dict) -> str:
    return f"{r.get('ticker')} {r.get('nct_id')}"


def _n(v):
    return v is None or v == ""


def checks(rows: list[dict]) -> list[tuple[str, list[tuple[str, str]]]]:
    """[(check name, [(row id, offending values)])] in report order."""
    out = []

    def add(name, pred, show):
        out.append((name, [(_id(r), show(r)) for r in rows if pred(r)]))

    add("masked (SINGLE/DOUBLE/TRIPLE/QUADRUPLE) but control_arm != yes",
        lambda r: r.get("masking") in MASKED and r.get("control_arm") != "yes",
        lambda r: f"masking={r.get('masking')} control_arm={r.get('control_arm')} "
                  f"types={r.get('control_arm_types')} basis: {r.get('control_arm_basis')}")
    add("allocation == RANDOMIZED but control_arm == no",
        lambda r: r.get("allocation") == "RANDOMIZED" and r.get("control_arm") == "no",
        lambda r: f"types={r.get('control_arm_types')} basis: {r.get('control_arm_basis')}")
    add("runway_q > 200, or burn_q_usd < $250k with cash > $10M, or burn withheld as burn_implausible",
        lambda r: ((r.get("runway_q") or 0) > 200
                   or (r.get("burn_q_usd") is not None and r["burn_q_usd"] < 250_000 and (r.get("total_liquid_cash") or 0) > 10e6)
                   or r.get("burn_status") == "burn_implausible"),
        lambda r: f"burn_status={r.get('burn_status')} runway_q={r.get('runway_q')} burn_q_usd={r.get('burn_q_usd')} "
                  f"total_liquid_cash={r.get('total_liquid_cash')} quarters: {r.get('burn_quarters')}")
    add("enrollment < 20 on a P2, P3 or P2/P3 trial",
        lambda r: r.get("phase") in EFFICACY_PHASES and r.get("enrollment") is not None and r["enrollment"] < 20,
        lambda r: f"phase={r.get('phase')} enrollment={r.get('enrollment')} ({r.get('enrollment_type')}) "
                  f"n_sites={r.get('n_sites')}")
    add("days_since_cash_asof > 150",
        lambda r: (r.get("days_since_cash_asof") or 0) > 150,
        lambda r: f"cash_as_of_date={r.get('cash_as_of_date')} days={r.get('days_since_cash_asof')} "
                  f"burn_status={r.get('burn_status')}")
    add("company_status == likely_delisted_or_acquired",
        lambda r: r.get("company_status") == "likely_delisted_or_acquired",
        lambda r: f"true_market_cap={r.get('true_market_cap')} latest_financial_filing={r.get('latest_financial_filing')} "
                  f"latest_filing={r.get('latest_filing_form')} {r.get('latest_filing_date')} "
                  f"deregistration={r.get('deregistration_filing')}")
    add("pcd_slip_months < 0 (primary completion date moved EARLIER: real or parse error?)",
        lambda r: r.get("pcd_slip_months") is not None and r["pcd_slip_months"] < 0,
        lambda r: f"pcd_slip_months={r.get('pcd_slip_months')} values held: "
                  + " -> ".join(f"v{v} {p} ({t})" for v, _d, p, t in (r.get("_pcd_values") or [])))
    add("n_sites <= 1 with enrollment >= 50",
        lambda r: r.get("n_sites") is not None and r["n_sites"] <= 1 and (r.get("enrollment") or 0) >= 50,
        lambda r: f"enrollment={r.get('enrollment')} n_sites={r.get('n_sites')} "
                  f"n_versions={r.get('n_versions')} site_list_maturity={r.get('site_list_maturity')}")
    add("market_cap_confidence == unverified (share count not reconciled to a filing; cash_to_mcap_est and "
        "ev_est withheld)",
        lambda r: r.get("market_cap_confidence") == "unverified",
        lambda r: f"true_market_cap={r.get('true_market_cap')} pre_funded={r.get('pre_funded_warrants')} "
                  f"source: {r.get('share_count_source')}")
    add("pre-funded warrants: XBRL and filing text disagree",
        lambda r: "TEXT DISAGREES" in (r.get("prefunded_detail") or ""),
        lambda r: r.get("prefunded_detail"))
    return out


def null_shares(rows: list[dict], cols: list[str]) -> list[tuple[str, int, float]]:
    n = max(len(rows), 1)
    return [(c, k, k / n) for c in cols for k in [sum(_n(r.get(c)) for r in rows)] if k / n > NULL_SHARE_LIMIT]


def boolean_shares(rows: list[dict], cols: list[str]) -> list[tuple[str, int, int, float, bool]]:
    """(column, n_true, n_null, share_true over ALL rows, low_discrimination)."""
    n = max(len(rows), 1)
    out = []
    for c in cols:
        vals = [r.get(c) for r in rows]
        if not any(isinstance(v, bool) for v in vals):
            continue
        t = sum(v is True for v in vals)
        share = t / n
        out.append((c, t, sum(_n(v) for v in vals), share,
                    share > LOW_DISCRIMINATION[1] or share < LOW_DISCRIMINATION[0]))
    return out


def report(rows: list[dict], cols: list[str], today: dt.date) -> str:
    res = checks(rows)
    nulls = null_shares(rows, cols)
    bools = boolean_shares(rows, cols)
    n = len(rows)
    ver = sum(r.get("market_cap_confidence") == "verified" for r in rows)
    with_run = [r for r in rows if r.get("funded_through") in (True, False)]
    short = sum(r.get("funded_through") is False for r in with_run)
    md = [f"# Integrity report, {today.isoformat()}", "",
          f"{n} candidate rows. Each section lists the rows that fail one check, with the values "
          "that fail it. A listed row is a value to inspect, not a verdict; no row is dropped.", "",
          "## What each field is worth", "",
          "**Depends on the share count** (reliable only where `market_cap_confidence = verified`; "
          f"{ver} of {n} rows): `true_market_cap`, `cash_to_mcap_est`, `ev_est`. On unverified rows "
          "`cash_to_mcap_est` and `ev_est` are withheld, and `true_market_cap` is a lower bound when the "
          "pre-funded count is unknown.", "",
          "**Depends on the price feed, not the share count**: `avg_dollar_vol` (price x volume), "
          "`reported_market_cap`.", "",
          "**Independent of share count and price** (safe): `runway_q`, `runway_needed_q`, `funded_through`, "
          "`burn_q_usd`, `cash_today_est`, every trial-design field (`control_arm`, `masking`, `allocation`, "
          "`enrollment`, `primary_outcome_is_clinical`, ...), `pcd_slip_months`, `n_versions`, `endpoint_changes`.", "",
          f"`runway_q` uses only cash and operating cash flow from XBRL; no market cap enters it. It is the "
          f"most trustworthy filter in the tool. In this run `funded_through` is False on {short} of "
          f"{len(with_run)} rows that have a runway ({short / max(len(with_run), 1):.0%}).", ""]
    for name, hits in res:
        md += [f"## {name}", ""]
        md += [f"- **{rid}**: {why}" for rid, why in hits] or ["None."]
        md.append("")
    md += [f"## Fields null on more than {NULL_SHARE_LIMIT:.0%} of rows", ""]
    md += ([f"| field | null rows | share |", "|---|---|---|"] +
           [f"| {c} | {k} | {s:.0%} |" for c, k, s in nulls]) if nulls else ["None."]
    md += ["", "## Boolean columns: share of rows True", "",
           "| column | True | null | share True | note |", "|---|---|---|---|---|"]
    md += [f"| {c} | {t} | {nn} | {s:.0%} | {'low discrimination: consider removing or rethresholding' if low else ''} |"
           for c, t, nn, s, low in bools]
    for c in ("control_arm", "burn_status", "company_status", "site_list_maturity"):
        counts: dict = {}
        for r in rows:
            counts[r.get(c)] = counts.get(r.get(c), 0) + 1
        md += ["", f"Distribution of `{c}`: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=str))]
    md += ["", "## Output files", ""]
    files: dict = {}
    for r in rows:
        files[r.get("output_file")] = files.get(r.get("output_file"), 0) + 1
    md += [f"- `{k}`: {v} rows" for k, v in sorted(files.items(), key=str)]
    md += ["", "## Molecule track record across the file", "",
           "Outcomes of prior controlled trials come only from a stated source: whyStopped, a posted CT.gov "
           "primary analysis, or the sponsor's own 8-K press release. `controlled_outcomes_unknown` means prior "
           "controlled trials exist and no source states how they ended.", "",
           "| molecule_track_record | all rows | candidates.csv |", "|---|---|---|"]
    tr_all: dict = {}
    for r in rows:
        k = r.get("molecule_track_record")
        tr_all.setdefault(k, [0, 0])
        tr_all[k][0] += 1
        tr_all[k][1] += r.get("output_file") == "candidates.csv"
    md += [f"| {k} | {v[0]} | {v[1]} |" for k, v in sorted(tr_all.items(), key=str)]
    with_tr = [r for r in rows if (r.get("prior_controlled_failures") or 0) or (r.get("prior_controlled_successes") or 0)]
    md += [""] + [f"- **{_id(r)}** ({r.get('output_file')}): {r.get('molecule_track_record')}; failures "
                  f"{r.get('prior_controlled_failures')} {r.get('prior_failure_indications')}, successes "
                  f"{r.get('prior_controlled_successes')}; evidence {r.get('track_record_evidence_url')}"
                  for r in with_tr]
    grades: dict = {}
    for r in rows:
        grades[r.get("candidate_evidence_grade")] = grades.get(r.get("candidate_evidence_grade"), 0) + 1
    md += ["", "Distribution of `candidate_evidence_grade`: "
           + ", ".join(f"{k}={v}" for k, v in sorted(grades.items(), key=str))]
    md += ["", "## Kill-screen verdict changed by the cash fix", "",
           "`funded_through` (runway_q >= runway_needed_q) recomputed on `total_liquid_cash` "
           "(cash + short-term + long-term investments) instead of cash + short-term investments.", ""]
    changed = [r for r in rows if r.get("_funded_through_pre_cash_fix") != r.get("_funded_through_computed")]
    md += [f"- **{_id(r)}**: {r.get('_funded_through_pre_cash_fix')} -> {r.get('_funded_through_computed')} "
           f"(liquid ex long-term {r.get('_liquid_ex_lti')}, total {r.get('total_liquid_cash')}, long-term "
           f"{r.get('long_term_investments')}; computed runway {r.get('runway_q_computed')} q vs needed "
           f"{r.get('runway_needed_q')})"
           for r in changed] or ["None."]
    md += ["", "## Company-stated runway vs computed (runway_q = the lower; flagged when computed exceeds stated by "
           "> 2 quarters)", ""]
    md += [f"- **{_id(r)}**: computed {r.get('runway_q_computed')} q, stated {r.get('runway_q_stated')} q (to "
           f"{r.get('runway_stated_end_date')}), gap {r.get('runway_disagreement_q')} q, used {r.get('runway_q')} "
           f"({r.get('runway_source')}); cash_confidence {r.get('cash_confidence')}"
           for r in rows if r.get("runway_disagreement_q") is not None and abs(r["runway_disagreement_q"]) > 2.0] \
        or ["None."]
    md += ["", "## Kill-screen verdict changed by the stated-runway ceiling", ""]
    md += [f"- **{_id(r)}**: funded_through {r.get('_funded_through_computed')} -> {r.get('funded_through')} "
           f"(computed {r.get('runway_q_computed')} q, stated {r.get('runway_q_stated')} q, needed "
           f"{r.get('runway_needed_q')} q)" for r in rows
           if r.get("_funded_through_computed") is not None and r.get("_funded_through_computed") != r.get("funded_through")] \
        or ["None."]
    md += ["", "## Pre-readout dilution: must_raise_before_catalyst (<= 2 months of runway margin at the catalyst)",
           ""]
    md += [f"- **{_id(r)}** ({r.get('output_file')}): margin {r.get('months_runway_minus_months_to_catalyst')} "
           f"months, price {r.get('share_price')} (sub_dollar {r.get('sub_dollar')}, {r.get('nasdaq_tier')}), "
           f"shortfall to catalyst + 6 months = {r.get('dilution_at_current_price_pct')}% of market cap; bid-price "
           f"notices {r.get('bid_price_notice_dates')}"
           for r in rows if r.get("must_raise_before_catalyst")] or ["None."]
    md += ["", "## Headline vs price divergence (positive-language headline, 1-day return < -15%)", ""]
    md += [f"- **{_id(r)}**: {r.get('divergence_events')}" for r in rows if r.get("headline_price_divergence")] \
        or ["None."]
    md += ["", "## Primary outcome classes (candidate trials)", ""]
    pcl: dict = {}
    for r in rows:
        for c in (r.get("_primary_classes") or ["(none)"]):
            pcl[c] = pcl.get(c, 0) + 1
    md += ["Outcome-level counts: " + ", ".join(f"{k}={v}" for k, v in sorted(pcl.items()))]
    md += [f"- **{_id(r)}**: safety/PK-only primary: {r.get('primary_outcome_text')}"
           for r in rows if r.get("primary_is_safety_only")]
    md += ["", "## candidate_is_lead_program = False", "",
           "Lead guess = the sponsor's highest-phase, then largest-enrollment trial in the screening window. "
           "The candidate is not switched.", ""]
    md += [f"- **{_id(r)}** ({r.get('phase')}, n={r.get('enrollment')}): lead guess {r.get('lead_program_guess')}; "
           f"{r.get('sponsor_trial_count_in_window')} sponsor trials in window"
           for r in rows if r.get("candidate_is_lead_program") is False] or ["None."]
    md += ["", "## Counts", "", "| check | rows caught |", "|---|---|"]
    md += [f"| {name} | {len(hits)} |" for name, hits in res]
    md += [f"| fields null on > {NULL_SHARE_LIMIT:.0%} of rows | {len(nulls)} fields |",
           f"| boolean columns flagged low discrimination | {sum(b[4] for b in bools)} |", ""]
    return "\n".join(md)
