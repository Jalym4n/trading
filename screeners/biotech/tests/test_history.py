"""Revision-history diffing on hand-built snapshots (no network)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat.dossier import experimental_interventions  # noqa: E402
from biocat.history import change_kind, criteria_lines, snapshot, timeline  # noqa: E402


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
    assert e["version"] == 9 and e["diff"] == ["+baseline VAS >= 7"]


def test_html_entities_do_not_count_as_changes():
    a = snapshot(study("2025-06", outcomes=(("Phase 1&#x2F;2 response", "Week 4"),)))
    b = snapshot(study("2025-06", outcomes=(("Phase 1/2 response", "Week 4"),)))
    assert not timeline([(0, "d0", a), (1, "d1", b)])["endpoint_changed"]


def test_html_criteria_diff_per_criterion():
    # shape of a real historical version (NCT05566795 v19 vs v20): one HTML string
    old = "<p>Inclusion Criteria:</p><ul><li>Less than 25 years of age</li><li>Measurable lesion</li></ul>"
    new = "<p>Inclusion Criteria:</p><ul><li>Less than 25 years of age</li><li>Measurable lesion</li>" \
          "<li>Prior MEK inhibitor &gt; 6 months ago</li></ul>"
    assert criteria_lines(old) == ["Inclusion Criteria:", "Less than 25 years of age", "Measurable lesion"]
    h = timeline([(0, "d0", snapshot(study("2025-06", crit=old))), (1, "d1", snapshot(study("2025-06", crit=new)))])
    assert h["eligibility_changes"][0]["diff"] == ["+Prior MEK inhibitor > 6 months ago"]
    # markdown bullets (current record) and HTML (history) reduce to the same lines
    md = "Inclusion Criteria:\n\n* Less than 25 years of age\n* Measurable lesion"
    assert criteria_lines(md) == criteria_lines(old)


def test_related_trials_query_only_the_experimental_asset():
    ps = {"armsInterventionsModule": {
        "armGroups": [{"label": "Tovorafenib", "type": "EXPERIMENTAL"},
                      {"label": "SoC", "type": "ACTIVE_COMPARATOR"}],
        "interventions": [{"name": "Tovorafenib", "armGroupLabels": ["Tovorafenib"]},
                          {"name": "Chemotherapeutic Agent", "armGroupLabels": ["SoC"]},
                          {"name": "Backbone", "armGroupLabels": ["Tovorafenib", "SoC"]}]}}
    assert experimental_interventions(ps) == ["Tovorafenib"]
    unlinked = {"armsInterventionsModule": {"interventions": [{"name": "X-101"}, {"name": "Placebo"}]}}
    assert experimental_interventions(unlinked) == ["X-101"]


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
