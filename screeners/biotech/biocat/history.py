"""Dossier section D: registration revision history, diffed and presented as a
dated timeline. NOT interpreted.

ROUTES (verified 2026-10-12 by live requests; undocumented internal web-UI API,
absent from the public spec at /api/oas/v2, so it can change without notice):

  version list      GET https://clinicaltrials.gov/api/int/studies/{nct}/history
                    -> {changes:[{version, date, status, moduleLabels, ...}], ...}
  record as of N    GET https://clinicaltrials.gov/api/int/studies/{nct}/history/{N}
                    -> {studyVersion: N, study: {protocolSection, ...}}   (N 0-based)
  (diff)            GET .../history/{N}?patchToVersion={M}  -> + RFC 6902 `patch`

Every /api/int call needs an `ncbi_sid` cookie (any value; a real one is set by
GET /api/v2/version). Without it: HTTP 403. Some networks get 403 even with it.
Historical text is HTML-entity-escaped; it is unescaped before comparison.

Only versions whose `moduleLabels` include a module this section reads are
fetched (Outcome Measures, Study Status, Study Design, Eligibility), plus v0.
A field can only change in a version that lists its module, so the timeline is
complete without downloading every version.
"""

from __future__ import annotations

import difflib
import html
import json
import re
import time
import urllib.request
from http.cookiejar import Cookie, CookieJar

from .trials import parse_date

INT = "https://clinicaltrials.gov/api/int/studies"
WANTED = {"Outcome Measures", "Study Status", "Study Design", "Eligibility"}


class HistoryClient:
    def __init__(self, http, max_versions: int = 150, min_gap_s: float = 0.25):
        self.http = http                      # reuses its disk cache
        self.max_versions = max_versions
        self.gap = min_gap_s
        self._last = 0.0
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self._primed = False

    def _get(self, url: str):
        hit = self.http._cache_get(url)
        if hit is not None:
            return hit
        if not self._primed:
            self.opener.open(urllib.request.Request(
                "https://clinicaltrials.gov/api/v2/version",
                headers={"User-Agent": "Mozilla/5.0 biocat"}), timeout=30).read()
            if not any(c.name == "ncbi_sid" for c in self.jar):
                # the value is not validated server-side; set one if none came back
                self.jar.set_cookie(Cookie(
                    0, "ncbi_sid", "biocat", None, False, "clinicaltrials.gov", True, False, "/", True,
                    True, None, False, None, None, {}))
            self._primed = True
        wait = self._last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 biocat",
                                                   "Accept": "application/json"})
        with self.opener.open(req, timeout=90) as r:
            data = json.loads(r.read())
        self._last = time.monotonic()
        self.http._cache_put(url, data)
        return data

    def versions(self, nct: str) -> list[dict]:
        return self._get(f"{INT}/{nct}/history").get("changes", [])

    def record(self, nct: str, v: int) -> dict:
        return self._get(f"{INT}/{nct}/history/{v}").get("study", {})

    def diff(self, nct: str) -> dict:
        changes = self.versions(nct)
        pick = [c["version"] for c in changes
                if c["version"] == 0 or WANTED & set(c.get("moduleLabels") or [])]
        truncated = len(pick) > self.max_versions
        if truncated:  # keep the first and the most recent; say so in the dossier
            pick = pick[:1] + pick[-(self.max_versions - 1):]
        dates = {c["version"]: c.get("date") for c in changes}
        snaps = [(v, dates.get(v), snapshot(self.record(nct, v))) for v in pick]
        out = timeline(snaps)
        out.update({"n_versions": len(changes), "n_fetched": len(pick), "truncated": truncated,
                    "first_date": changes[0].get("date") if changes else None,
                    "last_date": changes[-1].get("date") if changes else None})
        return out


# ------------------------------------------------------------- pure logic --
def _clean(s) -> str:
    return " ".join(html.unescape(s or "").split())


_BLOCK_TAG = re.compile(r"<\s*(?:br|/?p|/?li|/?ul|/?ol|/?h\d)\b[^>]*>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_BULLET = re.compile(r"^(?:[*\u2022-]\s+)")


def criteria_lines(text: str | None) -> list[str]:
    """Eligibility text as one criterion per line, so a diff shows criteria.

    Historical versions store criteria as one HTML string ("<ul><li>...");
    the current record uses markdown bullets. Both reduce to the same lines.
    """
    t = _BLOCK_TAG.sub("\n", html.unescape(text or ""))
    t = _ANY_TAG.sub("", t)
    out = []
    for line in t.splitlines():
        line = _BULLET.sub("", " ".join(line.split()))
        if line:
            out.append(line)
    return out


def snapshot(study: dict) -> dict:
    ps = study.get("protocolSection", {}) or {}
    st = ps.get("statusModule", {}) or {}
    pcd = st.get("primaryCompletionDateStruct", {}) or {}
    ei = (ps.get("designModule", {}) or {}).get("enrollmentInfo", {}) or {}
    el = ps.get("eligibilityModule", {}) or {}
    prim = (ps.get("outcomesModule", {}) or {}).get("primaryOutcomes", []) or []
    return {
        "primary": [(_clean(p.get("measure")), _clean(p.get("timeFrame"))) for p in prim],
        "pcd": pcd.get("date"), "pcd_type": pcd.get("type"),
        "enroll": ei.get("count"), "enroll_type": ei.get("type"),
        "criteria": criteria_lines(el.get("eligibilityCriteria")),
        "elig_meta": (el.get("minimumAge"), el.get("maximumAge"), el.get("sex"), el.get("healthyVolunteers")),
        "status": st.get("overallStatus"),
    }


def _months(a: str | None, b: str | None) -> float | None:
    """Months from date a to date b. When either is month-precision only
    ("2027-07"), both are compared at month precision: "2027-07-14" -> "2027-07"
    drops the day, it does not move the date (NGEN NCT07799532 read -0.4)."""
    da, db = parse_date(a), parse_date(b)
    if not (da and db):
        return None
    if len(a.strip()) <= 7 or len(b.strip()) <= 7:
        return float((db.year - da.year) * 12 + (db.month - da.month))
    return round((db - da).days / 30.4375, 1)


def norm_text(t: str | None) -> str:
    """Comparison key: HTML-unescaped, lowercase, punctuation stripped,
    whitespace collapsed. Two outcome texts that differ only in case,
    punctuation, spacing or entity escaping compare equal."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", html.unescape(t or "").lower()).split())


def norm_outcomes(xs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(norm_text(m), norm_text(t)) for m, t in xs]


def change_kind(a: list[tuple[str, str]], b: list[tuple[str, str]]) -> str | None:
    """Mechanical classification of an endpoint edit, nothing more.
    None when the normalised texts are equal (no change)."""
    na, nb = norm_outcomes(a), norm_outcomes(b)
    if na == nb:
        return None
    if len(na) != len(nb):
        return "number of primary outcomes changed"
    if [m for m, _ in na] == [m for m, _ in nb]:
        return "timeFrame text changed, measure text unchanged"
    return "measure text changed"


def _fmt_outcomes(xs: list[tuple[str, str]]) -> list[str]:
    return [f"{m} [timeFrame: {t}]" if t else m for m, t in xs]


def timeline(snaps: list[tuple[int, str, dict]]) -> dict:
    """snaps: [(version, date, snapshot)] in version order -> facts + events."""
    events, prev = [], None
    pcd_values, ep_changes, enr_changes, elig_changes = [], [], [], []
    for v, date, s in snaps:
        if prev is None:
            pcd_values.append((v, date, s["pcd"], s["pcd_type"]))
            events.append((v, date, "initial registration",
                           f"primaryOutcomes={len(s['primary'])}; primaryCompletionDate={s['pcd']} "
                           f"({s['pcd_type']}); enrollment={s['enroll']} ({s['enroll_type']})"))
            prev = s
            continue
        kind = change_kind(prev["primary"], s["primary"])
        if kind:
            ep_changes.append({"version": v, "date": date, "from": _fmt_outcomes(prev["primary"]),
                               "to": _fmt_outcomes(s["primary"]), "kind": kind,
                               "status_at_change": s["status"]})
            events.append((v, date, "PRIMARY ENDPOINT CHANGED",
                           f"{kind}; count {len(prev['primary'])} -> {len(s['primary'])}; "
                           f"overallStatus={s['status']}"))
        if s["pcd"] != prev["pcd"]:
            pcd_values.append((v, date, s["pcd"], s["pcd_type"]))
            d = _months(prev["pcd"], s["pcd"])
            events.append((v, date, "primary completion date",
                           f"{prev['pcd']} -> {s['pcd']} ({s['pcd_type']}); "
                           f"{'+' if (d or 0) > 0 else ''}{d} months"))
        elif s["pcd_type"] != prev["pcd_type"]:
            events.append((v, date, "primary completion date type", f"{prev['pcd_type']} -> {s['pcd_type']}"))
        if s["enroll"] != prev["enroll"]:
            enr_changes.append({"version": v, "date": date, "from": prev["enroll"], "to": s["enroll"],
                                "type": s["enroll_type"]})
            direction = ("reduced" if (s["enroll"] or 0) < (prev["enroll"] or 0) else "increased")
            events.append((v, date, f"enrollment target {direction}",
                           f"{prev['enroll']} -> {s['enroll']} ({s['enroll_type']})"))
        if s["criteria"] != prev["criteria"] or s["elig_meta"] != prev["elig_meta"]:
            diff = [l for l in difflib.unified_diff(prev["criteria"], s["criteria"], lineterm="", n=0)
                    if not l.startswith(("---", "+++", "@@"))]
            elig_changes.append({"version": v, "date": date, "diff": diff[:30], "n_lines": len(diff),
                                 "meta_from": prev["elig_meta"], "meta_to": s["elig_meta"]})
            events.append((v, date, "eligibility criteria changed",
                           f"{len(diff)} criteria lines added/removed"
                           + (f"; age/sex/volunteers {prev['elig_meta']} -> {s['elig_meta']}"
                              if s["elig_meta"] != prev["elig_meta"] else "")))
        prev = s

    later = [i for i in range(1, len(pcd_values))
             if (_months(pcd_values[i - 1][2], pcd_values[i][2]) or 0) > 0]
    earlier = [i for i in range(1, len(pcd_values))
               if (_months(pcd_values[i - 1][2], pcd_values[i][2]) or 0) < 0]
    total = _months(pcd_values[0][2], pcd_values[-1][2]) if len(pcd_values) > 1 else 0.0
    return {"events": events, "pcd_values": pcd_values, "endpoint_changes": ep_changes,
            "enrollment_changes": enr_changes, "eligibility_changes": elig_changes,
            "endpoint_changed": bool(ep_changes), "pcd_slips": len(later), "pcd_moves_earlier": len(earlier),
            "pcd_slip_months": total}


def render(h: dict) -> list[str]:
    out = ["## D. Revision history", "",
           f"Source: `GET clinicaltrials.gov/api/int/studies/{{nct}}/history` (undocumented web-UI API) "
           f"and `/history/{{N}}` per version. Record versions: **{h['n_versions']}** "
           f"({h['first_date']} to {h['last_date']}); versions read: {h['n_fetched']} "
           f"(v0 plus every version listing Outcome Measures, Study Status, Study Design or Eligibility)."
           + (" **Truncated: not every relevant version was read.**" if h.get("truncated") else ""), "",
           f"- primary endpoint text changes (after normalising case, punctuation, spacing): "
           f"**{len(h['endpoint_changes'])}**",
           f"- primary completion date: {len(h['pcd_values'])} distinct value(s); moved later "
           f"{h['pcd_slips']} time(s), earlier {h['pcd_moves_earlier']} time(s); first to latest "
           f"{h['pcd_slip_months']} months",
           f"- enrollment target changes: {len(h['enrollment_changes'])}",
           f"- eligibility criteria changes: {len(h['eligibility_changes'])}", "",
           "### Timeline", "", "| version | date | field | change |", "|---|---|---|---|"]
    out += [f"| {v} | {d} | {what} | {detail} |" for v, d, what, detail in h["events"]]
    out.append("")
    if h["pcd_values"]:
        out += ["### Primary completion date, every value held", "",
                "| version | date | primaryCompletionDate | type |", "|---|---|---|---|"]
        out += [f"| {v} | {d} | {p} | {t} |" for v, d, p, t in h["pcd_values"]]
        out.append("")
    for c in h["endpoint_changes"]:
        out += [f"### Primary endpoint change at version {c['version']} ({c['date']}), "
                f"overallStatus={c['status_at_change']}", "", f"Kind (mechanical): {c['kind']}", "", "From:"]
        out += [f"> {i + 1}. {x}" for i, x in enumerate(c["from"])] or ["> (none)"]
        out += ["", "To:"]
        out += [f"> {i + 1}. {x}" for i, x in enumerate(c["to"])] or ["> (none)"]
        out.append("")
    for c in h["eligibility_changes"]:
        out += [f"### Eligibility change at version {c['version']} ({c['date']}): "
                f"{c['n_lines']} line(s) added (+) / removed (-)", "", "```diff"] + c["diff"] + \
               (["... (more lines not shown)"] if c["n_lines"] > len(c["diff"]) else []) + ["```", ""]
    return out
