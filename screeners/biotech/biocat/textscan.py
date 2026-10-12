"""Free-text scanning: vocabulary presence with the field path and sentence.

Every result is PRESENCE or ABSENCE plus evidence. Nothing here weighs,
scores or interprets what it finds.
"""

from __future__ import annotations

import re

# Primary-endpoint vocabulary for the open-label observation (spec section B).
# Acronyms are matched case-sensitively so "PRO" does not hit "pro-inflammatory"
# and "scale" etc. are whole-word, case-insensitive.
SUBJECTIVE_WORDS = ["pain", "quality of life", "global assessment", "satisfaction", "fatigue",
                    "symptom score", "patient-reported", "questionnaire", "scale",
                    "clinician-assessed", "investigator-assessed"]
SUBJECTIVE_ACRONYMS = ["VAS", "NRS", "QoL", "PGA", "IGA", "PRO"]

MULTIPLICITY = ["alpha", "Bonferroni", "hierarchical", "gatekeeping", "type I error", "multiplicity"]

STAT_SIGNALS = ["interim analysis", "futility", "intention-to-treat", "per-protocol", "adaptive design",
                "data monitoring committee", "DSMB", "sample size re-estimation"]


def _pattern(term: str, case_sensitive: bool) -> re.Pattern:
    body = re.escape(term).replace(r"\-", "[-\\s]?").replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", 0 if case_sensitive else re.I)


_SUBJ = [(t, _pattern(t, False)) for t in SUBJECTIVE_WORDS] + \
        [(t, _pattern(t, True)) for t in SUBJECTIVE_ACRONYMS]
# "alpha" alone also names molecules (interferon alpha-2b, alpha-1 antitrypsin,
# alpha-synuclein); exclude a following hyphen/digit so only the statistical
# sense is reported. The evidence sentence is printed either way.
_ALPHA = re.compile(r"(?<![A-Za-z0-9-])alpha(?![A-Za-z0-9]|\s*-|\s*\d)", re.I)
_MULT = [(t, _ALPHA if t == "alpha" else _pattern(t, False)) for t in MULTIPLICITY]
# bare "adaptive" also hits "Adaptive Behavior Composite Score"; require the design sense
_ADAPTIVE = re.compile(r"(?<![A-Za-z0-9])adaptive\s+(?:design|randomi[sz]ation|trial|study|seamless|"
                       r"platform|enrichment|phase)", re.I)
_STAT = [(t, _ADAPTIVE if t == "adaptive design" else _pattern(t, t == "DSMB")) for t in STAT_SIGNALS]


def walk_text(obj, path: str = "") -> list[tuple[str, str]]:
    """(json_path, text) for every string in a nested record."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += walk_text(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += walk_text(v, f"{path}[{i}]")
    elif isinstance(obj, str) and len(obj) > 3:
        out.append((path, obj))
    return out


def _sentence(text: str, start: int, end: int, width: int = 240) -> str:
    """The sentence around a match, trimmed to `width` characters."""
    left = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    right_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    s = text[left + 1 if left >= 0 else 0:right].strip()
    if len(s) > width:
        mid = start - (left + 1 if left >= 0 else 0)
        lo = max(0, mid - width // 2)
        s = ("..." if lo else "") + s[lo:lo + width] + "..."
    return " ".join(s.split())


def find_terms(texts: list[tuple[str, str]], patterns) -> list[dict]:
    hits = []
    for term, pat in patterns:
        for path, text in texts:
            m = pat.search(text)
            if m:
                hits.append({"term": term, "field": path,
                             "evidence": _sentence(text, m.start(), m.end())})
    return hits


def subjective_terms(endpoint_text: str) -> list[str]:
    return sorted({t for t, p in _SUBJ if p.search(endpoint_text or "")})


def multiplicity_hits(texts):
    return find_terms(texts, _MULT)


def stat_signal_hits(texts):
    return find_terms(texts, _STAT)
