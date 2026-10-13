"""LIVE validation on EVMN / EVO756 (Evommune, CIK 2044725), the case in the
fix request. Run AFTER the screen:  BIOCAT_UA="Name email" python3 tests/live_evmn.py

Expected, verified against the sources 2026-10-13:
  prior_controlled_failures   2  (CSU NCT06873516: 8-K 2026-06-29 "did not meet the primary endpoint";
                                  AD  NCT07150845: 8-K 2026-09-09 "did not meet the primary and secondary endpoints")
  molecule_track_record       repeat_failure  -> watchlist_repeat_failure.csv, not candidates.csv
  CIndU NCT06603220           single_arm (two EVO756 dose arms, open label)
  total_liquid_cash           288,007,000 at 2026-06-30 (cash 60.0M + short-term 140.8M + long-term 87.2M)
"""
import csv
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from biocat import dossier as D, track as TR, trials as T  # noqa: E402
from biocat.cli import company_info  # noqa: E402
from biocat import financials as F  # noqa: E402
from biocat.net import Http  # noqa: E402

OUT = os.path.join(HERE, "..", "biocat_out")
CIK, NCT = 2044725, "NCT07616128"

if __name__ == "__main__":
    today = dt.date.today()
    http = Http(cache_dir=os.path.join(OUT, ".cache"), ttl_hours=24)
    info = company_info(http, CIK, "EVMN", today)
    rec = T.fetch_full(http, NCT)
    tr = TR.track_record(http, rec, "Evommune, Inc.", CIK, info["submissions"],
                         D.experimental_interventions(rec["protocolSection"]), today)
    print({k: v for k, v in tr.items() if not k.startswith("_")})
    assert tr["prior_controlled_failures"] == 2, tr
    assert sorted(tr["prior_failure_indications"]) == ["Atopic Dermatitis", "Chronic Spontaneous Urticaria"]
    assert tr["molecule_track_record"] == "repeat_failure"
    by = {t["nct_id"]: t for t in tr["_prior_trials"]}
    assert TR.evidence_grade(by["NCT06603220"]["control_arm"], by["NCT06603220"]["masking"]) == "single_arm"
    assert by["NCT07150845"]["outcome"]["url"] != by["NCT06873516"]["outcome"]["url"], "AD and CSU share evidence"
    run = F.compute_runway(info["facts"], 12, today)
    print("cash components:", run.components)
    assert run.components["total_liquid_cash"] == 288_007_000

    wl = os.path.join(OUT, "watchlist_repeat_failure.csv")
    if os.path.exists(wl):
        assert not [r for r in csv.DictReader(open(os.path.join(OUT, "candidates.csv"))) if r["ticker"] == "EVMN"]
        w = [r for r in csv.DictReader(open(wl)) if r["ticker"] == "EVMN"]
        assert w and w[0]["note"] and json.loads(w[0]["prior_failure_ncts"]), w
        print("outputs: EVMN in watchlist_repeat_failure.csv:", w[0]["note"][:200])
    print("ok live EVMN")
