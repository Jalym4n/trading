"""E1-E6 from consecutive registry snapshots. Pure functions; rules in EVENTS.md."""
from __future__ import annotations

import difflib

from biocat.history import _months, norm_text

OPEN = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING"}
ENROLLING = OPEN - {"NOT_YET_RECRUITING"}
STOPPED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
EST = {"ESTIMATED", "ANTICIPATED"}
PCD_MONTHS, ENROLL_CUT, ENROLL_RISE, SIMILARITY = 3.0, 0.75, 1.25, 0.60


def dropped_measures(prev: list, cur: list) -> list[str]:
    """Earlier primary measures with no counterpart (similarity >= 0.60) among the new ones."""
    new = [norm_text(m) for m, _ in cur]
    out = []
    for m, _ in prev:
        a = norm_text(m)
        best = max((difflib.SequenceMatcher(None, a, b).ratio() for b in new), default=0.0)
        if best < SIMILARITY:
            out.append(m)
    return out


def events_between(prev: dict, cur: dict) -> list[dict]:
    """prev / cur: biocat.history.snapshot dicts. Returns [{event, detail...}] (no dates)."""
    ev = []
    is_open = prev["status"] in OPEN
    if is_open and prev["pcd_type"] in EST and prev["pcd"] and cur["pcd"]:
        d = _months(prev["pcd"], cur["pcd"])
        if d is not None and abs(d) >= PCD_MONTHS:
            ev.append({"event": "E1" if d > 0 else "E2", "from": prev["pcd"], "to": cur["pcd"], "months": d,
                       "to_type": cur["pcd_type"]})
    if prev["status"] in ENROLLING:
        gone = dropped_measures(prev["primary"], cur["primary"])
        if gone:
            ev.append({"event": "E3", "dropped": gone, "n_before": len(prev["primary"]),
                       "n_after": len(cur["primary"])})
    if is_open and prev["enroll_type"] in EST and cur["enroll_type"] in EST and prev["enroll"] and cur["enroll"]:
        r = cur["enroll"] / prev["enroll"]
        if r <= ENROLL_CUT:
            ev.append({"event": "E4", "from": prev["enroll"], "to": cur["enroll"], "ratio": round(r, 3)})
        elif r >= ENROLL_RISE:
            ev.append({"event": "E5", "from": prev["enroll"], "to": cur["enroll"], "ratio": round(r, 3)})
    if cur["status"] in STOPPED and prev["status"] not in STOPPED:
        ev.append({"event": "E6", "from": prev["status"], "to": cur["status"]})
    return ev


def trial_events(snaps: list[tuple[int, str | None, str | None, dict]]) -> list[dict]:
    """snaps: [(version, submit_date, post_date, snapshot)] in version order.
    Each event carries the later version's dates; event_date = post_date (public), else None."""
    out = []
    for (_, _, _, a), (v, sub, post, b) in zip(snaps[:-1], snaps[1:]):
        for e in events_between(a, b):
            out.append({"version": v, "submit_date": sub, "event_date": post, "status_before": a["status"], **e})
    return out
