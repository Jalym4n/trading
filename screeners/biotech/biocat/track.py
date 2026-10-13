"""Intervention track record: every prior trial of the candidate's molecule,
and the outcome of each prior CONTROLLED trial as stated by a source.

An outcome is never inferred. A prior controlled trial counts as
  missed_primary  whyStopped names futility / lack of efficacy, or a posted
                  CT.gov primary analysis has p >= 0.05, or the sponsor's own
                  8-K press release says the trial did not meet its primary
                  endpoint, in a sentence attributable to this molecule;
  met_primary     a posted primary analysis has p < 0.05, or the sponsor's
                  8-K says it met its primary endpoint;
  unknown         none of the above (COMPLETED alone says nothing about efficacy).
Every classified outcome carries the URL and the sentence it came from.
"""

from __future__ import annotations

import datetime as dt
import html
import re

from .derive import control_arm
from .outcomes import classify_primary

CTGOV = "https://clinicaltrials.gov/api/v2/studies"
PRIOR_STATUSES = {"COMPLETED", "TERMINATED", "WITHDRAWN", "SUSPENDED"}
MASKED = {"DOUBLE", "TRIPLE", "QUADRUPLE"}

# ------------------------------------------------------------ molecule key --
_DOSE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg/kg|mg/m2|mg|mcg|µg|ug|g|ml|iu|units?|%)\b", re.I)
_FREQ = re.compile(r"\b(?:q\.?d|b\.?i\.?d|t\.?i\.?d|q\d+w|qw|q\d+d|once|twice|daily|weekly|monthly|"
                   r"every\s+\d+\s+(?:days?|weeks?|months?))\b", re.I)
_ROUTE = re.compile(r"\b(?:oral(?:ly)?|intravenous(?:ly)?|iv|subcutaneous(?:ly)?|sc|s\.c\.|intramuscular|im|"
                    r"topical|intravitreal|intrathecal|inhaled|tablets?|capsules?|injections?|infusions?|"
                    r"solution|suspension|cream|gel|ointment|formulation|extended[- ]release|er|xr|sr)\b", re.I)
_SALT = re.compile(r"\b(?:hydrochloride|hcl|sodium|potassium|calcium|mesylate|maleate|tartrate|citrate|"
                   r"succinate|fumarate|phosphate|sulfate|acetate|besylate|tosylate|bromide|chloride)\b", re.I)
_ARMWORD = re.compile(r"\b(?:protocol|regimen|combination|therapy)\b|\b(?:dose|cohort|arm|part|level|group)\s*[a-z0-9]{0,3}\b|\b(?:low|mid|middle|"
                      r"medium|high|loading|maintenance)\s*(?:dose)?\b", re.I)
_CODE = re.compile(r"\b([a-z]{1,6})[\s-]?(\d{2,6}[a-z]?)\b", re.I)
NON_MOLECULE = re.compile(r"\b(placebo|vehicle|sham|saline|standard of care|soc|best supportive care|usual care|"
                          r"no intervention|observation|control)\b", re.I)


def molecule_key(name: str | None) -> str:
    """'EVO756', 'EVO-756', 'EVO756 100 mg', 'Oral EVO756 300 mg QD', 'EVO756, Dose 1' -> 'evo756'.
    Dose, route, frequency, salt form and arm labels are stripped; a sponsor
    code (letters + digits, with or without a hyphen or space) is joined.
    Empty string for placebo / control terms."""
    s = html.unescape(name or "")
    if not s or (NON_MOLECULE.search(s) and not _CODE.search(s)):
        return ""
    s = re.sub(r"\(.*?\)", " ", s)
    for rx in (_DOSE, _FREQ, _ROUTE, _SALT, _ARMWORD):
        s = rx.sub(" ", s)
    m = _CODE.search(s)
    if m:
        return (m.group(1) + m.group(2)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return " ".join(s.split())


def code_variants(key: str) -> list[str]:
    """Search spellings for a key: 'evo756' -> ['EVO756', 'EVO-756', 'EVO 756']."""
    m = re.fullmatch(r"([a-z]{1,6})(\d{2,6}[a-z]?)", key)
    if not m:
        return [key]
    a, b = m.group(1).upper(), m.group(2).upper()
    return [f"{a}{b}", f"{a}-{b}", f"{a} {b}"]


def text_mentions_key(text: str, key: str) -> bool:
    if re.fullmatch(r"[a-z]{1,6}\d{2,6}[a-z]?", key):
        a, b = re.fullmatch(r"([a-z]{1,6})(\d{2,6}[a-z]?)", key).groups()
        return bool(re.search(rf"\b{a}[\s-]?{b}\b", text, re.I))
    rx = r"[\s-]?".join(re.escape(w) for w in key.split())
    return bool(re.search(rf"\b{rx}\b", text, re.I))


# ------------------------------------------------------------ prior trials --
FIELDS = ("NCTId,BriefTitle,Acronym,OfficialTitle,OrgStudyId,SecondaryId,LeadSponsorName,Phase,OverallStatus,StartDate,PrimaryCompletionDate,CompletionDate,"
          "EnrollmentCount,Condition,WhyStopped,HasResults,InterventionName,InterventionOtherName,"
          "InterventionArmGroupLabel,ArmGroupLabel,ArmGroupType,ArmGroupInterventionName,DesignMasking,"
          "DesignAllocation,PrimaryOutcomeMeasure,PrimaryOutcomeDescription")


def _date(s: str | None) -> str | None:
    if not s:
        return None
    return s + "-01" if len(s) == 7 else s


def trial_facts(study: dict) -> dict:
    ps = study.get("protocolSection", {}) or {}
    st = ps.get("statusModule", {}) or {}
    dm = ps.get("designModule", {}) or {}
    ctrl = control_arm(ps)
    return {
        "nct_id": ps.get("identificationModule", {}).get("nctId"),
        "title": ps.get("identificationModule", {}).get("briefTitle"),
        "acronym": ps.get("identificationModule", {}).get("acronym"),
        "study_ids": [x for x in [((ps.get("identificationModule", {}) or {}).get("orgStudyIdInfo") or {}).get("id")]
                      + [i.get("id") for i in (ps.get("identificationModule", {}) or {}).get("secondaryIdInfos") or []]
                      if x],
        "sponsor": (ps.get("sponsorCollaboratorsModule", {}) or {}).get("leadSponsor", {}).get("name"),
        "phase": "/".join(dm.get("phases") or []),
        "condition": ", ".join((ps.get("conditionsModule", {}) or {}).get("conditions") or []),
        "enrollment": (dm.get("enrollmentInfo") or {}).get("count"),
        "status": st.get("overallStatus"),
        "why_stopped": st.get("whyStopped"),
        "start": _date((st.get("startDateStruct") or {}).get("date")),
        "completion": _date((st.get("primaryCompletionDateStruct") or {}).get("date")
                            or (st.get("completionDateStruct") or {}).get("date")),
        "control_arm": ctrl["control_arm"],
        "masking": ((dm.get("designInfo") or {}).get("maskingInfo") or {}).get("masking"),
        "primary_outcome": "; ".join(o.get("measure", "") for o in
                                     ((ps.get("outcomesModule") or {}).get("primaryOutcomes") or [])),
        "has_results": study.get("hasResults"),
        **{k: v for k, v in classify_primary(ps).items() if k != "primary_outcome_text"},
        "intervention_keys": sorted({k for iv in (ps.get("armsInterventionsModule") or {}).get("interventions") or []
                                     for k in [molecule_key(iv.get("name"))] + [molecule_key(o) for o in
                                                                                 iv.get("otherNames") or []] if k}),
    }


def evidence_grade(control: str, masking: str | None) -> str:
    """controlled_masked: comparator arm + >= double masking; controlled_open:
    comparator, NONE or SINGLE masking; single_arm: no comparator arm (includes
    dose-comparison designs); unknown: comparator not determinable."""
    if control == "yes":
        return "controlled_masked" if masking in MASKED else "controlled_open"
    if control == "no":
        return "single_arm"
    return "unknown"


def find_trials(http, key: str, sponsor: str | None, cap: int = 200,
                keys: set | None = None) -> tuple[list[dict], bool]:
    """All trials naming this molecule (every spelling, any sponsor), unioned
    with the sponsor's trials whose interventions normalise to the same key.
    Returns (trials, truncated)."""
    seen, out, truncated = set(), [], False
    queries = [{"query.intr": v} for v in code_variants(key)]
    if sponsor:
        queries.append({"query.spons": sponsor})
    for q in queries:
        try:
            d = http.get_json(CTGOV, {**q, "pageSize": str(cap), "fields": FIELDS, "format": "json"})
        except Exception:  # noqa: BLE001  a query CT.gov rejects (400) is skipped, not fatal
            continue
        studies = d.get("studies", [])
        truncated = truncated or (len(studies) >= cap and "query.intr" in q)
        for s in studies:
            f = trial_facts(s)
            if f["nct_id"] in seen:
                continue
            if "query.spons" in q and not ((keys or {key}) & set(f["intervention_keys"])):
                continue
            seen.add(f["nct_id"])
            out.append(f)
    return out, truncated


# --------------------------------------------------------- outcome sources --
FUTILITY = re.compile(r"futility|lack of efficacy|insufficient efficacy|did not meet|failed to (?:meet|show|demonstrate)|"
                      r"not (?:meet|achieve)[^.]{0,30}endpoint|no (?:clinical )?benefit|efficacy (?:was )?not", re.I)
MISSED = re.compile(r"\b(?:did not|didn't|failed to|does not|was unable to)\s+(?:meet|achieve|reach|demonstrate)"
                    r"[^.]{0,60}?\bprimary (?:efficacy )?end ?point", re.I)
MET = re.compile(r"\b(?:met|achieved|reached|hit)\s+(?:its|the|both|all)?\s*(?:trial['’]s\s+|study['’]s\s+)?"
                 r"(?:co-)?primary (?:efficacy )?end ?points?\b|\bprimary (?:efficacy )?end ?points? (?:was|were) met\b",
                 re.I)
DESIGN_ONLY = re.compile(r"\bprimary end ?point (?:of|for) the (?:trial|study) (?:is|was|will be)\b|\bdesigned to\b",
                         re.I)
_OTHER_CODE = re.compile(r"\b[A-Z]{2,6}[- ]?\d{2,6}[A-Z]?\b")


def why_stopped_outcome(t: dict) -> dict | None:
    if t["why_stopped"] and FUTILITY.search(t["why_stopped"]):
        return {"outcome": "missed_primary", "source": "whyStopped", "evidence": t["why_stopped"],
                "url": f"https://clinicaltrials.gov/study/{t['nct_id']}"}
    return None


HIGHER_BETTER = re.compile(r"surviv|response|responder|remission|success|\bclear|cure|healing|free\b|-free|"
                           r"time to (?:progression|relapse|recurrence|death|event|first)", re.I)
LOWER_BETTER = re.compile(r"\brate of|annuali[sz]ed|number of (?:\w+ ){0,3}(?:events?|exacerbations?|seizures?|relapses?|"
                          r"attacks?|days)|incidence|mortality|death|hospitali[sz]", re.I)


def _norm_label(x: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()


def _arm_roles(ps: dict) -> dict:
    """{normalised arm label: 'experimental' | 'control'} from armGroups types and control vocabulary."""
    from .derive import COMPARATOR_TYPES, CONTROL_WORDS
    arms = ((ps.get("armsInterventionsModule") or {}).get("armGroups") or [])
    out = {}
    for a in arms:
        lbl = _norm_label(a.get("label"))
        if a.get("type") in COMPARATOR_TYPES or CONTROL_WORDS.fullmatch((a.get("label") or "").strip()):
            out[lbl] = "control"
        elif a.get("type") == "EXPERIMENTAL":
            out[lbl] = "experimental"
    if "experimental" not in out.values() and len(arms) == 2:
        # every arm typed comparator (ONCY NCT01622543: "Folfox plus Bevacizumab and Reolysin" and
        # "Folfox plus Bevacizumab", both ACTIVE_COMPARATOR): an add-on arm, whose label or
        # interventions strictly contain the other arm's, is the experimental one
        (a, b) = arms
        la, lb = _norm_label(a.get("label")), _norm_label(b.get("label"))
        ia, ib = set(a.get("interventionNames") or []), set(b.get("interventionNames") or [])
        for x, y, lx, ly, ix, iy in ((a, b, la, lb, ia, ib), (b, a, lb, la, ib, ia)):
            if (ly and ly in lx and lx != ly) or (iy and ix > iy):
                out[lx], out[ly] = "experimental", "control"
                break
    return out


def _group_role(title: str, roles: dict) -> str | None:
    t = _norm_label(title)
    if t in roles:
        return roles[t]
    # containment, longest label first: "folfox plus bevacizumab and reolysin" is not the control
    # arm "folfox plus bevacizumab" just because it contains it
    hits = sorted(((len(lbl), r) for lbl, r in roles.items() if lbl and (lbl in t or t in lbl)), reverse=True)
    return hits[0][1] if hits and (len(hits) == 1 or hits[0][0] > hits[1][0]) else None


def effect_direction(ps: dict, om: dict, an: dict) -> str | None:
    """Which arm a posted primary analysis favours: 'experimental', 'control' or None
    (not determinable). Arms come from armGroups types; polarity from the outcome
    title (survival / response / remission: higher is better; event rates,
    incidence, mortality: lower is better). Group values are compared first; a
    hazard ratio on a survival / time-to outcome is read as experimental vs control
    (< 1 favours experimental) only if the values are absent. A change-from-baseline
    score has no polarity here: None, and the sponsor's own statement decides."""
    title = f"{om.get('title') or ''} {om.get('description') or ''}"
    pol = "higher" if HIGHER_BETTER.search(om.get("title") or "") else \
        ("lower" if LOWER_BETTER.search(om.get("title") or "") else None)
    roles = _arm_roles(ps)
    gtitle = {g.get("id"): g.get("title") for g in om.get("groups") or []}
    ids = an.get("groupIds") or []
    role = {g: _group_role(gtitle.get(g, ""), roles) for g in ids}
    exp = [g for g in ids if role[g] == "experimental"]
    ctl = [g for g in ids if role[g] == "control"]
    if len(exp) != 1 or len(ctl) != 1 or pol is None:
        return None
    vals = {}
    for c in om.get("classes") or []:
        for cat in c.get("categories") or []:
            for m in cat.get("measurements") or []:
                try:
                    vals.setdefault(m.get("groupId"), float(m.get("value")))
                except (TypeError, ValueError):
                    pass
        break
    if exp[0] in vals and ctl[0] in vals and vals[exp[0]] != vals[ctl[0]]:
        better_exp = vals[exp[0]] > vals[ctl[0]] if pol == "higher" else vals[exp[0]] < vals[ctl[0]]
        return "experimental" if better_exp else "control"
    if "hazard" in (an.get("paramType") or "").lower() and pol == "higher" and re.search(r"surviv|time to|free", title, re.I):
        try:
            hr = float(an.get("paramValue"))
        except (TypeError, ValueError):
            return None
        return "experimental" if hr < 1 else ("control" if hr > 1 else None)
    return None


def posted_results_outcome(http, t: dict) -> dict | None:
    """Primary-outcome statistical analyses posted on CT.gov.
      p < 0.05 favouring the experimental arm  -> met_primary
      p < 0.05 favouring the control arm       -> missed_primary (a significant harm is a failure:
                                                   ONCY NCT01622543, PFS 7.33 vs 9.13 months, HR 1.59, p 0.046)
      p < 0.05, direction not determinable     -> None (the sponsor's statement decides)
      analyses present and all p >= 0.05       -> missed_primary"""
    if not t["has_results"]:
        return None
    rec = http.get_json(f"{CTGOV}/{t['nct_id']}", {"format": "json"})
    ps_ = rec.get("protocolSection", {}) or {}
    ps = []
    for om in ((rec.get("resultsSection") or {}).get("outcomeMeasuresModule") or {}).get("outcomeMeasures") or []:
        if om.get("type") != "PRIMARY":
            continue
        for an in om.get("analyses") or []:
            m = re.search(r"([<>]?=?)\s*(\d*\.?\d+)", str(an.get("pValue") or ""))
            if m:
                ps.append((m.group(1), float(m.group(2)), om.get("title"), om, an))
    if not ps:
        return None
    sig = [p for p in ps if p[1] < 0.05 or (p[0].startswith("<") and p[1] <= 0.05)]
    url = f"https://clinicaltrials.gov/study/{t['nct_id']}?tab=results"
    if sig:
        a, b, c, om, an = sig[0]
        d = effect_direction(ps_, om, an)
        ev = f"p {a}{b} on '{c}'" + (f", {an.get('paramType')} {an.get('paramValue')}" if an.get("paramValue") else "")
        if d == "experimental":
            return {"outcome": "met_primary", "source": "CT.gov posted primary analysis", "url": url,
                    "evidence": ev + ", favours the experimental arm", "p_value": f"p {a}{b}"}
        if d == "control":
            return {"outcome": "missed_primary", "source": "CT.gov posted primary analysis", "url": url,
                    "evidence": ev + ", favours the CONTROL arm (significant harm)", "p_value": f"p {a}{b}"}
        return None
    return {"outcome": "missed_primary", "source": "CT.gov posted primary analysis", "url": url,
            "evidence": "; ".join(f"p {a}{b} on '{c}'" for a, b, c, _o, _a in ps[:3])}


def _abbrev(condition: str) -> list[str]:
    """'Chronic Spontaneous Urticaria' -> ['chronic spontaneous urticaria', 'CSU']."""
    out = []
    for c in condition.split(","):
        c = c.strip()
        if not c:
            continue
        out.append(c.lower())
        words = [w for w in re.findall(r"[A-Za-z]+", c) if w.lower() not in ("of", "and", "the", "with", "in")]
        if len(words) >= 2:
            out.append("".join(w[0] for w in words).upper())
    return out


# A release whose headline or opening says one of these is about a process step, a designation or
# a presentation, never an outcome; it establishes nothing about any trial, whatever else it says
# (CGTX 2025-02-26 "Successfully Passes Pre-defined Futility Analysis", which also repeats that SHIMMER
# "met its primary endpoint of safety and tolerability").
NON_OUTCOME = re.compile(
    r"passe[sd] futility|passed the futility|passes pre-?defined futility|futility analysis|interim analysis|"
    r"continue without modification|DSMB recommends|enrollment (?:is )?complete|completes? enrollment|last patient|"
    r"dosing initiated|first patient|alignment with (?:the )?FDA|end-of-phase|meeting minutes|\bType [BC]\b|"
    r"breakthrough|fast track|orphan (?:drug )?designation|\bpatent|Notice of Allowance|presented at", re.I)
HEAD_BODY_CHARS = 500
_PVAL = re.compile(r"\bp\s*(?:[<=≤]|value of)\s*0?\.\d+", re.I)


def _strip_wrapper(t: str) -> str:
    lead = list(re.finditer(r"(?:EX-99\.?\d*|Exhibit\s+99\.?\d*)", t[:200], re.I))
    return t[lead[-1].end():].strip() if lead else t


def press_release_sentences(http, cik: int, submissions: dict, today: dt.date, days: int = 1095) -> list[dict]:
    """Outcome sentences from the sponsor's 8-K press releases (exhibit 99.x),
    last `days`. Each: {date, accepted, url, headline, sentence, outcome, doc_head,
    before, blocked}. `blocked` names the NON_OUTCOME phrase found in the
    headline + first 500 characters of the body, or in the sentence itself."""
    from .dilution import _headline
    since = (today - dt.timedelta(days=days)).isoformat()
    r = submissions.get("filings", {}).get("recent", {})
    out = []
    accepted_all = r.get("acceptanceDateTime") or [None] * len(r.get("form", []))
    for acc, form, d, items, accepted in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
                                             r.get("items", []), accepted_all):
        if form not in ("8-K", "8-K/A") or d < since or not re.search(r"7\.01|8\.01|2\.02", items or ""):
            continue
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
        try:
            names = [i["name"] for i in http.get_json(f"{base}/index.json")["directory"]["item"]]
        except Exception:  # noqa: BLE001
            continue
        for ex in [n for n in names if re.search(r"ex-?99|dex99", n, re.I) and n.endswith(".htm")]:
            try:
                t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", http.get_text(f"{base}/{ex}"))))
            except Exception:  # noqa: BLE001
                continue
            head = t[:600]
            headline = _headline(t)
            body = _strip_wrapper(t)
            opening = headline + " " + body[len(headline):len(headline) + HEAD_BODY_CHARS]
            blocked_doc = NON_OUTCOME.search(opening)
            for m in re.finditer(r"[^.]*?(?:primary (?:efficacy )?end ?points?)[^.]*\.", t, re.I):
                s_ = m.group(0).strip()
                if DESIGN_ONLY.search(s_) and not (MISSED.search(s_) or MET.search(s_)):
                    continue
                om = MISSED.search(s_) or MET.search(s_)
                if om:
                    blocked = blocked_doc or NON_OUTCOME.search(s_)
                    pv = _PVAL.search(s_)
                    out.append({"date": d, "accepted": accepted, "url": f"{base}/{ex}", "headline": headline,
                                "sentence": s_[:400], "p_value": pv.group(0) if pv else None,
                                "outcome": "missed_primary" if MISSED.search(s_) else "met_primary",
                                "doc_head": head,
                                "blocked": (f"'{blocked.group(0)}' in "
                                            f"{'release opening' if blocked_doc else 'sentence'}") if blocked else None,
                                # text BEFORE the outcome phrase: the indication it refers to is named here
                                "before": t[max(0, m.start() - 600):m.start()] + s_[:om.start()]})
    return out


def _last_pos(text: str, terms: list[str]) -> int:
    best = -1
    for t in terms:
        rx = rf"\b{re.escape(t)}\b"
        for m in re.finditer(rx, text) if t.isupper() else re.finditer(rx, text, re.I):
            best = max(best, m.start())
    return best


def _names_trial(text: str, t: dict) -> bool:
    if t["nct_id"] and t["nct_id"] in text:
        return True
    ac = (t.get("acronym") or "").strip()
    if ac and len(ac) >= 3 and re.search(rf"\b{re.escape(ac)}\b", text, re.I):
        return True
    # protocol numbers: "V006" in the sentence and in the org study ID "CLN-PRO-V006"
    for sid in t.get("study_ids") or []:
        for tok in re.findall(r"[A-Za-z]*\d+[A-Za-z0-9]*", sid):
            if len(tok) >= 3 and re.search(r"[A-Za-z]", tok) and re.search(rf"\b{re.escape(tok)}\b", text, re.I):
                return True
    return False


def attribute_press(sentences: list[dict], key: str, trial: dict, all_controlled: list[dict],
                    want: str | None = None) -> dict | None:
    """The sponsor-statement outcome for one prior trial, if a sentence can be
    attributed to it:
      - the release is not blocked (NON_OUTCOME in its opening or the sentence);
      - the sentence names the molecule, or the release headline does and the
        sentence names no other molecule code (EVO301 results share releases
        with EVO756);
      - if the sentence (or the 200 characters before it) names a prior controlled
        trial by NCT, acronym or protocol number, it belongs to that trial only
        (UPB 2025-11-05: "VIBRANT met its primary endpoint" is not a VALIANT statement);
      - otherwise, of all prior controlled trials of the molecule, this trial's
        condition is the one named CLOSEST BEFORE the outcome phrase ("...Phase 2b
        Trial in Chronic Spontaneous Urticaria - Trial did not meet the primary
        endpoint ... continue development in Atopic Dermatitis" is a CSU statement);
      - the release is dated after the trial started.
    want = "met_primary" / "missed_primary" restricts to one outcome."""
    for p in sorted(sentences, key=lambda x: x["date"]):
        if p.get("blocked") or (want and p["outcome"] != want):
            continue
        if trial["start"] and p["date"] < trial["start"]:
            continue
        other_codes = {c.lower().replace("-", "").replace(" ", "") for c in _OTHER_CODE.findall(p["sentence"])}
        names_key = text_mentions_key(p["sentence"], key) or \
            (text_mentions_key(p["doc_head"], key) and not (other_codes - {key}))
        if not names_key:
            continue
        # the sentence's own naming first, then the 200 characters before it
        named = [o for o in all_controlled if _names_trial(p["sentence"], o)] or \
            [o for o in all_controlled if _names_trial(p["sentence"] + " " + p["before"][-200:], o)]
        if named:
            if len(named) != 1 or named[0]["nct_id"] != trial["nct_id"]:
                continue
        else:
            pos = {o["nct_id"]: _last_pos(p["before"], _abbrev(o["condition"])) for o in all_controlled}
            mine = pos.get(trial["nct_id"], -1)
            if mine < 0 or any(v > mine for k, v in pos.items() if k != trial["nct_id"]):
                continue
            if sum(v == mine for v in pos.values()) > 1:
                continue        # one sentence, one trial: an unbroken tie attributes to nobody
        return {"outcome": p["outcome"], "source": f"sponsor 8-K press release {p['date']}", "url": p["url"],
                "evidence": p["sentence"], "date": p["date"], "accepted": p.get("accepted"),
                "headline": p.get("headline"), "p_value": p.get("p_value")}
    return None


def molecule_keys(name: str | None, other_names: list | None = None) -> list[str]:
    """Every key an intervention is known by: the name, codes in parentheses
    ('Exaluren (ELX-02)' -> exaluren, elx02) and each otherNames[] entry."""
    raw = [(name or "", True)] + [(x, False) for x in re.findall(r"\((.*?)\)", name or "")] + \
        [(x, False) for x in other_names or []]
    keys = []
    for r_, primary in raw:
        k = molecule_key(r_)
        if not k:
            continue
        n_tok = len(k.split())
        # a name of more than 4 words is a description ("Physician's Choice of Immune Checkpoint
        # Inhibitor: pembrolizumab, nivolumab, ..."), not a molecule; an alias must be short
        # ("Olvi-Vec", "GL-ONC", "GLV-1h68"; not "Brand of drug is based on institutional procurement")
        if n_tok > 4 or (not primary and (n_tok > 3 or len(k) > 25)):
            continue
        if GENERIC.search(k):
            continue
        if k not in keys:
            keys.append(k)
    return keys


GENERIC = re.compile(r"\b(study drug|active|matching|investigational product|ip|drug|treatment|therapy)\b", re.I)


FAIL_MIN_FOR_WATCHLIST = 2
UNTESTED_MIN_PRIOR = 3
WIDELY_USED_TRIALS = 150          # an intervention named in more registered trials than this is not the asset
EFFICACY_CLASSES = {"clinical", "biomarker"}
STOPPED = {"TERMINATED", "WITHDRAWN"}
SUCCESS_REACTION_FLAG = -0.15
FDA_URL = "https://api.fda.gov/drug/drugsfda.json"
_CODE_KEY = re.compile(r"[a-z]{1,6}\d{2,6}[a-z]?")
ASSET_TYPES = {"DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT"}


# ------------------------------------------------------ widely-used names --
def registered_trial_count(http, name: str) -> int | None:
    try:
        d = http.get_json(CTGOV, {"query.intr": name, "countTotal": "true", "pageSize": "1", "fields": "NCTId",
                                  "format": "json"})
    except Exception:  # noqa: BLE001
        return None
    return d.get("totalCount")


def fda_approval(http, name: str) -> dict | None:
    """Earliest original approval on Drugs@FDA (openFDA) whose generic or brand
    name contains every word of `name`: {application, holder, date}. None if not
    found (a 404 is openFDA's "no match")."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 2 and w not in ("and", "with")]
    if not words or _CODE_KEY.fullmatch(name):
        return None
    best = None
    for field in ("openfda.generic_name", "openfda.brand_name"):
        q = " AND ".join(f'{field}:"{w}"' for w in words)
        try:
            d = http.get_json(FDA_URL, {"search": q, "limit": "20"})
        except Exception:  # noqa: BLE001
            continue
        for r in d.get("results") or []:
            for sub in r.get("submissions") or []:
                if sub.get("submission_type") == "ORIG" and sub.get("submission_status") == "AP" \
                        and sub.get("submission_status_date"):
                    dd = sub["submission_status_date"]
                    dd = f"{dd[:4]}-{dd[4:6]}-{dd[6:8]}"
                    if best is None or dd < best["date"]:
                        best = {"application": r.get("application_number"), "holder": r.get("sponsor_name"),
                                "date": dd}
    return best


def _same_company(holder: str | None, sponsor: str | None) -> bool:
    w = lambda x: [t for t in re.findall(r"[a-z]+", (x or "").lower())  # noqa: E731
                   if t not in ("inc", "the", "corp", "ltd", "llc", "plc", "pharmaceuticals", "pharma", "therapeutics",
                                "biotherapeutics", "bio", "biosciences", "co")]
    a, b = w(holder), w(sponsor)
    return bool(a and b and a[0] == b[0])


def widely_used_reason(http, name: str, sponsor: str | None, before: str | None) -> str | None:
    """Why `name` is not an investigational asset, or None:
       - named in more than WIDELY_USED_TRIALS registered trials, or
       - FDA-approved before the candidate trial started, by a holder other than the sponsor."""
    n = registered_trial_count(http, name)
    if n is not None and n > WIDELY_USED_TRIALS:
        return f"{name}: {n} registered trials"
    a = fda_approval(http, name)
    if a and (before is None or a["date"] < before) and not _same_company(a["holder"], sponsor):
        return f"{name}: FDA {a['application']} approved {a['date']} ({a['holder']})"
    return None


# ------------------------------------------------- primary molecule key --
def filing_text(http, cik: int | None, submissions: dict | None, cache: dict | None = None) -> str:
    """Plain text of the sponsor's latest 10-Q / 10-K / 20-F (product-candidate names live here)."""
    if cache is not None and cik in cache:
        return cache[cik]
    txt = ""
    if cik and submissions:
        r = submissions.get("filings", {}).get("recent", {})
        for acc, form, doc in zip(r.get("accessionNumber", []), r.get("form", []), r.get("primaryDocument", [])):
            if form in ("10-Q", "10-K", "20-F", "10-Q/A", "10-K/A") and doc:
                try:
                    raw = http.get_text(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}")
                    txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))
                except Exception:  # noqa: BLE001
                    txt = ""
                break
    if cache is not None:
        cache[cik] = txt
    return txt


def _count_mentions(text: str, key: str) -> int:
    """'ct1812' matches CT1812 / CT-1812 / CT 1812; 'trifluridine tipiracil' matches either spacing."""
    parts = [re.escape(x) for x in re.findall(r"[a-z]+|\d+[a-z]?", key)]
    return len(re.findall(r"\b" + "[ -]?".join(parts) + r"\b", text, re.I)) if parts else 0


def identify_molecule(http, record: dict, sponsor: str | None, filing: str = "") -> dict:
    """The sponsor's own investigational molecule on this trial: ONE primary key.

    Every non-control intervention is a candidate. An intervention any of whose
    names is widely used (> 150 registered trials, or FDA-approved before this trial
    started by another holder) is excluded as a unit. The rest rank by
      1. a sponsor code (letters + digits: EVO756, CT1812, UPB101) or a mention in
         the sponsor's latest 10-Q/10-K,
      2. intervention type DRUG / BIOLOGICAL / GENETIC / COMBINATION_PRODUCT,
      3. mentions in that filing, then registration order.
    The top one is the primary key; its other names are synonyms; every other
    intervention is a combination partner."""
    ps = record.get("protocolSection", {}) or {}
    start = _date(((ps.get("statusModule") or {}).get("startDateStruct") or {}).get("date"))
    ivs = ((ps.get("armsInterventionsModule") or {}).get("interventions") or [])
    ranked, excluded, partners = [], [], []
    for i, iv in enumerate(ivs):
        keys = molecule_keys(iv.get("name"), iv.get("otherNames"))
        if not keys:
            continue
        why = next((w for w in (widely_used_reason(http, k, sponsor, start) for k in keys) if w), None)
        if why:
            excluded.append(why)
            partners.append(keys[0])
            continue
        mentions = sum(_count_mentions(filing, k) for k in keys) if filing else 0
        code = any(_CODE_KEY.fullmatch(k) for k in keys)
        ranked.append(((code or mentions > 0), iv.get("type") in ASSET_TYPES, mentions, -i, keys))
    ranked.sort(reverse=True)
    if not ranked:
        return {"primary_molecule_key": None, "molecule_synonyms": [], "combination_partners": partners,
                "widely_used_excluded": excluded, "_ranking": []}
    top = ranked[0][4]
    return {"primary_molecule_key": top[0], "molecule_synonyms": top[1:],
            "combination_partners": [r[4][0] for r in ranked[1:]] + partners,
            "widely_used_excluded": excluded,
            "_ranking": [f"{r[4][0]} (code/filing={r[0]}, drug/bio={r[1]}, filing mentions={r[2]})" for r in ranked]}


# ------------------------------------------------------------ track record --
def _not_outcome(reason: str) -> dict:
    return {"outcome": "unknown", "source": None, "url": None, "evidence": None, "note": reason}


def track_record_for_key(http, key: str | None, synonyms: list[str], sponsors: list[str], cik: int | None,
                         submissions: dict | None, closes: list, today: dt.date, press_cache: dict | None = None,
                         exclude_ncts: set | None = None) -> dict:
    """Molecule-level facts: identical for every row with the same primary key.
    Prior = COMPLETED / TERMINATED / WITHDRAWN / SUSPENDED trials of the key or a
    synonym (any sponsor), excluding the screen's own candidate trials.

    A prior controlled trial's outcome counts (success or failure) only if
      - the trial has a comparator arm AND a clinical or biomarker primary outcome, and
      - a source states it: whyStopped futility (failures only), a CT.gov posted
        primary analysis, or an unblocked sponsor 8-K sentence saying the primary
        endpoint was met / not met.
    A TERMINATED or WITHDRAWN trial is never a success: termination supersedes any
    earlier interim communication."""
    from .dilution import one_day_return
    if not key:
        return {**_EMPTY_TR, "molecule_track_record": "no_primary_molecule", "_prior_trials": []}
    exclude_ncts = exclude_ncts or set()
    keys = [key] + [k for k in synonyms if k != key]
    trials, seen = [], set()
    for k in keys:
        for sp in sponsors or [None]:
            found, _trunc = find_trials(http, k, sp, keys=set(keys))
            for t in found:
                if t["nct_id"] not in seen:
                    seen.add(t["nct_id"])
                    trials.append(t)
    trials.sort(key=lambda t: (t["start"] or "", t["nct_id"]))
    prior = [t for t in trials if t["status"] in PRIOR_STATUSES and t["nct_id"] not in exclude_ncts]
    controlled = [t for t in prior if t["control_arm"] == "yes"]
    sentences = None
    if cik and submissions:
        if press_cache is not None and cik in press_cache:
            sentences = press_cache[cik]
        else:
            sentences = press_release_sentences(http, cik, submissions, today)
            if press_cache is not None:
                press_cache[cik] = sentences
    for t in controlled:
        efficacy_primary = bool(set(t.get("primary_outcome_classes") or []) & EFFICACY_CLASSES)
        o = why_stopped_outcome(t) or posted_results_outcome(http, t)
        if o is None and sentences:
            for k in keys:
                o = attribute_press(sentences, k, t, controlled)
                if o:
                    break
        if o and not efficacy_primary:
            o = _not_outcome(f"{o['outcome']} stated ({o['source']}) but the registered primary is "
                             f"{'/'.join(t.get('primary_outcome_classes') or ['none'])}, not clinical or biomarker")
        elif o and o["outcome"] == "met_primary" and t["status"] in STOPPED:
            o = _not_outcome(f"met_primary stated ({o['source']}) but the trial is {t['status']}: termination "
                             f"supersedes")
        t["outcome"] = o or _not_outcome("no source states the outcome")
    fails = [t for t in controlled if t["outcome"]["outcome"] == "missed_primary"]
    wins = [t for t in controlled if t["outcome"]["outcome"] == "met_primary"]
    unknown = [t for t in controlled if t["outcome"]["outcome"] == "unknown"]
    clinical_prior = [t for t in prior if t.get("primary_outcome_is_clinical")]
    safety_only_prior = [t for t in prior if t.get("primary_is_safety_only")]
    if not controlled:
        verdict = "naive"
    elif fails and wins:
        verdict = "mixed"
    elif wins:
        verdict = "validated"
    elif fails:
        verdict = "repeat_failure"
    else:
        verdict = "controlled_outcomes_unknown"
    untested = not clinical_prior and len(prior) >= UNTESTED_MIN_PRIOR
    if untested and verdict in ("naive", "controlled_outcomes_unknown", "validated"):
        verdict = "untested"
    # market reaction to each stated success: the announcing release (for a posted-results
    # success, the first unblocked sponsor release attributable to the trial with "met")
    reactions = []
    for t in wins:
        o = t["outcome"]
        ann = o if o.get("date") else next((a for a in (attribute_press(sentences or [], k, t, controlled,
                                                                          want="met_primary") for k in keys) if a), None)
        if not ann:
            reactions.append({"nct": t["nct_id"], "date": None, "return": None, "headline": None,
                              "note": "no attributable announcement found"})
            continue
        session, ret = one_day_return(closes or [], ann.get("accepted"), ann["date"])
        reactions.append({"nct": t["nct_id"], "date": ann["date"], "session": session, "return": ret,
                          "headline": ann.get("headline"), "url": ann.get("url")})
    rets = [r["return"] for r in reactions if r["return"] is not None]
    return {
        "prior_trials_with_clinical_primary": len(clinical_prior),
        "prior_trials_safety_primary_only": len(safety_only_prior),
        "largest_prior_clinical_primary_n": max((t["enrollment"] or 0 for t in clinical_prior), default=0),
        "track_record_warning": (f"{len(prior)} prior trials of {'|'.join(keys)}, none with a clinical primary "
                                 f"endpoint ({len(safety_only_prior)} safety/PK-only): efficacy claims from them are "
                                 f"exploratory") if untested else None,
        "prior_trials_total": len(prior),
        "prior_controlled_trials": len(controlled),
        "prior_controlled_failures": len(fails),
        "prior_controlled_successes": len(wins),
        "prior_controlled_outcome_unknown": len(unknown),
        "prior_failure_indications": sorted({t["condition"] for t in fails}),
        "prior_failure_ncts": [t["nct_id"] for t in fails],
        "prior_success_ncts": [t["nct_id"] for t in wins],
        "prior_terminated_for_futility": any(why_stopped_outcome(t) for t in prior),
        "molecule_track_record": verdict,
        "track_record_evidence_url": " | ".join(t["outcome"]["url"] for t in fails + wins if t["outcome"]["url"]),
        "prior_success_reactions": reactions,
        "worst_prior_success_1d": min(rets) if rets else None,
        "success_reaction_flag": any(r < SUCCESS_REACTION_FLAG for r in rets) if wins else None,
        "_prior_trials": prior,
    }


_EMPTY_TR = {"prior_trials_with_clinical_primary": 0, "prior_trials_safety_primary_only": 0,
             "largest_prior_clinical_primary_n": 0, "track_record_warning": None, "prior_trials_total": 0,
             "prior_controlled_trials": 0, "prior_controlled_failures": 0, "prior_controlled_successes": 0,
             "prior_controlled_outcome_unknown": 0, "prior_failure_indications": [], "prior_failure_ncts": [],
             "prior_success_ncts": [], "prior_terminated_for_futility": False, "track_record_evidence_url": "",
             "prior_success_reactions": [], "worst_prior_success_1d": None, "success_reaction_flag": None}
TRACK_FIELDS = list(_EMPTY_TR) + ["molecule_track_record"]


def track_record(http, record: dict, sponsor: str, cik: int | None, submissions: dict | None,
                 experimental_names: list[str] | None, today: dt.date, press_cache: dict | None = None,
                 closes: list | None = None) -> dict:
    """One trial end to end (live tests): identify the primary molecule, then its
    molecule-level track record, plus this trial's evidence grade."""
    ident = identify_molecule(http, record, sponsor, filing_text(http, cik, submissions))
    me = trial_facts(record)
    tr = track_record_for_key(http, ident["primary_molecule_key"], ident["molecule_synonyms"], [sponsor], cik,
                              submissions, closes or [], today, press_cache, {me["nct_id"]})
    return {**ident, **tr, "candidate_evidence_grade": evidence_grade(me["control_arm"], me["masking"])}


def track_section(tr: dict) -> list[str]:
    """Dossier section G: every prior trial and the stated outcome of each controlled one."""
    out = ["## G. Intervention track record", "",
           f"Primary molecule key: `{tr.get('primary_molecule_key') or '(none)'}`; synonyms "
           f"`{'|'.join(tr.get('molecule_synonyms') or []) or '-'}`; combination partners "
           f"`{'|'.join(tr.get('combination_partners') or []) or '-'}`.", "",
           f"Excluded as widely used (> {WIDELY_USED_TRIALS} registered trials, or FDA-approved before this trial by "
           f"another holder): {'; '.join(tr.get('widely_used_excluded') or []) or 'none'}.", "",
           "Prior = status COMPLETED / TERMINATED / WITHDRAWN / SUSPENDED, any sponsor, the key or a synonym; "
           "identical for every row with this key. An outcome counts only on a comparator-arm trial with a clinical "
           "or biomarker primary, stated by whyStopped, a posted CT.gov primary analysis or an unblocked sponsor 8-K "
           "sentence; a terminated or withdrawn trial is never a success.", "",
           f"- prior trials: **{tr['prior_trials_total']}**; with a comparator arm: **{tr['prior_controlled_trials']}**"
           f" (missed primary: {tr['prior_controlled_failures']}, met primary: {tr['prior_controlled_successes']}, "
           f"outcome not established: {tr['prior_controlled_outcome_unknown']})",
           f"- prior trials with a clinical primary endpoint: **{tr['prior_trials_with_clinical_primary']}** "
           f"(largest n {tr['largest_prior_clinical_primary_n']}); safety/PK-only primaries: "
           f"{tr['prior_trials_safety_primary_only']}",
           f"- molecule_track_record (mechanical): **{tr['molecule_track_record']}**"
           + (f"  \n  WARNING: {tr['track_record_warning']}" if tr.get("track_record_warning") else ""),
           f"- market reaction to prior successes: {tr.get('prior_success_reactions') or 'none'}"
           + ("  \n  **success_reaction_flag**: a stated success fell more than 15% on its announcement day"
              if tr.get("success_reaction_flag") else ""),
           f"- this trial's design (candidate_evidence_grade): **{tr.get('candidate_evidence_grade')}**", ""]
    if tr["_prior_trials"]:
        out += ["| NCT | phase | condition | n | status | comparator | masking | primary outcome | primary class | "
                "outcome (source) |", "|---|---|---|---|---|---|---|---|---|---|"]
        for t in tr["_prior_trials"]:
            o = t.get("outcome") or {"outcome": "not controlled", "source": None}
            src = f"{o['outcome']} ({o['source']})" if o.get("source") else o["outcome"]
            out.append(f"| {t['nct_id']} | {t['phase']} | {t['condition'][:60]} | {t['enrollment']} | {t['status']}"
                       f"{' (' + t['why_stopped'][:60] + ')' if t['why_stopped'] else ''} | {t['control_arm']} | "
                       f"{t['masking']} | {t['primary_outcome'][:90]} | {'/'.join(t.get('primary_outcome_classes') or [])} | "
                       f"{src} |")
        out.append("")
        for t in tr["_prior_trials"]:
            o = t.get("outcome")
            if o and o.get("evidence"):
                out += [f"{t['nct_id']} evidence ({o['source']}, {o['url']}):", f"> {o['evidence']}", ""]
            elif o and o.get("note") and o["note"] != "no source states the outcome":
                out += [f"{t['nct_id']}: not counted: {o['note']}", ""]
    return out
