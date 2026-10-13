"""Track record, cash components, stated runway and lead program, on fixtures (no network)."""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat.derive import lead_program  # noqa: E402
from biocat.financials import cash_components, runway_crosscheck, stated_runway  # noqa: E402
from biocat.track import (attribute_press, evidence_grade, molecule_key, molecule_keys,  # noqa: E402
                          why_stopped_outcome)
from biocat.trials import Trial  # noqa: E402


def test_molecule_key_normalises_dose_route_and_code_spelling():
    for name in ["EVO756", "EVO-756", "EVO756 100 mg", "Oral EVO756 300 mg QD", "EVO756, Dose 1",
                 "EVO756 50 mg BID", "evo 756"]:
        assert molecule_key(name) == "evo756", name
    assert molecule_key("Placebo") == "" and molecule_key("Matching vehicle cream") == ""
    assert molecule_key("verekitug 400 mg Q24W") == "verekitug"
    assert molecule_keys("Exaluren (ELX-02)") == ["exaluren", "elx02"]
    assert molecule_keys("Tovorafenib", ["DAY101"]) == ["tovorafenib", "day101"]
    # GNLX NCT06463665: short aliases kept, sentence-like names dropped
    assert molecule_keys("Olvimulogene nanivacirepvec", ["Olvi-Vec", "GL-ONC", "GLV-1h68",
                                                         "Brand of drug is based on institutional procurement"]) == \
        ["olvimulogene nanivacirepvec", "olvi vec", "gl onc", "glv 1h68"]
    assert molecule_keys("Physician's Choice of Immune Checkpoint Inhibitor: pembrolizumab, nivolumab") == []


def test_primary_outcome_classes():
    from biocat.outcomes import classify_outcome, classify_primary
    assert classify_outcome("Number of Study Participants With at Least One TEAE") == "safety"
    assert classify_outcome("Change from baseline in the Clinical Dementia Rating Scale Sum of Boxes (CDR-SB)") == \
        "clinical"
    assert classify_outcome("Area under the plasma-concentration time curve of CT1812") == "pk_pd"
    assert classify_outcome("Change from baseline in Geographic Atrophy (GA) lesion area") == "biomarker"
    assert classify_outcome("Mean change from baseline in monthly migraine days") == "clinical"
    assert classify_outcome("Overall Survival") == "clinical"
    assert classify_outcome("Zzz") == "unclassified"
    # secondaries never enter: SHINE NCT03507790 has ADAS-Cog only as a secondary
    shine = {"outcomesModule": {"primaryOutcomes": [{"measure": "Number of participants with TEAEs"}],
                                "secondaryOutcomes": [{"measure": "Change in ADAS-Cog 11"}]}}
    c = classify_primary(shine)
    assert c["primary_is_safety_only"] and not c["primary_outcome_is_clinical"] and c["primary_outcomes_n"] == 1


def test_evidence_grade():
    assert evidence_grade("yes", "QUADRUPLE") == "controlled_masked"
    assert evidence_grade("yes", "DOUBLE") == "controlled_masked"
    assert evidence_grade("yes", "SINGLE") == "controlled_open"
    assert evidence_grade("yes", "NONE") == "controlled_open"
    assert evidence_grade("no", "NONE") == "single_arm"          # EVMN CIndU NCT06603220: two dose arms, open
    assert evidence_grade("unknown", "NONE") == "unknown"


def _trial(nct, cond, start, completion):
    return {"nct_id": nct, "condition": cond, "start": start, "completion": completion, "why_stopped": None}


CSU = _trial("NCT06873516", "Chronic Spontaneous Urticaria", "2025-03-20", "2026-05-29")
AD = _trial("NCT07150845", "Atopic Dermatitis", "2025-08-26", "2026-07-23")
HEAD_CSU = ("Evommune Announces Top-line Results from EVO756 Phase 2b Trial in Moderate-to-Severe Chronic "
            "Spontaneous Urticaria")
CSU_PR = {"date": "2026-06-29", "url": "u-csu", "outcome": "missed_primary", "doc_head": HEAD_CSU,
          "sentence": HEAD_CSU + " - Trial did not meet the primary endpoint of mean change in UAS7 at 12 weeks - "
                                 "Evommune to continue development of EVO756 in Atopic Dermatitis and Migraine",
          "before": HEAD_CSU + " - Trial "}
AD_PR = {"date": "2026-09-09", "url": "u-ad", "outcome": "missed_primary",
         "doc_head": "Evommune Announces Top-line Results from EVO756 Phase 2b Trial in Atopic Dermatitis",
         "sentence": "The Phase 2b trial enrolled 121 adults with moderate-to-severe AD over a 12-week treatment period, "
                     "and did not meet the primary and secondary endpoints in any of the doses studied.",
         "before": "Evommune Announces Top-line Results from EVO756 Phase 2b Trial in Atopic Dermatitis. The Phase 2b "
                   "trial enrolled 121 adults with moderate-to-severe AD over a 12-week treatment period, and "}
EVO301_PR = {"date": "2026-10-01", "url": "u-301", "outcome": "met_primary",
             "doc_head": "Evommune presents EVO301 data in atopic dermatitis; EVO756 migraine trial enrolling",
             "sentence": "EVO301 met the trial's primary endpoint, achieving a 55% reduction in EASI in atopic dermatitis.",
             "before": "Data Presentation Highlights: EVO301 in atopic dermatitis. "}


def test_press_release_attribution_evmn_shape():
    sents = [CSU_PR, AD_PR, EVO301_PR]
    a = attribute_press(sents, "evo756", AD, [CSU, AD])
    c = attribute_press(sents, "evo756", CSU, [CSU, AD])
    # the CSU release mentions AD AFTER its outcome phrase; it must not be read as the AD result
    assert (a["outcome"], a["url"]) == ("missed_primary", "u-ad")
    assert (c["outcome"], c["url"]) == ("missed_primary", "u-csu")
    # EVO301 met its endpoint in AD; that is another molecule and never counts for EVO756
    only_301 = attribute_press([EVO301_PR], "evo756", AD, [CSU, AD])
    assert only_301 is None


def test_one_sentence_cannot_fail_two_trials():
    # HUMA 8-K 2026-03-19: a V006 sentence; V006 and V007 register identical condition lists
    cond = "Renal Failure, End Stage Renal Disease, Hemodialysis, Vascular Access"
    v006 = {**_trial("NCT02644941", cond, "2016-05-01", "2019-06-01"), "acronym": "HUMANITY"}
    v007 = {**_trial("NCT03183245", cond, "2017-06-01", "2020-10-01"), "acronym": None}
    pr = {"date": "2026-03-19", "url": "u", "outcome": "missed_primary", "doc_head": "Humacyte reports ATEV results",
          "sentence": "our V006 Phase 3 clinical trial in AV access for hemodialysis did not meet its primary endpoint",
          "before": "In end stage renal disease patients requiring hemodialysis, our V006 Phase 3 clinical trial in AV "
                    "access for hemodialysis "}
    v006["study_ids"], v007["study_ids"] = ["CLN-PRO-V006"], ["CLN-PRO-V007"]
    pr["sentence"] += " (ATEV)"
    # "V006" matches V006's org study ID only: attributed to V006, never also to V007
    assert attribute_press([pr], "atev", v006, [v006, v007])["outcome"] == "missed_primary"
    assert attribute_press([pr], "atev", v007, [v006, v007]) is None
    # no identifier at all: a tie is attributed to neither
    v006["study_ids"] = []
    assert attribute_press([pr], "atev", v006, [v006, v007]) is None


def test_why_stopped_futility():
    t = {**AD, "why_stopped": "Study terminated after interim analysis for futility"}
    assert why_stopped_outcome(t)["outcome"] == "missed_primary"
    assert why_stopped_outcome({**AD, "why_stopped": "Sponsor business decision"}) is None


# ----------------------------------------------------------------- cash --
def _facts(rows):
    return {"facts": {"us-gaap": {t: {"units": {"USD": [{"end": "2026-06-30", "val": v, "filed": "x"}]}}
                                  for t, v in rows.items()}}}


def test_cash_is_a_sum_of_categories_and_double_tags_count_once():
    # EVMN 10-Q 2026-06-30: the current HTM securities appear under two tags
    f = _facts({"CashAndCashEquivalentsAtCarryingValue": 60_034_000, "ShortTermInvestments": 140_759_000,
                "HeldToMaturitySecuritiesCurrent": 140_759_000, "LongTermInvestments": 87_214_000,
                "HeldToMaturitySecuritiesNoncurrent": 87_214_000, "RestrictedCashNoncurrent": 1_203_000})
    c = cash_components(f)
    assert c["total_liquid_cash"] == 287_999_000 + 8_000                    # 288,007,000: EVMN's "$288.0M"
    assert c["liquid_cash_ex_long_term"] == 200_793_000                     # what the old screen used
    assert c["restricted_cash"] == 1_203_000                                # reported, excluded
    assert c["cash_tags_found"] == ["CashAndCashEquivalentsAtCarryingValue", "ShortTermInvestments",
                                    "LongTermInvestments"]
    # only a total-including-restricted tag: restricted is taken out
    g = _facts({"CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": 50_000_000,
                "RestrictedCashCurrent": 2_000_000})
    assert cash_components(g)["total_liquid_cash"] == 48_000_000


def test_stated_runway_windows_and_crosscheck():
    s = stated_runway("we estimate that our cash will be sufficient to fund our operating expenses into the third "
                      "quarter of 2027.")
    assert (s["lo"], s["hi"]) == (dt.date(2027, 7, 1), dt.date(2027, 9, 28))
    s2 = stated_runway("we believe we have working capital sufficient to fund operations at least through "
                       "November 2027.")
    assert s2["lo"] == dt.date(2027, 11, 28) and s2["hi"] is None
    s3 = stated_runway("expected to fund operations into mid-2028.")
    assert (s3["lo"], s3["hi"]) == (dt.date(2028, 4, 1), dt.date(2028, 9, 30))
    assert stated_runway("the material weaknesses were remediated for a sufficient period through 2025.") is None
    # TTRX shape: computed 7.78 quarters from 2026-06-30 ends 2028-06-08, stated window Q3 2027 -> +8.3 months
    x = runway_crosscheck(7.78, "2026-06-30", s)
    assert x["computed_runway_end"] == "2028-06-08" and x["runway_discrepancy_months"] == 8.3
    assert runway_crosscheck(4.0, "2026-06-30", s3)["runway_discrepancy_months"] < -6


# --------------------------------------------------------- lead program --
def _t(nct, phases, n, cond):
    return Trial(nct, "t", "S", phases, "RECRUITING", "2027-06", "ESTIMATED", [], n, [cond])


def test_lead_program_facts():
    mig = _t("NCT1", ["PHASE2"], 330, "Migraine")
    p3 = _t("NCT2", ["PHASE3"], 120, "CSU")
    p2b = _t("NCT3", ["PHASE2"], 400, "AD")
    r = lead_program(mig, [mig, p3, p2b])
    assert r["candidate_is_lead_program"] is False and r["lead_program_guess"].startswith("NCT2 P3")
    assert r["candidate_is_latest_phase"] is False and r["candidate_is_largest_trial"] is False
    assert r["sponsor_trial_count_in_window"] == 3 and len(r["sponsor_other_trials"]) == 2
    solo = lead_program(mig, [mig])
    assert solo["candidate_is_lead_program"] is True and solo["sponsor_other_trials"] == []




# --------------------------------------------- fixes 6-8 (runway, dilution, PR) --
def test_runway_ceiling_believes_the_company():
    from biocat.derive import runway_ceiling
    # CGTX 2026-06-30: computed 7.91 q; "into the fourth quarter of 2027" -> 2027-10-01 = 5.02 q
    stmt = {"sentence": "sufficient ... into the fourth quarter of 2027", "lo": dt.date(2027, 10, 1),
            "hi": dt.date(2027, 12, 28), "url": "u"}
    c = runway_ceiling(7.91, stmt, "2026-06-30")
    assert (c["runway_q"], c["runway_source"], c["runway_q_stated"]) == (5.02, "stated", 5.02)
    assert c["runway_disagreement_q"] == 2.89 and c["runway_gap_flag"] is True
    # a floor ("at least the next 12 months") never caps
    floor = {"sentence": "at least the next 12 months", "lo": dt.date(2027, 8, 1), "hi": None}
    f = runway_ceiling(7.91, floor, "2026-06-30")
    assert f["runway_q"] == 7.91 and f["runway_source"] == "computed" and f["runway_q_stated"] is None
    # within a quarter: agreed; no computed burn: the statement is the runway
    assert runway_ceiling(5.5, stmt, "2026-06-30")["runway_source"] == "agreed"
    assert runway_ceiling(None, stmt, "2026-06-30")["runway_q"] == 5.02


def test_dilution_margin_and_shortfall():
    from biocat.dilution import dilution_facts
    today = dt.date(2026, 10, 13)
    split = {"reverse_split_known": True, "months_since_reverse_split": None}
    # CGTX shape: runway 5.02 q from 2026-06-30, primary completion 7.6 months out, $0.877
    d = dilution_facts(0.877, "NasdaqCM", ["2024-09-13"], split, 5.02, 34.8e6, "2026-06-30", 7.6, 60e6, today)
    assert d["sub_dollar"] and d["nasdaq_tier"] == "Capital Market" and d["prior_bid_price_deficiency"]
    assert d["months_runway_minus_months_to_catalyst"] == 1.0 and d["must_raise_before_catalyst"] is True
    d0 = dilution_facts(0.877, "NasdaqCM", [], split, 5.02, 34.8e6, "2026-06-30", 7.6, 60e6, today,
                        readout_lag_months=0)
    assert d0["months_runway_minus_months_to_catalyst"] == 4.0 and d0["must_raise_before_catalyst"] is False
    # shortfall: burn 34.8M / 15.06 months; need to (3.4 + 10.6 + 6) months = 20.0 months
    monthly = 34.8e6 / (5.02 * 3)
    need = monthly * ((today - dt.date(2026, 6, 30)).days / 30.4375 + 10.6 + 6) - 34.8e6
    assert d["dilution_at_current_price_pct"] == round(100 * need / 60e6, 1)


def test_reaction_day_and_divergence():
    from biocat.dilution import divergence, one_day_return
    closes = [("2026-06-22", 1.40), ("2026-06-23", 1.44), ("2026-06-24", 0.89), ("2026-06-25", 0.90)]
    # accepted 07:00 ET (11:00Z, EDT) -> same session; return vs prior close
    assert one_day_return(closes, "2026-06-24T11:00:00.000Z", "2026-06-24") == ("2026-06-24", -0.3819)
    # accepted 16:05 ET -> next session
    assert one_day_return(closes, "2026-06-23T20:05:00.000Z", "2026-06-23")[0] == "2026-06-24"
    prs = [{"date": "2026-06-24", "accepted": "2026-06-24T11:00:00.000Z", "url": "u",
            "headline": "Cognition Therapeutics Aligns on Key Aspects of Pivotal Study"},
           {"date": "2026-06-25", "accepted": "2026-06-25T11:00:00.000Z", "url": "u2",
            "headline": "Cognition Therapeutics to Present at Conference"}]
    dv = divergence(prs, closes)
    assert dv["headline_price_divergence"] and [e["date"] for e in dv["divergence_events"]] == ["2026-06-24"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
