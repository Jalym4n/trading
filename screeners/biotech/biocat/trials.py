"""Stage 1: ClinicalTrials.gov API v2 (free, no auth)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

CTGOV = "https://clinicaltrials.gov/api/v2"
STATUSES = "RECRUITING|ACTIVE_NOT_RECRUITING|ENROLLING_BY_INVITATION"
STAGE1_FIELDS = ",".join([
    "NCTId", "BriefTitle", "LeadSponsorName", "Phase", "OverallStatus",
    "PrimaryCompletionDate", "PrimaryCompletionDateType", "InterventionName",
])


@dataclass
class Trial:
    nct_id: str
    title: str
    sponsor: str
    phases: list[str]
    status: str
    pcd: str | None            # primary completion date, as registered (YYYY-MM or YYYY-MM-DD)
    pcd_type: str | None       # ESTIMATED / ACTUAL
    interventions: list[str] = field(default_factory=list)

    @property
    def phase(self) -> str:
        return "/".join(p.replace("PHASE", "P") for p in self.phases) or "NA"

    @property
    def phase_rank(self) -> int:
        """Mechanical sort key only: later phase first."""
        return -max((int(p[-1]) for p in self.phases if p[-1].isdigit()), default=0)


def parse_date(s: str | None) -> dt.date | None:
    """CT.gov dates are 'YYYY-MM-DD' or 'YYYY-MM' (month precision -> day 1)."""
    if not s:
        return None
    parts = s.split("-")
    try:
        y, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 1
        d = int(parts[2]) if len(parts) > 2 else 1
        return dt.date(y, m, d)
    except (ValueError, IndexError):
        return None


def months_between(a: dt.date, b: dt.date) -> float:
    return round((b - a).days / 30.4375, 1)


def stage1_query(today: dt.date) -> dict:
    lo = today + dt.timedelta(days=91)     # +3 months
    hi = today + dt.timedelta(days=365)    # +12 months
    return {
        "query.term": (f"AREA[Phase](PHASE2 OR PHASE3) AND AREA[LeadSponsorClass]INDUSTRY "
                       f"AND AREA[PrimaryCompletionDate]RANGE[{lo.isoformat()},{hi.isoformat()}]"),
        "filter.overallStatus": STATUSES,
        "fields": STAGE1_FIELDS,
        "pageSize": "1000",
        "format": "json",
    }


def _to_trial(s: dict) -> Trial:
    ps = s.get("protocolSection", {})
    st = ps.get("statusModule", {})
    pcd = st.get("primaryCompletionDateStruct", {}) or {}
    return Trial(
        nct_id=ps.get("identificationModule", {}).get("nctId", ""),
        title=ps.get("identificationModule", {}).get("briefTitle", ""),
        sponsor=ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", ""),
        phases=ps.get("designModule", {}).get("phases", []) or [],
        status=st.get("overallStatus", ""),
        pcd=pcd.get("date"),
        pcd_type=pcd.get("type"),
        interventions=[i.get("name", "") for i in
                       ps.get("armsInterventionsModule", {}).get("interventions", []) or []],
    )


def fetch_stage1(http, today: dt.date, log=print) -> list[Trial]:
    params = stage1_query(today)
    out, token, page = [], None, 0
    while True:
        p = dict(params)
        if token:
            p["pageToken"] = token
        d = http.get_json(f"{CTGOV}/studies", p)
        out.extend(_to_trial(s) for s in d.get("studies", []))
        page += 1
        token = d.get("nextPageToken")
        if not token:
            break
    log(f"stage 1: {len(out)} trials in {page} page(s)")
    return out


def fetch_full(http, nct_id: str) -> dict:
    return http.get_json(f"{CTGOV}/studies/{nct_id}", {"format": "json"})


def related_trials(http, sponsor: str, intervention: str, limit: int = 100) -> list[dict]:
    """Every registered trial (any status) naming this intervention."""
    d = http.get_json(f"{CTGOV}/studies", {
        "query.intr": intervention,
        "fields": "NCTId,BriefTitle,LeadSponsorName,Phase,OverallStatus,PrimaryCompletionDate,"
                  "WhyStopped,HasResults",
        "pageSize": str(limit), "format": "json"})
    rows = []
    for s in d.get("studies", []):
        ps = s.get("protocolSection", {})
        rows.append({
            "nct_id": ps.get("identificationModule", {}).get("nctId"),
            "title": ps.get("identificationModule", {}).get("briefTitle"),
            "sponsor": ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"),
            "phase": "/".join(ps.get("designModule", {}).get("phases", []) or []),
            "status": ps.get("statusModule", {}).get("overallStatus"),
            "why_stopped": ps.get("statusModule", {}).get("whyStopped"),
            "pcd": (ps.get("statusModule", {}).get("primaryCompletionDateStruct") or {}).get("date"),
            "has_results": s.get("hasResults"),
        })
    return rows
