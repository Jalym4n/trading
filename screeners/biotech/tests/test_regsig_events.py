import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from regsig.events import events_between, trial_events  # noqa: E402


def snap(**kw):
    base = {"primary": [("Change in EASI score at week 16", "16 weeks")], "pcd": "2025-06", "pcd_type": "ESTIMATED",
            "enroll": 200, "enroll_type": "ESTIMATED", "status": "RECRUITING"}
    return {**base, **kw}


def kinds(a, b):
    return [e["event"] for e in events_between(a, b)]


def test_pcd_slip_and_pull_in_thresholds():
    assert kinds(snap(), snap(pcd="2025-09")) == ["E1"]
    assert kinds(snap(), snap(pcd="2025-08")) == []                      # 2 months: below threshold
    assert kinds(snap(), snap(pcd="2025-03")) == ["E2"]
    assert kinds(snap(pcd_type="ACTUAL"), snap(pcd="2026-06")) == []     # an actual date being corrected
    assert kinds(snap(status="COMPLETED"), snap(status="COMPLETED", pcd="2026-06")) == []


def test_endpoint_swap_not_rewording_or_addition():
    reworded = snap(primary=[("Change in EASI score at Week 16.", "Week 16")])
    added = snap(primary=[("Change in EASI score at week 16", "16 weeks"), ("Itch NRS", "16 weeks")])
    swapped = snap(primary=[("Number of participants with adverse events", "52 weeks")])
    assert kinds(snap(), reworded) == [] and kinds(snap(), added) == []
    assert kinds(snap(), swapped) == ["E3"]
    assert kinds(snap(status="NOT_YET_RECRUITING"), swapped) == []       # before enrolment started


def test_enrollment_cut_and_rise():
    assert kinds(snap(), snap(enroll=150)) == ["E4"] and kinds(snap(), snap(enroll=151)) == []
    assert kinds(snap(), snap(enroll=250)) == ["E5"]
    assert kinds(snap(), snap(enroll=90, enroll_type="ACTUAL")) == []    # final count at completion, not a cut


def test_termination_and_dates():
    assert kinds(snap(), snap(status="TERMINATED")) == ["E6"]
    assert kinds(snap(status="SUSPENDED"), snap(status="TERMINATED")) == []
    ev = trial_events([(0, "2024-01-02", "2024-01-05", snap()), (1, "2024-06-03", "2024-06-06", snap(pcd="2026-01"))])
    assert [(e["event"], e["event_date"], e["version"]) for e in ev] == [("E1", "2024-06-06", 1)]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
