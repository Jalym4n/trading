"""Revision-history diffing on hand-built snapshots (no network)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat.history import change_kind, snapshot, timeline  # noqa: E402


def study(pcd, typ="ESTIMATED", enroll=100, outcomes=(("HbA1c change", "Week 26"),), crit="Inclusion:\n- age 18+",
          status="RECRUITING"):
    return {"protocolSection": {
        "statusModule": {"overallStatus": status, "primaryCompletionDateStruct": {"date": pcd, "type": typ}},
        "designModule": {"enrollmentInfo": {"count": enroll, "type": "ESTIMATED"}},
        "eligibilityModule": {"eligibilityCriteria": crit, "minimumAge": "18 Years", "sex": "ALL"},
        "outcomesModule": {"primaryOutcomes": [{"measure": m, "timeFrame": t} for m, t in outcomes]}}}


def test_slips_endpoint_change_enrollment_and_eligibility():
    snaps = [
        (0, "2024-01-10", snapshot(study("2025-06"))),
        (3, "2024-05-01", snapshot(study("2025-12"))),                               # +6 months
        (5, "2024-09-01", snapshot(study("2026-03-15", enroll=60))),                 # +~3.5, power cut
        (7, "2025-01-01", snapshot(study("2026-03-15", enroll=60,
                                         outcomes=(("Pain VAS at week 12", "Week 12"),)))),  # swap
        (9, "2025-03-01", snapshot(study("2026-03-15", enroll=60, outcomes=(("Pain VAS at week 12", "Week 12"),),
                                         crit="Inclusion:\n- age 18+\n- baseline VAS >= 7"))),
    ]
    h = timeline(snaps)
    assert h["pcd_slips"] == 2 and h["pcd_moves_earlier"] == 0
    assert [p[2] for p in h["pcd_values"]] == ["2025-06", "2025-12", "2026-03-15"]
    assert h["pcd_slip_months"] == 9.4          # 2025-06-01 -> 2026-03-15 = 287 days
    assert h["endpoint_changed"] and h["endpoint_changes"][0]["version"] == 7
    assert h["endpoint_changes"][0]["kind"] == "measure text changed"
    assert h["enrollment_changes"] == [{"version": 5, "date": "2024-09-01", "from": 100, "to": 60,
                                        "type": "ESTIMATED"}]
    e = h["eligibility_changes"][0]
    assert e["version"] == 9 and e["diff"] == ["+- baseline VAS >= 7"]


def test_html_entities_do_not_count_as_changes():
    a = snapshot(study("2025-06", outcomes=(("Phase 1&#x2F;2 response", "Week 4"),)))
    b = snapshot(study("2025-06", outcomes=(("Phase 1/2 response", "Week 4"),)))
    assert not timeline([(0, "d0", a), (1, "d1", b)])["endpoint_changed"]


def test_change_kind():
    assert change_kind([("Change in body weight", "W68")], [("Change in Body Weight", "W68")]) == \
        "letter case / whitespace only"
    assert change_kind([("A", "W1")], [("A", "W2")]) == "timeFrame text changed, measure text unchanged"
    assert change_kind([("A", "W1")], [("A", "W1"), ("B", "W1")]) == "number of primary outcomes changed"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
