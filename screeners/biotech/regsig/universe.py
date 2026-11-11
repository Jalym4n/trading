"""Step 1: every industry-sponsored Phase 2/3 interventional trial updated since the start
of price coverage (2018-05-01). Output: regsig_out/universe.csv (one row per trial).

    python -m regsig.universe
"""
import csv
import json
import os
import time
import urllib.parse
import urllib.request

API = "https://clinicaltrials.gov/api/v2/studies"
TERM = ("AREA[LeadSponsorClass]INDUSTRY AND AREA[StudyType]INTERVENTIONAL AND "
        "(AREA[Phase]PHASE2 OR AREA[Phase]PHASE3) AND AREA[LastUpdatePostDate]RANGE[2018-05-01,MAX]")
FIELDS = ["NCTId", "LeadSponsorName", "Phase", "OverallStatus", "StartDate", "PrimaryCompletionDate",
          "PrimaryCompletionDateType", "StudyFirstPostDate", "LastUpdatePostDate", "EnrollmentCount"]
OUT = os.path.join(os.path.dirname(__file__), "..", "regsig_out")


def _get(params):
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "Mozilla/5.0 regsig"})
    for attempt in range(5):
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception:  # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def main():
    os.makedirs(OUT, exist_ok=True)
    rows, token = [], None
    while True:
        p = {"query.term": TERM, "pageSize": "1000", "fields": ",".join(FIELDS), "format": "json"}
        if token:
            p["pageToken"] = token
        d = _get(p)
        for s in d.get("studies", []):
            ps = s.get("protocolSection", {})
            st, dm = ps.get("statusModule", {}), ps.get("designModule", {})
            rows.append({
                "nct_id": ps.get("identificationModule", {}).get("nctId"),
                "sponsor": ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"),
                "phase": "/".join(dm.get("phases") or []),
                "status": st.get("overallStatus"),
                "start": (st.get("startDateStruct") or {}).get("date"),
                "pcd": (st.get("primaryCompletionDateStruct") or {}).get("date"),
                "pcd_type": (st.get("primaryCompletionDateStruct") or {}).get("type"),
                "first_post": (st.get("studyFirstPostDateStruct") or {}).get("date"),
                "last_update_post": (st.get("lastUpdatePostDateStruct") or {}).get("date"),
                "enrollment": (dm.get("enrollmentInfo") or {}).get("count"),
            })
        token = d.get("nextPageToken")
        print(f"{len(rows)} trials", flush=True)
        if not token:
            break
    with open(os.path.join(OUT, "universe.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    sponsors = {}
    for r in rows:
        sponsors[r["sponsor"]] = sponsors.get(r["sponsor"], 0) + 1
    with open(os.path.join(OUT, "sponsors.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sponsor", "n_trials"])
        w.writerows(sorted(sponsors.items(), key=lambda kv: -kv[1]))
    print(f"wrote {len(rows)} trials, {len(sponsors)} sponsors -> {OUT}")


if __name__ == "__main__":
    main()
