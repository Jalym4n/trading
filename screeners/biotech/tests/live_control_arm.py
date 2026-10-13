"""LIVE check (network): the four bug-1 trials against ClinicalTrials.gov.

Not part of the unit suite. Run: BIOCAT_UA="Name email" python3 tests/live_control_arm.py

Expected values are what the LIVE registry supports, verified 2026-10-13:
  NCT07355075 TTRX  yes  "Vehicle" arm typed EXPERIMENTAL, only intervention "Other: Vehicle"
  NCT06388200 OCGN  yes  two arms typed NO_INTERVENTION ("Control for RHO Arm", ...)
  NCT07207408 DVAX  no   six EXPERIMENTAL arms, all rF1V-1018: a regimen comparison; the masking
                         description says "partially blinded" between treatment arms
  NCT06966479 UPB   no   VALOUR long-term extension: both arms receive verekitug; placebo is a
                         double-dummy that blinds the DOSE SCHEDULE, not a placebo arm
Masking without a comparator is possible (blinded dose or schedule comparison), so
the last two are listed by integrity_report.md for review rather than forced to "yes".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from biocat.derive import control_arm  # noqa: E402
from biocat.net import Http  # noqa: E402

EXPECTED = {"NCT07355075": "yes", "NCT06388200": "yes", "NCT07207408": "no", "NCT06966479": "no"}

if __name__ == "__main__":
    http = Http(cache_dir=os.path.join(os.path.dirname(__file__), "..", "biocat_out", ".cache"), ttl_hours=0)
    bad = []
    for nct, want in EXPECTED.items():
        rec = http.get_json(f"https://clinicaltrials.gov/api/v2/studies/{nct}", {"format": "json"}, use_cache=False)
        got = control_arm(rec["protocolSection"])
        print(nct, got["control_arm"], "|", got["control_arm_types"], "|", got["control_arm_basis"])
        if got["control_arm"] != want:
            bad.append((nct, want, got))
    assert not bad, bad
    print("ok live control_arm")
