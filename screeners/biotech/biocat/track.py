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


def find_trials(http, key: str, sponsor: str | None, cap: int = 200) -> tuple[list[dict], bool]:
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
            if "query.spons" in q and key not in f["intervention_keys"]:
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


def posted_results_outcome(http, t: dict) -> dict | None:
    """Primary-outcome statistical analyses posted on CT.gov: any p < 0.05 ->
    met_primary; analyses present and all p >= 0.05 -> missed_primary."""
    if not t["has_results"]:
        return None
    rec = http.get_json(f"{CTGOV}/{t['nct_id']}", {"format": "json"})
    ps = []
    for om in ((rec.get("resultsSection") or {}).get("outcomeMeasuresModule") or {}).get("outcomeMeasures") or []:
        if om.get("type") != "PRIMARY":
            continue
        for an in om.get("analyses") or []:
            m = re.search(r"([<>]?=?)\s*(\d*\.?\d+)", str(an.get("pValue") or ""))
            if m:
                ps.append((m.group(1), float(m.group(2)), om.get("title")))
    if not ps:
        return None
    sig = [p for p in ps if p[1] < 0.05 or (p[0].startswith("<") and p[1] <= 0.05)]
    url = f"https://clinicaltrials.gov/study/{t['nct_id']}?tab=results"
    if sig:
        return {"outcome": "met_primary", "source": "CT.gov posted primary analysis", "url": url,
                "evidence": f"p {sig[0][0]}{sig[0][1]} on '{sig[0][2]}'"}
    return {"outcome": "missed_primary", "source": "CT.gov posted primary analysis", "url": url,
            "evidence": "; ".join(f"p {a}{b} on '{c}'" for a, b, c in ps[:3])}


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


def press_release_sentences(http, cik: int, submissions: dict, today: dt.date, days: int = 1095) -> list[dict]:
    """Outcome sentences from the sponsor's 8-K press releases (exhibit 99.x),
    last `days`. Each: {date, url, sentence, outcome, doc_head}."""
    since = (today - dt.timedelta(days=days)).isoformat()
    r = submissions.get("filings", {}).get("recent", {})
    out = []
    for acc, form, d, items in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
                                   r.get("items", [])):
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
            for m in re.finditer(r"[^.]*?(?:primary (?:efficacy )?end ?points?)[^.]*\.", t, re.I):
                s = m.group(0).strip()
                if DESIGN_ONLY.search(s) and not (MISSED.search(s) or MET.search(s)):
                    continue
                om = MISSED.search(s) or MET.search(s)
                if om:
                    out.append({"date": d, "url": f"{base}/{ex}", "sentence": s[:400],
                                "outcome": "missed_primary" if MISSED.search(s) else "met_primary",
                                "doc_head": head,
                                # text BEFORE the outcome phrase: the indication it refers to is named here
                                "before": t[max(0, m.start() - 600):m.start()] + s[:om.start()]})
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


def attribute_press(sentences: list[dict], key: str, trial: dict, all_controlled: list[dict]) -> dict | None:
    """The sponsor-statement outcome for one prior trial, if a sentence can be
    attributed to it:
      - the sentence names the molecule, or the release headline does and the
        sentence names no other molecule code (EVO301 results share releases
        with EVO756);
      - of all prior controlled trials of the molecule, this trial's condition
        is the one named CLOSEST BEFORE the outcome phrase ("...Phase 2b Trial in
        Chronic Spontaneous Urticaria - Trial did not meet the primary endpoint
        ... continue development in Atopic Dermatitis" is a CSU statement);
      - the release is dated after the trial started."""
    for p in sorted(sentences, key=lambda x: x["date"]):
        if trial["start"] and p["date"] < trial["start"]:
            continue
        other_codes = {c.lower().replace("-", "").replace(" ", "") for c in _OTHER_CODE.findall(p["sentence"])}
        names_key = text_mentions_key(p["sentence"], key) or \
            (text_mentions_key(p["doc_head"], key) and not (other_codes - {key}))
        if not names_key:
            continue
        pos = {o["nct_id"]: _last_pos(p["before"], _abbrev(o["condition"])) for o in all_controlled}
        mine = pos.get(trial["nct_id"], -1)
        if mine < 0 or any(v > mine for k, v in pos.items() if k != trial["nct_id"]):
            continue
        tied = [o for o in all_controlled if pos.get(o["nct_id"]) == mine]
        if len(tied) > 1:
            # one sentence, one trial: a tie must be broken by the sentence naming the trial
            # (HUMA: "our V006 Phase 3 trial ... did not meet" vs two trials with identical conditions)
            named = [o for o in tied if _names_trial(p["sentence"] + " " + p["before"][-200:], o)]
            if len(named) != 1 or named[0]["nct_id"] != trial["nct_id"]:
                continue
        return {"outcome": p["outcome"], "source": f"sponsor 8-K press release {p['date']}", "url": p["url"],
                "evidence": p["sentence"]}
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


def track_record(http, record: dict, sponsor: str, cik: int | None, submissions: dict | None,
                 experimental_names: list[str], today: dt.date, press_cache: dict | None = None) -> dict:
    """Prior-trial facts for the candidate's molecule(s). See module docstring."""
    ps = record.get("protocolSection", {}) or {}
    me = trial_facts(record)
    cand_start = me["start"] or today.isoformat()
    aim = ps.get("armsInterventionsModule", {}) or {}
    other = {iv.get("name"): iv.get("otherNames") for iv in aim.get("interventions") or []}
    keys, widely_used = [], []
    if not experimental_names:
        # drug arms mistyped (CGTX NCT05531656: CT1812 dose arms typed ACTIVE_COMPARATOR): use every
        # intervention that is not control vocabulary
        experimental_names = [iv.get("name") for iv in aim.get("interventions") or []
                              if iv.get("name") and molecule_key(iv.get("name"))]
    trials, seen = [], set()
    for n in experimental_names:
        group = molecule_keys(n, other.get(n))
        # one intervention is one unit: if any of its names is an established backbone (>= 200 trials),
        # every alias is excluded (bevacizumab's biosimilar names each fall under the cap: NTHI)
        found_by_key, backbone = {}, False
        for k in group:
            if k in keys or k in widely_used:
                continue
            found, truncated = find_trials(http, k, sponsor)
            if truncated:
                backbone = True
                break
            found_by_key[k] = found
        if backbone:
            widely_used.extend(k for k in group if k not in widely_used)
            continue
        for k, found in found_by_key.items():
            keys.append(k)
            for t in found:
                if t["nct_id"] not in seen:
                    seen.add(t["nct_id"])
                    trials.append(t)
    asset_keys = [k for k in keys if k not in widely_used]
    prior = [t for t in trials if t["nct_id"] != me["nct_id"] and t["status"] in PRIOR_STATUSES
             and t["start"] and t["start"] < cand_start]
    controlled = [t for t in prior if t["control_arm"] == "yes"]
    sentences = None
    for t in controlled:
        o = why_stopped_outcome(t) or posted_results_outcome(http, t)
        if o is None and cik and submissions:
            if sentences is None:
                if press_cache is not None and cik in press_cache:
                    sentences = press_cache[cik]
                else:
                    sentences = press_release_sentences(http, cik, submissions, today)
                    if press_cache is not None:
                        press_cache[cik] = sentences
            for k in asset_keys:
                o = attribute_press(sentences, k, t, controlled)
                if o:
                    break
        t["outcome"] = o or {"outcome": "unknown", "source": None, "url": None, "evidence": None}
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
    # never tested on an efficacy endpoint: overrides naive / unknown / validated (a "met" on a
    # safety primary validates nothing); mixed and repeat_failure keep their stated failures
    untested = not clinical_prior and len(prior) >= UNTESTED_MIN_PRIOR
    if untested and verdict in ("naive", "controlled_outcomes_unknown", "validated"):
        verdict = "untested"
    return {
        "prior_trials_with_clinical_primary": len(clinical_prior),
        "prior_trials_safety_primary_only": len(safety_only_prior),
        "largest_prior_clinical_primary_n": max((t["enrollment"] or 0 for t in clinical_prior), default=0),
        "track_record_warning": (f"{len(prior)} prior trials of {'|'.join(asset_keys)}, none with a clinical primary "
                                 f"endpoint ({len(safety_only_prior)} safety/PK-only): efficacy claims from them are "
                                 f"exploratory") if untested else None,
        "molecule_keys": "|".join(asset_keys) + (f" (excluded as widely used: {'|'.join(widely_used)})"
                                                 if widely_used else ""),
        "prior_trials_total": len(prior),
        "prior_controlled_trials": len(controlled),
        "prior_controlled_failures": len(fails),
        "prior_controlled_successes": len(wins),
        "prior_controlled_outcome_unknown": len(unknown),
        "prior_failure_indications": sorted({t["condition"] for t in fails}),
        "prior_failure_ncts": [t["nct_id"] for t in fails],
        "prior_terminated_for_futility": any(why_stopped_outcome(t) for t in prior),
        "molecule_track_record": verdict,
        "track_record_evidence_url": " | ".join(t["outcome"]["url"] for t in fails + wins if t["outcome"]["url"]),
        "candidate_evidence_grade": evidence_grade(me["control_arm"], me["masking"]),
        "_prior_trials": prior,
    }


def track_section(tr: dict) -> list[str]:
    """Dossier section G: every prior trial and the stated outcome of each controlled one."""
    out = ["## G. Intervention track record", "",
           f"Molecule keys searched (every spelling, any sponsor, plus the sponsor's own trials): "
           f"`{tr['molecule_keys'] or '(none)'}`. Prior = status COMPLETED / TERMINATED / WITHDRAWN / SUSPENDED "
           "and started before this trial.", "",
           f"- prior trials: **{tr['prior_trials_total']}**; with a comparator arm: **{tr['prior_controlled_trials']}**"
           f" (missed primary: {tr['prior_controlled_failures']}, met primary: {tr['prior_controlled_successes']}, "
           f"outcome not stated by any source: {tr['prior_controlled_outcome_unknown']})",
           f"- prior trials with a clinical primary endpoint: **{tr['prior_trials_with_clinical_primary']}** "
           f"(largest n {tr['largest_prior_clinical_primary_n']}); safety/PK-only primaries: "
           f"{tr['prior_trials_safety_primary_only']}",
           f"- molecule_track_record (mechanical): **{tr['molecule_track_record']}**"
           + (f"  \n  WARNING: {tr['track_record_warning']}" if tr.get("track_record_warning") else ""),
           f"- this trial's design (candidate_evidence_grade): **{tr['candidate_evidence_grade']}**", ""]
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
    return out
