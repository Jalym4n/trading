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
                   or (r.get("burn_q_usd") is not None and r["burn_q_usd"] < 250_000 and (r.get("cash_usd") or 0) > 10e6)
                   or r.get("burn_status") == "burn_implausible"),
        lambda r: f"burn_status={r.get('burn_status')} runway_q={r.get('runway_q')} burn_q_usd={r.get('burn_q_usd')} "
                  f"cash_usd={r.get('cash_usd')} quarters: {r.get('burn_quarters')}")
    add("enrollment < 20 on a P2, P3 or P2/P3 trial",
        lambda r: r.get("phase") in EFFICACY_PHASES and r.get("enrollment") is not None and r["enrollment"] < 20,
        lambda r: f"phase={r.get('phase')} enrollment={r.get('enrollment')} ({r.get('enrollment_type')}) "
                  f"n_sites={r.get('n_sites')}")
    add("days_since_cash_asof > 150",
        lambda r: (r.get("days_since_cash_asof") or 0) > 150,
        lambda r: f"cash_asof={r.get('cash_asof')} days={r.get('days_since_cash_asof')} "
                  f"burn_status={r.get('burn_status')}")
    add("company_status == likely_delisted_or_acquired",
        lambda r: r.get("company_status") == "likely_delisted_or_acquired",
        lambda r: f"market_cap={r.get('market_cap')} latest_financial_filing={r.get('latest_financial_filing')} "
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
    md = [f"# Integrity report, {today.isoformat()}", "",
          f"{len(rows)} candidate rows. Each section lists the rows that fail one check, with the values "
          "that fail it. A listed row is a value to inspect, not a verdict; no row is dropped.", ""]
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
    md += ["", "## Counts", "", "| check | rows caught |", "|---|---|"]
    md += [f"| {name} | {len(hits)} |" for name, hits in res]
    md += [f"| fields null on > {NULL_SHARE_LIMIT:.0%} of rows | {len(nulls)} fields |",
           f"| boolean columns flagged low discrimination | {sum(b[4] for b in bools)} |", ""]
    return "\n".join(md)
