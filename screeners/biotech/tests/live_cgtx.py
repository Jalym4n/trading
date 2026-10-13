"""LIVE validation on CGTX / CT1812 (Cognition Therapeutics), the case in fixes 5-8.
Run AFTER the screen:  BIOCAT_UA="Name email" python3 tests/live_cgtx.py

Expected (fix request, checked against the sources 2026-10-13):
  track record      >= 8 prior trials, 0 with a clinical primary -> "untested"
  runway            company guidance caps the computed runway (believe the company)
  dilution          must_raise_before_catalyst and sub_dollar -> excluded_dilution_risk.csv
                    (readout = primary completion 2027-05-31 + the default 3-month readout lag; with
                    lag 0 the margin is ~4 months and the kill does not fire)
  divergence        2026-06-24 "Aligns on Key Aspects of Pivotal Study", about -38%
"""
import csv
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from biocat import derive as X, dilution as DL, dossier as D, financials as F, track as TR, trials as T  # noqa: E402
from biocat.cli import company_info  # noqa: E402
from biocat.net import Http  # noqa: E402

OUT = os.path.join(HERE, "..", "biocat_out")
NCT = "NCT05531656"

if __name__ == "__main__":
    today = dt.date.today()
    http = Http(cache_dir=os.path.join(OUT, ".cache"), ttl_hours=24)
    cik = next(int(r["cik"]) for r in csv.DictReader(open(os.path.join(OUT, "identity.csv")))
               if r["ticker"] == "CGTX")
    info = company_info(http, cik, "CGTX", today)
    rec = T.fetch_full(http, NCT)
    tr = TR.track_record(http, rec, "Cognition Therapeutics, Inc.", cik, info["submissions"],
                         D.experimental_interventions(rec["protocolSection"]), today)
    print({k: tr[k] for k in ("prior_trials_total", "prior_trials_with_clinical_primary", "molecule_track_record")})
    assert tr["prior_trials_total"] >= 8 and tr["prior_trials_with_clinical_primary"] == 0
    assert tr["molecule_track_record"] == "untested"

    pcd = T.parse_date(rec["protocolSection"]["statusModule"]["primaryCompletionDateStruct"]["date"])
    months = T.months_between(today, pcd)
    run = F.compute_runway(info["facts"], months, today)
    ceil = X.runway_ceiling(run.runway_q, info["runway_stmt"], run.components["cash_as_of_date"])
    print("runway:", {k: ceil[k] for k in ("runway_q_computed", "runway_q_stated", "runway_stated_end_date",
                                           "runway_q", "runway_source", "runway_disagreement_q")})
    print("stated:", ceil["runway_stated_text"])
    assert ceil["runway_q_stated"] is not None and ceil["runway_q"] == min(ceil["runway_q_computed"],
                                                                          ceil["runway_q_stated"])
    dil = DL.dilution_facts(info["mkt"].price, info["mkt"].exchange, info["bid_notices"], info["split"],
                            ceil["runway_q"], run.components["total_liquid_cash"], run.components["cash_as_of_date"],
                            months, info["mcap"], today)
    print("dilution:", dil)
    pcd_only = DL.dilution_facts(info["mkt"].price, info["mkt"].exchange, info["bid_notices"], info["split"],
                                 ceil["runway_q"], run.components["total_liquid_cash"],
                                 run.components["cash_as_of_date"], months, info["mcap"], today, readout_lag_months=0)
    print("with readout = primary completion (lag 0): margin", pcd_only["months_runway_minus_months_to_catalyst"],
          "must_raise", pcd_only["must_raise_before_catalyst"])
    print("divergence:", info["pr"]["divergence_events"])
    assert dil["must_raise_before_catalyst"] and dil["sub_dollar"], dil
    ev = [e for e in info["pr"]["divergence_events"] if e["date"] == "2026-06-24"]
    assert ev and abs(ev[0]["return"] - (-0.3818)) < 0.01 and "Align" in ev[0]["headline"], info["pr"]

    kill = os.path.join(OUT, "excluded_dilution_risk.csv")
    if os.path.exists(kill):
        rows = [r for r in csv.DictReader(open(kill)) if r["ticker"] == "CGTX"]
        assert rows and rows[0]["kill_dates"], "CGTX not in excluded_dilution_risk.csv"
        assert not [r for r in csv.DictReader(open(os.path.join(OUT, "candidates.csv"))) if r["ticker"] == "CGTX"]
        print("outputs: CGTX in excluded_dilution_risk.csv:", rows[0]["kill_dates"])
    print("ok live CGTX")
