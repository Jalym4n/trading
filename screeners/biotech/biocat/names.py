"""Company-name normalisation for joining ClinicalTrials.gov sponsors to SEC filers.

Matching is EXACT equality of normalised keys. There is no fuzzy matching:
a near miss is a rejected candidate, never a guess. A wrong join attaches
another company's balance sheet to a thesis; a missed join costs one name.
"""

from __future__ import annotations

import re
import unicodedata

# Removed only from the END of the name, repeatedly, so "Holdings Pharmaceuticals
# Inc" fully strips but a word like "Labs" in "Labstyle Innovations" stays.
SUFFIXES = {
    # legal forms
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "plc", "llc", "lp", "llp", "sa", "s/a", "ag", "nv", "bv", "gmbh", "se", "spa",
    "ab", "as", "asa", "oy", "oyj", "kk", "sl", "sas", "srl", "pty", "pte", "bhd",
    # descriptors named in the spec, plus their obvious variants
    "holdings", "holding", "group",
    "pharmaceuticals", "pharmaceutical", "pharma",
    "therapeutics", "therapeutic",
    "biosciences", "bioscience",
    "technologies", "technology",
    "labs", "laboratories", "laboratory",
}
TRAILING_JOINERS = {"and", "the", "of"}
_SEC_TAG = re.compile(r"\s*/\s*(?:[a-z]{2,3}|adr|new|cayman(?: islands)?|marshall islands)\.?\s*$")


def _fold(s: str) -> str:
    """Accents to ASCII ("Química" -> "quimica")."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def normalise(name: str) -> str:
    """Canonical key for a company name. Empty string if nothing is left."""
    s = _fold(name or "").lower()
    s = s.replace("&", " and ")
    # SEC conformed-name tags: "VERTEX PHARMACEUTICALS INC / MA", "TuHURA ... Inc./NV",
    # "... /ADR". Two or more letters only, so the legal forms "A/S" and "S/A" survive.
    s = _SEC_TAG.sub(" ", s)
    s = re.sub(r"\(.*?\)", " ", s)            # "(Shanghai)", "(Suzhou)"
    s = s.replace("s/a", " sa ")
    # dotted abbreviations: "s.a." -> "sa", "n.v." -> "nv", "s.p.a." -> "spa"
    s = re.sub(r"\b([a-z])\.([a-z])\.?", r"\1\2", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)          # punctuation, commas, dots, dashes
    toks = s.split()
    if toks and toks[0] == "the":
        toks = toks[1:]
    while len(toks) > 1 and (toks[-1] in SUFFIXES or toks[-1] in TRAILING_JOINERS):
        toks.pop()
    return " ".join(toks)


_DISPLAY = re.compile(r"^(?P<name>.*?)\s+(?:\((?P<ticker>[A-Z0-9.\-, ]+)\)\s+)?\(CIK (?P<cik>\d{10})\)\s*$")


def parse_display_name(s: str) -> dict | None:
    """'Apellis Pharmaceuticals, Inc.  (APLS)  (CIK 0001492422)' -> name/ticker/cik.

    Returns None when the string does not have the expected shape, so a format
    change at SEC fails closed (no match) rather than open.
    """
    m = _DISPLAY.match((s or "").strip())
    if not m:
        return None
    tick = m.group("ticker")
    tickers = [t.strip() for t in tick.split(",")] if tick else []
    return {"name": m.group("name").strip(), "tickers": [t for t in tickers if t],
            "cik": int(m.group("cik"))}
