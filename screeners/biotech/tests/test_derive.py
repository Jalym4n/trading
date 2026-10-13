"""Derived fields and integrity checks, on hand-built fixtures (no network)."""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat.derive import (cash_aging, company_status, control_arm, design_flags, ev_est,  # noqa: E402
                           site_facts)
from biocat.financials import (compute_runway, cover_step_down, debt_facts, prefunded_from_instance,  # noqa: E402
                               prefunded_from_text)
from biocat.market import feed_split_events, latest_reverse_split  # noqa: E402
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
    assert s == {"patients_per_site": 200.0, "site_list_maturity": "new"}
    assert site_facts(None, 0, None) == {"patients_per_site": None, "site_list_maturity": None}


# -------------------------------------------------------- company status --
def _subs(rows):
    return {"filings": {"recent": {"filingDate": [d for d, _ in rows], "form": [f for _, f in rows]}}}


def test_company_status():
    st = lambda rows: company_status(_subs(rows), TODAY)  # noqa: E731
    # APLS shape: 10-Q, then the exchange's 25-NSE, then a 15-12G. The 15 is later; the 25 decides.
    apls = st([("2026-05-26", "15-12G"), ("2026-05-14", "25-NSE"), ("2026-05-07", "10-Q")])
    assert (apls["company_status"], apls["delisting_signal"], apls["form_25_filed"], apls["form_25_date"]) == \
        ("delisted", "form_25", True, "2026-05-14")
    assert apls["form_15_filed"] and apls["form_15_date"] == "2026-05-26"
    # a 15-12G alone is often routine deregistration by a company that still trades
    only15 = st([("2026-09-01", "15-12G"), ("2026-08-10", "10-Q")])
    assert (only15["company_status"], only15["delisting_signal"]) == ("check_manually", "form_15_only")
    # no periodic financial filing for > 270 days, whatever else is filed
    lagging = st([("2026-09-30", "8-K"), ("2025-11-10", "10-Q")])
    assert (lagging["company_status"], lagging["delisting_signal"]) == ("delisted", "no_recent_financial_filing")
    # an annual-only 20-F filer is not stale at 10 months
    annual = st([("2026-09-30", "6-K"), ("2025-12-15", "20-F")])
    assert annual["company_status"] == "active"
    # an old Form 25 for a warrant class, before current financials: not counted, shown
    old25 = st([("2026-08-10", "10-Q"), ("2024-01-05", "25-NSE")])
    assert (old25["company_status"], old25["form_25_filed"], old25["form_25_older"]) == \
        ("active", False, "25-NSE 2024-01-05")
    assert company_status(None, TODAY)["company_status"] == "unknown"
    # HUMA: the exchange's 25-NSE after the 10-Q removes the WARRANTS, not the common stock
    huma = _subs([("2026-08-25", "25-NSE"), ("2026-08-12", "10-Q")])
    huma["filings"]["recent"]["accessionNumber"] = ["0001354457-26-000819", "x"]
    w = company_status(huma, TODAY, form25_class={"0001354457-26-000819": "Redeemable Warrants, each whole warrant "
                                                                          "exercisable for one share of Common Stock"})
    assert (w["company_status"], w["form_25_filed"]) == ("active", False) and "Redeemable Warrants" in w["form_25_older"]
    c = company_status(huma, TODAY, form25_class={"0001354457-26-000819": "Common Stock"})
    assert c["company_status"] == "delisted"
    # RTSN / STA / NUOX: S-1 and new-IPO filers have never filed a 10-Q; not delisted
    ipo = st([("2026-10-09", "424B4"), ("2026-10-08", "EFFECT"), ("2025-12-11", "S-1")])
    assert (ipo["company_status"], ipo["delisting_signal"]) == ("check_manually", "no_periodic_filing_yet")


# ------------------------------------------------------------- integrity --
def test_integrity_checks_catch_the_run_one_defects():
    rows = [
        {"ticker": "TTRX", "nct_id": "NCT07355075", "masking": "QUADRUPLE", "allocation": "RANDOMIZED",
         "control_arm": "no", "n_sites": 1, "enrollment": 190, "phase": "P2/P3"},
        {"ticker": "ZURA", "nct_id": "N2", "burn_status": "burn_implausible", "cash_usd": 205e6},
        {"ticker": "OLD", "nct_id": "N3", "runway_q": 10302.03, "burn_q_usd": 19_912, "cash_usd": 205e6},
        {"ticker": "VNDA", "nct_id": "NCT07223632", "phase": "P2", "enrollment": 1},
        {"ticker": "UPB", "nct_id": "NCT06981078", "pcd_slip_months": -18.0, "days_since_cash_asof": 151},
        {"ticker": "ITCI", "nct_id": "N6", "company_status": "delisted"},
    ]
    caught = [[rid.split()[0] for rid, _ in hits] for _name, hits in checks(rows)]
    assert caught == [["TTRX"],            # masked but control_arm != yes
                      ["TTRX"],            # randomized but control_arm == no
                      ["ZURA", "OLD"],     # runway > 200 / burn floor / burn_implausible
                      ["VNDA"],            # enrollment < 20 on P2/P3
                      ["UPB"],             # cash older than 150 days
                      ["ITCI"],            # delisted
                      [],                  # check_manually (Form 15 only)
                      ["UPB"],             # pcd moved earlier
                      ["TTRX"],            # <= 1 site with >= 50 patients
                      [],                  # market_cap_confidence unverified
                      []]                  # XBRL and text disagree


def test_boolean_share_flags_low_discrimination():
    rows = [{"a": True, "b": i < 5, "c": i == 0} for i in range(20)]       # 100%, 25%, 5%
    shares = {c: (s, low) for c, _t, _n, s, low in boolean_shares(rows, ["a", "b", "c"])}
    assert shares["a"] == (1.0, True) and shares["b"] == (0.25, False) and shares["c"] == (0.05, False)
    rows[0]["c"] = False                                                     # 0% -> flagged
    assert boolean_shares(rows, ["c"])[0][4] is True
    assert null_shares([{"x": None}, {"x": 1}, {"x": ""}, {"x": 2}], ["x"]) == [("x", 2, 0.5)]


# --------------------------------------------------- pre-funded warrants --
def _instance(period_end, facts):
    """facts: [(concept, value, date, [(axis, member), ...])] -> minimal XBRL instance."""
    ctx, body = [], []
    for i, (concept, val, date, mems) in enumerate(facts):
        seg = "".join(f'<xbrldi:explicitMember dimension="{a}">{m}</xbrldi:explicitMember>' for a, m in mems)
        seg = f"<xbrli:segment>{seg}</xbrli:segment>" if seg else ""
        ctx.append(f'<xbrli:context id="c{i}"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>'
                   f'{seg}</xbrli:entity><xbrli:period><xbrli:instant>{date}</xbrli:instant></xbrli:period>'
                   f'</xbrli:context>')
        body.append(f'<us-gaap:{concept} contextRef="c{i}">{val}</us-gaap:{concept}>')
    return ('<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:us-gaap="http://fasb.org/us-gaap/2024" '
            'xmlns:dei="http://xbrl.sec.gov/dei/2024">'
            + "".join(ctx) + f"<dei:DocumentPeriodEndDate>{period_end}</dei:DocumentPeriodEndDate>"
            + "".join(body) + "</xbrli:xbrl>")


OUT, STRIKE = "ClassOfWarrantOrRightOutstanding", "ClassOfWarrantOrRightExercisePriceOfWarrantsOrRights1"
D = "2026-06-30"
SAX = "us-gaap:SubsidiarySaleOfStockAxis"


def test_prefunded_elox_shape_reconciles_and_ignores_duplicate_axes():
    # ELOX 10-Q 2026-06-30, reduced: class axis sums to the total; the June pre-funded
    # class and an exercise event ALSO appear on PlanNameAxis with penny strikes
    xml = _instance(D, [
        (OUT, 7434321, D, []),
        (OUT, 50402, D, [(SAX, "elox:CommonWarrantsMember")]), (STRIKE, 54.07, D, [(SAX, "elox:CommonWarrantsMember")]),
        (OUT, 4358919, D, [(SAX, "elox:March2026PreFundedWarrantMember")]),
        (OUT, 3025000, D, [(SAX, "elox:JuneTwoThousandTwentySixPreFundedWarrantMember")]),
        (OUT, 3025000, D, [("us-gaap:PlanNameAxis", "elox:SecondQuarter2026EquityActivityMember")]),
        (STRIKE, 0.01, D, [("us-gaap:PlanNameAxis", "elox:SecondQuarter2026EquityActivityMember")]),
        (OUT, 600000, D, [("srt:InvesteeAxis", "elox:DomiciliumMember"), ("us-gaap:PlanNameAxis", "elox:Q2Member")]),
    ])
    p = prefunded_from_instance(xml)
    assert (p.shares, p.status, p.asof) == (7383919.0, "reconciled", D)


def test_prefunded_reconciled_by_strike_without_a_name():
    xml = _instance(D, [(OUT, 1100, D, []),
                        (OUT, 1000, D, [(SAX, "x:Series2025Member")]), (STRIKE, 0.0001, D, [(SAX, "x:Series2025Member")]),
                        (OUT, 100, D, [(SAX, "x:Series2023Member")]), (STRIKE, 5.0, D, [(SAX, "x:Series2023Member")])])
    p = prefunded_from_instance(xml)
    assert (p.shares, p.status) == (1000.0, "reconciled")


def test_prefunded_outside_the_total_is_added():
    # IRD 10-Q 2026-06-30: total 19,616,292 = two exercise-price classes; 16,009,928
    # pre-funded reported outside it
    ax = "us-gaap:ClassOfWarrantOrRightAxis"
    xml = _instance(D, [(OUT, 19616292, D, []),
                        (OUT, 18439821, D, [(ax, "ird:WarrantExercisePriceOneMember")]),
                        (OUT, 1176471, D, [(ax, "ird:WarrantExercisePriceTwoMember")]),
                        (OUT, 16009928, D, [("us-gaap:EquityAxis", "ird:PreFundedWarrantsMember")]),
                        (STRIKE, 0.0001, D, [("us-gaap:EquityAxis", "ird:PreFundedWarrantsMember")])])
    p = prefunded_from_instance(xml)
    assert (p.shares, p.status) == (16009928.0, "named")


def test_prefunded_named_without_a_total():
    # ZURA 10-Q 2026-06-30: one class, no total
    xml = _instance(D, [(OUT, 29295396, D, [(SAX, "zura:PreFundedWarrantsMember")]),
                        (STRIKE, 0.001, D, [(SAX, "zura:PreFundedWarrantsMember")])])
    p = prefunded_from_instance(xml)
    assert (p.shares, p.status) == (29295396.0, "named")


def test_prefunded_only_at_an_old_event_date_is_not_determinable():
    # NKTX: the latest 10-Q tags pre-funded warrants only at the 2024-03-31 issuance
    xml = _instance(D, [(OUT, 3000031, "2024-03-31", [(SAX, "nktx:PreFundedWarrantMember"),
                                                      ("us-gaap:SubsidiarySaleOfStockAxis2", "nktx:SaleMember")])])
    p = prefunded_from_instance(xml)
    assert p.shares is None and p.status == "not_determinable"


def test_prefunded_none_when_no_class_is_prefunded():
    assert prefunded_from_instance(_instance(D, [(OUT, 514500, D, [])])).shares == 0.0      # CGTX: total only
    assert prefunded_from_instance(_instance(D, [])).status == "none"                        # no warrant facts


# ------------------------------------------ pre-funded warrants: text path --
def test_prefunded_text_states_a_balance():
    pali_cover = ("As of August 6, 2026, there were 174,270,558 shares of common stock, $0.01 par value, outstanding. "
                  "This number does not include 37,814,754 shares of common stock issuable upon the exercise of "
                  "pre-funded warrants outstanding as of August 6, 2026 (which are immediately exercisable).")
    r = prefunded_from_text(pali_cover)
    assert (r["count"], r["kind"], r["verified"]) == (37814754.0, "cover_note", True)
    pali_note = ("As of June 30, 2026 , 37,814,754 of the October 2025 Offering Pre-funded Common Stock Warrants "
                 "remained unexercised.")
    assert prefunded_from_text(pali_note)["count"] == 37814754.0
    # ACRV reserved-shares table: current column is a dash (all exercised), prior 7,060,000
    acrv = ("June 30, 2026 December 31, 2025 Options to purchase common stock 8,132,397 6,176,141 Pre-funded "
            "warrants to purchase common stock — 7,060,000 Unvested restricted stock units 1,018,593 222,252")
    r = prefunded_from_text(acrv)
    assert (r["count"], r["kind"], r["verified"]) == (0.0, "table_row", True)


def test_prefunded_text_average_or_issuance_is_not_a_verified_balance():
    # NKTX EPS denominator: an average for the period
    nktx = ("Denominator: Weighted average common shares outstanding 71,494,824 70,977,594 Add: weighted average "
            "of common stock to be issued upon exercise of pre-funded warrants 3,000,031 3,000,031 3,000,031")
    r = prefunded_from_text(nktx)
    assert (r["count"], r["kind"], r["verified"]) == (3000031.0, "eps_weighted", False)
    # VTVT: issuance sentence + EPS policy sentence, no balance stated
    vtvt = ("In August 2025, the Company issued and sold 682,018 shares of Class A common stock and Pre-funded "
            "Warrants to purchase 4,561,714 shares of Class A common stock at a purchase price of $ 15.255. "
            "The shares underlying the pre-funded warrants have been included in the calculation of the weighted "
            "average number of shares outstanding.")
    r = prefunded_from_text(vtvt)
    assert r["count"] is None and r["mentions"] == 2
    r = prefunded_from_text("No warrants here.")
    assert (r["count"], r["kind"], r["verified"], r["mentions"], r["contexts"]) == (None, None, False, 0, [])


def test_prefunded_xbrl_other_class_tags():
    # CRBP 10-Q: ClassOfWarrantOrRightNumberOfSecuritiesCalledByWarrantsOrRights on Pre-FundedWarrantsMember,
    # tagged twice at the balance date; EYPT-style issuance date facts are ignored
    called = "ClassOfWarrantOrRightNumberOfSecuritiesCalledByWarrantsOrRights"
    xml = _instance(D, [(called, 1025000, D, [(SAX, "crbp:Pre-FundedWarrantsMember")]),
                        (called, 1025000, D, [(SAX, "crbp:Pre-FundedWarrantsMember")]),
                        (called, 550000, "2025-06-30", [(SAX, "bmea:PreFundedWarrantsMember")])])
    p = prefunded_from_instance(xml)
    assert (p.shares, p.status) == (1025000.0, "prefunded_tag")


def test_prefunded_mentions_ignore_the_inline_xbrl_header():
    # RNTX: "22 mentions" were member names in the hidden ix:header
    raw = ("<ix:header><xbrli:context>rntx:PreFundedWarrantsMember rntx:BiosPre-FundedWarrantsMember</xbrli:context>"
           "</ix:header><p>The Pre-Funded Warrants are classified as equity.</p>")
    r = prefunded_from_text(raw)
    assert r["mentions"] == 1 and r["count"] is None and len(r["contexts"]) == 1


def test_prefunded_footnote_tables():
    # A: outstanding-securities table, row labelled pre-funded, current column first
    a = ("<table><tr><td></td><td>June 30, 2026</td><td>December 31, 2025</td></tr>"
         "<tr><td>Common stock warrants outstanding</td><td>1,200,000</td><td>1,200,000</td></tr>"
         "<tr><td>Pre-funded warrants</td><td>4,250,000</td><td>5,000,000</td></tr></table>")
    r = prefunded_from_text(a)
    assert (r["count"], r["method"], r["verified"]) == (4250000.0, "footnote_table", True)
    # B: roll-forward with a pre-funded COLUMN; the last Outstanding row
    b = ("<table><tr><td></td><td>Pre-funded warrants</td><td>Common warrants</td></tr>"
         "<tr><td>Outstanding at December 31, 2025</td><td>3,000,000</td><td>900,000</td></tr>"
         "<tr><td>Exercised</td><td>(1,000,000)</td><td>—</td></tr>"
         "<tr><td>Outstanding at June 30, 2026</td><td>2,000,000</td><td>900,000</td></tr></table>")
    assert prefunded_from_text(b)["count"] == 2000000.0
    # RNXT: a pre-funded ISSUANCE row inside a roll-forward is not a balance
    c = ("<table><tr><td></td><td>Warrants</td><td>Weighted average exercise price</td></tr>"
         "<tr><td>Outstanding as of December 31, 2025</td><td>24,991,784</td><td>$ 2.39</td></tr>"
         "<tr><td>Issued in March 2026 to:</td></tr><tr><td>Pre-funded warrants</td><td>2,200,000</td>"
         "<td>$ 0.0001</td></tr><tr><td>Outstanding as of June 30, 2026</td><td>28,554,994</td><td>$ 2.25</td></tr>"
         "</table>")
    assert prefunded_from_text(c)["count"] is None


def test_prefunded_balance_sheet_parenthetical():
    t = ("Common stock, $0.0001 par value; 52,113,004 shares issued and outstanding, excluding 4,250,000 "
         "pre-funded warrants")
    r = prefunded_from_text(t)
    assert (r["count"], r["method"]) == (4250000.0, "balance_sheet_parenthetical")


# ------------------------------------------------------------ reverse splits --
def test_latest_reverse_split_prefers_sec_and_merges_sources():
    sec = [{"effective_date": "2026-05-29", "date_basis": "stated effective date", "filing_date": "2026-06-01",
            "ratio": "1-for-11", "source": "8-K 0001193125-26-249692", "evidence": "x"}]
    # ELOX: price feed starts at the 2026-06-09 relisting, so it has no split at all
    r = latest_reverse_split(None, sec, TODAY)
    assert (r["reverse_split_date"], r["reverse_split_ratio"]) == ("2026-05-29", "1-for-11")
    assert r["months_since_reverse_split"] == 4.5
    # the same split seen by both sources is one split, SEC record kept
    feed = feed_split_events(["2026-05-29 1.0:11.0"])
    r2 = latest_reverse_split(feed, sec, TODAY)
    assert r2["reverse_split_date"] == "2026-05-29" and "confirmed by price feed" in r2["reverse_split_source"]
    assert feed[0]["ratio"] == "1-for-11"
    # nothing readable -> unknown, not "none"
    u = latest_reverse_split(None, None, TODAY)
    assert u["reverse_split_known"] is False and u["reverse_split_date"] is None
    assert latest_reverse_split([], [], TODAY)["reverse_split_known"] is True


def test_cover_step_down_corroborates_a_split():
    f = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2026-03-12", "val": 5071935}, {"end": "2026-05-01", "val": 7573935},
        {"end": "2026-08-07", "val": 4036459}]}}}}}
    assert cover_step_down(f, "2026-05-29") == "2026-05-01 7,573,935 -> 2026-08-07 4,036,459 (x0.53)"
    assert cover_step_down(f, "2025-01-01") == "no cover-count step-down spanning 2025-01-01"
    assert cover_step_down({"facts": {}}) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
