"""biocat command line.

    export BIOCAT_UA="Your Name you@example.com"
    python -m biocat screen --out out/
    python -m biocat journal add --ticker VERA --catalyst "..." ... (all fields required)
    python -m biocat journal score --id VERA-20261012093000 --outcome 1
    python -m biocat journal report
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from collections import Counter

from . import journal as J
from .track import FAIL_MIN_FOR_WATCHLIST

CSV_COLS = ["ticker", "company", "cik", "nct_id", "phase", "catalyst_date", "date_type", "months_out",
            "fails_gates", "row_warnings", "dilution_warning",
            "company_status", "delisting_signal", "form_25_filed", "form_25_date", "form_25_accession",
            "form_15_filed", "form_15_date", "true_market_cap", "reported_market_cap", "pre_funded_warrants", "pre_funded_method",
            "share_count_source", "market_cap_confidence", "avg_dollar_vol",
            "reverse_split_date", "reverse_split_ratio", "months_since_reverse_split", "reverse_split_source",
            "reverse_split_cover_check",
            "total_liquid_cash", "cash_and_equivalents", "short_term_investments", "long_term_investments",
            "restricted_cash", "cash_tags_found", "cash_confidence", "cash_as_of_date", "days_since_cash_asof",
            "burn_q_usd", "burn_status",
            "cash_today_est", "cash_to_mcap_est", "total_debt_usd", "debt_asof", "has_convertible", "ev_est",
            "runway_q", "runway_q_computed", "runway_q_stated", "runway_stated_end_date", "runway_disagreement_q",
            "runway_source", "runway_gap_flag", "runway_stated_text", "runway_needed_q", "funded_through",
            "share_price", "sub_dollar", "nasdaq_tier", "prior_bid_price_deficiency", "reverse_split_last_36mo",
            "readout_lag_months", "readout_est_date", "months_runway_minus_months_to_catalyst",
            "must_raise_before_catalyst", "dilution_at_current_price_pct", "dilution_gate_reason",
            "runway_shortfall_months",
            "shelf_count", "atm_count", "days_since_latest_atm_filing",
            "n_primary_endpoints", "masking", "allocation", "control_arm", "control_arm_types",
            "enrollment", "enrollment_type", "enrollment_stale", "is_single_arm_open_label",
            "primary_outcomes_n", "primary_outcome_classes", "primary_outcome_is_clinical", "primary_is_safety_only",
            "primary_outcome_text",
            "n_sites", "patients_per_site", "site_list_maturity",
            "n_versions", "n_endpoint_changes", "endpoint_changes", "pcd_slips", "pcd_slip_months",
            "candidate_evidence_grade", "not_evidence_reason", "candidate_switched_to",
            "primary_molecule_key", "molecule_synonyms", "combination_partners", "widely_used_excluded",
            "prior_trials_total", "prior_controlled_trials",
            "prior_controlled_failures", "prior_controlled_successes", "prior_controlled_outcome_unknown",
            "prior_failure_indications", "prior_failure_ncts", "prior_terminated_for_futility",
            "prior_trials_with_clinical_primary", "prior_trials_safety_primary_only",
            "largest_prior_clinical_primary_n", "molecule_track_record", "success_reaction_flag",
            "worst_prior_success_1d", "prior_success_reactions", "prior_success_ncts", "track_record_warning",
            "track_record_evidence_url", "headline_price_divergence", "divergence_events", "press_releases",
            "sponsor_trial_count_in_window", "candidate_is_largest_trial", "candidate_is_latest_phase",
            "candidate_is_lead_program", "lead_program_guess", "sponsor_other_trials", "lead_program_warning"]
WATCHLIST_COLS = ["note"] + CSV_COLS
KILL_COLS = ["kill_dates"] + CSV_COLS
UNDERFUNDED_COLS = ["runway_q", "runway_needed_q", "runway_source", "runway_shortfall_months"] + \
    [c for c in CSV_COLS if c not in ("runway_q", "runway_needed_q", "runway_source", "runway_shortfall_months")]
OUTPUTS = [("candidates.csv", CSV_COLS), ("excluded_delisted.csv", CSV_COLS),
           ("excluded_underfunded.csv", UNDERFUNDED_COLS), ("excluded_dilution_risk.csv", KILL_COLS),
           ("excluded_not_evidence.csv", CSV_COLS), ("watchlist_repeat_failure.csv", WATCHLIST_COLS)]


def mkt_date(cache: dict, row: dict) -> str | None:
    info = cache.get(row.get("cik")) or {}
    return getattr(info.get("mkt"), "price_date", None)


def company_info(http, cik: int, ticker: str, today: dt.date) -> dict:
    """Every company-level fact stages 3-4 use: XBRL, dilution counts, price
    feed, SEC submissions, share count (common + pre-funded warrants), reverse
    splits. One function so tests run the same path as the screen."""
    from . import financials as F, market as M, identity as I
    info: dict = {"errors": []}
    try:
        facts = http.get_json(F.FACTS_URL.format(cik=cik))
        info["facts"] = facts
        info["shares"], info["shares_asof"] = F.shares_outstanding(facts)
    except Exception as e:  # noqa: BLE001
        info["facts"], info["shares"], info["shares_asof"] = None, None, None
        info["errors"].append(f"xbrl: {e}")
    try:
        info["dil"] = F.dilution_facts(http, cik, today)
    except Exception as e:  # noqa: BLE001
        info["dil"] = {"shelf_count": None, "atm_count": None, "days_since_latest_atm_filing": None}
        info["errors"].append(f"fts: {e}")
    info["mkt"] = M.fetch_market(http, ticker, today)
    try:
        info["submissions"] = http.get_json(I.SUBMISSIONS_URL.format(cik=cik))
    except Exception as e:  # noqa: BLE001
        info["submissions"] = None
        info["errors"].append(f"submissions: {e}")

    # ---- reverse splits: SEC 8-Ks (36 months) + price feed, rule is 24 months
    try:
        sec_events = F.reverse_split_events(http, cik, today)
    except Exception as e:  # noqa: BLE001
        sec_events = None
        info["errors"].append(f"sec reverse-split check: {e}")
    split = M.latest_reverse_split(M.feed_split_events(info["mkt"].reverse_splits_24m), sec_events, today)
    split["reverse_split_cover_check"] = F.cover_step_down(info.get("facts"), split["reverse_split_date"])
    info["split"] = split
    if not split["reverse_split_known"]:
        info["splits_24m"] = None
    else:
        info["splits_24m"] = [f"{e['effective_date']} {e['ratio']} ({e['source']})" for e in split["_split_events"]
                              if (today - dt.date.fromisoformat(e["effective_date"])).days < 730]

    try:
        info["runway_stmt"] = F.runway_statement(http, cik, info["submissions"])
    except Exception as e:  # noqa: BLE001
        info["runway_stmt"] = None
        info["errors"].append(f"runway statement: {e}")

    from . import dilution as DL
    try:
        info["bid_notices"] = DL.bid_price_notices(http, cik, info["submissions"], today) \
            if info["submissions"] else None
    except Exception as e:  # noqa: BLE001
        info["bid_notices"] = None
        info["errors"].append(f"bid-price notices: {e}")
    try:
        prs = DL.press_releases(http, cik, info["submissions"]) if info["submissions"] else []
        info["pr"] = DL.divergence(prs, info["mkt"].closes)
    except Exception as e:  # noqa: BLE001
        info["pr"] = {"press_releases": [], "headline_price_divergence": None, "divergence_events": []}
        info["errors"].append(f"press releases: {e}")

    # ---- share count: common (dei cover) + pre-funded warrants (XBRL classes, else filing text)
    info["pf"] = F.prefunded_warrants(http, cik, info["submissions"])
    sc = F.share_count(http, cik, info["submissions"], info["pf"])
    info["sc"] = sc
    price = info["mkt"].price
    info["mcap"] = info["mcap_reported"] = info["mcap_src"] = None
    info["confidence"] = "unverified"
    if info.get("shares") and price:
        info["mcap_reported"] = info["shares"] * price
        info["mcap"] = (info["shares"] + (sc.pre_funded or 0)) * price
        info["confidence"] = sc.confidence
        info["mcap_src"] = (f"dei cover {info['shares']:,.0f} at {info['shares_asof']}"
                            + (f" + pre-funded {sc.pre_funded:,.0f} from {sc.source}" if sc.pre_funded
                               else f"; pre-funded: {sc.source}")
                            + (" (LOWER BOUND: pre-funded count unknown)" if sc.pre_funded is None else ""))
    elif not info.get("shares"):
        try:
            info["mcap"] = M.nasdaq_market_cap(http, ticker)
            info["mcap_src"] = "Nasdaq summary MarketCap (price feed; share count not reconciled to a filing)" \
                if info["mcap"] else None
        except Exception as e:  # noqa: BLE001
            info["errors"].append(f"nasdaq summary: {e}")
    return info


def screen(a) -> int:
    from .net import Http
    from . import trials as T, identity as I, financials as F, market as M, dossier as D, history as H
    from . import derive as X, integrity as Q, track as TR, dilution as DL
    from .outcomes import classify_primary

    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    os.makedirs(os.path.join(a.out, "dossiers"), exist_ok=True)
    http = Http(cache_dir=os.path.join(a.out, ".cache"), ttl_hours=a.cache_hours)
    log = lambda m: print(m, flush=True)  # noqa: E731

    # ---- stage 1 ----------------------------------------------------------
    trials = T.fetch_stage1(http, today, log)
    if a.limit_trials:
        trials = trials[: a.limit_trials]
    by_sponsor = Counter(t.sponsor for t in trials)

    # ---- stage 2: fewest trials first (micro-caps run 1-2 programmes) ------
    order = sorted(by_sponsor.items(), key=lambda kv: (kv[1], kv[0]))
    idx = I.TickerIndex.load(http)
    res = I.sic_gate(I.resolve_all(order, http, idx, a.max_fts, log), http, log)
    ok = {r.sponsor: r for r in res if r.status == "resolved"}
    with open(os.path.join(a.out, "identity.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sponsor", "n_trials", "key", "status", "path", "cik", "ticker", "sec_name", "sic", "sic_desc",
                    "detail"])
        for r in res:
            w.writerow([r.sponsor, by_sponsor[r.sponsor], r.key, r.status, r.path, r.cik, r.ticker,
                        r.sec_name, r.sic, r.sic_desc, r.detail])

    # ---- stages 3 + 4 per resolved company --------------------------------
    rows, dropped = [], []
    company_cache: dict[int, dict] = {}
    sponsor_trials: dict[str, list] = {}
    for t in trials:
        sponsor_trials.setdefault(t.sponsor, []).append(t)
    for t in trials:
        r = ok.get(t.sponsor)
        if not r:
            continue
        pcd = T.parse_date(t.pcd)
        months = T.months_between(today, pcd) if pcd else None
        if r.cik not in company_cache:
            company_cache[r.cik] = company_info(http, r.cik, r.ticker, today)
        info = company_cache[r.cik]
        run = F.compute_runway(info["facts"], months, today) if info.get("facts") else None
        mkt = info["mkt"]
        mcap = info["mcap"]
        keep, reasons = M.size_filter(mcap, mkt.avg_dollar_vol, info["splits_24m"])
        debt = F.debt_facts(info["facts"], run.cash_asof if run else None) if info.get("facts") \
            else F.Debt(None, None, {}, None)
        aged = X.cash_aging(run.cash_usd if run else None, run.cash_asof if run else None,
                            run.burn_q_usd if run else None, run.burn_status if run else None, mcap, today)
        status = X.company_status(info.get("submissions"), today)
        ev = X.ev_est(mcap, debt.total_debt_usd, aged["cash_today_est"])
        if info["confidence"] != "verified":
            # an unverified share count never drives a cash-vs-market-cap conclusion
            aged["cash_to_mcap_est"], ev = None, None
        comp = run.components if run else {}
        ceil = X.runway_ceiling(run.runway_q if run else None, info.get("runway_stmt"), comp.get("cash_as_of_date"))
        needed = run.runway_needed_q if run else (round(months / 3 + 2, 2) if months is not None else None)
        funded_final = (ceil["runway_q"] >= needed) if (ceil["runway_q"] is not None and needed is not None) else None
        tags = comp.get("cash_tags_found") or []
        if ceil["runway_disagreement_q"] is not None and abs(ceil["runway_disagreement_q"]) > 2.0:
            cash_conf = "partial"
        elif len(tags) <= 1:
            cash_conf = "single_tag"
        else:
            cash_conf = "verified"
        pre = comp.get("liquid_cash_ex_long_term")
        funded_pre = (round(pre / run.burn_q_usd, 2) >= run.runway_needed_q) \
            if (run and run.burn_status == "burning" and pre is not None and run.runway_needed_q is not None) \
            else (run.funded_through if run else None)
        lead = X.lead_program(t, sponsor_trials.get(t.sponsor, [t]))
        dil = DL.dilution_facts(
            mkt.price, mkt.exchange, info.get("bid_notices"), info["split"], ceil["runway_q"],
            comp.get("total_liquid_cash"), comp.get("cash_as_of_date"), months, mcap, today,
            readout_lag_months=a.readout_lag_months)
        row = {
            **aged, **status, **ceil, **lead, **dil,
            "funded_through": funded_final, "_funded_through_computed": run.funded_through if run else None,
            "headline_price_divergence": info["pr"]["headline_price_divergence"],
            "divergence_events": json.dumps(info["pr"]["divergence_events"]),
            "press_releases": json.dumps(info["pr"]["press_releases"]),
            "lead_program_warning": None if lead["candidate_is_lead_program"] else
            f"candidate is not the sponsor's highest-phase / largest trial in the window; lead guess: "
            f"{lead['lead_program_guess']}",
            "total_liquid_cash": comp.get("total_liquid_cash"), "cash_and_equivalents": comp.get("cash_and_equivalents"),
            "short_term_investments": comp.get("short_term_investments"),
            "long_term_investments": comp.get("long_term_investments"),
            "restricted_cash": comp.get("restricted_cash"), "cash_tags_found": "|".join(tags) or None,
            "cash_confidence": cash_conf if comp.get("total_liquid_cash") is not None else None,
            "cash_as_of_date": comp.get("cash_as_of_date"), "_funded_through_pre_cash_fix": funded_pre,
            "_liquid_ex_lti": pre,
            **{k: v for k, v in info["split"].items() if not k.startswith("_") and k != "reverse_split_known"},
            "total_debt_usd": debt.total_debt_usd, "debt_asof": debt.debt_asof,
            "debt_components": "; ".join(f"{k}={v:,.0f}" for k, v in debt.components.items()) or None,
            "has_convertible": debt.has_convertible,
            "ev_est": ev,
            "ticker": r.ticker, "company": r.sec_name, "cik": r.cik, "sponsor": t.sponsor,
            "sec_sic": f"{r.sic} {r.sic_desc}",
            "nct_id": t.nct_id, "phase": t.phase, "phase_rank": t.phase_rank,
            "catalyst_date": t.pcd, "date_type": t.pcd_type, "months_out": months,
            "true_market_cap": round(mcap) if mcap else None,
            "reported_market_cap": round(info["mcap_reported"]) if info.get("mcap_reported") else None,
            "pre_funded_warrants": info["sc"].pre_funded, "share_count_source": info["mcap_src"],
            "market_cap_confidence": info["confidence"],
            "prefunded_detail": f"{info['sc'].detail} (XBRL status {info['pf'].status}, filing {info['pf'].source})",
            "pre_funded_method": info["sc"].method, "_pf_contexts": info["sc"].contexts,
            "avg_dollar_vol": round(mkt.avg_dollar_vol) if mkt.avg_dollar_vol else None,
            "burn_q_usd": run.burn_q_usd if run else None,
            "burn_status": run.burn_status if run else "no_data",
            "runway_needed_q": needed,
            "cash_tags": run.cash_tags if run else None,
            "burn_quarters": "; ".join(f"{q['start']}..{q['end']} {q['val']:+,.0f} ({q['derived_from']})"
                                       for q in run.quarters_used) if run and run.quarters_used else None,
            **{k: info["dil"].get(k) for k in ("shelf_count", "atm_count", "days_since_latest_atm_filing")},
            "_filter_notes": "; ".join(reasons),
            "data_notes": "; ".join(
                reasons + info["errors"]
                + ([f"price history starts {mkt.history_start} (< 24 months; split check relies on SEC 8-Ks)"]
                   if mkt.history_start and mkt.reverse_splits_24m is None else [])
                + ([] if mkt.price else [f"market: {e}" for e in mkt.errors] or ["market: no price"])
                + ([] if info.get("shares") else ["shares: no dei:EntityCommonStockSharesOutstanding in XBRL "
                                                  "(20-F filers often omit it; not derived from IFRS counts "
                                                  "because the ADS ratio is unknown)"])) or "none",
        }
        (rows if keep else dropped).append(row)
    log(f"stages 3-4: {len(rows)} trial rows pass size filters, {len(dropped)} dropped on known values")

    # ---- mechanical ranking ONLY: phase, months to catalyst, market cap ----
    rows.sort(key=lambda r: (r["phase_rank"], r["months_out"] if r["months_out"] is not None else 99,
                             r["true_market_cap"] if r["true_market_cap"] is not None else 9e12))

    # ---- stage 5: dossiers ------------------------------------------------
    hist_client = H.HistoryClient(http)
    press_cache: dict = {}
    filing_cache: dict = {}
    built: dict = {}
    for row in rows:
        try:
            rec = T.fetch_full(http, row["nct_id"])
        except Exception as e:  # noqa: BLE001
            row["_dossier_error"] = str(e)
            continue
        try:
            hist = hist_client.diff(row["nct_id"])
            hist_md = H.render(hist)
        except Exception as e:  # noqa: BLE001
            hist = None
            hist_md = ["## D. Revision history", "", f"Not available: {e}", ""]
        related = {}
        for name in D.experimental_interventions(rec.get("protocolSection", {}))[:3]:
            try:
                related[name] = T.related_trials(http, row["sponsor"], name, limit=D.RELATED_CAP)
            except Exception:  # noqa: BLE001
                related[name] = []
        try:
            ident = TR.identify_molecule(http, rec, row["sponsor"],
                                         TR.filing_text(http, row["cik"],
                                                        company_cache.get(row["cik"], {}).get("submissions"),
                                                        filing_cache))
        except Exception as e:  # noqa: BLE001
            ident = {"primary_molecule_key": None, "molecule_synonyms": [], "combination_partners": [],
                     "widely_used_excluded": [], "_ranking": []}
            row["data_notes"] = f"{row.get('data_notes')}; molecule identification: {e}"
        row.update(ident)
        me = TR.trial_facts(rec)
        row["candidate_evidence_grade"] = TR.evidence_grade(me["control_arm"], me["masking"])
        md, facts = D.build(row, rec, hist_md, related, today)
        row.update({"n_primary_endpoints": facts["n_primary"], "masking": facts["masking"],
                    "allocation": facts["allocation"], "control_arm": facts["control_arm"],
                    "control_arm_types": facts["control_arm_types"],
                    "control_arm_basis": facts["control_arm_basis"],
                    "enrollment": facts["enrollment"], "enrollment_type": facts["enrollment_type"],
                    "n_sites": facts["n_sites"], "start_date": facts["start_date"]})
        row.update(X.design_flags(facts["allocation"], facts["masking"], facts["control_arm"],
                                  facts["enrollment_type"], facts["months_since_start"]))
        pc = classify_primary(rec.get("protocolSection", {}))
        row["_primary_classes"] = pc["primary_outcome_classes"]
        row.update({k: (json.dumps(v) if isinstance(v, list) and not k.startswith("_") else v) for k, v in pc.items()})
        if hist:
            row.update({"n_versions": hist["n_versions"], "n_endpoint_changes": len(hist["endpoint_changes"]),
                        "endpoint_changes": json.dumps([{k: c[k] for k in ("version", "date", "from", "to", "kind",
                                                                         "status_at_change")}
                                                        for c in hist["endpoint_changes"]]),
                        "pcd_slips": hist["pcd_slips"], "pcd_slip_months": hist["pcd_slip_months"],
                        "_pcd_values": hist["pcd_values"]})
        row.update(X.site_facts(facts["enrollment"], facts["n_sites"], row.get("n_versions")))
        built[id(row)] = (rec, hist_md, related)

    # ---- track record: once per PRIMARY molecule key, identical on every row that shares it
    by_key: dict = {}
    for row in rows:
        if id(row) in built:
            by_key.setdefault(row.get("primary_molecule_key"), []).append(row)
    screen_ncts = {r["nct_id"] for r in rows}
    for key, group in by_key.items():
        syn = sorted({x for r in group for x in r.get("molecule_synonyms") or []} - {key})
        first = group[0]
        info = company_cache.get(first["cik"], {})
        try:
            tr = TR.track_record_for_key(http, key, syn, sorted({r["sponsor"] for r in group}), first["cik"],
                                         info.get("submissions"), info["mkt"].closes if info.get("mkt") else [],
                                         today, press_cache, screen_ncts)
        except Exception as e:  # noqa: BLE001
            tr = {**TR._EMPTY_TR, "molecule_track_record": "error", "_prior_trials": []}
            for r in group:
                r["data_notes"] = f"{r.get('data_notes')}; track record: {e}"
        for row in group:
            row["molecule_synonyms"] = syn
            row.update({k: v for k, v in tr.items() if not k.startswith("_")})
            row["_tr"] = tr
    for row in rows:
        if id(row) not in built:
            continue
        rec, hist_md, related = built[id(row)]
        md, _facts = D.build(_csv_view(row), rec, hist_md, related, today)
        if row.get("_tr"):
            md += "\n".join(TR.track_section({**row, **row["_tr"]})) + "\n"
        with open(os.path.join(a.out, "dossiers", f"{row['ticker']}-{row['nct_id']}.md"), "w") as f:
            f.write(md)

    # ---- hard invariants: a violation fails the run --------------------------
    violations = invariants(rows)

    # ---- gates: every row lands in exactly one file; every gate it fails is listed
    by_nct = {r["nct_id"]: r for r in rows}
    for row in rows:
        row.update(gates(row, by_nct))
    for row in rows:
        row["output_file"] = route(row)
        if row["output_file"] == "excluded_dilution_risk.csv":
            row["kill_dates"] = (f"cash {row.get('total_liquid_cash')} as of {row.get('cash_as_of_date')}; runway "
                                 f"{row.get('runway_q')} q ({row.get('runway_source')}) ends {row.get('runway_end_date')}; "
                                 f"primary completion {row.get('catalyst_date')}, readout est. {row.get('readout_est_date')} "
                                 f"(+{row.get('readout_lag_months')} months); margin "
                                 f"{row.get('months_runway_minus_months_to_catalyst')} months; shortfall "
                                 f"{row.get('dilution_at_current_price_pct')}% of market cap at "
                                 f"{row.get('share_price')} on {mkt_date(company_cache, row)}")
        elif row["output_file"] == "watchlist_repeat_failure.csv":
            row["note"] = (f"{row['prior_controlled_failures']} prior controlled trials of "
                           f"{row.get('primary_molecule_key')} missed their primary endpoint "
                           f"({', '.join(row['prior_failure_indications'])}; {', '.join(row['prior_failure_ncts'])}); "
                           f"evidence: {row.get('track_record_evidence_url')}. Candidate trial {row['nct_id']} is "
                           f"{row.get('candidate_evidence_grade')}.")
    for name, cols in OUTPUTS:
        with open(os.path.join(a.out, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                if row["output_file"] == name:
                    w.writerow(_csv_view(row))
    with open(os.path.join(a.out, "integrity_report.md"), "w") as f:
        f.write(Q.report(rows, CSV_COLS, today, violations=violations, n_screened=len(rows)))
    with open(os.path.join(a.out, "dropped.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "nct_id", "true_market_cap", "reported_market_cap",
                                              "pre_funded_warrants", "market_cap_confidence", "avg_dollar_vol",
                                              "reverse_split_date", "reverse_split_ratio",
                                              "months_since_reverse_split", "reverse_split_source", "_filter_notes"],
                           extrasaction="ignore")
        w.writeheader()
        for row in dropped:
            w.writerow(row)
    per = Counter(r["output_file"] for r in rows)
    log("wrote " + ", ".join(f"{per[n]} {n}" for n, _c in OUTPUTS) + f"; {len(rows)} dossiers -> {a.out} "
        f"(network calls {http.calls['network']}, cache hits {http.calls['cache']})")
    if violations:
        for name, bad in violations:
            for line in bad:
                print(f"INVARIANT VIOLATED: {name}: {line}", file=sys.stderr)
        return 4
    return 0


# ------------------------------------------------------------------ gates --
DILUTION_KILL_PCT = 50.0
DILUTION_KILL_PCT_MUST_RAISE = 35.0
DILUTION_WARN_PCT = 20.0
RUNWAY_KILL_Q = 2.0


def gates(row: dict, by_nct: dict) -> dict:
    """Mechanical gate facts for one row. Each gate names the field values that trip it."""
    out = {"delisted_gate": row.get("company_status") == "delisted", "underfunded_gate": False,
           "dilution_gate": False, "not_evidence_gate": False, "dilution_warning": None,
           "runway_shortfall_months": None, "candidate_switched_to": None, "fails_gates": None}
    if row.get("funded_through") is False:
        out["underfunded_gate"] = True
        if row.get("runway_q") is not None and row.get("runway_needed_q") is not None:
            out["runway_shortfall_months"] = round((row["runway_needed_q"] - row["runway_q"]) * 3, 1)
    pct, rq = row.get("dilution_at_current_price_pct"), row.get("runway_q")
    why = []
    if pct is not None and pct >= DILUTION_KILL_PCT:
        why.append(f"dilution_at_current_price_pct {pct} >= {DILUTION_KILL_PCT:g}")
    if rq is not None and rq < RUNWAY_KILL_Q:
        why.append(f"runway_q {rq} < {RUNWAY_KILL_Q:g}")
    if row.get("must_raise_before_catalyst") and pct is not None and pct >= DILUTION_KILL_PCT_MUST_RAISE:
        why.append(f"must_raise_before_catalyst and dilution {pct} >= {DILUTION_KILL_PCT_MUST_RAISE:g}")
    out["dilution_gate"] = bool(why)
    out["dilution_gate_reason"] = "; ".join(why) or None
    if not why and pct is not None and DILUTION_WARN_PCT <= pct < DILUTION_KILL_PCT_MUST_RAISE:
        out["dilution_warning"] = f"shortfall to readout + 6 months is {pct}% of market cap (warn band 20-35%)"
    weak = row.get("candidate_evidence_grade") == "single_arm" or row.get("primary_is_safety_only") is True
    if weak:
        lead_nct = (row.get("lead_program_guess") or "").split(" ")[0] or None
        lead = by_nct.get(lead_nct)
        if row.get("candidate_is_lead_program") is False and lead is not None and \
                lead.get("candidate_evidence_grade") == "controlled_masked":
            # the company stays a candidate on its lead trial, which is its own row
            out["candidate_switched_to"] = f"{lead_nct} (controlled_masked lead trial; its own row)"
        out["not_evidence_gate"] = True
        out["not_evidence_reason"] = "; ".join(
            x for x in ("candidate_evidence_grade single_arm" if row.get("candidate_evidence_grade") == "single_arm"
                        else "", "primary_is_safety_only" if row.get("primary_is_safety_only") is True else "") if x)
    failed = [g for g in ("delisted", "underfunded", "dilution", "not_evidence") if out[f"{g}_gate"]]
    out["fails_gates"] = "|".join(failed) or None
    warn = []
    if row.get("company_status") == "check_manually":
        warn.append(f"CHECK MANUALLY: Form 15 deregistration filed {row.get('form_15_date')} with no Form 25; "
                    f"often routine, confirm the listing")
    if out["dilution_warning"]:
        warn.append(out["dilution_warning"])
    if row.get("success_reaction_flag"):
        warn.append(f"a prior stated success fell {row.get('worst_prior_success_1d')} on its announcement day")
    out["row_warnings"] = " | ".join(warn) or None
    return out


def route(row: dict) -> str:
    """First failing gate decides the file (delisted, underfunded, dilution, not evidence),
    then the unchanged repeat-failure watchlist, then candidates."""
    if row["delisted_gate"]:
        return "excluded_delisted.csv"
    if row["underfunded_gate"]:
        return "excluded_underfunded.csv"
    if row["dilution_gate"]:
        return "excluded_dilution_risk.csv"
    if row["not_evidence_gate"]:
        return "excluded_not_evidence.csv"
    if row.get("molecule_track_record") == "repeat_failure" and \
            (row.get("prior_controlled_failures") or 0) >= FAIL_MIN_FOR_WATCHLIST:
        return "watchlist_repeat_failure.csv"
    return "candidates.csv"


def invariants(rows: list[dict]) -> list[tuple[str, list[str]]]:
    """[(invariant, [violating rows])]; empty lists mean it held."""
    bad1 = [f"{r['ticker']} {r['nct_id']} key={r.get('primary_molecule_key')}: successes "
            f"{r.get('prior_controlled_successes')}, failures {r.get('prior_controlled_failures')}, trials with a "
            f"clinical primary {r.get('prior_trials_with_clinical_primary')}"
            for r in rows if ((r.get("prior_controlled_successes") or 0) > 0 or (r.get("prior_controlled_failures") or 0) > 0)
            and not (r.get("prior_trials_with_clinical_primary") or 0) > 0]
    from .track import TRACK_FIELDS
    bad2, groups = [], {}
    for r in rows:
        if r.get("primary_molecule_key"):
            groups.setdefault(r["primary_molecule_key"], []).append(r)
    for k, g in groups.items():
        base = g[0]
        for r in g[1:]:
            diff = {f: (base.get(f), r.get(f)) for f in TRACK_FIELDS if base.get(f) != r.get(f)}
            if diff:
                bad2.append(f"key {k}: {base['ticker']} {base['nct_id']} vs {r['ticker']} {r['nct_id']}: {diff}")
    return [("prior_controlled_successes > 0 or prior_controlled_failures > 0 REQUIRES "
             "prior_trials_with_clinical_primary > 0", bad1),
            ("rows with the same primary_molecule_key have identical track-record fields", bad2)]


def _csv_view(row: dict) -> dict:
    """Lists and dicts as JSON for the CSV; everything else as is."""
    return {k: (json.dumps(v) if isinstance(v, (list, dict)) and not k.startswith("_") else v) for k, v in row.items()}


def journal_cmd(a) -> int:
    path = a.journal
    try:
        if a.jcmd == "add":
            e = J.add(path, {k: getattr(a, k) for k in J.REQUIRED})
            print(f"added {e['id']}")
        elif a.jcmd == "score":
            J.score(path, a.id, a.outcome)
            print(f"scored {a.id} = {a.outcome} (final)")
        elif a.jcmd == "report":
            print(J.report(path))
        elif a.jcmd == "list":
            for e in J.entries(path).values():
                print(json.dumps(e, sort_keys=True))
    except J.JournalError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="biocat", description="Extract and present; never judge.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("screen", help="run stages 1-5")
    s.add_argument("--out", default="biocat_out")
    s.add_argument("--max-fts", type=int, default=600, help="EDGAR FTS identity lookups (budget)")
    s.add_argument("--limit-trials", type=int, default=0, help="debug: only the first N trials")
    s.add_argument("--today", default=None, help="YYYY-MM-DD, default today")
    s.add_argument("--cache-hours", type=float, default=24.0)
    s.add_argument("--readout-lag-months", type=float, default=3.0,
                   help="months from primary completion to topline readout, for the dilution margin (0 = PCD)")
    j = sub.add_parser("journal", help="thesis journal")
    j.add_argument("--journal", default="journal.jsonl")
    js = j.add_subparsers(dest="jcmd", required=True)
    ja = js.add_parser("add")
    for k in J.REQUIRED:
        ja.add_argument(f"--{k.replace('_', '-')}", dest=k, required=True)
    jsc = js.add_parser("score")
    jsc.add_argument("--id", required=True)
    jsc.add_argument("--outcome", required=True, choices=["0", "1"])
    js.add_parser("report")
    js.add_parser("list")
    a = p.parse_args(argv)
    if a.cmd == "screen":
        from .net import ConfigError
        try:
            return screen(a)
        except ConfigError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 3
    return journal_cmd(a)


if __name__ == "__main__":
    sys.exit(main())
