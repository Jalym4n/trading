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

CSV_COLS = ["ticker", "company", "cik", "nct_id", "phase", "catalyst_date", "date_type", "months_out",
            "company_status", "true_market_cap", "reported_market_cap", "pre_funded_warrants",
            "share_count_source", "market_cap_confidence", "avg_dollar_vol",
            "reverse_split_date", "reverse_split_ratio", "months_since_reverse_split", "reverse_split_source",
            "reverse_split_cover_check",
            "cash_usd", "cash_asof", "days_since_cash_asof", "burn_q_usd", "burn_status",
            "cash_today_est", "cash_to_mcap_est", "total_debt_usd", "debt_asof", "has_convertible", "ev_est",
            "runway_q", "runway_needed_q", "funded_through",
            "shelf_count", "atm_count", "days_since_latest_atm_filing",
            "n_primary_endpoints", "masking", "allocation", "control_arm", "control_arm_types",
            "enrollment", "enrollment_type", "enrollment_stale", "is_efficacy_scale", "is_single_arm_open_label",
            "n_sites", "patients_per_site", "site_list_maturity",
            "n_versions", "n_endpoint_changes", "endpoint_changes", "pcd_slips", "pcd_slip_months"]


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
    from . import derive as X, integrity as Q

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
        status = X.company_status(info.get("submissions"), mcap, today)
        ev = X.ev_est(mcap, debt.total_debt_usd, aged["cash_today_est"])
        if info["confidence"] != "verified":
            # an unverified share count never drives a cash-vs-market-cap conclusion
            aged["cash_to_mcap_est"], ev = None, None
        row = {
            **aged, **status,
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
            "avg_dollar_vol": round(mkt.avg_dollar_vol) if mkt.avg_dollar_vol else None,
            "cash_usd": run.cash_usd if run else None, "cash_asof": run.cash_asof if run else None,
            "burn_q_usd": run.burn_q_usd if run else None,
            "burn_status": run.burn_status if run else "no_data",
            "runway_q": run.runway_q if run else None,
            "runway_needed_q": run.runway_needed_q if run else
            (round(months / 3 + 2, 2) if months is not None else None),
            "cash_tags": run.cash_tags if run else None,
            "burn_quarters": "; ".join(f"{q['start']}..{q['end']} {q['val']:+,.0f} ({q['derived_from']})"
                                       for q in run.quarters_used) if run and run.quarters_used else None,
            "funded_through": run.funded_through if run else None,
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
        md, facts = D.build(row, rec, hist_md, related, today)
        row.update({"n_primary_endpoints": facts["n_primary"], "masking": facts["masking"],
                    "allocation": facts["allocation"], "control_arm": facts["control_arm"],
                    "control_arm_types": facts["control_arm_types"],
                    "control_arm_basis": facts["control_arm_basis"],
                    "enrollment": facts["enrollment"], "enrollment_type": facts["enrollment_type"],
                    "n_sites": facts["n_sites"], "start_date": facts["start_date"]})
        row.update(X.design_flags(facts["allocation"], facts["masking"], facts["control_arm"],
                                  facts["enrollment_type"], facts["months_since_start"]))
        if hist:
            row.update({"n_versions": hist["n_versions"], "n_endpoint_changes": len(hist["endpoint_changes"]),
                        "endpoint_changes": json.dumps([{k: c[k] for k in ("version", "date", "from", "to", "kind",
                                                                         "status_at_change")}
                                                        for c in hist["endpoint_changes"]]),
                        "pcd_slips": hist["pcd_slips"], "pcd_slip_months": hist["pcd_slip_months"],
                        "_pcd_values": hist["pcd_values"]})
        row.update(X.site_facts(facts["enrollment"], facts["n_sites"], row.get("n_versions")))
        with open(os.path.join(a.out, "dossiers", f"{row['ticker']}-{row['nct_id']}.md"), "w") as f:
            f.write(md)

    with open(os.path.join(a.out, "candidates.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    with open(os.path.join(a.out, "integrity_report.md"), "w") as f:
        f.write(Q.report(rows, CSV_COLS, today))
    with open(os.path.join(a.out, "dropped.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "nct_id", "true_market_cap", "reported_market_cap",
                                              "pre_funded_warrants", "market_cap_confidence", "avg_dollar_vol",
                                              "reverse_split_date", "reverse_split_ratio",
                                              "months_since_reverse_split", "reverse_split_source", "_filter_notes"],
                           extrasaction="ignore")
        w.writeheader()
        for row in dropped:
            w.writerow(row)
    log(f"wrote {len(rows)} candidates, {len(rows)} dossiers -> {a.out} "
        f"(network calls {http.calls['network']}, cache hits {http.calls['cache']})")
    return 0


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
