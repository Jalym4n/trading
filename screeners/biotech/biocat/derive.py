"""Derived row fields: pure functions, no network.

Every function returns a FACT computed from named inputs. None means the
inputs were missing; nothing here infers a value from absent data, scores,
or ranks.
"""

from __future__ import annotations

import datetime as dt
import re

COMPARATOR_TYPES = {"PLACEBO_COMPARATOR", "ACTIVE_COMPARATOR", "SHAM_COMPARATOR", "NO_INTERVENTION"}
# An arm counts as a control when EVERY intervention it receives matches this
# vocabulary (TTRX NCT07355075: arm "Vehicle" typed EXPERIMENTAL, intervention
# "Vehicle"). A placebo given alongside active drug is a double-dummy, not a
# control arm (UPB NCT06966479: both arms "Verekitug ... and Placebo").
CONTROL_WORDS = re.compile(r"\b(placebo|vehicle|sham|saline|dummy|standard of care|standard[- ]of[- ]care|"
                           r"soc|best supportive care|usual care|no treatment|no intervention|untreated|"
                           r"observation|control)\b", re.I)
MASKED = {"SINGLE", "DOUBLE", "TRIPLE", "QUADRUPLE"}
DAYS_PER_QUARTER = 91.25


def _strip_type(name: str) -> str:
    """'Drug: Placebo' -> 'Placebo' (armGroups[].interventionNames carry a type prefix)."""
    return name.split(":", 1)[1].strip() if ":" in name else name.strip()


def control_arm(ps: dict) -> dict:
    """Three-state control-arm fact from armsInterventionsModule.

    yes      an arm typed PLACEBO/ACTIVE/SHAM_COMPARATOR or NO_INTERVENTION, or an
             arm (any type) whose interventions are all control vocabulary, or
             an arm with no interventions whose label is control vocabulary
    unknown  armGroups absent, or some arm untyped and no control found
    no       every arm typed and none of the above
    Returns {control_arm, control_arm_types, control_arm_basis}.
    """
    aim = (ps or {}).get("armsInterventionsModule") or {}
    arms = aim.get("armGroups") or []
    if not arms:
        return {"control_arm": "unknown", "control_arm_types": "", "control_arm_basis": "armGroups absent"}
    types = [a.get("type") or "UNTYPED" for a in arms]
    raw_types = "|".join(types)
    by_label: dict[str, list[str]] = {}
    for iv in aim.get("interventions") or []:
        for lbl in iv.get("armGroupLabels") or []:
            by_label.setdefault(lbl, []).append(iv.get("name") or "")
    for i, a in enumerate(arms):
        t = a.get("type")
        if t in COMPARATOR_TYPES:
            return {"control_arm": "yes", "control_arm_types": raw_types,
                    "control_arm_basis": f"armGroups[{i}].type={t}"}
        names = [_strip_type(n) for n in (a.get("interventionNames") or [])] or by_label.get(a.get("label"), [])
        if names and all(CONTROL_WORDS.search(n) for n in names):
            return {"control_arm": "yes", "control_arm_types": raw_types,
                    "control_arm_basis": f"armGroups[{i}] ({t}) receives only {names}"}
        if not names and CONTROL_WORDS.search(a.get("label") or ""):
            return {"control_arm": "yes", "control_arm_types": raw_types,
                    "control_arm_basis": f"armGroups[{i}] ({t}) has no interventions, label '{a.get('label')}'"}
    if "UNTYPED" in types:
        return {"control_arm": "unknown", "control_arm_types": raw_types,
                "control_arm_basis": "untyped armGroups and no control arm found"}
    return {"control_arm": "no", "control_arm_types": raw_types,
            "control_arm_basis": f"{len(arms)} typed arms; none comparator-typed or control-only"}


def design_flags(allocation: str | None, masking: str | None, ctrl: str,
                 enrollment_type: str | None, months_since_start: float | None) -> dict:
    """Mechanical design facts. Not verdicts."""
    return {
        "is_single_arm_open_label": (allocation in ("NA", "NON_RANDOMIZED") and ctrl != "yes"
                                     and masking == "NONE"),
        "enrollment_stale": (enrollment_type == "ESTIMATED" and months_since_start is not None
                             and months_since_start > 24),
    }


def site_facts(enrollment: int | None, n_sites: int | None, n_versions: int | None) -> dict:
    """patients_per_site, guarded: a registration with <= 2 versions often has
    not listed its sites yet (SGMT NCT07863193: 800 patients, 4 sites, v1)."""
    pps = round(enrollment / n_sites, 1) if enrollment and n_sites else None
    maturity = None if n_versions is None else ("new" if n_versions <= 2 else "established")
    return {"patients_per_site": pps, "site_list_maturity": maturity}


def cash_aging(cash_usd: float | None, cash_asof: str | None, burn_q_usd: float | None,
               burn_status: str | None, market_cap: float | None, today: dt.date) -> dict:
    """cash_today_est = cash - burn_q x (days since cash_asof / 91.25). An ESTIMATE:
    linear burn, no financing since the balance date. None unless burning."""
    days = (today - dt.date.fromisoformat(cash_asof)).days if cash_asof else None
    est = None
    if burn_status == "burning" and cash_usd is not None and burn_q_usd is not None and days is not None:
        est = round(cash_usd - burn_q_usd * (days / DAYS_PER_QUARTER))
    ratio = round(est / market_cap, 3) if (est is not None and market_cap) else None
    return {"days_since_cash_asof": days, "cash_today_est": est, "cash_to_mcap_est": ratio}


def ev_est(market_cap: float | None, total_debt_usd: float | None, cash_today_est: float | None) -> float | None:
    """market_cap + total_debt - cash_today_est; None if any input is unknown."""
    if market_cap is None or total_debt_usd is None or cash_today_est is None:
        return None
    return round(market_cap + total_debt_usd - cash_today_est)


FINANCIAL_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-KT"}
ANNUAL_ONLY_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
FORM_25 = {"25", "25-NSE", "25/A", "25-NSE/A"}
FORM_15 = {"15-12B", "15-12G", "15-15D", "15F-12B", "15F-12G", "15F-15D", "15-12B/A", "15-12G/A", "15-15D/A"}
STALE_DAYS = 270
STALE_DAYS_ANNUAL_FILER = 455   # a 20-F / 40-F filer reports once a year (12 months + 3-month filing window)


def company_status(submissions: dict | None, today: dt.date, stale_days: int = STALE_DAYS) -> dict:
    """Delisting facts from the SEC submissions record.

    form_25_filed  a Form 25 / 25-NSE (Notification of Removal from Listing) filed ON OR AFTER the
                   newest periodic financial report. An older Form 25 sits before current financials
                   (a warrant class or note series delisted; the common still reports) and is shown in
                   form_25_older, not counted.
    form_15_filed  a Form 15 deregistration, same timing rule. Demoted: a 15-12G alone is often routine.
    delisting_signal  form_25 > no_recent_financial_filing > form_15_only > none
    company_status    delisted on form_25 or no_recent_financial_filing (no 10-K/10-Q/20-F for more
                      than 270 days; 455 for an annual-only 20-F/40-F filer); check_manually on
                      form_15_only; active otherwise; unknown when the record could not be read."""
    empty = {"company_status": "unknown", "delisting_signal": None, "form_25_filed": None, "form_25_date": None,
             "form_25_accession": None, "form_25_older": None, "form_15_filed": None, "form_15_date": None,
             "latest_filing_date": None, "latest_filing_form": None, "latest_financial_filing": None,
             "latest_financial_form": None, "days_since_financial_filing": None, "deregistration_filing": None}
    if not submissions:
        return empty
    r = submissions.get("filings", {}).get("recent", {})
    accs = r.get("accessionNumber") or [None] * len(r.get("form", []))
    rows = list(zip(r.get("filingDate", []), r.get("form", []), accs))
    latest = max(rows, default=(None, None, None))
    fin = max(((d, f) for d, f, _ in rows if f in FINANCIAL_FORMS), default=(None, None))
    f25 = sorted(((d, f, a) for d, f, a in rows if f in FORM_25), reverse=True)
    f15 = sorted(((d, f, a) for d, f, a in rows if f in FORM_15), reverse=True)
    after = [x for x in f25 if fin[0] is None or x[0] >= fin[0]]
    older = [x for x in f25 if x not in after]
    f15_after = [x for x in f15 if fin[0] is None or x[0] >= fin[0]]
    days_fin = (today - dt.date.fromisoformat(fin[0])).days if fin[0] else None
    limit = STALE_DAYS_ANNUAL_FILER if fin[1] in ANNUAL_ONLY_FORMS else stale_days
    stale = days_fin is None or days_fin > limit
    if after:
        signal = "form_25"
    elif stale:
        signal = "no_recent_financial_filing"
    elif f15_after:
        signal = "form_15_only"
    else:
        signal = "none"
    status = {"form_25": "delisted", "no_recent_financial_filing": "delisted",
              "form_15_only": "check_manually", "none": "active"}[signal]
    return {**empty, "company_status": status, "delisting_signal": signal,
            "form_25_filed": bool(after), "form_25_date": after[-1][0] if after else None,
            "form_25_accession": after[-1][2] if after else None,
            "form_25_older": "|".join(f"{f} {d}" for d, f, _ in older) or None,
            "form_15_filed": bool(f15_after), "form_15_date": f15_after[-1][0] if f15_after else None,
            "latest_filing_date": latest[0], "latest_filing_form": latest[1],
            "latest_financial_filing": fin[0], "latest_financial_form": fin[1],
            "days_since_financial_filing": days_fin,
            "deregistration_filing": "; ".join(f"{f} {d}" for d, f, _ in (after + f15_after)) or None}


def lead_program(candidate, sponsor_trials: list) -> dict:
    """Where the candidate sits among the sponsor's trials in the screening
    window. lead_program_guess = highest phase, then largest enrollment (a
    mechanical tie-break, not a judgment of which program matters). The
    candidate is never switched; a False candidate_is_lead_program is shown."""
    trials = [t for t in sponsor_trials]
    def key(t):
        return (t.phase_rank, -(t.enrollment or 0))
    lead = min(trials, key=key) if trials else None
    max_n = max((t.enrollment or 0) for t in trials) if trials else 0
    best_phase = min(t.phase_rank for t in trials) if trials else 0
    others = [{"nct": t.nct_id, "phase": t.phase, "n": t.enrollment, "condition": ", ".join(t.conditions)[:80]}
              for t in trials if t.nct_id != candidate.nct_id]
    return {
        "sponsor_trial_count_in_window": len(trials),
        "candidate_is_largest_trial": (candidate.enrollment or 0) >= max_n,
        "candidate_is_latest_phase": candidate.phase_rank <= best_phase,
        "sponsor_other_trials": others,
        "lead_program_guess": (f"{lead.nct_id} {lead.phase} n={lead.enrollment} {', '.join(lead.conditions)[:60]}"
                               if lead else None),
        "candidate_is_lead_program": lead is not None and lead.nct_id == candidate.nct_id,
    }


def runway_ceiling(computed_q: float | None, stmt: dict | None, cash_as_of: str | None) -> dict:
    """The company's own runway statement caps the computed one.
    runway_q_stated = (stated end - cash date) / 91.25 days, where stated end is
    the START of the stated period ('into the fourth quarter of 2027' -> 2027-10-01;
    'through 2027' -> 2027-12-31). 'At least ...' statements are floors and never
    cap. runway_q = min(computed, stated); source agreed when within 1 quarter."""
    out = {"runway_q_computed": computed_q, "runway_stated_text": stmt["sentence"] if stmt else None,
           "runway_stated_source": stmt.get("url") if stmt else None, "runway_stated_end_date": None,
           "runway_q_stated": None, "runway_disagreement_q": None, "runway_gap_flag": None,
           "runway_q": computed_q, "runway_source": "computed" if computed_q is not None else None}
    if stmt and stmt.get("hi") is not None and cash_as_of:
        end = stmt["lo"]
        out["runway_stated_end_date"] = end.isoformat()
        sq = round((end - dt.date.fromisoformat(cash_as_of)).days / DAYS_PER_QUARTER, 2)
        out["runway_q_stated"] = sq
        if computed_q is None:
            out["runway_q"], out["runway_source"] = sq, "stated"
        else:
            d = round(computed_q - sq, 2)
            out["runway_disagreement_q"] = d
            out["runway_gap_flag"] = d > 2.0
            out["runway_q"] = min(computed_q, sq)
            out["runway_source"] = "agreed" if abs(d) <= 1.0 else ("stated" if sq < computed_q else "computed")
    return out
