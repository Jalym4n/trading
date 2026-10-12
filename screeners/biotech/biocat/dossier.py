"""Stage 5: per-candidate protocol dossier (markdown).

EXTRACT AND PRESENT, NEVER JUDGE. Every line is a field value, a count, a
presence/absence, or a mechanical derivation stated with its inputs. No
adjectives, no scores, no recommendations. Each item cites its API field.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from .textscan import multiplicity_hits, stat_signal_hits, subjective_terms, walk_text
from .trials import months_between, parse_date

CONTROL_TYPES = {"PLACEBO_COMPARATOR", "ACTIVE_COMPARATOR", "SHAM_COMPARATOR"}


def _g(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
        if d is None:
            return default
    return d


def _block(text: str | None) -> str:
    if not text:
        return "> (field empty)"
    return "\n".join("> " + line if line.strip() else ">" for line in text.strip().splitlines())


def endpoint_section(ps: dict) -> tuple[list[str], dict]:
    om = ps.get("outcomesModule", {}) or {}
    prim = om.get("primaryOutcomes", []) or []
    sec = om.get("secondaryOutcomes", []) or []
    out = ["## A. Primary endpoint, verbatim", "",
           f"Source: `protocolSection.outcomesModule.primaryOutcomes[]`. "
           f"primaryOutcomes count = **{len(prim)}**; secondaryOutcomes count = **{len(sec)}**.", ""]
    for i, p in enumerate(prim):
        out += [f"### Primary outcome {i + 1}", "",
                f"`primaryOutcomes[{i}].measure`", _block(p.get("measure")), "",
                f"`primaryOutcomes[{i}].description`", _block(p.get("description")), "",
                f"`primaryOutcomes[{i}].timeFrame`", _block(p.get("timeFrame")), ""]
    mult = []
    if len(prim) > 1:
        mult = multiplicity_hits(walk_text(ps))
        out += ["### Multiplicity language (searched because primaryOutcomes count > 1)", "",
                "Terms searched: alpha, Bonferroni, hierarchical, gatekeeping, type I error, multiplicity.",
                ""]
        if mult:
            out += ["| term | field | sentence |", "|---|---|---|"] + \
                   [f"| {h['term']} | `{h['field']}` | {h['evidence']} |" for h in mult]
        else:
            out.append("Found: **none** of the terms, in any free-text field of the record.")
        out.append("")
    ep_text = " ".join(f"{p.get('measure', '')} {p.get('description', '')}" for p in prim)
    return out, {"n_primary": len(prim), "n_secondary": len(sec), "endpoint_text": ep_text,
                 "multiplicity_hits": len(mult)}


def bias_section(ps: dict, ep_text: str) -> tuple[list[str], dict]:
    di = _g(ps, "designModule", "designInfo", default={}) or {}
    masking = _g(di, "maskingInfo", "masking")
    who = _g(di, "maskingInfo", "whoMasked", default=[]) or []
    arms = _g(ps, "armsInterventionsModule", "armGroups", default=[]) or []
    types = Counter(a.get("type", "UNSPECIFIED") for a in arms)
    controls = sorted(t for t in types if t in CONTROL_TYPES)
    terms = subjective_terms(ep_text)
    out = ["## B. Bias controls", "",
           "| field | value |", "|---|---|",
           f"| `designModule.designInfo.allocation` | {di.get('allocation')} |",
           f"| `designModule.designInfo.maskingInfo.masking` | {masking} |",
           f"| `designModule.designInfo.maskingInfo.whoMasked` | {', '.join(who) or '(none listed)'} |",
           f"| `designModule.designInfo.interventionModel` | {di.get('interventionModel')} |",
           f"| `armsInterventionsModule.armGroups[].type` | "
           f"{', '.join(f'{k} x{v}' for k, v in sorted(types.items())) or '(no arms listed)'} |",
           f"| control arm types present (placebo / active / sham) | {', '.join(controls) or 'none'} |", ""]
    if masking == "NONE" and terms:
        out += [f"Derived observation: open-label; primary endpoint matched [{', '.join(terms)}]. "
                f"(Inputs: masking=NONE; vocabulary match on primaryOutcomes[].measure + description.)", ""]
    elif terms:
        out += [f"Primary endpoint text matched [{', '.join(terms)}]; masking={masking}.", ""]
    return out, {"masking": masking, "allocation": di.get("allocation"),
                 "has_control_arm": bool(controls)}


def enrollment_section(ps: dict, today: dt.date) -> tuple[list[str], dict]:
    ei = _g(ps, "designModule", "enrollmentInfo", default={}) or {}
    n, typ = ei.get("count"), ei.get("type")
    start = parse_date(_g(ps, "statusModule", "startDateStruct", "date"))
    locs = _g(ps, "contactsLocationsModule", "locations", default=[]) or []
    countries = Counter(l.get("country", "unknown") for l in locs)
    months = months_between(start, today) if start else None
    per_site = round(n / len(locs), 1) if n and locs else None
    out = ["## C. Enrollment and operations", "",
           "| field | value |", "|---|---|",
           f"| `designModule.enrollmentInfo.count` | {n} |",
           f"| `designModule.enrollmentInfo.type` | {typ} |",
           f"| `statusModule.startDateStruct.date` | {_g(ps, 'statusModule', 'startDateStruct', 'date')} |",
           f"| months since start (derived) | {months} |",
           f"| site count, `contactsLocationsModule.locations[]` | {len(locs)} |",
           f"| countries | {', '.join(f'{c} {k}' for c, k in countries.most_common()) or '(none listed)'} |",
           f"| patients per site (derived: count / sites) | {per_site} |", ""]
    if typ == "ESTIMATED" and months is not None and months > 24:
        out += [f"Derived observation: enrollmentInfo.type=ESTIMATED and the trial started "
                f"{months} months ago (> 24).", ""]
    return out, {"enrollment": n, "enrollment_type": typ, "n_sites": len(locs)}


def stats_section(ps: dict) -> list[str]:
    hits = stat_signal_hits(walk_text(ps))
    found = sorted({h["term"] for h in hits})
    out = ["## E. Statistical-plan terms in free text", "",
           "Searched: interim analysis, futility, intention-to-treat, per-protocol, adaptive, "
           "data monitoring committee, DSMB, sample size re-estimation.", "",
           f"Present: {', '.join(found) or 'none'}. "
           f"Absent: {', '.join(t for t in ['interim analysis', 'futility', 'intention-to-treat', 'per-protocol', 'adaptive', 'data monitoring committee', 'DSMB', 'sample size re-estimation'] if t not in found) or 'none'}.",
           ""]
    if hits:
        out += ["| term | field | sentence |", "|---|---|---|"] + \
               [f"| {h['term']} | `{h['field']}` | {h['evidence']} |" for h in hits]
        out.append("")
    if any(h["term"] in ("interim analysis", "futility") for h in hits):
        out += ["Note: an interim or futility analysis can end a trial before "
                "`primaryCompletionDate`, which would move the catalyst date.", ""]
    return out


def context_section(nct_id: str, sponsor: str, related: dict[str, list[dict]],
                    record: dict) -> list[str]:
    meshes = [m.get("term") for m in _g(record, "derivedSection", "interventionBrowseModule", "meshes",
                                         default=[]) or []]
    anc = [m.get("term") for m in _g(record, "derivedSection", "interventionBrowseModule", "ancestors",
                                      default=[]) or []]
    out = ["## F. Context", "",
           f"Intervention MeSH terms (`derivedSection.interventionBrowseModule.meshes`): "
           f"{', '.join(meshes) or '(none)'}", "",
           f"MeSH ancestors / class (`...ancestors`): {', '.join(anc[:12]) or '(none)'}", ""]
    for name, rows in related.items():
        rows = [r for r in rows if r["nct_id"] != nct_id]
        same = [r for r in rows if r["sponsor"] == sponsor]
        other = [r for r in rows if r["sponsor"] != sponsor]
        out += [f"### Intervention \"{name}\" (`query.intr`, all statuses)", "",
                f"Same sponsor, other trials: {len(same)}. Other sponsors: {len(other)}.", ""]
        if rows:
            out += ["| NCT | sponsor | phase | status | primary completion | results posted | why stopped |",
                    "|---|---|---|---|---|---|---|"]
            for r in (same + other)[:40]:
                out.append(f"| {r['nct_id']} | {r['sponsor']} | {r['phase']} | {r['status']} | {r['pcd']} | "
                           f"{r['has_results']} | {r['why_stopped'] or ''} |")
            if len(rows) > 40:
                out.append(f"| ... | {len(rows) - 40} more rows not shown | | | | | |")
            out.append("")
    return out


def build(row: dict, record: dict, history_md: list[str], related: dict, today: dt.date) -> tuple[str, dict]:
    ps = record.get("protocolSection", {})
    a, a_f = endpoint_section(ps)
    b, b_f = bias_section(ps, a_f["endpoint_text"])
    c, c_f = enrollment_section(ps, today)
    e = stats_section(ps)
    f = context_section(row["nct_id"], row["sponsor"], related, record)
    head = [f"# {row['ticker']} / {row['nct_id']}", "",
            f"{_g(ps, 'identificationModule', 'briefTitle')}", "",
            "| field | value |", "|---|---|"] + \
           [f"| {k} | {row.get(k)} |" for k in
            ("company", "cik", "sponsor", "phase", "catalyst_date", "date_type", "months_out", "market_cap",
             "avg_dollar_vol", "cash_usd", "cash_asof", "burn_q_usd", "burn_status", "runway_q",
             "runway_needed_q", "funded_through", "shelf_count", "atm_count", "dilution_risk")] + \
           ["", f"Generated {today.isoformat()} from ClinicalTrials.gov API v2 and SEC EDGAR. "
                "Fields are reported as registered; nothing here is an assessment.", ""]
    md = "\n".join(head + a + b + c + history_md + e + f) + "\n"
    return md, {**a_f, **b_f, **c_f}
