"""LIVE validation for round 3 (bugs 1-5, gates 1-3, success reactions). Run AFTER the screen:
    BIOCAT_UA="Name email" python3 tests/live_round3.py

Expected (checked against the sources 2026-10-13):
  Form 25     APLS, DVAX, DAWN, FOLD, CNTA, ITCI, BPMC: form_25_filed True with a date; APLS 2026-05-14
  ONCY        both pelareorep rows: primary_molecule_key "pelareorep", identical track-record fields;
              trifluridine/tipiracil, atezolizumab, bevacizumab, FOLFIRI excluded as widely used
  UPB         both rows: success_reaction_flag True, 2026-02-11 VALIANT release at -0.4718
  invariants  held (integrity_report.md says HELD twice)
"""
import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
OUT = os.path.join(HERE, "..", "biocat_out")
TRACK = ["prior_trials_total", "prior_controlled_trials", "prior_controlled_failures", "prior_controlled_successes",
         "prior_controlled_outcome_unknown", "prior_trials_with_clinical_primary", "molecule_track_record",
         "prior_failure_ncts", "success_reaction_flag", "track_record_evidence_url"]

if __name__ == "__main__":
    rows = [(os.path.basename(f), r) for f in glob.glob(os.path.join(OUT, "*.csv"))
            if os.path.basename(f) not in ("identity.csv", "dropped.csv") for r in csv.DictReader(open(f))]
    by = {}
    for f, r in rows:
        by.setdefault(r["ticker"], []).append((f, r))

    for t in ("APLS", "DVAX", "DAWN", "FOLD", "CNTA", "ITCI", "BPMC"):
        for f, r in by[t]:
            assert r["form_25_filed"] == "True" and r["form_25_date"], (t, r["delisting_signal"], r["form_25_date"])
            assert r["delisting_signal"] == "form_25" and f == "excluded_delisted.csv", (t, f, r["delisting_signal"])
        print(t, "form_25", by[t][0][1]["form_25_date"], by[t][0][1]["form_25_accession"])
    assert all(r["form_25_date"] == "2026-05-14" for _f, r in by["APLS"])

    oncy = [r for _f, r in by["ONCY"]]
    assert len(oncy) == 2 and {r["primary_molecule_key"] for r in oncy} == {"pelareorep"}, oncy
    a, b = oncy
    assert {k: a[k] for k in TRACK} == {k: b[k] for k in TRACK}, {k: (a[k], b[k]) for k in TRACK if a[k] != b[k]}
    excluded = " ".join(r["widely_used_excluded"] for r in oncy).lower()
    for drug in ("trifluridine tipiracil", "atezolizumab", "bevacizumab", "folfiri"):
        assert drug in excluded, (drug, excluded)
    print("ONCY", {k: a[k] for k in TRACK})

    for _f, r in by["UPB"]:
        assert r["success_reaction_flag"] == "True", r["success_reaction_flag"]
        ev = [e for e in json.loads(r["prior_success_reactions"]) if e["date"] == "2026-02-11"]
        assert ev and ev[0]["return"] == -0.4718, r["prior_success_reactions"]
    print("UPB success_reaction_flag True, 2026-02-11 -0.4718")

    from biocat import track as TR
    from biocat.net import Http
    http = Http(cache_dir=os.path.join(OUT, ".cache"), ttl_hours=24 * 30)
    for name in ("lonsurf", "trifluridine tipiracil", "folfiri", "folfox", "carboplatin", "pembrolizumab",
                 "nivolumab", "dexamethasone", "methotrexate", "prednisone"):
        why = TR.widely_used_reason(http, name, "Oncolytics Biotech", "2026-01-01")
        assert why, name
        print("widely used:", why)
    assert TR.widely_used_reason(http, "pelareorep", "Oncolytics Biotech", "2026-01-01") is None

    rep = open(os.path.join(OUT, "integrity_report.md")).read()
    assert rep.count("**HELD**") == 2, "an invariant did not hold"
    print("ok live round 3")
