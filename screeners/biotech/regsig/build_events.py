"""Step 3: registry version history -> events E1-E6, for trials of SEC-reporting sponsors.

Sponsors with more than MAX_SPONSOR_TRIALS universe trials are large pharma and are left out
(no small company runs that many Phase 2/3 trials); the market-cap filter proper is applied
later, with prices. Resumable: each finished trial is appended to regsig_out/snapshots.jsonl
(compact per-version snapshots, so events can be re-derived without refetching) and its
events to regsig_out/events.jsonl. Only versions that list a module the events read are
fetched (Study Status, Outcome Measures, Study Design), plus version 0.

    python -m regsig.build_events [--max-minutes 110]
"""
import argparse
import csv
import json
import os
import time

from biocat.history import HistoryClient, snapshot
from biocat.net import Http

from .events import trial_events

OUT = os.path.join(os.path.dirname(__file__), "..", "regsig_out")
MAX_SPONSOR_TRIALS = 100
WANTED = {"Study Status", "Outcome Measures", "Study Design"}


class _NoCache:
    """HistoryClient wants an http with a cache; raw records are not kept (snapshots are)."""
    def _cache_get(self, url):
        return None

    def _cache_put(self, url, data):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=110)
    a = ap.parse_args()
    listed = {r["sponsor"] for r in csv.DictReader(open(os.path.join(OUT, "listed_sponsors.csv")))
              if int(r["n_trials"]) <= MAX_SPONSOR_TRIALS}
    trials = [r for r in csv.DictReader(open(os.path.join(OUT, "universe.csv"))) if r["sponsor"] in listed]
    snap_path, ev_path = os.path.join(OUT, "snapshots.jsonl"), os.path.join(OUT, "events.jsonl")
    done = set()
    if os.path.exists(snap_path):
        for line in open(snap_path):
            try:
                done.add(json.loads(line)["nct_id"])
            except Exception:  # noqa: BLE001  a half-written last line
                pass
    todo = [t for t in trials if t["nct_id"] not in done]
    print(f"{len(trials)} trials of {len(listed)} sponsors; {len(done)} done; {len(todo)} to fetch", flush=True)
    hc = HistoryClient(_NoCache(), max_versions=10_000, min_gap_s=0.12)
    t0, n_calls, n_ev = time.time(), 0, 0
    with open(snap_path, "a") as fs, open(ev_path, "a") as fe:
        for i, t in enumerate(todo, 1):
            if time.time() - t0 > a.max_minutes * 60:
                print(f"time limit: stopped after {i - 1} trials this run", flush=True)
                break
            nct = t["nct_id"]
            try:
                changes = hc.versions(nct)
                pick = [c for c in changes if c["version"] == 0 or WANTED & set(c.get("moduleLabels") or [])]
                snaps = []
                for c in pick:
                    rec = hc.record(nct, c["version"])
                    st = rec.get("protocolSection", {}).get("statusModule", {})
                    s = snapshot(rec)
                    s.pop("criteria", None)
                    s.pop("elig_meta", None)
                    snaps.append((c["version"], c.get("date"), (st.get("lastUpdatePostDateStruct") or {}).get("date"), s))
                n_calls += 1 + len(pick)
                err = None
            except Exception as e:  # noqa: BLE001
                snaps, changes, err = [], [], str(e)[:200]
            fs.write(json.dumps({"nct_id": nct, "sponsor": t["sponsor"], "phase": t["phase"],
                                 "n_versions": len(changes), "error": err, "snaps": snaps}) + "\n")
            fs.flush()
            for e in trial_events([tuple(x) for x in snaps]):
                fe.write(json.dumps({"nct_id": nct, "sponsor": t["sponsor"], "phase": t["phase"], **e}) + "\n")
                n_ev += 1
            fe.flush()
            if i % 100 == 0:
                el = time.time() - t0
                print(f"{i}/{len(todo)} trials, {n_calls} calls, {n_ev} events, {el / 60:.0f} min, "
                      f"eta {(len(todo) - i) * el / i / 60:.0f} min", flush=True)
    print("DONE" if len(todo) and i == len(todo) and time.time() - t0 <= a.max_minutes * 60 else "PARTIAL", flush=True)


if __name__ == "__main__":
    main()
