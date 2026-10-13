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
    return {"patients_per_site": pps, "site_list_maturity": maturity,
            "is_efficacy_scale": (enrollment >= 20) if enrollment is not None else None}


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


FINANCIAL_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "10-KT"}
DEREG_FORMS = {"15-12B", "15-12G", "15-15D", "25-NSE", "25", "15F-12B", "15F-12G", "15F-15D"}


def company_status(submissions: dict | None, market_cap: float | None, today: dt.date,
                   stale_days: int = 270) -> dict:
    """likely_delisted_or_acquired when market_cap is null AND either
      - the newest financial filing (10-K/10-Q/20-F/40-F) AND the newest filing
        of ANY type are both older than stale_days (a live company keeps filing
        8-Ks when its financials lag), or
      - a deregistration / delisting form (15-*, 25) was filed on or after the
        newest financial filing.
    Otherwise active. unknown if the submissions record could not be read."""
    if not submissions:
        return {"company_status": "unknown", "latest_filing_date": None, "latest_filing_form": None,
                "latest_financial_filing": None, "deregistration_filing": None}
    r = submissions.get("filings", {}).get("recent", {})
    rows = list(zip(r.get("filingDate", []), r.get("form", [])))
    latest = max(rows, default=(None, None))
    fin = max((d for d, f in rows if f in FINANCIAL_FORMS), default=None)
    dereg = max(((d, f) for d, f in rows if f in DEREG_FORMS), default=None)

    def age(d):
        return (today - dt.date.fromisoformat(d)).days if d else None
    fin_stale = fin is None or age(fin) > stale_days
    any_stale = latest[0] is None or age(latest[0]) > stale_days
    # a deregistration filed AFTER the last financial report is direct evidence on
    # its own (an old Form 25 for a warrant class is not)
    dereg_after = dereg is not None and (fin is None or dereg[0] >= fin)
    likely = market_cap is None and ((fin_stale and any_stale) or dereg_after)
    return {"company_status": "likely_delisted_or_acquired" if likely else "active",
            "latest_filing_date": latest[0], "latest_filing_form": latest[1],
            "latest_financial_filing": fin,
            "deregistration_filing": f"{dereg[1]} {dereg[0]}" if dereg else None}
