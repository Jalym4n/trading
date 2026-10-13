"""Derived fields and integrity checks, on hand-built fixtures (no network)."""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat.derive import (cash_aging, company_status, control_arm, design_flags, ev_est,  # noqa: E402
                           site_facts)
from biocat.financials import compute_runway, debt_facts  # noqa: E402
from biocat.integrity import boolean_shares, checks, null_shares  # noqa: E402

TODAY = dt.date(2026, 10, 12)


# ------------------------------------------------------------ burn floors --
def _facts(cash, cash_end, ocf_rows):
    return {"facts": {"us-gaap": {
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            {"end": cash_end, "val": cash, "filed": "2026-08-10"}]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf_rows}}}}}


def test_zura_shape_is_burn_implausible():
    # real ZURA 2026: Q1 -18,923,000 direct, H1 YTD -39,824 -> Q2 diff +18,883,176
    f = _facts(205_134_000, "2026-06-30", [
        {"start": "2026-01-01", "end": "2026-03-31", "val": -18_923_000, "filed": "2026-05-10"},
        {"start": "2026-01-01", "end": "2026-06-30", "val": -39_824, "filed": "2026-08-10"}])
    r = compute_runway(f, 6.6, TODAY)
    assert r.burn_status == "burn_implausible" and r.burn_q_usd is None and r.runway_q is None
    assert [q["val"] for q in r.quarters_used] == [-18_923_000, 18_883_176]   # inputs stay visible


def test_runway_above_40_quarters_is_withheld():
    # burn 1M/q (above the 250k floor) on 50M cash = 50 quarters -> withheld
    f = _facts(50_000_000, "2026-06-30", [
        {"start": "2026-01-01", "end": "2026-03-31", "val": -1_000_000, "filed": "2026-05-10"},
        {"start": "2026-01-01", "end": "2026-06-30", "val": -2_000_000, "filed": "2026-08-10"}])
    r = compute_runway(f, 6, TODAY)
    assert r.burn_status == "burn_implausible" and r.runway_q is None


def test_small_company_with_small_burn_is_not_floored():
    # 5M cash burning 200k/q: below the cash threshold, so the floor does not apply (25 quarters)
    f = _facts(5_000_000, "2026-06-30", [
        {"start": "2026-01-01", "end": "2026-03-31", "val": -200_000, "filed": "2026-05-10"},
        {"start": "2026-01-01", "end": "2026-06-30", "val": -400_000, "filed": "2026-08-10"}])
    r = compute_runway(f, 6, TODAY)
    assert r.burn_status == "burning" and r.runway_q == 25.0


# ------------------------------------------------------------ cash aging --
def test_cash_aging_arithmetic_matches_the_worked_examples():
    # ELOX: 62.0M at 2026-06-30, burn 5.121M/q, 104 days -> 62.0 - 5.121 * 104/91.25
    a = cash_aging(61_960_000, "2026-06-30", 5_121_000, "burning", 55_460_946, TODAY)
    assert a["days_since_cash_asof"] == 104
    assert a["cash_today_est"] == round(61_960_000 - 5_121_000 * 104 / 91.25)       # 56,123,507
    assert a["cash_to_mcap_est"] == round(a["cash_today_est"] / 55_460_946, 3)      # 1.012
    # CRBP shape: 117.9M, 27.6M/q -> ~86.4M, 0.83 of a 104.1M cap
    c = cash_aging(117_900_000, "2026-06-30", 27_600_000, "burning", 104_056_159, TODAY)
    assert 0.82 < c["cash_to_mcap_est"] < 0.84


def test_cash_today_est_is_none_unless_burning():
    for status in ("cash_positive", "stale", "no_data", "burn_implausible"):
        a = cash_aging(100e6, "2026-06-30", None, status, 200e6, TODAY)
        assert a["cash_today_est"] is None and a["cash_to_mcap_est"] is None
        assert a["days_since_cash_asof"] == 104


def test_ev_needs_every_input():
    assert ev_est(100e6, 10e6, 60e6) == 50e6
    assert ev_est(100e6, None, 60e6) is None
    assert ev_est(None, 0.0, 60e6) is None


def test_debt_sums_components_at_the_cash_date_and_flags_convertibles():
    f = {"facts": {"us-gaap": {
        "LongTermDebtNoncurrent": {"units": {"USD": [{"end": "2026-06-30", "val": 40e6, "filed": "x"},
                                                     {"end": "2025-12-31", "val": 45e6, "filed": "x"}]}},
        "OperatingLeaseLiabilityNoncurrent": {"units": {"USD": [{"end": "2026-06-30", "val": 2e6, "filed": "x"}]}},
        "ConvertibleNotesPayable": {"units": {"USD": [{"end": "2026-06-30", "val": 25e6, "filed": "x"}]}}}}}
    d = debt_facts(f, "2026-06-30")
    assert d.total_debt_usd == 67e6 and d.debt_asof == "2026-06-30" and d.has_convertible is True
    assert set(d.components) == {"LongTermDebtNoncurrent", "OperatingLeaseLiabilityNoncurrent",
                                 "ConvertibleNotesPayable"}
    none = debt_facts({"facts": {"us-gaap": {}}})
    assert none.total_debt_usd is None and none.has_convertible is None      # unknown, not zero


# ----------------------------------------------------------- control arm --
def _ps(arms, interventions=None):
    m = {}
    if arms is not None:
        m["armGroups"] = arms
    if interventions is not None:
        m["interventions"] = interventions
    return {"armsInterventionsModule": m}


def test_control_arm_three_states_against_each_armgroups_shape():
    cases = [
        # (description, ps, expected)
        ("placebo comparator typed", _ps([{"label": "A", "type": "EXPERIMENTAL"},
                                          {"label": "P", "type": "PLACEBO_COMPARATOR"}]), "yes"),
        ("active comparator typed", _ps([{"label": "A", "type": "EXPERIMENTAL"},
                                         {"label": "SoC", "type": "ACTIVE_COMPARATOR"}]), "yes"),
        ("sham comparator typed", _ps([{"label": "A", "type": "EXPERIMENTAL"},
                                       {"label": "S", "type": "SHAM_COMPARATOR"}]), "yes"),
        # OCGN NCT06388200: untreated control arms typed NO_INTERVENTION
        ("NO_INTERVENTION arm", _ps([{"label": "RHO Arm", "type": "EXPERIMENTAL"},
                                     {"label": "Control for RHO Arm", "type": "NO_INTERVENTION"}]), "yes"),
        # TTRX NCT07355075: vehicle arm mislabelled EXPERIMENTAL, its only intervention "Other: Vehicle"
        ("vehicle arm typed EXPERIMENTAL", _ps([
            {"label": "GX-03", "type": "EXPERIMENTAL", "interventionNames": ["Combination Product: GX-03"]},
            {"label": "Vehicle", "type": "EXPERIMENTAL", "interventionNames": ["Other: Vehicle"]}]), "yes"),
        ("OTHER-typed arm, control only via interventions[].armGroupLabels", _ps(
            [{"label": "Drug X", "type": "EXPERIMENTAL"}, {"label": "Arm B", "type": "OTHER"}],
            [{"name": "Drug X", "armGroupLabels": ["Drug X"]}, {"name": "Matching placebo", "armGroupLabels": ["Arm B"]}]),
         "yes"),
        ("arm with no interventions, label 'Untreated control'", _ps(
            [{"label": "Drug X", "type": "EXPERIMENTAL", "interventionNames": ["Drug: X"]},
             {"label": "Untreated control", "type": "OTHER"}]), "yes"),
        # UPB NCT06966479: double-dummy placebo given WITH active drug in both arms -> no control arm
        ("double-dummy, every arm gets active drug", _ps([
            {"label": "Verekitug 100 mg Q12W and Placebo", "type": "EXPERIMENTAL",
             "interventionNames": ["Drug: Verekitug", "Drug: Placebo"]},
            {"label": "Verekitug 400 mg Q24W and Placebo", "type": "EXPERIMENTAL",
             "interventionNames": ["Drug: Verekitug", "Drug: Placebo"]}]), "no"),
        # DVAX NCT07207408: regimen comparison, every arm the same vaccine
        ("all arms experimental, same product", _ps([
            {"label": f"Part 1 Arm {i}", "type": "EXPERIMENTAL", "interventionNames": ["Biological: rF1V-1018"]}
            for i in range(1, 7)]), "no"),
        ("armGroups absent", _ps(None), "unknown"),
        ("armGroups empty list", _ps([]), "unknown"),
        ("untyped arm, no control vocabulary", _ps([{"label": "Drug X", "interventionNames": ["Drug: X"]},
                                                    {"label": "Drug X high dose"}]), "unknown"),
        ("untyped arm but a typed comparator exists", _ps([{"label": "X"},
                                                          {"label": "P", "type": "PLACEBO_COMPARATOR"}]), "yes"),
    ]
    bad = [(d, control_arm(ps)) for d, ps, want in cases if control_arm(ps)["control_arm"] != want]
    assert not bad, bad
    r = control_arm(cases[4][1])
    assert r["control_arm_types"] == "EXPERIMENTAL|EXPERIMENTAL" and "Vehicle" in r["control_arm_basis"]


def test_design_and_site_flags():
    assert design_flags("NA", "NONE", "no", "ACTUAL", 5)["is_single_arm_open_label"] is True
    assert design_flags("NON_RANDOMIZED", "NONE", "unknown", "ACTUAL", 5)["is_single_arm_open_label"] is True
    assert design_flags("NA", "NONE", "yes", "ACTUAL", 5)["is_single_arm_open_label"] is False
    assert design_flags("RANDOMIZED", "NONE", "no", "ACTUAL", 5)["is_single_arm_open_label"] is False
    assert design_flags("NA", "NONE", "no", "ESTIMATED", 30)["enrollment_stale"] is True
    assert design_flags("NA", "NONE", "no", "ACTUAL", 30)["enrollment_stale"] is False
    s = site_facts(800, 4, 1)          # SGMT NCT07863193
    assert s == {"patients_per_site": 200.0, "site_list_maturity": "new", "is_efficacy_scale": True}
    assert site_facts(1, 1, 5)["is_efficacy_scale"] is False           # VNDA NCT07223632
    assert site_facts(None, 0, None) == {"patients_per_site": None, "site_list_maturity": None,
                                         "is_efficacy_scale": None}


# -------------------------------------------------------- company status --
def _subs(rows):
    return {"filings": {"recent": {"filingDate": [d for d, _ in rows], "form": [f for _, f in rows]}}}


def test_company_status():
    acquired = _subs([("2025-04-02", "15-12B"), ("2025-04-01", "S-8 POS"), ("2025-02-20", "10-K")])   # ITCI shape
    assert company_status(acquired, None, TODAY)["company_status"] == "likely_delisted_or_acquired"
    # FOLD shape: acquired 2026-04, last 10-K only 7 months old, then a Form 25
    recent_close = _subs([("2026-05-07", "25-NSE"), ("2026-04-27", "8-K"), ("2026-02-26", "10-K")])
    assert company_status(recent_close, None, TODAY)["company_status"] == "likely_delisted_or_acquired"
    # financials lag but the company keeps filing 8-Ks: active
    lagging = _subs([("2026-09-30", "8-K"), ("2025-11-10", "10-Q")])
    assert company_status(lagging, None, TODAY)["company_status"] == "active"
    # a price exists: active whatever the filings say
    assert company_status(acquired, 1e8, TODAY)["company_status"] == "active"
    # an old Form 25 for a warrant class, before current financials: not evidence
    old25 = _subs([("2026-08-10", "10-Q"), ("2024-01-05", "25-NSE")])
    assert company_status(old25, None, TODAY)["company_status"] == "active"
    assert company_status(None, None, TODAY)["company_status"] == "unknown"


# ------------------------------------------------------------- integrity --
def test_integrity_checks_catch_the_run_one_defects():
    rows = [
        {"ticker": "TTRX", "nct_id": "NCT07355075", "masking": "QUADRUPLE", "allocation": "RANDOMIZED",
         "control_arm": "no", "n_sites": 1, "enrollment": 190, "phase": "P2/P3"},
        {"ticker": "ZURA", "nct_id": "N2", "burn_status": "burn_implausible", "cash_usd": 205e6},
        {"ticker": "OLD", "nct_id": "N3", "runway_q": 10302.03, "burn_q_usd": 19_912, "cash_usd": 205e6},
        {"ticker": "VNDA", "nct_id": "NCT07223632", "phase": "P2", "enrollment": 1},
        {"ticker": "UPB", "nct_id": "NCT06981078", "pcd_slip_months": -18.0, "days_since_cash_asof": 151},
        {"ticker": "ITCI", "nct_id": "N6", "company_status": "likely_delisted_or_acquired"},
    ]
    caught = [[rid.split()[0] for rid, _ in hits] for _name, hits in checks(rows)]
    assert caught == [["TTRX"],            # masked but control_arm != yes
                      ["TTRX"],            # randomized but control_arm == no
                      ["ZURA", "OLD"],     # runway > 200 / burn floor / burn_implausible
                      ["VNDA"],            # enrollment < 20 on P2/P3
                      ["UPB"],             # cash older than 150 days
                      ["ITCI"],            # likely delisted or acquired
                      ["UPB"],             # pcd moved earlier
                      ["TTRX"]]            # <= 1 site with >= 50 patients


def test_boolean_share_flags_low_discrimination():
    rows = [{"a": True, "b": i < 5, "c": i == 0} for i in range(20)]       # 100%, 25%, 5%
    shares = {c: (s, low) for c, _t, _n, s, low in boolean_shares(rows, ["a", "b", "c"])}
    assert shares["a"] == (1.0, True) and shares["b"] == (0.25, False) and shares["c"] == (0.05, False)
    rows[0]["c"] = False                                                     # 0% -> flagged
    assert boolean_shares(rows, ["c"])[0][4] is True
    assert null_shares([{"x": None}, {"x": 1}, {"x": ""}, {"x": 2}], ["x"]) == [("x", 2, 0.5)]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
